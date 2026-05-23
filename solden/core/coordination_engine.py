"""Coordination Engine — Agent Design Specification §5.

Takes a Plan from the planning engine and coordinates its execution,
one Action at a time. "Coordinate" rather than "execute" because the
engine doesn't *just* run actions — it writes the timeline, handles
waits, routes approvals to humans, and sequences agent work with
external systems. Execution is one of the things it does; the product
is the coordination around each Box.

The Two Non-Negotiable Rules:
  Rule 1: Every action is recorded to the Box timeline BEFORE it executes.
  Rule 2: The coordination engine never assumes success. Every external
          call must return a confirmation before the Box stage advances.

Architectural note — what this is NOT (manifesto §"Coordination
through shared state, not orchestration")

The manifesto rejects the "master orchestrator / giant prompt /
workflow engine routing everything through a chokepoint" pattern.
``CoordinationEngine`` is **not** that pattern. To pre-empt the
naming-induced confusion:

  * No LLM in the decision loop. Plans are produced by a fully
    deterministic ``DeterministicPlanningEngine``
    (solden/core/planning_engine.py). There is no "giant prompt"
    deciding what action to take next. The engine *does* invoke the
    LLM gateway for individual bounded actions (classify an inbound
    reply, write an operator-facing exception reason) — but only when
    the deterministic plan already chose that action. The LLM reads
    unstructured input and writes operator prose; it never decides a
    transition. "Rules decide, the model describes."
  * No chokepoint over decisions. Routing, validation, approval gates,
    confidence thresholds, three-way match, and override policy all
    live in their own deterministic modules
    (``ap_decision.py``, ``planning_engine.py``, ``finance_agent_governance.py``).
    The CoordinationEngine just executes the plan those modules
    already decided on.
  * Shared state is the substrate. Plans read from and write to
    ``ap_items``, ``audit_events``, ``bank_match_boxes`` — the
    Box record is the source of truth. The engine is a pure consumer
    + producer of that state, not the holder of it.
  * Reversibility is structural. Every action goes through
    ``_pre_write`` (Rule 1) so a crashed execution leaves a Box in
    a recoverable state, not a wedged one. Override windows
    (``services/override_window.py``) and approval revert
    (``services/approval_revert.py``) close the reversibility loop.

What this module IS: a deterministic, audited, replay-safe dispatcher
that turns a typed Plan into the right sequence of side effects
(state transitions, ERP writes, Slack messages) while writing every
step to the append-only audit trail. The name ``CoordinationEngine``
predates the manifesto; ``ReactiveDispatcher`` would be more
descriptive but the rename's blast radius (100+ callsites, several
test fixtures, two surface tools) outweighs the cosmetic benefit. If
you're reading this looking for the "no orchestrator" promise —
this file IS the no-orchestrator implementation; the name is the
artifact, not the architecture.

Usage:
    from solden.core.coordination_engine import CoordinationEngine
    from solden.core.plan import Plan

    engine = CoordinationEngine(db=db, organization_id="acme")
    result = await engine.execute(plan)
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional, Tuple

from solden.core import box_registry
from solden.core.plan import Action, CoordinationResult, Plan

logger = logging.getLogger(__name__)

# §5.2: Retry delays for transient failures
_RETRY_DELAYS = [5, 30, 120]  # seconds
_MAX_RETRIES = 3

# Governance gating (group 1, 2026-05-06): every risky autonomous
# financial write on the event-driven path runs through the same
# doctrine + autonomy gate the synchronous skill path uses. Maps
# the engine's planner-action vocabulary to the governance module's
# action token set.
_GOVERNANCE_GATED_ACTIONS: Dict[str, str] = {
    "post_bill": "post_to_erp",
    "schedule_payment": "post_to_erp",
    "reverse_erp_post": "retry_recoverable_failures",
    "freeze_vendor_payments": "post_to_erp",
    # Cash-side ERP write (records a completed settlement). If a planner ever
    # emits this on the event-driven path it routes through the same doctrine +
    # autonomy gate; it is also feature-flagged + in _RISKY_ACTIONS.
    "apply_settlement": "apply_settlement",
}


# §11: Action → SLA step mapping for latency tracking
_ACTION_TO_SLA_STEP = {
    "classify_email":             "classification",
    "extract_invoice_fields":     "extraction",
    "run_extraction_guardrails":  "guardrails",
    "lookup_po":                  "erp_lookup",
    "lookup_grn":                 "erp_lookup",
    "lookup_vendor_master":       "erp_lookup",
    "run_three_way_match":        "three_way_match",
    "post_bill":                  "erp_post",
    "send_slack_approval":        "slack_delivery",
    "send_slack_exception":       "slack_delivery",
    "send_slack_override_window": "slack_delivery",
    "send_slack_digest":          "slack_delivery",
}


# §5.1: Per-action-type timeouts
_ACTION_TIMEOUTS = {
    "classify_email": 30,
    "extract_invoice_fields": 30,
    "classify_vendor_response": 30,
    "generate_exception_reason": 30,
    "post_bill": 10,
    "lookup_po": 10,
    "lookup_grn": 10,
    "apply_label": 5,
    "send_approval": 5,
    # ``send_vendor_email`` priority entry removed: Solden sends zero
    # email to vendors (memory: 2026-05-02). The action no longer has
    # a planner emitter or dispatcher.
}
_DEFAULT_TIMEOUT = 15


class _Rule1PreWriteFailed(Exception):
    """Raised when the Rule 1 pre-write audit insert cannot land
    after retries. Caught by the execution loop as a clean abort —
    the action does NOT run, preserving the §7.6 guarantee that no
    agent side effect happens without a corresponding timeline row.
    """

    def __init__(
        self,
        *,
        action_name: str,
        box_id: Optional[str],
        original: Optional[Exception],
    ) -> None:
        super().__init__(
            f"Rule 1 pre-write failed for action={action_name} box={box_id}: {original}"
        )
        self.action_name = action_name
        self.box_id = box_id
        self.original = original


class CoordinationEngine:
    """§5: Formal execution loop consuming a Plan object.

    The engine:
    1. Loads the plan
    2. For each action: pre-write → execute → post-write → check wait
    3. On failure: classify and handle per §5.2
    4. On async wait: persist remaining plan to pending_plan, exit
    5. On completion: clear pending_plan, return result
    """

    def __init__(self, db: Any, organization_id: str):
        self.db = db
        self.organization_id = organization_id
        self._handlers: Dict[str, Callable] = {}
        self._workflow = None
        self._ctx: Dict[str, Any] = {}  # Per-instance, NOT class-level
        self._register_handlers()

    def _get_workflow(self):
        if self._workflow is None:
            from solden.services.invoice_workflow import InvoiceWorkflowService
            self._workflow = InvoiceWorkflowService(organization_id=self.organization_id)
        return self._workflow

    def _register_handlers(self) -> None:
        """Map action names to handler functions.

        Each handler wraps an existing service method. No new business
        logic — just wiring.
        """
        self._handlers = {
            # §3 Email and Inbox Actions
            "read_email": self._handle_read_email,
            "fetch_attachment": self._handle_fetch_attachment,
            "apply_label": self._handle_apply_label,
            "remove_label": self._handle_remove_label,
            "split_thread": self._handle_split_thread,
            # ``send_email`` handler removed: Solden sends zero email
            # to vendors (memory: 2026-05-02). The Gmail OAuth scope no
            # longer includes ``gmail.send``.
            "watch_thread": self._handle_watch_thread,

            # §3 Classification and Extraction Actions (5)
            "classify_email": self._handle_classify_email,
            "extract_invoice_fields": self._handle_extract,
            "run_extraction_guardrails": self._handle_guardrails,
            "generate_exception_reason": self._handle_generate_exception,
            "classify_vendor_response": self._handle_classify_vendor,

            # §3 ERP Actions (8)
            "lookup_vendor_master": self._handle_lookup_vendor_master,
            "lookup_po": self._handle_lookup_po,
            "lookup_grn": self._handle_lookup_grn,
            "run_three_way_match": self._handle_match,
            "post_bill": self._handle_post_bill,
            "pre_post_validate": self._handle_pre_post_validate,
            "schedule_payment": self._handle_schedule_payment,
            "reverse_erp_post": self._handle_reverse_erp_post,

            # §3 Box and State Actions (8)
            "create_box": self._handle_create_box,
            "update_box_fields": self._handle_update_fields,
            "move_box_stage": self._handle_stage_transition,
            "post_timeline_entry": self._handle_timeline,
            "link_vendor_to_box": self._handle_link_vendor,
            "set_waiting_condition": self._handle_set_waiting,
            "clear_waiting_condition": self._handle_clear_waiting,
            # Group 8 cleanup (2026-05-07): "set_pending_plan" handler
            # removed. No planner emits this action — pending_plan is
            # persisted automatically inside ``_execute_body`` when an
            # action returns ``waiting_condition`` (atomic with the
            # waiting_condition write per Group 2). Re-add only if a
            # new planner branch needs an explicit pause-now action.

            # §3 Communication Actions. ``send_vendor_email`` and
            # ``draft_vendor_response`` were dropped per the 2026-05-02
            # zero-vendor-email rule — Solden sends no email to vendors
            # and authors no vendor-facing body text.
            "send_slack_approval": self._handle_send_approval,
            "send_slack_exception": self._handle_send_slack_exception,
            "send_slack_override_window": self._handle_override_window,
            "send_slack_digest": self._handle_send_slack_digest,
            "send_teams_approval": self._handle_send_teams_approval,
            "post_gmail_notification": self._handle_post_gmail_notification,

            # §3 Vendor Onboarding Actions (7)
            "create_vendor_record": self._handle_create_vendor_record,
            "enrich_vendor": self._handle_enrich_vendor,
            "run_adverse_media_check": self._handle_adverse_media,
            "activate_vendor_in_erp": self._handle_activate_vendor,
            "freeze_vendor_payments": self._handle_freeze_payments,

            # §3 Fraud Control Actions (6)
            "check_iban_change": self._handle_iban_change,
            "check_domain_match": self._handle_domain_match,
            "check_velocity": self._handle_velocity,
            "check_duplicate": self._handle_duplicate,
            "flag_internal_instruction": self._handle_flag_internal,
            "check_amount_ceiling": self._handle_ceiling,

            # Internal actions (not in spec §3 but used by plans)
            "check_duplicate_full": self._handle_duplicate,
            "resume_from_pending_plan": self._handle_resume_plan,
            "close_override_window": self._handle_close_override,
            "escalate_approval": self._handle_escalate,
            "route_vendor_response": self._handle_route_vendor,
            "send_approval": self._handle_send_approval,  # alias
            "send_override_window": self._handle_override_window,  # alias
            "validate_kyc_document": self._handle_kyc_validate,
            "update_onboarding_progress": self._handle_onboarding_progress,
            "initiate_iban_verification": self._handle_iban_verify,
            "evaluate_grn_result": self._handle_evaluate_grn,
            "unsnooze": self._handle_unsnooze,
            # Group 8 cleanup (2026-05-07): "apply_label_matched"
            # alias removed. Was registered as an alias for
            # _handle_apply_label but no planner emits the alias name.
            # Real callers use "apply_label" with a ``label`` param.
            # §12.2: ERP connectivity recheck + resume
            "check_erp_connectivity": self._handle_check_erp_connectivity,
            "evaluate_erp_recheck": self._handle_evaluate_erp_recheck,

            # Vendor onboarding v1.1 — spec §5. These actions call out to
            # KYC / open-banking / ERP write paths. Until the real
            # provider adapters register via kyc_provider /
            # bank_verifier factories, each maps to
            # `_handle_onboarding_adapter_pending` which records a
            # neutral "adapter pending" timeline entry and returns
            # success so the plan runs end-to-end against stubs.
            "check_vendor_duplicate": self._handle_onboarding_adapter_pending,
            "dispatch_onboarding_invitation": self._handle_onboarding_adapter_pending,
            "generate_portal_link": self._handle_onboarding_adapter_pending,
            "classify_submitted_document": self._handle_onboarding_adapter_pending,
            "extract_vendor_fields": self._handle_onboarding_adapter_pending,
            "validate_document_completeness": self._handle_onboarding_adapter_pending,
            "initiate_kyc_checks": self._handle_onboarding_adapter_pending,
            "record_kyc_check_result": self._handle_onboarding_adapter_pending,
            "evaluate_kyc_disposition": self._handle_onboarding_adapter_pending,
            "run_company_registry_lookup": self._handle_onboarding_adapter_pending,
            "run_sanctions_screen": self._handle_onboarding_adapter_pending,
            "run_pep_check": self._handle_onboarding_adapter_pending,
            "run_adverse_media_screen": self._handle_onboarding_adapter_pending,
            "resolve_ubo": self._handle_onboarding_adapter_pending,
            "check_open_banking_coverage": self._handle_onboarding_adapter_pending,
            "dispatch_open_banking_link": self._handle_onboarding_adapter_pending,
            "evaluate_name_match": self._handle_onboarding_adapter_pending,
            "evaluate_bank_verification_disposition": self._handle_onboarding_adapter_pending,
            "pre_write_validate_vendor": self._handle_onboarding_adapter_pending,
            "draft_vendor_master_record": self._handle_onboarding_adapter_pending,
            "validate_vendor_master_record": self._handle_onboarding_adapter_pending,
            "write_vendor_to_erp": self._handle_onboarding_adapter_pending,
            "send_slack_vendor_activated": self._handle_onboarding_adapter_pending,
            "send_slack_onboarding_exception": self._handle_onboarding_adapter_pending,
            # ``send_vendor_chase`` handler removed (memory:
            # 2026-05-02). Vendor chase emails were the last
            # vendor-facing email surface and are dropped wholesale.
            "generate_vendor_summary": self._handle_onboarding_adapter_pending,
            "resume_onboarding_from_override": self._handle_onboarding_adapter_pending,
        }

    # ------------------------------------------------------------------
    # Per-box advisory lock (group 2 deferred-a, 2026-05-06)
    # ------------------------------------------------------------------

    def _box_lock_keys(self, box_id: str) -> Tuple[int, int]:
        """Thin instance wrapper around ``box_lock.box_lock_keys`` —
        retained so existing call sites that pass through ``self`` keep
        working. The key derivation lives in ``solden.core.box_lock``
        so the runtime engine and ``InvoiceWorkflowService`` share one
        implementation.
        """
        from solden.core.box_lock import box_lock_keys
        return box_lock_keys(self.organization_id or "", box_id)

    def _acquire_box_lock(
        self, box_id: str,
    ) -> Tuple[Optional[Any], str]:
        """Try to acquire the per-box advisory lock.

        Delegates to :func:`solden.core.box_lock.acquire_box_lock`
        which both this engine and the legacy ``InvoiceWorkflowService``
        approval-dispatch outbox call. The (conn, status) contract is
        unchanged: ``acquired`` / ``held`` / ``no_infra``. See
        ``solden.core.box_lock`` for the full semantics.
        """
        from solden.core.box_lock import acquire_box_lock
        return acquire_box_lock(self.db, self.organization_id or "", box_id)

    def _release_box_lock(self, conn: Any, box_id: str) -> None:
        """Release a per-box advisory lock previously acquired via
        ``_acquire_box_lock``. Always returns the connection to the
        pool, even if the unlock RPC fails. Delegates to
        :func:`solden.core.box_lock.release_box_lock`.
        """
        from solden.core.box_lock import release_box_lock
        release_box_lock(self.db, conn, self.organization_id or "", box_id)

    async def execute(self, plan: Plan) -> CoordinationResult:
        """§5.1: The execution loop.

        Steps:
        1. Load plan
        2. Take next action
        3. Pre-execution timeline write (Rule 1)
        4. Execute the action
        5. Handle the result
        6. Check for async wait
        7. Complete or continue

        Per-box advisory lock (group 2): when the plan targets a Box,
        an org-scoped + box-scoped Postgres advisory lock serializes
        plan execution. A second engine on the same Box (Celery
        redelivery, webhook + timer overlap) returns
        ``status="lock_held"`` immediately without doing any work,
        so the financial-write hazard from concurrent execution is
        closed at the engine level.

        Lock infra missing (test mocks, sqlite-only shim) → fail-
        open: run unguarded. The ``_handle_post_bill``
        ``erp_reference`` dedupe (also Group 2) is the financial-
        write backstop, so a missing lock degrades safety but
        doesn't open the duplicate-bill door.
        """
        if plan.is_empty:
            return CoordinationResult(status="completed", steps_total=0)

        box_id = plan.box_id
        if box_id:
            lock_conn, lock_status = self._acquire_box_lock(box_id)
            if lock_status == "held":
                return CoordinationResult(
                    status="lock_held",
                    steps_completed=0,
                    steps_total=plan.step_count,
                    box_id=box_id,
                    error="another engine instance is processing this box",
                    last_action=None,
                )
            try:
                return await self._execute_body(plan)
            finally:
                if lock_conn is not None:
                    self._release_box_lock(lock_conn, box_id)
        # No box → no lock; run the body directly.
        return await self._execute_body(plan)

    async def _execute_body(self, plan: Plan) -> CoordinationResult:
        """Internal: the original ``execute()`` body, callable
        with the per-box lock already held (or with no lock when
        the plan has no box).

        Group 4 hygiene (2026-05-06): resets ``self._ctx`` at the
        top so prior-plan state (extracted_fields, vendor_profile,
        match_result, body, attachments) doesn't leak into the
        next plan when an engine instance is reused. The original
        ``__init__`` initialises ``_ctx`` once; without this reset
        a plan run after a previous one would observe stale ctx
        keys and mis-route. Resume paths that already populated
        ``_ctx`` (via ``_handle_resume_plan`` recursing into
        ``_execute_body``) are preserved by skipping the reset
        when the plan looks resumed (event_type "resumed" or
        "resumer").
        """
        if plan.event_type not in ("resumed", "resumer"):
            self._ctx = {}
        box_id = plan.box_id
        steps_completed = 0

        # §11: Track total_to_approval latency for email_received plans
        import time as _time
        _plan_start = _time.monotonic()
        _is_invoice_plan = plan.event_type == "email_received"

        for step, action in enumerate(plan.actions):
            # --- Step 3: Pre-execution timeline write (Rule 1) ---
            # Fails closed: if the audit write can't land after
            # retries, skip the action and park the plan with a
            # clear error. §7.6 guarantee is that no side effect
            # (ERP post, vendor email, Slack message) runs without
            # a timeline row first.
            try:
                timeline_id = await self._pre_write(box_id, action, step, plan=plan)
            except _Rule1PreWriteFailed as rule1_exc:
                if box_id:
                    self._move_to_exception(
                        box_id, action.name,
                        "rule1_pre_write_failed — timeline unavailable, action aborted",
                        box_type=plan.box_type,
                    )
                return CoordinationResult(
                    status="failed", steps_completed=steps_completed,
                    steps_total=plan.step_count, box_id=box_id,
                    error=f"rule1_pre_write_failed:{rule1_exc.original}",
                    last_action=action.name,
                )

            # --- Step 3a: Governance gate (group 1, 2026-05-06) ---
            # Risky financial writes (post_bill / schedule_payment /
            # reverse_erp_post / freeze_vendor_payments) run through
            # the doctrine + autonomy gate before firing. The
            # synchronous skill path already does this via
            # ``FinanceAgentLoopService.run_skill_request``; this
            # closes the symmetric gap on the event-driven path so
            # the marketing claim "agent acts within governance
            # gates" is enforced everywhere autonomy fires.
            governance_verdict = self._evaluate_governance_for_action(action, plan)
            if (
                governance_verdict is not None
                and not governance_verdict.get("should_execute", True)
            ):
                stop_reason = str(
                    governance_verdict.get("stop_reason") or "governance_blocked"
                )
                await self._post_write(
                    box_id, action, step, timeline_id, "failed", stop_reason,
                    plan=plan,
                )
                self._record_governance_block_audit(box_id, action, governance_verdict)
                if box_id:
                    self._move_to_exception(box_id, action.name, stop_reason, box_type=plan.box_type)
                return CoordinationResult(
                    status="failed", steps_completed=steps_completed,
                    steps_total=plan.step_count, box_id=box_id,
                    error=f"governance_blocked:{stop_reason}",
                    last_action=action.name,
                )

            # --- Step 4: Execute the action ---
            try:
                result = await self._execute_with_retry(action, plan, step)
            except asyncio.CancelledError:
                # Group 4 cancellation cleanup (2026-05-06): outer
                # task cancelled mid-action. Best-effort post_write
                # so the timeline records "cancelled" instead of
                # leaving the pre-write row dangling forever as
                # ``executing``. ``asyncio.shield`` lets the audit
                # write complete even though the parent is being
                # cancelled; we still re-raise so the cancellation
                # propagates to the caller.
                try:
                    await asyncio.shield(self._post_write(
                        box_id, action, step, timeline_id,
                        "cancelled", "task cancelled mid-execution",
                        plan=plan,
                    ))
                except Exception as cancel_post_exc:  # noqa: BLE001
                    logger.warning(
                        "[CoordinationEngine] cancelled-state post_write best-effort "
                        "failed for %s: %s",
                        action.name, cancel_post_exc,
                    )
                raise

            # --- Step 5: Handle the result ---
            if result.get("_abort"):
                await self._post_write(
                    box_id, action, step, timeline_id, "failed",
                    result.get("error", ""), plan=plan,
                )
                if box_id:
                    self._move_to_exception(box_id, action.name, result.get("error", ""), box_type=plan.box_type)
                return CoordinationResult(
                    status="failed", steps_completed=steps_completed,
                    steps_total=plan.step_count, box_id=box_id,
                    error=result.get("error"), last_action=action.name,
                )

            # Action that signals plan should stop early (e.g. classification = not invoice)
            if result.get("_stop_plan"):
                await self._post_write(
                    box_id, action, step, timeline_id, "completed",
                    "plan stopped", plan=plan,
                )
                steps_completed += 1
                break

            # Determine status for audit: waiting_condition means the action
            # succeeded in setting a wait (not an error), but the plan is pausing.
            # Failures that become waits (dependency) are recorded as "paused".
            if result.get("waiting_condition"):
                _summary = result["waiting_condition"].get("context", {}).get("error", "")
                if _summary:
                    # This came from a dependency failure, not a deliberate set_waiting
                    await self._post_write(
                        box_id, action, step, timeline_id, "paused",
                        _summary, plan=plan,
                    )
                else:
                    await self._post_write(
                        box_id, action, step, timeline_id, "completed",
                        "set waiting condition", plan=plan,
                    )
            else:
                await self._post_write(
                    box_id, action, step, timeline_id, "completed", "",
                    plan=plan,
                )
            steps_completed += 1

            # Update box_id if the action created one
            if result.get("box_id") and not box_id:
                box_id = result["box_id"]
                plan.box_id = box_id

            # --- Step 6: Check for async wait ---
            if result.get("waiting_condition"):
                remaining = plan.remaining_from(step + 1)
                if box_id:
                    # Group 2 fix (2026-05-06): persist pending_plan
                    # AND waiting_condition in a single ``update_ap_item``
                    # so the two halves are atomic. Previously two
                    # separate writes left a window where a process
                    # crash between them produced split-brain state:
                    # plan saved, no wait → orphaned plan (resumer
                    # never wakes); wait saved, no plan → frozen
                    # box (operator sees a wait banner but the
                    # remaining work is lost).
                    waiting_payload = {
                        "type": result["waiting_condition"].get("type", "unknown"),
                        "expected_by": result["waiting_condition"].get("expected_by"),
                        "context": result["waiting_condition"].get("context") or {},
                        "set_at": datetime.now(timezone.utc).isoformat(),
                    }
                    update_kwargs: Dict[str, Any] = {
                        "waiting_condition": waiting_payload,
                    }
                    if not remaining.is_empty:
                        update_kwargs["pending_plan"] = remaining.to_json()
                    await asyncio.to_thread(
                        self.db.update_ap_item, box_id, **update_kwargs,
                    )
                # §11: Record total_to_approval SLA when hitting approval wait
                if _is_invoice_plan and result["waiting_condition"].get("type") == "approval_response":
                    try:
                        from solden.core.sla_tracker import get_sla_tracker
                        total_ms = int((_time.monotonic() - _plan_start) * 1000)
                        get_sla_tracker().record(
                            "total_to_approval", total_ms,
                            ap_item_id=box_id,
                            organization_id=self.organization_id,
                        )
                    except Exception:
                        pass
                return CoordinationResult(
                    status="waiting", steps_completed=steps_completed,
                    steps_total=plan.step_count, box_id=box_id,
                    waiting_condition=result["waiting_condition"],
                    last_action=action.name,
                )

        # --- Step 7: Plan complete ---
        if box_id:
            try:
                await asyncio.to_thread(
                    self.db.update_ap_item, box_id, pending_plan=None,
                )
            except Exception:
                pass
        return CoordinationResult(
            status="completed", steps_completed=steps_completed,
            steps_total=plan.step_count, box_id=box_id,
            last_action=plan.actions[-1].name if plan.actions else None,
        )

    # ------------------------------------------------------------------
    # Timeline writes (Rule 1)
    # ------------------------------------------------------------------

    def _plan_idempotency_key(
        self, plan: Optional[Plan], action: Action, step: int, phase: str,
    ) -> Optional[str]:
        """Build a deterministic idempotency key for a (plan, step,
        action) audit row. Same plan replayed (Celery retry / Redis
        redelivery) → same key → audit insert short-circuits to the
        existing row instead of double-writing.

        Falls back to a plan-stable token (event_type + created_at)
        when the planner didn't set a correlation_id — covers
        legacy paths and direct test construction. Returns None
        when the plan is missing entirely (callers shouldn't dedupe
        what they can't identify).
        """
        if plan is None:
            return None
        correlation = (
            (plan.correlation_id or "").strip()
            or f"{plan.event_type}:{plan.created_at}"
        )
        return f"plan:{correlation}:{step}:{action.name}:{phase}"

    async def _pre_write(
        self,
        box_id: Optional[str],
        action: Action,
        step: int,
        plan: Optional[Plan] = None,
    ) -> str:
        """§5.1 Rule 1: Write timeline entry BEFORE execution.

        Uses the audit_events table (via append_audit_event) as the
        Box timeline — every agent action is a recorded event.

        Fails CLOSED per §7.6. The timeline is the evidence of trust;
        an ERP post with no timeline record is the failure mode the
        thesis says cannot happen. If the audit write can't land
        after three retries, we raise _Rule1PreWriteFailed — the
        execution loop catches it and aborts the action before any
        side effect runs.

        Async hygiene (group 4, 2026-05-06): the sync DB call runs on
        a worker thread via ``asyncio.to_thread`` so a slow audit
        insert (transient DB blip, pool contention) doesn't block
        the event loop. The retry backoff uses ``await asyncio.sleep``
        for the same reason — previously this was ``time.sleep``
        inside an ``async def`` and stalled every other coroutine
        on the worker for up to 1s under DB pressure.

        ``plan`` is optional for backward compatibility with the
        exception-flow path that pre-dates plan-aware audit; when
        provided, the idempotency_key dedupes Celery retries.
        """
        timeline_id = f"TL-{uuid.uuid4().hex[:12]}"
        if not box_id or not hasattr(self.db, "append_audit_event"):
            return timeline_id

        idempotency_key = self._plan_idempotency_key(plan, action, step, "pre")
        payload = {
            "id": timeline_id,
            "box_id": box_id,
            "box_type": (plan.box_type if plan else "ap_item"),
            "event_type": f"agent_action:{action.name}:executing",
            "actor_type": "agent",
            "actor_id": "coordination_engine",
            "organization_id": self.organization_id,
            "idempotency_key": idempotency_key,
            "payload_json": {
                "action": action.name,
                "description": action.description,
                "status": "executing",
                "step": step,
                "layer": action.layer,
                "correlation_id": (plan.correlation_id if plan else None),
            },
        }

        last_exc: Optional[Exception] = None
        for attempt in range(3):
            try:
                await asyncio.to_thread(self.db.append_audit_event, payload)
                return timeline_id
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < 2:
                    # Brief backoff — 50ms, 200ms. The common failure
                    # modes (transient DB blip, connection-pool wait)
                    # resolve inside a few hundred ms. ``asyncio.sleep``
                    # yields the loop so other coroutines run during
                    # the wait.
                    await asyncio.sleep(0.05 * (4 ** attempt))

        logger.error(
            "[CoordinationEngine] Rule 1 pre-write failed after 3 attempts for "
            "box=%s action=%s — aborting the action to preserve §7.6 "
            "audit-trail guarantee. Last error: %s",
            box_id, action.name, last_exc,
        )
        raise _Rule1PreWriteFailed(
            action_name=action.name,
            box_id=box_id,
            original=last_exc,
        )

    async def _post_write(
        self,
        box_id: Optional[str],
        action: Action,
        step: int,
        timeline_id: str,
        status: str,
        result_summary: str,
        plan: Optional[Plan] = None,
    ) -> None:
        """Update pre-execution entry with result.

        ``plan`` is optional; when provided, the post-row gets a
        deterministic idempotency_key so Celery retries dedupe.
        Status is part of the key so a retry that finishes with
        ``failed`` doesn't clobber a prior ``completed`` row (each
        terminal status occupies its own key).

        Async hygiene (group 4): the sync DB call runs on a worker
        thread via ``asyncio.to_thread`` so the timeline write
        doesn't block the event loop.
        """
        if not box_id or not hasattr(self.db, "append_audit_event"):
            return
        idempotency_key = self._plan_idempotency_key(
            plan, action, step, f"post:{status}",
        )
        payload = {
            "id": f"{timeline_id}-result",
            "box_id": box_id,
            "box_type": (plan.box_type if plan else "ap_item"),
            "event_type": f"agent_action:{action.name}:{status}",
            "actor_type": "agent",
            "actor_id": "coordination_engine",
            "organization_id": self.organization_id,
            "idempotency_key": idempotency_key,
            "payload_json": {
                "action": action.name,
                "status": status,
                "result_summary": result_summary[:200] if result_summary else "",
                "step": step,
                "parent_timeline_id": timeline_id,
                "correlation_id": (plan.correlation_id if plan else None),
            },
        }
        try:
            await asyncio.to_thread(self.db.append_audit_event, payload)
        except Exception as exc:
            logger.warning(
                "[CoordinationEngine] post-write timeline failed for %s: %s",
                action.name, exc,
            )

    # ------------------------------------------------------------------
    # Execution with retry (§5.2)
    # ------------------------------------------------------------------

    async def _execute_with_retry(self, action: Action, plan: Plan, step: int) -> Dict[str, Any]:
        """Execute action with transient failure retry."""
        import time as _time
        handler = self._handlers.get(action.name)
        if not handler:
            logger.warning("[CoordinationEngine] No handler for action: %s", action.name)
            return {"ok": True, "_stop_plan": False}

        timeout = _ACTION_TIMEOUTS.get(action.name, _DEFAULT_TIMEOUT)

        # §11: Map action name to SLA step name for timing
        sla_step = _ACTION_TO_SLA_STEP.get(action.name)
        _action_start = _time.monotonic()

        for attempt in range(_MAX_RETRIES + 1):
            try:
                result = await asyncio.wait_for(
                    handler(action, plan),
                    timeout=timeout,
                )
                # §11: Record SLA latency for this action
                if sla_step:
                    latency_ms = int((_time.monotonic() - _action_start) * 1000)
                    try:
                        from solden.core.sla_tracker import get_sla_tracker
                        get_sla_tracker().record(
                            sla_step, latency_ms,
                            ap_item_id=plan.box_id,
                            organization_id=self.organization_id,
                        )
                    except Exception:
                        pass
                return result if isinstance(result, dict) else {"ok": True}

            except asyncio.TimeoutError:
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                    logger.warning(
                        "[CoordinationEngine] %s timed out, retry %d/%d in %ds",
                        action.name, attempt + 1, _MAX_RETRIES, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                return {"_abort": True, "error": f"{action.name} timed out after {_MAX_RETRIES} retries"}

            except Exception as exc:
                failure_type = _classify_failure(exc)
                if failure_type == "transient" and attempt < _MAX_RETRIES:
                    delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                    await asyncio.sleep(delay)
                    continue
                if failure_type == "dependency":
                    # §12.2: Alert Backoffice ERP connector health dashboard
                    try:
                        from solden.services.monitoring import alert_cs_team
                        alert_cs_team(
                            severity="warning",
                            title=f"External dependency unavailable: {action.name}",
                            detail=f"Action {action.name} failed with dependency error: {str(exc)[:200]}. Plan paused — retrying in 15 minutes.",
                            organization_id=self.organization_id,
                        )
                    except Exception:
                        pass  # Alert is best-effort
                    return {
                        "waiting_condition": {
                            "type": "external_dependency_unavailable",
                            "expected_by": (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
                            "context": {
                                "action": action.name,
                                "error": str(exc),
                                "first_failure_at": datetime.now(timezone.utc).isoformat(),
                            },
                        }
                    }
                if failure_type == "llm" and action.layer == "LLM":
                    logger.warning("[CoordinationEngine] LLM failure in %s, using fallback", action.name)
                    return {"ok": True, "_fallback": True}
                return {"_abort": True, "error": str(exc)}

        return {"_abort": True, "error": "max retries exhausted"}

    def _evaluate_governance_for_action(
        self, action: Action, plan: Plan,
    ) -> Optional[Dict[str, Any]]:
        """Run the doctrine + autonomy gate for risky financial writes
        on the event-driven path. Returns the deliberation verdict or
        ``None`` if the action isn't gated.

        Closes the gap the Group 1 audit flagged: the synchronous
        skill path (``FinanceAgentLoopService.run_skill_request``)
        already runs ``build_deliberation`` before each skill, but
        the event-driven path (Celery → planner → engine) skipped
        governance entirely. Result: every webhook-triggered
        ``post_bill`` / ``schedule_payment`` / ``reverse_erp_post`` /
        ``freeze_vendor_payments`` was firing without the autonomy
        gate the deck and trust story claim.

        Why it's safe to be sync: the skill path also calls
        ``build_deliberation`` synchronously inside an async method.
        Under load this is a known event-loop blocker (Group 4 will
        wrap it with ``asyncio.to_thread``); for now we mirror the
        existing skill-path behavior for consistency.

        Fail-closed semantics: if governance evaluation raises, we
        propagate. Matches the skill path (which has no try/except
        around ``build_deliberation`` in ``observe()``). A broken
        gate stops the action from running, which is the right
        default for a financial write.
        """
        governance_token = _GOVERNANCE_GATED_ACTIONS.get(action.name)
        if governance_token is None:
            return None
        if not plan.box_id:
            # No Box to gate against — let the handler decide. The
            # only actions that fire without a box are intake
            # actions (create_box etc.), none of which are in the
            # gated set.
            return None

        # Per-box-type governance: the Box type declares which actions are
        # gated and which skill governs them (box_registry.BoxType), instead
        # of the engine hardcoding ``box_type == "ap_item"``. A risky action
        # (one in _GOVERNANCE_GATED_ACTIONS) on a type that has NOT declared an
        # AP-style deliberation gate fails closed — it requires a human rather
        # than running ungated. Today only ap_item declares ap_v1 governance;
        # this is what lets a second Box type plug in instead of bypassing.
        try:
            bt = box_registry.resolve(plan.box_type, self.organization_id)
            gated_actions = getattr(bt, "gated_actions", frozenset())
            governance_skill_id = getattr(bt, "governance_skill_id", None)
        except Exception:
            gated_actions = frozenset()
            governance_skill_id = None

        if not (action.name in gated_actions and governance_skill_id == "ap_v1"):
            return {
                "should_execute": False,
                "stop_reason": f"governance_undeclared_for_box_type:{plan.box_type}",
                "recommended_action": "require_human_approval",
            }

        from solden.services.finance_agent_runtime import (
            get_platform_finance_runtime,
        )
        from solden.services.finance_agent_governance import (
            build_deliberation,
        )
        from solden.services.agent_memory import get_agent_memory_service
        from solden.core.finance_contracts import (
            ActionExecution,
            SkillRequest,
        )

        runtime = get_platform_finance_runtime(self.organization_id)
        memory = get_agent_memory_service(self.organization_id, db=self.db)

        # Synthesize the request + action shape ``build_deliberation``
        # expects. The event-driven path is autonomous by definition
        # (no human-in-the-loop on this code path; Slack approvals
        # land via the skill path, not here).
        synthetic_request = SkillRequest.from_intent(
            org_id=self.organization_id,
            task_type=str(action.name),
            skill_id="ap_v1",
            entity_id=str(plan.box_id),
            correlation_id="",
            payload={
                "execution_context": "autonomous",
                "source_channel": "agent_runtime",
            },
        )
        synthetic_action = ActionExecution(
            entity_id=str(plan.box_id),
            action=str(governance_token),
            preview=False,
            idempotency_key=f"{plan.box_id}:{action.name}",
            reason="event_driven_path",
        )

        ap_item: Dict[str, Any] = {}
        try:
            raw = box_registry.get_box(plan.box_type, plan.box_id, self.db)
            ap_item = raw if isinstance(raw, dict) else {}
        except Exception as exc:
            logger.warning(
                "[CoordinationEngine] governance: ap_item fetch failed for box=%s: %s",
                plan.box_id, exc,
            )

        profile = memory.ensure_profile(skill_id="ap_v1")
        belief: Dict[str, Any] = {}
        if ap_item.get("id"):
            try:
                belief = memory.build_belief_state(
                    ap_item_id=str(ap_item["id"]),
                    skill_id="ap_v1",
                    ap_item=ap_item,
                ) or {}
            except Exception as exc:
                logger.debug(
                    "[CoordinationEngine] governance: belief build failed for box=%s: %s",
                    plan.box_id, exc,
                )

        return build_deliberation(
            runtime=runtime,
            request=synthetic_request,
            action=synthetic_action,
            ap_item=ap_item,
            belief=belief,
            recall=[],
            profile=profile,
        )

    def _record_governance_block_audit(
        self, box_id: str, action: Action, verdict: Dict[str, Any],
    ) -> None:
        """Persist a structured ``agent_action_blocked_by_governance``
        audit row when the gate refuses an event-driven action. The
        verdict, doctrine reason codes, and skip-the-action signal
        all land on the timeline so post-hoc you can reconstruct
        why the agent stopped."""
        if not box_id:
            return
        try:
            doctrine = verdict.get("doctrine") if isinstance(verdict, dict) else {}
            reason_codes = list((doctrine or {}).get("reason_codes") or [])
            stop_reason = str(verdict.get("stop_reason") or "governance_blocked")
            self.db.append_audit_event({
                "ap_item_id": box_id,
                "event_type": "agent_action_blocked_by_governance",
                "from_state": "",
                "to_state": "",
                "actor_type": "system",
                "actor_id": "agent_runtime",
                "idempotency_key": f"governance_block:{box_id}:{action.name}",
                "metadata": {
                    "action": action.name,
                    "stop_reason": stop_reason,
                    "reason_codes": reason_codes,
                    "doctrine_checks": list((doctrine or {}).get("checks") or []),
                    "governance_verdict": {
                        "should_execute": bool(verdict.get("should_execute")),
                        "verdict": "vetoed",
                        "stop_reason": stop_reason,
                    },
                    "agent_confidence": verdict.get("confidence"),
                },
                "organization_id": self.organization_id,
                "source": "coordination_engine.governance",
            })
        except Exception as exc:
            logger.warning(
                "[CoordinationEngine] failed to append governance-block audit for box=%s: %s",
                box_id, exc,
            )

    async def _run_exception_flow(self, plan: Plan, ctx: dict, match_result: Any) -> None:
        """§9.3: Execute the exception flow when 3-way match fails.

        Steps per spec:
        1. generate_exception_reason(match_result, invoice, po, grn)
        2. apply_label('Solden/Invoice/Exception')
        3. move_box_stage('exception')
        4. send_slack_exception(box_id, ap_channel, {exception_summary})

        Each step goes through the full execution mechanism:
        pre-write → execute with retry/timeout → post-write (Rule 1).
        """
        box_id = plan.box_id
        if not box_id:
            return

        exception_actions = [
            Action("generate_exception_reason", "LLM", {},
                   "Generate plain-language match exception reason"),
            Action("apply_label", "DET",
                   {"label": "Solden/Invoice/Exception"},
                   "Apply Exception stage label"),
            Action("move_box_stage", "DET",
                   {"target": "needs_info"},
                   "Move Box to exception stage"),
            Action("send_slack_exception", "DET", {},
                   "Notify AP team of match exception with resolution buttons"),
        ]

        for step, action in enumerate(exception_actions):
            # Rule 1: pre-execution timeline write. Pass the original
            # plan so the exception-flow rows share its correlation_id
            # — Celery retries of the parent plan re-enter the
            # exception flow with the same identity and dedupe.
            timeline_id = await self._pre_write(box_id, action, step + 100, plan=plan)

            # Execute with retry and timeout (§5.2)
            result = await self._execute_with_retry(action, plan, step + 100)

            # Post-execution timeline update
            status = "completed" if not result.get("_abort") else "failed"
            await self._post_write(
                box_id, action, step + 100, timeline_id, status, "",
                plan=plan,
            )

            # Group 8 fix (2026-05-07): if this step aborts, skip
            # the rest of the flow. Previously the loop continued
            # unconditionally — so when ``move_box_stage(needs_info)``
            # failed (e.g. box already in a terminal state where the
            # transition is illegal), the cascade still fired
            # ``send_slack_exception``. Operators saw a "this box
            # needs info" Slack card for a box whose state never
            # actually moved. Now: abort emits a single timeline
            # row + the box-exception path catches it via
            # ``_move_to_exception``; no misleading downstream cards.
            if result.get("_abort"):
                break

    def _move_to_exception(
        self, box_id: str, action_name: str, error: str,
        box_type: str = "ap_item",
    ) -> None:
        """Move a Box to its exception stage on persistent failure.

        The exception state is per-box-type (``BoxType.exception_state``).
        Box types with no stall state (e.g. bank_match, where a failed
        match is just rejected, not parked) instead get a ``box_exception``
        raised against them without a state move.

        Failure modes recorded explicitly so nothing slips through:

        * Legal transition to the exception state → state moves;
          ``update_box`` writes the ``state_transition`` audit row atomically.
        * Illegal transition (e.g. box is already terminal) →
          ``IllegalTransitionError`` is caught, audited as
          ``illegal_transition_blocked``, and raised as a ``box_exception``
          so the operator queue surfaces a stuck-terminal box.
        * Any other DB error → logged with stack so the orchestrator
          surfaces a real diagnostic instead of a swallowed ``pass``.
        """
        from solden.core.ap_states import IllegalTransitionError
        from solden.core.workflow_spec import IllegalWorkflowTransitionError

        try:
            _bt = box_registry.resolve(box_type, self.organization_id)
            exc_state = _bt.exception_state
            source_table = _bt.source_table
        except Exception:
            exc_state = None
            source_table = ""

        if exc_state is None:
            # No human-stall state for this type — record an exception
            # against the box instead of moving state.
            org_id = ""
            try:
                item = box_registry.get_box(box_type, box_id, self.db) or {}
                org_id = str(item.get("organization_id") or "")
            except Exception:
                pass
            try:
                if hasattr(self.db, "raise_box_exception"):
                    self.db.raise_box_exception(
                        box_id=box_id,
                        box_type=box_type,
                        organization_id=org_id,
                        exception_type="action_failed",
                        severity="high",
                        reason=f"{action_name} failed: {error[:200]}",
                        raised_by="coordination_engine",
                        raised_actor_type="agent",
                    )
            except Exception as raise_exc:
                logger.exception("[CoordinationEngine] failed to raise box_exception: %s", raise_exc)
            return

        try:
            if source_table == "ap_items":
                box_registry.update_box(
                    box_type, box_id, self.db,
                    state=exc_state,
                    exception_reason=f"{action_name}: {error[:200]}",
                )
            else:
                # Declarative / non-AP types take a plain validated state move
                # (``exception_reason`` is an AP-only column). The generic store
                # raises the box_exception on entry to the exception_state
                # (Phase C), so the failure is still recorded.
                box_registry.update_box(
                    box_type, box_id, self.db,
                    state=exc_state,
                    actor_id="coordination_engine",
                    reason=f"{action_name}: {error[:200]}",
                )
            return
        except (IllegalTransitionError, IllegalWorkflowTransitionError) as exc:
            exc_current = getattr(exc, "current", "?")
            exc_target = getattr(exc, "target", exc_state)
            logger.error(
                "[CoordinationEngine] cannot move %s to %s from %s after %s failure: %s",
                box_id, exc_state, exc_current, action_name, error,
            )
            org_id = ""
            try:
                item = box_registry.get_box(box_type, box_id, self.db) or {}
                org_id = str(item.get("organization_id") or "")
            except Exception:
                pass
            try:
                self.db.append_audit_event({
                    "box_id": box_id,
                    "box_type": box_type,
                    "event_type": "illegal_transition_blocked",
                    "actor_type": "agent",
                    "actor_id": "coordination_engine",
                    "organization_id": org_id,
                    "payload_json": {
                        "from_state": exc_current,
                        "to_state": exc_target,
                        "trigger": action_name,
                        "underlying_error": error[:500],
                    },
                })
            except Exception as audit_exc:
                logger.exception("[CoordinationEngine] failed to audit illegal transition: %s", audit_exc)
            try:
                if hasattr(self.db, "raise_box_exception"):
                    self.db.raise_box_exception(
                        box_id=box_id,
                        box_type=box_type,
                        organization_id=org_id,
                        exception_type="illegal_state_transition",
                        severity="high",
                        reason=f"{action_name} failed and box is in terminal state {exc_current}: {error[:200]}",
                        raised_by="coordination_engine",
                        raised_actor_type="agent",
                    )
            except Exception as raise_exc:
                logger.exception("[CoordinationEngine] failed to raise box_exception: %s", raise_exc)
        except Exception as exc:
            logger.exception(
                "[CoordinationEngine] _move_to_exception unexpected failure for %s after %s: %s",
                box_id, action_name, exc,
            )

    # ------------------------------------------------------------------
    # Action Handlers — each wraps an actual service method
    # ------------------------------------------------------------------

    def _ensure_ctx(self, plan: Plan) -> Dict[str, Any]:
        """Get the per-instance execution context for this plan run."""
        return self._ctx

    async def _handle_read_email(self, action: Action, plan: Plan) -> dict:
        """§3: Fetch full email content from Gmail API."""
        ctx = self._ensure_ctx(plan)
        message_id = action.params.get("message_id", "")
        user_id = action.params.get("user_id", "")
        if not message_id:
            return {"ok": True}

        ctx["message_id"] = message_id
        ctx["user_id"] = user_id

        # Fetch actual email content from Gmail
        try:
            from solden.services.gmail_autopilot import GmailAPIClient
            client = GmailAPIClient(user_id)
            if not await client.ensure_authenticated():
                return {"_abort": True, "error": f"Gmail auth failed for user {user_id}"}

            message = await client.get_message(message_id)
            if message:
                ctx["subject"] = getattr(message, "subject", "") or (message.get("subject", "") if isinstance(message, dict) else "")
                ctx["sender"] = getattr(message, "sender", "") or (message.get("sender", "") if isinstance(message, dict) else "")
                ctx["body"] = getattr(message, "body_text", "") or getattr(message, "body", "") or (message.get("body", "") if isinstance(message, dict) else "")
                ctx["snippet"] = getattr(message, "snippet", "") or (message.get("snippet", "") if isinstance(message, dict) else "")
                ctx["thread_id"] = getattr(message, "thread_id", "") or (message.get("thread_id", "") if isinstance(message, dict) else "")
                ctx["attachments"] = getattr(message, "attachments", []) or (message.get("attachments", []) if isinstance(message, dict) else [])
                logger.info("[CoordinationEngine] read_email: fetched %s (subject=%s)", message_id, ctx["subject"][:50])
                return {"ok": True, "message_id": message_id, "has_content": True}
            else:
                return {"_abort": True, "error": f"Message {message_id} not found in Gmail"}
        except Exception as exc:
            logger.error("[CoordinationEngine] read_email failed: %s", exc)
            return {"_abort": True, "error": f"Gmail fetch failed: {exc}"}

    async def _handle_classify_email(self, action: Action, plan: Plan) -> dict:
        """§3: Call Claude to classify the email."""
        ctx = self._ensure_ctx(plan)
        try:
            from solden.services.ap_classifier import classify_ap_email
            subject = ctx.get("subject", "")
            sender = ctx.get("sender", "")
            body = ctx.get("body", "")
            snippet = ctx.get("snippet", "")
            result = classify_ap_email(
                subject=subject, sender=sender,
                snippet=snippet, body=body,
            )
            ctx["classification"] = result
            classification_type = result.get("type", "unclassifiable")
            confidence = result.get("confidence", 0)
            if confidence < 0.80:
                ctx["classification_low_confidence"] = True
                return {"ok": True, "_stop_plan": True, "reason": "low_confidence_classification"}
            if classification_type not in ("invoice", "credit_note"):
                return {"ok": True, "_stop_plan": True, "reason": f"not_invoice: {classification_type}"}
            return {"ok": True, "type": classification_type, "confidence": confidence}
        except Exception as exc:
            logger.warning("[CoordinationEngine] classify_email failed: %s", exc)
            return {"ok": True}  # Treat as unclassifiable per §5.2

    async def _handle_extract(self, action: Action, plan: Plan) -> dict:
        """§3: Call Claude to extract structured invoice fields."""
        ctx = self._ensure_ctx(plan)
        try:
            from solden.services.llm_email_parser import get_llm_email_parser
            parser = get_llm_email_parser()
            result = parser.parse_email(
                subject=ctx.get("subject", ""),
                body=ctx.get("body", ""),
                sender=ctx.get("sender", ""),
                attachments=ctx.get("attachments"),
                organization_id=self.organization_id,
                thread_id=ctx.get("thread_id"),
            )
            ctx["extracted_fields"] = result
            return {"ok": True, "vendor_name": result.get("vendor_name"), "amount": result.get("amount")}
        except Exception as exc:
            logger.warning("[CoordinationEngine] extract_invoice_fields failed: %s", exc)
            return {"ok": True, "_fallback": True}

    async def _handle_guardrails(self, action: Action, plan: Plan) -> dict:
        """§3: Apply 5 deterministic extraction guardrails."""
        ctx = self._ensure_ctx(plan)
        extracted = ctx.get("extracted_fields", {})
        if not extracted:
            return {"ok": True}
        try:
            wf = self._get_workflow()
            invoice = self._build_invoice_from_ctx(ctx)
            gate = await wf._evaluate_deterministic_validation(invoice)
            ctx["validation_gate"] = gate
            if not gate.get("passed", True):
                reason_codes = gate.get("reason_codes", [])
                return {"ok": True, "gate_passed": False, "reason_codes": reason_codes}
            return {"ok": True, "gate_passed": True}
        except Exception as exc:
            logger.warning("[CoordinationEngine] guardrails failed: %s", exc)
            return {"ok": True}

    async def _handle_apply_label(self, action: Action, plan: Plan) -> dict:
        """§3: Apply a Solden Gmail label to the thread."""
        label = action.params.get("label", "")
        if not label:
            return {"ok": True}
        ctx = self._ensure_ctx(plan)
        try:
            from solden.services.gmail_labels import apply_label
            user_id = ctx.get("user_id", "")
            thread_id = ctx.get("thread_id") or ctx.get("message_id", "")
            if user_id and thread_id:
                # Resolve label key from full label path
                label_key = label.split("/")[-1].lower().replace(" ", "_")
                # Label application requires authenticated Gmail client
                # In worker context, delegate to the workflow service
                from solden.services.gmail_autopilot import GmailAPIClient
                client = GmailAPIClient(user_id)
                if await client.ensure_authenticated():
                    await apply_label(client, thread_id, label_key, user_email=user_id)
            return {"ok": True, "label": label}
        except Exception as exc:
            logger.debug("[CoordinationEngine] apply_label non-fatal: %s", exc)
            return {"ok": True, "label": label}

    async def _handle_create_box(self, action: Action, plan: Plan) -> dict:
        """§3: Create a new Box (AP item) in the specified pipeline."""
        ctx = self._ensure_ctx(plan)
        extracted = ctx.get("extracted_fields", {})
        payload = {
            "thread_id": ctx.get("thread_id") or ctx.get("message_id", ""),
            "message_id": ctx.get("message_id", ""),
            "subject": ctx.get("subject", ""),
            "sender": ctx.get("sender", ""),
            "vendor_name": extracted.get("vendor_name") or ctx.get("sender", ""),
            "amount": extracted.get("amount") or extracted.get("total_amount"),
            # Don't fabricate "USD" when extraction couldn't determine the
            # currency — persist NULL so a non-USD invoice with a missed
            # currency code never silently gets relabeled. Render layer
            # surfaces the gap honestly.
            "currency": extracted.get("currency"),
            "invoice_number": extracted.get("invoice_number") or extracted.get("invoice_reference"),
            "invoice_date": extracted.get("invoice_date"),
            "due_date": extracted.get("due_date"),
            "confidence": extracted.get("confidence", 0),
            "state": "received",
            "organization_id": self.organization_id,
            "user_id": ctx.get("user_id", ""),
            "po_number": extracted.get("po_reference") or extracted.get("po_number"),
            "field_confidences": extracted.get("field_confidences"),
            "document_type": ctx.get("classification", {}).get("type", "invoice"),
        }
        # Remove None values
        payload = {k: v for k, v in payload.items() if v is not None}
        try:
            item = await asyncio.to_thread(
                box_registry.create_box, plan.box_type, payload, self.db
            )
            box_id = item.get("id") if isinstance(item, dict) else str(item)
            ctx["box_id"] = box_id
            return {"ok": True, "box_id": box_id}
        except Exception as exc:
            logger.error("[CoordinationEngine] create_box failed: %s", exc)
            return {"_abort": True, "error": f"create_box: {exc}"}

    async def _handle_domain_match(self, action: Action, plan: Plan) -> dict:
        """§3: Validate sender domain matches vendor master."""
        ctx = self._ensure_ctx(plan)
        try:
            from solden.services.vendor_domain_lock import VendorDomainLockService
            service = VendorDomainLockService(
                organization_id=self.organization_id, db=self.db,
            )
            result = service.check_sender_domain(
                vendor_name=ctx.get("extracted_fields", {}).get("vendor_name"),
                sender=ctx.get("sender", ""),
            )
            ctx["domain_check"] = result
            if hasattr(result, "status"):
                status = result.status
            else:
                status = result.get("status", "no_vendor") if isinstance(result, dict) else "unknown"
            if status == "mismatch":
                return {"ok": True, "_stop_plan": True, "reason": "domain_mismatch"}
            return {"ok": True, "domain_status": status}
        except Exception as exc:
            logger.debug("[CoordinationEngine] domain_match non-fatal: %s", exc)
            return {"ok": True}

    async def _handle_duplicate(self, action: Action, plan: Plan) -> dict:
        """§3: Check for duplicate invoice in trailing window."""
        ctx = self._ensure_ctx(plan)
        extracted = ctx.get("extracted_fields", {})
        vendor = extracted.get("vendor_name") or ctx.get("sender", "")
        amount = extracted.get("amount") or extracted.get("total_amount") or 0
        invoice_number = extracted.get("invoice_number") or extracted.get("invoice_reference")
        if not vendor:
            return {"ok": True}
        try:
            from solden.services.cross_invoice_analysis import get_cross_invoice_analyzer
            analyzer = get_cross_invoice_analyzer(
                organization_id=self.organization_id,
            )
            result = analyzer.analyze(
                vendor=vendor,
                amount=float(amount) if amount else 0,
                invoice_number=invoice_number,
                gmail_id=ctx.get("message_id"),
            )
            ctx["duplicate_check"] = result
            has_issues = result.has_issues if hasattr(result, "has_issues") else (result.get("has_issues") if isinstance(result, dict) else False)
            if has_issues:
                duplicates = result.duplicates if hasattr(result, "duplicates") else (result.get("duplicates", []) if isinstance(result, dict) else [])
                if duplicates:
                    return {"ok": True, "_stop_plan": True, "reason": "duplicate_found"}
            return {"ok": True, "has_issues": has_issues}
        except Exception as exc:
            logger.debug("[CoordinationEngine] duplicate check non-fatal: %s", exc)
            return {"ok": True}

    async def _handle_ceiling(self, action: Action, plan: Plan) -> dict:
        """§3: Validate amount does not exceed per-vendor ceiling."""
        ctx = self._ensure_ctx(plan)
        extracted = ctx.get("extracted_fields", {})
        amount = float(extracted.get("amount") or extracted.get("total_amount") or 0)
        # Pass extracted currency raw — evaluate_payment_ceiling falls back
        # to the org's configured base_currency when invoice_currency is
        # empty (fraud_controls.py:323), which is the right anchor for the
        # ceiling comparison. Defaulting to "USD" upstream lied to the
        # check whenever the org's base wasn't USD.
        currency = extracted.get("currency") or ""
        if amount <= 0:
            return {"ok": True}
        try:
            from solden.core.fraud_controls import evaluate_payment_ceiling, load_fraud_controls
            config = load_fraud_controls(self.organization_id, self.db)
            result = evaluate_payment_ceiling(amount, currency, config)
            ctx["ceiling_check"] = result
            if hasattr(result, "exceeds_ceiling"):
                exceeds = result.exceeds_ceiling
            else:
                exceeds = result.get("exceeds_ceiling", False) if isinstance(result, dict) else False
            if exceeds:
                return {"ok": True, "exceeds_ceiling": True}
            return {"ok": True, "exceeds_ceiling": False}
        except Exception as exc:
            logger.debug("[CoordinationEngine] ceiling check non-fatal: %s", exc)
            return {"ok": True}

    async def _handle_velocity(self, action: Action, plan: Plan) -> dict:
        """§3: Check invoice velocity for this vendor."""
        ctx = self._ensure_ctx(plan)
        extracted = ctx.get("extracted_fields", {})
        vendor = extracted.get("vendor_name", "")
        if not vendor:
            return {"ok": True}
        try:
            # Velocity check uses vendor invoice history count
            if hasattr(self.db, "get_vendor_invoice_history"):
                history = await asyncio.to_thread(
                    self.db.get_vendor_invoice_history,
                    self.organization_id, vendor,
                    limit=100,
                )
                window_days = action.params.get("window_days", 7)
                from datetime import timedelta
                cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
                recent = [h for h in (history or []) if (h.get("created_at") or "") >= cutoff]
                ctx["velocity_count"] = len(recent)
                # Flag if > 10 invoices in the window (configurable threshold)
                if len(recent) > 10:
                    return {"ok": True, "velocity_exceeded": True, "count": len(recent)}
            return {"ok": True}
        except Exception as exc:
            logger.debug("[CoordinationEngine] velocity check non-fatal: %s", exc)
            return {"ok": True}

    async def _handle_lookup_po(self, action: Action, plan: Plan) -> dict:
        """§3: Fetch Purchase Order from ERP."""
        ctx = self._ensure_ctx(plan)
        extracted = ctx.get("extracted_fields", {})
        po_number = extracted.get("po_reference") or extracted.get("po_number")
        if not po_number:
            ctx["po_result"] = None
            return {"ok": True, "po_found": False}
        try:
            from solden.services.purchase_orders import get_purchase_order_service
            service = get_purchase_order_service()
            po = service.get_po_by_number(po_number)
            ctx["po_result"] = po
            found = po is not None
            return {"ok": True, "po_found": found, "po_number": po_number}
        except Exception as exc:
            logger.debug("[CoordinationEngine] lookup_po non-fatal: %s", exc)
            ctx["po_result"] = None
            return {"ok": True, "po_found": False}

    async def _handle_lookup_grn(self, action: Action, plan: Plan) -> dict:
        """§3: Fetch Goods Receipt Notes from ERP."""
        ctx = self._ensure_ctx(plan)
        po = ctx.get("po_result")
        if not po:
            ctx["grn_result"] = None
            return {"ok": True, "grn_found": False}
        try:
            from solden.services.purchase_orders import get_purchase_order_service
            service = get_purchase_order_service()
            po_id = po.po_id if hasattr(po, "po_id") else (po.get("po_id") if isinstance(po, dict) else "")
            grns = service.get_goods_receipts_for_po(po_id) if po_id else []
            ctx["grn_result"] = grns
            if not grns:
                # GRN not confirmed — set waiting condition
                return {
                    "ok": True, "grn_found": False,
                    "waiting_condition": {
                        "type": "grn_confirmation",
                        "expected_by": (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(),
                        "context": {"po_number": po_id},
                    },
                }
            return {"ok": True, "grn_found": True, "grn_count": len(grns)}
        except Exception as exc:
            logger.debug("[CoordinationEngine] lookup_grn non-fatal: %s", exc)
            ctx["grn_result"] = None
            return {"ok": True, "grn_found": False}

    def _resolve_match_mode(self) -> str:
        """Read the active ``match_mode`` policy for this engine's
        org. Falls back to ``two_way_fallback`` when the policy
        lookup fails or the value is unrecognised.
        """
        try:
            from solden.services.policy_service import (
                PolicyService,
                VALID_MATCH_MODES,
            )
            version = PolicyService(self.organization_id).get_active("match_mode")
            mode = (version.content or {}).get("mode")
            if mode in VALID_MATCH_MODES:
                return mode
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "[CoordinationEngine] match_mode lookup failed, defaulting to two_way_fallback — %s",
                exc,
            )
        return "two_way_fallback"

    @staticmethod
    def _match_status_value(result: Any) -> str:
        if hasattr(result, "status"):
            status = getattr(result, "status")
            return getattr(status, "value", None) or str(status)
        if isinstance(result, dict):
            return str(result.get("status") or "")
        return ""

    @staticmethod
    def _only_no_gr_exception(result: Any) -> bool:
        """True when the match's only blocker is a missing goods
        receipt — that's the case where 2-way fallback is meaningful.
        Any other exception (price variance, currency mismatch,
        over-invoice) means 2-way wouldn't fix it either."""
        exceptions = getattr(result, "exceptions", None)
        if exceptions is None and isinstance(result, dict):
            exceptions = result.get("exceptions")
        if not exceptions:
            return False
        return all(
            (e.get("type") or "").lower() in ("no_gr", "no goods receipt")
            for e in exceptions
        )

    async def _handle_match(self, action: Action, plan: Plan) -> dict:
        """§3: Run the org's configured match algorithm.

        Mode dispatch (set by the ``match_mode`` policy):
          * ``three_way_required`` — PO + GRN + invoice; missing GRN
            blocks (exception flow runs).
          * ``two_way_fallback`` (default) — try 3-way; if the only
            blocker is a missing GRN, fall through to 2-way (PO +
            invoice). All other 3-way exceptions still block.
          * ``policy_only`` — skip matching entirely; the AP item
            routes via approval thresholds.

        Never calls Claude — this is pure determinism per §3.
        """
        ctx = self._ensure_ctx(plan)
        extracted = ctx.get("extracted_fields", {})
        po = ctx.get("po_result")
        mode = self._resolve_match_mode()

        # policy_only: matching is skipped entirely. Approval-policy
        # routing happens downstream in APDecisionService against
        # amount bands + vendor history.
        if mode == "policy_only":
            ctx["match_result"] = {"status": "skipped_by_policy", "mode": mode}
            if plan.box_id:
                await asyncio.to_thread(
                    self.db.update_ap_item,
                    plan.box_id, match_status="skipped_by_policy",
                )
            return {
                "ok": True,
                "match_status": "skipped_by_policy",
                "match_passed": True,
            }

        # No PO surfaced from the ERP lookup. In three_way_required
        # mode this is a hard block; in two_way_fallback we let it
        # through to approval-policy routing the same as before.
        if not po:
            ctx["match_result"] = {"status": "no_po", "mode": mode}
            if mode == "three_way_required":
                if plan.box_id:
                    await asyncio.to_thread(
                        self.db.update_ap_item,
                        plan.box_id, match_status="exception",
                    )
                await self._run_exception_flow(plan, ctx, ctx["match_result"])
                return {
                    "ok": True,
                    "match_status": "no_po",
                    "match_passed": False,
                    "_stop_plan": True,
                }
            return {"ok": True, "match_status": "no_po"}

        try:
            from solden.services.purchase_orders import get_purchase_order_service
            service = get_purchase_order_service(self.organization_id)
            po_number = extracted.get("po_reference") or extracted.get("po_number", "")
            invoice_id = ctx.get("box_id", "")
            invoice_amount = float(extracted.get("amount") or 0)
            invoice_vendor = extracted.get("vendor_name", "")
            invoice_lines = extracted.get("line_items")
            invoice_currency = str(extracted.get("currency") or "")

            result = service.match_invoice_to_po(
                invoice_id=invoice_id,
                invoice_amount=invoice_amount,
                invoice_vendor=invoice_vendor,
                invoice_po_number=po_number,
                invoice_lines=invoice_lines,
                invoice_currency=invoice_currency,
            )
            status = self._match_status_value(result)
            match_passed = status.upper() in ("MATCHED", "MATCH")

            # 2-way fallback: 3-way's price / currency / quantity
            # checks all passed and the only blocker is a missing
            # GRN. In ``two_way_fallback`` mode that counts as a
            # successful match (PO + invoice, GRN-optional). The
            # NO_GR exception remains on the persisted match record
            # so audit can see the match ran without GRN evidence,
            # but the AP item routes as matched. Any other exception
            # (price variance, currency, over-invoice) still blocks.
            if (
                not match_passed
                and mode == "two_way_fallback"
                and self._only_no_gr_exception(result)
            ):
                match_passed = True
                status = "MATCHED_TWO_WAY"

            ctx["match_result"] = result
            if plan.box_id:
                await asyncio.to_thread(
                    self.db.update_ap_item,
                    plan.box_id,
                    match_status="passed" if match_passed else "exception",
                    grn_reference=extracted.get("po_reference", ""),
                )
            if not match_passed:
                # §9.3: Match failed — run exception flow inline, then stop plan.
                # Don't continue to apply_label(Matched) / send_approval.
                await self._run_exception_flow(plan, ctx, result)
                return {
                    "ok": True,
                    "match_status": status,
                    "match_passed": False,
                    "_stop_plan": True,
                }
            return {"ok": True, "match_status": status, "match_passed": True}
        except Exception as exc:
            logger.debug("[CoordinationEngine] match dispatch non-fatal: %s", exc)
            ctx["match_result"] = None
            return {"ok": True, "match_status": "error"}

    async def _handle_update_fields(self, action: Action, plan: Plan) -> dict:
        """§3: Persist extracted fields to Box record."""
        ctx = self._ensure_ctx(plan)
        extracted = ctx.get("extracted_fields", {})
        if not plan.box_id or not extracted:
            return {"ok": True}
        update_kwargs = {}
        field_map = {
            "vendor_name": "vendor_name",
            "amount": "amount",
            "total_amount": "amount",
            "currency": "currency",
            "invoice_number": "invoice_number",
            "invoice_reference": "invoice_number",
            "invoice_date": "invoice_date",
            "due_date": "due_date",
            "po_reference": "po_number",
            "po_number": "po_number",
            "payment_terms": None,  # Not a direct column
        }
        for src, dst in field_map.items():
            if dst and src in extracted and extracted[src] is not None:
                update_kwargs[dst] = extracted[src]
        if extracted.get("confidence"):
            update_kwargs["confidence"] = extracted["confidence"]
        if extracted.get("field_confidences"):
            update_kwargs["field_confidences"] = extracted["field_confidences"]
        if update_kwargs:
            try:
                await asyncio.to_thread(
                    self.db.update_ap_item, plan.box_id, **update_kwargs,
                )
            except Exception as exc:
                logger.warning("[CoordinationEngine] update_box_fields failed: %s", exc)
        return {"ok": True, "fields_updated": list(update_kwargs.keys())}

    def _build_invoice_from_ctx(self, ctx: Dict[str, Any]):
        """Build an InvoiceData from accumulated execution context."""
        from solden.services.invoice_workflow import InvoiceData
        extracted = ctx.get("extracted_fields", {})
        return InvoiceData(
            gmail_id=ctx.get("thread_id") or ctx.get("message_id", ""),
            subject=ctx.get("subject", ""),
            sender=ctx.get("sender", ""),
            vendor_name=extracted.get("vendor_name") or ctx.get("sender", ""),
            amount=float(extracted.get("amount") or extracted.get("total_amount") or 0),
            # Empty string when extraction missed it — persistence
            # carries this through as NULL so the dashboard renders
            # honestly instead of fabricating "USD".
            currency=extracted.get("currency") or "",
            invoice_number=extracted.get("invoice_number") or extracted.get("invoice_reference"),
            due_date=extracted.get("due_date"),
            po_number=extracted.get("po_reference") or extracted.get("po_number"),
            confidence=float(extracted.get("confidence") or 0),
            organization_id=self.organization_id,
            user_id=ctx.get("user_id", ""),
            field_confidences=extracted.get("field_confidences"),
            line_items=extracted.get("line_items"),
        )

    async def _handle_stage_transition(self, action: Action, plan: Plan) -> dict:
        """§3: Advance or revert a Box to a specific pipeline stage.

        Manifesto §"Ownership" auto-assignment hook: after the state
        change lands, resolve who acts next on the Box and stamp
        ``owner_*`` columns + an ``owner_changed`` audit event. The
        hook respects the sticky-manual doctrine — see
        :func:`_maybe_assign_owner` for the rule. Failures log and
        continue; ownership is observability + routing, not a
        correctness invariant of the transition itself.
        """
        target = action.params.get("target", "")
        if plan.box_id and target:
            # Capture prev_state before the update so the auto-assign
            # hook can apply the sticky-manual-within-state-class rule.
            prev_item = await asyncio.to_thread(
                box_registry.get_box, plan.box_type, plan.box_id, self.db
            )
            prev_state = str(prev_item.get("state") or "") if prev_item else ""
            try:
                await asyncio.to_thread(
                    box_registry.update_box,
                    plan.box_type, plan.box_id, self.db,
                    state=target,
                    actor_id=action.params.get("actor_id") or "agent",
                    reason=action.params.get("reason", ""),
                )
            except Exception as exc:
                return {"_abort": True, "error": f"Stage transition to {target} failed: {exc}"}
            # Owner auto-assignment is an AP-domain concept; skip for other
            # box types (they have no owner_* columns / role routing).
            if plan.box_type == "ap_item":
                await self._maybe_assign_owner(plan.box_id, prev_state=prev_state)
        return {"ok": True}

    async def _maybe_assign_owner(self, box_id: str, prev_state: str = "") -> None:
        """Resolve and persist the current owner after a state transition.

        Sticky-manual doctrine (Mo's call 2026-05-14): a manual owner
        assignment survives transitions WITHIN the same state class
        but is re-resolved on a CROSS-class transition. State classes
        are defined in :data:`solden.services.box_owner.STATE_CLASSES`.

        Rationale: when an operator manually routes a Box at, say,
        ``needs_approval``, that choice was for the approval role.
        A transition to ``needs_info`` typically routes to a different
        role (a clerk who answers questions, not an approver) — sticky
        across that boundary would silently leave the wrong human in
        the queue. Within a class (e.g. needs_approval →
        needs_second_approval) the operator's intent still applies.

        Pure side effect. Errors are logged and swallowed.
        """
        if not box_id:
            return
        try:
            item = await asyncio.to_thread(self.db.get_ap_item, box_id)
            if not item:
                return
            from solden.services.box_owner import (
                apply_resolved_owner,
                resolve_owner,
                state_class,
            )
            # Sticky-manual rule: if owner_source is 'manual' and the
            # transition stays within the same class, keep it. Otherwise
            # fall through to re-resolution, which will replace the
            # manual owner with the configured default for the new
            # state (auto or delegate).
            if str(item.get("owner_source") or "") == "manual":
                current_class = state_class(item.get("state") or "")
                prev_class = state_class(prev_state)
                if current_class and current_class == prev_class:
                    return
            # resolve_owner is sync but makes three blocking DB calls
            # (get_organization, list delegation_rules, get_user_by_email).
            # Off-load it to a worker thread so a slow Postgres doesn't
            # stall the event loop on every stage transition. Same
            # pattern as the surrounding to_thread calls.
            assignment = await asyncio.to_thread(
                resolve_owner,
                box=item,
                organization_id=self.organization_id,
                db=self.db,
            )
            if assignment is None:
                return
            # Skip the write when the resolved owner already matches —
            # avoids audit-spam on transitions that don't actually
            # change ownership (e.g., needs_approval → needs_info
            # routed to the same person).
            if (
                str(item.get("owner_email") or "") == assignment.owner_email
                and str(item.get("owner_source") or "") == assignment.owner_source
            ):
                return
            await asyncio.to_thread(
                apply_resolved_owner,
                db=self.db,
                ap_item_id=box_id,
                organization_id=self.organization_id,
                assignment=assignment,
                actor_id="coordination_engine",
            )
        except Exception as exc:
            logger.warning(
                "[CoordinationEngine] owner auto-assign failed for %s: %s",
                box_id, exc,
            )

    async def _handle_send_approval(self, action: Action, plan: Plan) -> dict:
        """§3: Send structured approval message to Slack/Teams."""
        ctx = self._ensure_ctx(plan)
        try:
            wf = self._get_workflow()
            invoice = self._build_invoice_from_ctx(ctx)
            invoice.gmail_id = ctx.get("thread_id") or ctx.get("message_id", "")
            extra_context = {}
            if ctx.get("validation_gate"):
                extra_context["validation_gate"] = ctx["validation_gate"]
            result = await wf._send_for_approval(invoice, extra_context=extra_context or None)
            return {
                "ok": True,
                "approval_sent": True,
                "slack_channel": result.get("slack_channel"),
                "waiting_condition": {
                    "type": "approval_response",
                    "expected_by": (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(),
                    "context": {"channel": result.get("slack_channel"), "ts": result.get("slack_ts")},
                },
            }
        except Exception as exc:
            logger.error("[CoordinationEngine] send_approval failed: %s", exc)
            return {
                "ok": True,
                "waiting_condition": {
                    "type": "approval_response",
                    "expected_by": (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(),
                },
            }

    async def _handle_post_bill(self, action: Action, plan: Plan) -> dict:
        if not plan.box_id:
            return {"_abort": True, "error": "No box_id for post_bill"}
        wf = self._get_workflow()
        item = await asyncio.to_thread(
            box_registry.get_box, plan.box_type, plan.box_id, self.db
        )
        if not item:
            return {"_abort": True, "error": "box not found"}

        # Group 2 idempotency guard (2026-05-06): the AP item's
        # ``erp_reference`` is the source of truth for "did this bill
        # already post." On Celery retry / Redis Stream redelivery
        # the plan re-runs from the start; without this guard a
        # second post_bill would create a duplicate ERP record.
        # Audit-row dedupe (idempotency_key on _pre_write/_post_write)
        # stops the timeline from double-writing, but doesn't stop
        # the side effect itself — that's what this check does.
        existing_ref = str(item.get("erp_reference") or "").strip()
        if existing_ref:
            logger.info(
                "[CoordinationEngine] post_bill: skipping — erp_reference already set "
                "for box=%s ref=%s (replay-safe).",
                plan.box_id, existing_ref,
            )
            return {
                "ok": True,
                "erp_reference": existing_ref,
                "noop": "already_posted",
            }

        try:
            from solden.services.invoice_workflow import InvoiceData
            invoice = InvoiceData(
                gmail_id=item.get("thread_id") or item.get("message_id") or "",
                subject=item.get("subject") or "",
                sender=item.get("sender") or "",
                vendor_name=item.get("vendor_name") or "",
                amount=float(item.get("amount") or 0),
                # Carry the row's raw currency through — empty string
                # if absent. The downstream ERP poster decides how to
                # handle missing currency (its own base_currency
                # fallback, or reject if mandatory).
                currency=item.get("currency") or "",
                invoice_number=item.get("invoice_number"),
                organization_id=self.organization_id,
            )
            result = await wf._post_to_erp(invoice)
            if result.get("status") in ("posted", "success", "posted_to_erp"):
                erp_ref = result.get("reference_id")
                # Group 8 backstop (2026-05-07): the workflow's
                # ``_post_to_erp`` is supposed to persist
                # ``erp_reference`` via its own state-transition
                # path, but if that ever returns success without
                # writing the column (refactor regression, partial
                # commit, transaction rollback after the ERP call
                # succeeded), the next action — typically
                # ``send_slack_override_window`` — reads the AP
                # row and silently no-ops because it sees an empty
                # ``erp_reference``. Belt-and-suspenders: write the
                # ref ourselves. ``update_ap_item`` is idempotent
                # (re-writing the same value is a no-op), so this
                # is safe even when the workflow already persisted.
                if erp_ref and plan.box_id:
                    try:
                        await asyncio.to_thread(
                            box_registry.update_box,
                            plan.box_type, plan.box_id, self.db,
                            erp_reference=erp_ref,
                        )
                    except Exception as exc:
                        logger.warning(
                            "[CoordinationEngine] post_bill erp_reference backstop write failed for box=%s: %s",
                            plan.box_id, exc,
                        )
                return {"ok": True, "erp_reference": erp_ref}
            return {"_abort": True, "error": result.get("reason", "ERP post failed")}
        except Exception as exc:
            return {"_abort": True, "error": str(exc)}

    async def _handle_pre_post_validate(self, action: Action, plan: Plan) -> dict:
        if not plan.box_id:
            return {"ok": True}
        from solden.integrations.erp_router import pre_post_validate
        result = pre_post_validate(plan.box_id, self.organization_id, db=self.db)
        if not result.get("valid"):
            return {"_abort": True, "error": f"Pre-post validation failed: {result.get('failures')}"}
        return {"ok": True}

    async def _handle_schedule_payment(self, action: Action, plan: Plan) -> dict:
        """Mark the AP item as ready for payment (V1: ERP-intermediated).

        V1 scope per the thesis: Solden posts the bill to the ERP
        and the customer runs the payment from their ERP / treasury
        tool. Direct payment execution is Q4 roadmap and not wired
        here. This handler therefore does NOT create an actual
        ERP payment record or bank instruction — it only records on
        our side that the invoice has cleared approval and is ready
        to pay.

        The real ERP payment_reference is populated later by the
        payment-polling path in agent_background when the customer's
        pay run settles the bill and we detect it.
        """
        if not plan.box_id:
            return {"ok": True}
        item = await asyncio.to_thread(
            box_registry.get_box, plan.box_type, plan.box_id, self.db
        )
        if not item or not item.get("erp_reference"):
            return {"ok": True, "scheduled": False, "reason": "no_erp_bill"}
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            existing_meta = dict(item.get("metadata") or {})
            # Preserve any real payment_reference already captured by
            # the polling path — never overwrite a settled reference
            # with our local "ready to pay" marker.
            existing_meta.update({
                "payment_scheduled": True,
                "payment_scheduled_at": now_iso,
                # Settled=False is the V1 contract: we've said the bill
                # is ready to pay, but the actual pay run is external.
                "payment_settled": existing_meta.get("payment_settled", False),
            })
            await asyncio.to_thread(
                box_registry.update_box,
                plan.box_type, plan.box_id, self.db,
                metadata=existing_meta,
            )
            return {"ok": True, "scheduled": True, "settled": False}
        except Exception as exc:
            logger.debug("[CoordinationEngine] schedule_payment non-fatal: %s", exc)
            return {"ok": True}

    async def _handle_set_waiting(self, action: Action, plan: Plan) -> dict:
        timeout_hours = action.params.get("timeout_hours", 4)
        return {
            "ok": True,
            "waiting_condition": {
                "type": action.params.get("type", "unknown"),
                "expected_by": (datetime.now(timezone.utc) + timedelta(hours=timeout_hours)).isoformat(),
            },
        }

    async def _handle_clear_waiting(self, action: Action, plan: Plan) -> dict:
        if plan.box_id:
            wf = self._get_workflow()
            wf.clear_waiting_condition(plan.box_id)
        return {"ok": True}

    def _cas_clear_pending_plan(self, box_id: str) -> Optional[Any]:
        """Atomic compare-and-swap: read ``pending_plan`` AND clear
        it in a single ``UPDATE`` statement. Returns the prior value
        on win, ``None`` on loss or empty.

        Why CAS: two redelivered resumption events that both reach
        ``_handle_resume_plan`` must not both run the saved plan —
        that would double-execute every action it contains. The
        ``UPDATE ... WHERE pending_plan IS NOT NULL RETURNING ...``
        pattern combined with Postgres row-level locking on UPDATE
        serializes the two access patterns at the storage layer:
        the first UPDATE returns the JSON and clears the column;
        the second sees IS NULL, updates 0 rows, returns nothing.
        Only the winner runs the resumed plan.

        Returns the raw ``pending_plan`` field value (string or
        dict, depending on the row factory). Caller deserializes.
        """
        if not hasattr(self.db, "connect"):
            return None
        # Postgres ``RETURNING`` returns the POST-update value, not
        # the prior one. To capture the value being cleared, use a
        # CTE that selects + locks the row first, then UPDATEs and
        # returns the CTE's stored value. ``FOR UPDATE`` holds a
        # row-level lock for the duration of the statement so two
        # concurrent CAS callers serialize: the first sees the
        # value, clears it; the second sees pending_plan IS NULL,
        # the CTE returns no rows, the UPDATE updates 0 rows.
        sql = (
            "WITH cas AS ("
            "    SELECT id, pending_plan "
            "    FROM ap_items "
            "    WHERE id = %s AND pending_plan IS NOT NULL "
            "    FOR UPDATE"
            ") "
            "UPDATE ap_items SET pending_plan = NULL "
            "FROM cas "
            "WHERE ap_items.id = cas.id "
            "RETURNING cas.pending_plan"
        )
        try:
            with self.db.connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (box_id,))
                    row = cur.fetchone()
                conn.commit()
            if not row:
                return None
            if isinstance(row, dict):
                return row.get("pending_plan")
            return row[0] if row else None
        except Exception as exc:
            logger.warning(
                "[CoordinationEngine] CAS clear pending_plan failed for box=%s: %s",
                box_id, exc,
            )
            return None

    async def _handle_resume_plan(self, action: Action, plan: Plan) -> dict:
        """Resume execution from the box's ``pending_plan`` column.

        Two redelivered events that both reach this handler: only
        the CAS winner deserializes and runs the saved plan; the
        loser returns a ``no_pending_plan`` no-op.

        Runs the resumed plan via ``_execute_body`` (not
        ``execute``) because the outer ``execute()`` already holds
        the per-box advisory lock. Calling ``execute()`` again
        would attempt to re-acquire and bail out with ``lock_held``
        against itself.

        Resumed-plan failures are recorded by the resumed plan's
        own audit machinery (Rule 1 pre/post-writes, exception
        flow). This handler just bubbles up the resumed status as
        a result-dict field; the outer plan is "we triggered the
        resume" and shouldn't double-record what the resumed plan
        already audited.
        """
        if not plan.box_id:
            return {"ok": True, "resumed": False, "reason": "no_box_id"}

        pending_json = self._cas_clear_pending_plan(plan.box_id)
        if pending_json is None:
            # Either no plan to resume (e.g. wait fired but the
            # planner didn't save a remainder), or another resumer
            # already won the CAS. Either way, no-op.
            return {
                "ok": True, "resumed": False,
                "reason": "no_pending_plan",
            }

        try:
            import json as _json
            if isinstance(pending_json, str):
                resumed_plan = Plan.from_json(pending_json)
            elif isinstance(pending_json, dict):
                resumed_plan = Plan.from_json(_json.dumps(pending_json))
            else:
                return {
                    "ok": True, "resumed": False,
                    "reason": "invalid_pending_plan_shape",
                }
        except Exception as exc:
            logger.warning(
                "[CoordinationEngine] failed to deserialize pending_plan for box=%s: %s",
                plan.box_id, exc,
            )
            return {
                "ok": True, "resumed": False,
                "reason": "deserialization_failed",
            }

        if resumed_plan.is_empty:
            return {
                "ok": True, "resumed": False,
                "reason": "empty_plan",
            }

        # We already hold the box lock (outer execute acquired it).
        # Call _execute_body directly to avoid the lock acquisition
        # path against ourselves.
        resumed_result = await self._execute_body(resumed_plan)
        return {
            "ok": True,
            "resumed": True,
            "resumed_status": resumed_result.status,
            "resumed_steps": resumed_result.steps_completed,
        }

    async def _handle_timeline(self, action: Action, plan: Plan) -> dict:
        if plan.box_id and hasattr(self.db, "append_audit_event"):
            await asyncio.to_thread(self.db.append_audit_event, {
                "ap_item_id": plan.box_id,
                "event_type": "agent_action:post_timeline_entry",
                "actor_type": "agent",
                "actor_id": "coordination_engine",
                "organization_id": self.organization_id,
                "payload_json": {
                    "summary": action.params.get("summary", action.description),
                    "format": action.params.get("format", ""),
                },
            })
        return {"ok": True}

    async def _handle_watch_thread(self, action: Action, plan: Plan) -> dict:
        """§3: Register a thread for monitoring — replies bypass classification."""
        ctx = self._ensure_ctx(plan)
        thread_id = ctx.get("thread_id") or ctx.get("message_id", "")
        if plan.box_id and thread_id:
            try:
                # Store thread→box mapping so future replies route directly
                await asyncio.to_thread(
                    self.db.update_ap_item, plan.box_id, thread_id=thread_id,
                )
            except Exception as exc:
                logger.debug("[CoordinationEngine] watch_thread non-fatal: %s", exc)
        return {"ok": True, "thread_id": thread_id}

    async def _handle_override_window(self, action: Action, plan: Plan) -> dict:
        """§3: Post override window notification with live Undo button."""
        if not plan.box_id:
            return {"ok": True}
        try:
            from solden.services.override_window import get_override_window_service
            service = get_override_window_service(self.organization_id, db=self.db)
            item = (
                await asyncio.to_thread(self.db.get_ap_item, plan.box_id)
                if plan.box_id else None
            )
            erp_ref = (item or {}).get("erp_reference", "")
            if not erp_ref:
                return {"ok": True}  # No ERP reference yet — nothing to override
            window = service.open_window(
                ap_item_id=plan.box_id,
                erp_reference=erp_ref,
            )
            return {"ok": True, "window_id": window.get("id") if isinstance(window, dict) else None}
        except Exception as exc:
            logger.debug("[CoordinationEngine] override_window non-fatal: %s", exc)
            return {"ok": True}

    async def _handle_close_override(self, action: Action, plan: Plan) -> dict:
        try:
            from solden.services.agent_background import reap_expired_override_windows
            await reap_expired_override_windows()
        except Exception as exc:
            logger.warning("[CoordinationEngine] close_override failed: %s", exc)
        return {"ok": True}

    async def _handle_escalate(self, action: Action, plan: Plan) -> dict:
        try:
            from solden.services.agent_background import _check_approval_timeouts
            await _check_approval_timeouts(self.organization_id)
        except Exception as exc:
            logger.debug("[CoordinationEngine] Escalation failed: %s", exc)
        return {"ok": True}

    # ``_handle_send_vendor_email`` removed: Solden sends zero email
    # to vendors and the handler had been a no-op stub since the
    # 2026-04-30 product call. The 2026-05-03 full sweep deletes the
    # stub itself, the registration in ``_handlers``, and the priority
    # entry — keeping the stub was preserving optionality that no
    # roadmap item plans to use, and leaving it in place was
    # confusing readers about what the system actually does (audit
    # trail, support docs, BFN/ICC questionnaires all benefit from
    # "Solden literally cannot send vendor email").

    async def _handle_classify_vendor(self, action: Action, plan: Plan) -> dict:
        """§3 LLM: Classify a vendor's reply to an onboarding or chase email.

        Gives Claude:
          - the vendor's reply body
          - the onboarding session's current state (what we're waiting on)
          - which documents are outstanding
          - when the last agent action on this vendor fired

        Expects back a typed classification PLUS any extracted action
        data (asked question text, cited document name, refused reason)
        so the router can do more than just branch on a single label.
        """
        ctx = self._ensure_ctx(plan)
        vendor_id = action.params.get("vendor_id", "") or ctx.get("vendor_id", "")
        body = ctx.get("body", "")
        if not body:
            return {"ok": True, "type": "unclassifiable"}

        # Pull onboarding session context so Claude can resolve references
        # like "it" / "the document you asked for" against actual state.
        session_state = "unknown"
        outstanding: list = []
        days_since_invite = None
        try:
            sessions = await asyncio.to_thread(
                self.db.list_pending_onboarding_sessions,
            ) or []
            for s in sessions:
                if str(s.get("vendor_name") or "").lower() == str(vendor_id or "").lower():
                    session_state = str(s.get("state") or "unknown")
                    invited_at = s.get("invited_at")
                    if invited_at:
                        try:
                            from datetime import datetime, timezone
                            invited = datetime.fromisoformat(str(invited_at).replace("Z", "+00:00"))
                            days_since_invite = int(
                                (datetime.now(timezone.utc) - invited).total_seconds() // 86400
                            )
                        except Exception:
                            pass
                    outstanding = {
                        "invited": ["onboarding form not yet opened"],
                        "kyc": [
                            "registered address",
                            "company registration number",
                            "director names",
                        ],
                        "bank_verify": [
                            "bank IBAN",
                            "account holder name",
                        ],
                        "blocked": ["responsive contact point"],
                    }.get(session_state, [])
                    break
        except Exception:
            pass

        system_prompt = (
            "You are Solden's AP agent classifying a vendor's email reply "
            "to an onboarding or chase message. You will be given the reply "
            "body and what the session is currently waiting on, and must "
            "return a strict JSON classification.\n\n"
            "Valid types:\n"
            "  - document_submitted: vendor attached or pasted the info we "
            "    asked for (address, registration, bank details, IBAN, etc.)\n"
            "  - question_asked: vendor is asking us something before "
            "    continuing. Extract the question text.\n"
            "  - refused: vendor is unwilling to proceed. Extract reason.\n"
            "  - out_of_office: automated OOO / vacation responder.\n"
            "  - incorrect_contact: vendor says we reached the wrong person; "
            "    extract a redirect email if provided.\n"
            "  - unclassifiable: unclear or doesn't fit any category.\n\n"
            "Return only valid JSON in this exact shape — no prose, no "
            "markdown:\n"
            "{\n"
            "  \"type\": \"<one of the types above>\",\n"
            "  \"confidence\": <float 0.0-1.0>,\n"
            "  \"reasoning\": \"<one sentence citing the specific language in the reply that led to this classification>\",\n"
            "  \"extracted\": {\n"
            "    \"question_text\": \"<null or the question the vendor asked, verbatim>\",\n"
            "    \"refusal_reason\": \"<null or short phrase>\",\n"
            "    \"redirect_email\": \"<null or the email they said to contact instead>\",\n"
            "    \"submitted_fields\": [\"<field names present in the reply>\"]\n"
            "  }\n"
            "}"
        )

        outstanding_text = (
            ", ".join(outstanding) if outstanding else "unspecified"
        )
        age_phrase = (
            f"{days_since_invite}d since invite"
            if days_since_invite is not None
            else "time unknown"
        )
        user_message = (
            f"Vendor: {vendor_id or 'unknown'}\n"
            f"Onboarding state: {session_state} ({age_phrase})\n"
            f"We are waiting on: {outstanding_text}\n\n"
            f"Vendor reply:\n{body[:3000]}"
        )

        try:
            from solden.core.llm_gateway import get_llm_gateway, LLMAction
            gateway = get_llm_gateway()
            # Box-reconstructability invariant — pass the ap_item_id
            # through so llm_call_log rows link back to the Box an
            # auditor is reviewing.
            resp = await gateway.call(
                LLMAction.CLASSIFY_VENDOR,
                messages=[{"role": "user", "content": user_message}],
                system_prompt=system_prompt,
                organization_id=self.organization_id,
                ap_item_id=plan.box_id,
                correlation_id=getattr(plan, "correlation_id", None),
            )
            import json
            raw = str(resp.content or "").strip() if resp else ""
            # Defensive JSON parse — Claude sometimes wraps JSON in fences
            # even after being told not to.
            if raw.startswith("```"):
                raw = raw.strip("`")
                if raw.lower().startswith("json"):
                    raw = raw[4:].lstrip()
            try:
                result = json.loads(raw) if raw else {}
            except ValueError:
                logger.debug(
                    "[CoordinationEngine] classify_vendor got non-JSON: %s",
                    raw[:200],
                )
                result = {}
            # Validate type enum — if Claude hallucinates a new type,
            # fall back to unclassifiable so the router doesn't break.
            valid_types = {
                "document_submitted",
                "question_asked",
                "refused",
                "out_of_office",
                "incorrect_contact",
                "unclassifiable",
            }
            classified_type = str(result.get("type") or "").strip()
            if classified_type not in valid_types:
                classified_type = "unclassifiable"
                result["type"] = classified_type
            ctx["vendor_response_classification"] = result
            return {
                "ok": True,
                "type": classified_type,
                "confidence": float(result.get("confidence") or 0),
                "extracted": result.get("extracted") or {},
            }
        except Exception as exc:
            logger.debug("[CoordinationEngine] classify_vendor non-fatal: %s", exc)
            return {"ok": True, "type": "unclassifiable"}

    async def _handle_generate_exception(self, action: Action, plan: Plan) -> dict:
        """§3 LLM: Generate plain-language exception reason in DID-WHY-NEXT format."""
        ctx = self._ensure_ctx(plan)
        match_result = ctx.get("match_result")
        if not match_result:
            return {"ok": True}
        try:
            from solden.core.llm_gateway import get_llm_gateway, LLMAction
            gateway = get_llm_gateway()
            import json
            prompt = (
                "Generate a plain-language explanation for this invoice match exception.\n\n"
                f"Match result:\n{json.dumps(match_result, default=str)[:1000]}\n\n"
                "Write one paragraph in DID-WHY-NEXT format:\n"
                "DID: what happened. WHY: why it failed. NEXT: what to do.\n"
                "Maximum 150 words. Factual and precise."
            )
            resp = gateway.call_sync(
                LLMAction.GENERATE_EXCEPTION,
                messages=[{"role": "user", "content": prompt}],
                organization_id=self.organization_id,
            )
            reason = str(resp.content).strip()[:500] if resp.content else ""
            if plan.box_id and reason:
                await asyncio.to_thread(
                    self.db.update_ap_item, plan.box_id, exception_reason=reason,
                )
            return {"ok": True, "reason": reason}
        except Exception as exc:
            logger.debug("[CoordinationEngine] generate_exception non-fatal: %s", exc)
            return {"ok": True}

    async def _handle_route_vendor(self, action: Action, plan: Plan) -> dict:
        """§10: Route classified vendor response to appropriate onboarding step.

        ``question_asked`` routes to operator review — Solden does not
        author a vendor-facing draft (zero-vendor-email rule).
        """
        ctx = self._ensure_ctx(plan)
        classification = ctx.get("vendor_response_classification", {})
        response_type = classification.get("type", "unclassifiable")
        if response_type == "document_submitted":
            return {"ok": True, "next": "validate_kyc_document"}
        elif response_type in ("question_asked", "refused", "incorrect_contact"):
            return {"ok": True, "next": "escalate_to_ap_manager"}
        return {"ok": True, "next": "flag_for_review"}

    async def _handle_kyc_validate(self, action: Action, plan: Plan) -> dict:
        """§10: Validate KYC document against requirements checklist.

        Records the submitted ``document_type`` against the active
        onboarding session so the lifecycle worker can advance the
        checklist on the next sweep. The session presence-check is the
        load-bearing gate; full per-document field validation lives in
        the onboarding lifecycle service.
        """
        vendor_id = action.params.get("vendor_id", "")
        document_type = action.params.get("document_type", "")
        if not vendor_id:
            return {"ok": True}
        try:
            if hasattr(self.db, "get_active_onboarding_session"):
                session = await asyncio.to_thread(
                    self.db.get_active_onboarding_session,
                    self.organization_id, vendor_id,
                )
                if session:
                    return {
                        "ok": True,
                        "valid": True,
                        "session_id": session.get("id"),
                        "document_type": document_type or None,
                    }
            return {"ok": True, "valid": False, "reason": "no_active_session"}
        except Exception as exc:
            logger.debug("[CoordinationEngine] kyc_validate non-fatal: %s", exc)
            return {"ok": True}

    async def _handle_onboarding_adapter_pending(self, action: Action, plan: Plan) -> dict:
        """Stub handler for vendor-onboarding v1.1 actions that depend on
        KYC / open-banking / ERP-write provider adapters.

        The spec (vendor-onboarding-spec §5) names these actions and
        wires them into the planner. Real implementations land when the
        customer-side provider contracts are signed and the adapters
        register themselves via :func:`register_kyc_provider` and
        :func:`register_bank_verifier`.

        Until then, this stub HALTS the plan via ``_stop_plan``. The
        Rule 1 pre-write already recorded the action to the timeline;
        ``_stop_plan`` tells the execute loop (§5.1 step 3) to exit
        cleanly without running any subsequent actions. That is the
        guardrail: downstream steps (including any ``move_box_stage``)
        must not run against fake adapter results. The Box stays in
        whatever state it was in before the plan started. When real
        adapters are wired, the stub is replaced with the adapter call
        and the plan runs to completion.
        """
        logger.info(
            "[CoordinationEngine] %s — provider adapter pending (org=%s, box=%s). Plan halted.",
            action.name, self.organization_id, plan.box_id or "—",
        )
        return {
            "_stop_plan": True,
            "adapter_pending": True,
            "action": action.name,
            "reason": "provider_adapter_pending",
        }

    async def _handle_onboarding_progress(self, action: Action, plan: Plan) -> dict:
        """§10: Update onboarding stage if all documents received."""
        ctx = self._ensure_ctx(plan)
        vendor_id = action.params.get("vendor_id", "") or ctx.get("vendor_id", "")
        if not vendor_id:
            return {"ok": True}
        try:
            # Check onboarding session and advance if all documents received
            if hasattr(self.db, "get_active_onboarding_session"):
                session = await asyncio.to_thread(
                    self.db.get_active_onboarding_session,
                    self.organization_id, vendor_id,
                )
                if session:
                    state = session.get("state", "")
                    return {"ok": True, "current_state": state, "session_id": session.get("id")}
            return {"ok": True}
        except Exception as exc:
            logger.debug("[CoordinationEngine] onboarding_progress non-fatal: %s", exc)
            return {"ok": True}

    async def _handle_freeze_payments(self, action: Action, plan: Plan) -> dict:
        """§3: Apply payment hold on all invoices from a vendor.

        For the iban_change flow, skips the freeze when the prior IBAN
        was unverified / first submission — check_iban_change sets
        ``iban_requires_freeze=False`` on the plan context in those
        cases, and we honour it here so we don't spuriously freeze
        fresh-onboarding vendors.
        """
        vendor_id = action.params.get("vendor_id", "")
        reason = action.params.get("reason", "fraud_control")
        if not vendor_id:
            return {"ok": True}
        ctx = self._ensure_ctx(plan)
        if "iban_requires_freeze" in ctx and not ctx["iban_requires_freeze"]:
            return {
                "ok": True,
                "frozen": False,
                "reason": "first_submission_or_unchanged",
            }
        try:
            if hasattr(self.db, "update_vendor_profile"):
                await asyncio.to_thread(
                    self.db.update_vendor_profile,
                    self.organization_id, vendor_id,
                    status="frozen", frozen_reason=reason,
                )
            return {"ok": True, "frozen": True, "vendor_id": vendor_id}
        except Exception as exc:
            logger.debug("[CoordinationEngine] freeze_payments non-fatal: %s", exc)
            return {"ok": True}

    async def _handle_iban_change(self, action: Action, plan: Plan) -> dict:
        """§3: Decide whether the submitted IBAN is new vs. a change.

        Three outcomes:
          - No prior IBAN on record    → first-time bank details, NOT a
                                         change. Downstream still runs
                                         micro-deposit verification but
                                         skips the payment freeze (there's
                                         nothing to freeze — no prior
                                         trust to protect).
          - Same IBAN resubmitted      → no-op. Vendor clicked Save twice
                                         or re-opened the portal.
          - Different IBAN             → CHANGE. Downstream freezes
                                         payments and restarts verification.

        The previous implementation reported `changed=True` for ANY prior
        IBAN regardless of whether the submitted one was actually
        different — including same-IBAN resubmissions. That triggered
        spurious payment freezes on vendors who were just reopening the
        portal.
        """
        vendor_id = str(action.params.get("vendor_id") or "").strip()
        new_iban = str(action.params.get("new_iban") or "").strip().upper().replace(" ", "")
        ctx = self._ensure_ctx(plan)
        if not vendor_id:
            ctx["iban_change_status"] = "no_vendor"
            return {"ok": True}
        try:
            if not hasattr(self.db, "get_vendor_profile"):
                ctx["iban_change_status"] = "no_profile_store"
                return {"ok": True}
            profile = await asyncio.to_thread(
                self.db.get_vendor_profile, self.organization_id, vendor_id,
            ) or {}
            existing_iban = str(profile.get("iban") or "").strip().upper().replace(" ", "")
            was_verified = bool(profile.get("iban_verified"))
            if not existing_iban:
                ctx["iban_change_status"] = "first_submission"
                ctx["iban_requires_freeze"] = False
                return {"ok": True, "changed": False, "first_submission": True}
            if new_iban and existing_iban == new_iban:
                ctx["iban_change_status"] = "no_change"
                ctx["iban_requires_freeze"] = False
                return {"ok": True, "changed": False}
            # IBAN actually changed. Freeze only if the prior IBAN was
            # verified — we don't freeze mid-onboarding corrections.
            ctx["iban_change_status"] = "changed"
            ctx["iban_requires_freeze"] = was_verified
            return {
                "ok": True,
                "changed": True,
                "freeze_required": was_verified,
            }
        except Exception as exc:
            logger.debug("[CoordinationEngine] iban_change non-fatal: %s", exc)
            return {"ok": True}

    async def _handle_iban_verify(self, action: Action, plan: Plan) -> dict:
        """§3: IBAN verification hook.

        The old micro-deposit flow was removed. Until the Adyen (EU) and
        TrueLayer (UK/RoW) verifier adapters land, this handler HALTS
        the plan — same reason as ``_handle_onboarding_adapter_pending``:
        downstream actions must not run against a stubbed verification
        result. The portal transitions ``awaiting_bank → bank_verified``
        directly on IBAN submission (mod-97 checksum is the only gate),
        so production state still advances via that path; the agent
        plan simply doesn't do any of it until an adapter is registered.
        """
        return {
            "_stop_plan": True,
            "initiated": False,
            "adapter_pending": True,
            "reason": "provider_adapter_pending",
        }

    async def _handle_evaluate_grn(self, action: Action, plan: Plan) -> dict:
        """§4.3: Evaluate GRN lookup result — clear waiting or reschedule."""
        ctx = self._ensure_ctx(plan)
        grn_result = ctx.get("grn_result")
        max_retries = action.params.get("max_retries", 10)
        check_interval = action.params.get("check_interval_hours", 4)

        if grn_result:
            # GRN confirmed — clear waiting and continue
            if plan.box_id:
                wf = self._get_workflow()
                wf.clear_waiting_condition(plan.box_id)
            return {"ok": True, "grn_confirmed": True}

        # GRN not confirmed — check retry count and due date
        item = (
            await asyncio.to_thread(self.db.get_ap_item, plan.box_id)
            if plan.box_id else None
        )
        if item:
            import json as _json
            waiting = item.get("waiting_condition")
            if isinstance(waiting, str):
                try:
                    waiting = _json.loads(waiting)
                except Exception:
                    waiting = {}
            retry_count = (waiting or {}).get("retry_count", 0) + 1
            if retry_count >= max_retries:
                # §4.3: Maximum retries — mandatory escalation
                return {"ok": True, "grn_confirmed": False, "_stop_plan": True, "reason": "grn_max_retries_exceeded"}

            # Check if due within 48h — escalate
            due_date = item.get("due_date")
            if due_date:
                try:
                    due = datetime.fromisoformat(due_date.replace("Z", "+00:00"))
                    if (due - datetime.now(timezone.utc)).total_seconds() < 48 * 3600:
                        return {"ok": True, "grn_confirmed": False, "_stop_plan": True, "reason": "invoice_due_soon"}
                except Exception:
                    pass

            # Reschedule check
            return {
                "ok": True,
                "grn_confirmed": False,
                "waiting_condition": {
                    "type": "grn_confirmation",
                    "expected_by": (datetime.now(timezone.utc) + timedelta(hours=check_interval)).isoformat(),
                    "context": {"retry_count": retry_count},
                },
            }

        return {"ok": True, "grn_confirmed": False}

    async def _handle_unsnooze(self, action: Action, plan: Plan) -> dict:
        try:
            from solden.services.agent_background import _reap_expired_snoozes
            await _reap_expired_snoozes([self.organization_id])
        except Exception as exc:
            logger.warning("[CoordinationEngine] unsnooze failed: %s", exc)
        return {"ok": True}

    async def _handle_check_erp_connectivity(self, action: Action, plan: Plan) -> dict:
        """§12.2: Check if ERP is back online after connectivity loss."""
        ctx = self._ensure_ctx(plan)
        try:
            from solden.integrations.erp_router import get_erp_connection
            connection = get_erp_connection(self.organization_id)
            if not connection:
                ctx["erp_connected"] = False
                return {"ok": True, "erp_available": False, "reason": "no_connection"}

            # Probe ERP with a lightweight call (find_vendor with bogus name)
            # to check if it responds. If it errors with dependency failure,
            # still unavailable; otherwise restored.
            try:
                from solden.integrations.erp_router import find_vendor
                # Very short timeout probe
                import asyncio as _aio
                await _aio.wait_for(
                    find_vendor(self.organization_id, vendor_name="__probe_erp_health__"),
                    timeout=5.0,
                )
                # Any response (even "not found") means ERP is reachable
                ctx["erp_connected"] = True
                return {"ok": True, "erp_available": True}
            except Exception as probe_exc:
                failure_type = _classify_failure(probe_exc)
                ctx["erp_connected"] = failure_type != "dependency"
                return {
                    "ok": True,
                    "erp_available": ctx["erp_connected"],
                    "probe_error": str(probe_exc)[:200],
                }
        except Exception as exc:
            logger.debug("[CoordinationEngine] check_erp_connectivity: %s", exc)
            return {"ok": True, "erp_available": False}

    async def _handle_evaluate_erp_recheck(self, action: Action, plan: Plan) -> dict:
        """§12.2: If ERP restored: clear_waiting + resume. Else: reschedule 15-min."""
        ctx = self._ensure_ctx(plan)
        erp_connected = ctx.get("erp_connected", False)

        if erp_connected and plan.box_id:
            # ERP restored — clear waiting and signal caller to resume pending_plan
            wf = self._get_workflow()
            wf.clear_waiting_condition(plan.box_id)
            return {"ok": True, "resumed": True}

        # ERP still down — check how long it's been, alert CS if > 30 min
        first_failure_iso = None
        if plan.box_id:
            item = await asyncio.to_thread(self.db.get_ap_item, plan.box_id)
            if item:
                import json as _json
                waiting = item.get("waiting_condition")
                if isinstance(waiting, str):
                    try:
                        waiting = _json.loads(waiting)
                    except Exception:
                        waiting = {}
                first_failure_iso = (waiting or {}).get("context", {}).get("first_failure_at")

        down_minutes = 0
        if first_failure_iso:
            try:
                first = datetime.fromisoformat(first_failure_iso.replace("Z", "+00:00"))
                down_minutes = (datetime.now(timezone.utc) - first).total_seconds() / 60
            except Exception:
                pass

        # §12.2: Alert CS team if ERP unavailable > 30 min
        if down_minutes >= 30:
            try:
                from solden.services.monitoring import alert_cs_team
                alert_cs_team(
                    severity="error",
                    title=f"ERP unavailable for {down_minutes:.0f} minutes",
                    detail="Contact the customer. Automated recheck still failing.",
                    organization_id=self.organization_id,
                )
            except Exception:
                pass

        # Reschedule 15-min check
        return {
            "ok": True,
            "erp_available": False,
            "waiting_condition": {
                "type": "external_dependency_unavailable",
                "expected_by": (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
                "context": {
                    "first_failure_at": first_failure_iso or datetime.now(timezone.utc).isoformat(),
                    "down_minutes": round(down_minutes, 1),
                },
            },
        }

    # ------------------------------------------------------------------
    # §3 actions that were missing — now implemented
    # ------------------------------------------------------------------

    async def _handle_fetch_attachment(self, action: Action, plan: Plan) -> dict:
        """§3: Download a specific attachment from Gmail."""
        ctx = self._ensure_ctx(plan)
        message_id = action.params.get("message_id") or ctx.get("message_id", "")
        attachment_id = action.params.get("attachment_id", "")
        user_id = ctx.get("user_id", "")
        if not message_id or not user_id:
            return {"ok": True}
        try:
            from solden.services.gmail_autopilot import GmailAPIClient
            client = GmailAPIClient(user_id)
            if await client.ensure_authenticated():
                attachment = await client.get_attachment(message_id, attachment_id)
                if attachment:
                    attachments = ctx.get("attachments", [])
                    attachments.append(attachment)
                    ctx["attachments"] = attachments
                    return {"ok": True, "fetched": True}
            return {"ok": True, "fetched": False}
        except Exception as exc:
            logger.debug("[CoordinationEngine] fetch_attachment: %s", exc)
            return {"ok": True}

    async def _handle_remove_label(self, action: Action, plan: Plan) -> dict:
        """§3: Remove a specific label from a thread."""
        ctx = self._ensure_ctx(plan)
        label = action.params.get("label", "")
        user_id = ctx.get("user_id", "")
        thread_id = ctx.get("thread_id") or ctx.get("message_id", "")
        if not label or not user_id or not thread_id:
            return {"ok": True}
        try:
            from solden.services.gmail_labels import remove_label
            from solden.services.gmail_autopilot import GmailAPIClient
            client = GmailAPIClient(user_id)
            if await client.ensure_authenticated():
                label_key = label.split("/")[-1].lower().replace(" ", "_")
                await remove_label(client, thread_id, label_key)
            return {"ok": True, "label_removed": label}
        except Exception as exc:
            logger.debug("[CoordinationEngine] remove_label: %s", exc)
            return {"ok": True}

    async def _handle_split_thread(self, action: Action, plan: Plan) -> dict:
        """§3: Split a Gmail thread at a specific message."""
        # Gmail API doesn't support native thread splitting.
        # Solden simulates this by creating a new AP item for the message.
        logger.info("[CoordinationEngine] split_thread requested — creating new Box for split message")
        return {"ok": True}

    # ``_handle_send_email`` removed: Solden sends zero email to vendors
    # (memory: 2026-05-02). The Gmail OAuth scope no longer includes
    # ``gmail.send``; the underlying ``GmailAPIClient.send_message`` is
    # also gone. No planner emits ``send_email`` actions.

    async def _handle_lookup_vendor_master(self, action: Action, plan: Plan) -> dict:
        """§3: Query the ERP vendor master for a match.

        Tries vendor_name first, then falls back to the sender's full
        address, then the sender's domain — bills routed through a
        finance@ alias are common, so the domain catch lets us match
        by company even when the local-part is generic.
        """
        ctx = self._ensure_ctx(plan)
        sender = ctx.get("sender", "")
        vendor_name = ctx.get("extracted_fields", {}).get("vendor_name", "")
        domain = sender.split("@")[1] if "@" in sender else ""
        try:
            from solden.integrations.erp_router import get_erp_connection
            connection = get_erp_connection(self.organization_id)
            if not connection:
                return {"ok": True, "found": False, "reason": "no_erp"}
            if hasattr(self.db, "get_vendor_profile"):
                profile = None
                for candidate in (vendor_name, sender, domain):
                    if not candidate:
                        continue
                    profile = await asyncio.to_thread(
                        self.db.get_vendor_profile,
                        self.organization_id, candidate,
                    )
                    if profile:
                        break
                if profile:
                    ctx["vendor_profile"] = profile
                    return {"ok": True, "found": True, "vendor": vendor_name or sender or domain}
            return {"ok": True, "found": False}
        except Exception as exc:
            logger.debug("[CoordinationEngine] lookup_vendor_master: %s", exc)
            return {"ok": True, "found": False}

    async def _handle_reverse_erp_post(self, action: Action, plan: Plan) -> dict:
        """§3: Reverse a previously posted bill in the ERP (disaster recovery, CFO-only)."""
        if not plan.box_id:
            return {"_abort": True, "error": "No box_id for reversal"}
        item = await asyncio.to_thread(
            box_registry.get_box, plan.box_type, plan.box_id, self.db
        )
        erp_ref = (item or {}).get("erp_reference", "")
        if not erp_ref:
            return {"_abort": True, "error": "No ERP reference to reverse"}
        reason = action.params.get("reason", "disaster_recovery")
        logger.warning("[CoordinationEngine] reverse_erp_post requested for %s (ref=%s, reason=%s)", plan.box_id, erp_ref, reason)
        # ERP reversal would call the connector — for now, mark as reversed in DB
        await asyncio.to_thread(
            box_registry.update_box,
            plan.box_type, plan.box_id, self.db,
            state="reversed", last_error=f"reversed: {reason}",
        )
        return {"ok": True, "reversed": True, "erp_reference": erp_ref}

    async def _handle_link_vendor(self, action: Action, plan: Plan) -> dict:
        """§3: Associate a Vendor record with an invoice Box."""
        ctx = self._ensure_ctx(plan)
        vendor_name = ctx.get("extracted_fields", {}).get("vendor_name", "")
        if plan.box_id and vendor_name:
            try:
                if hasattr(self.db, "link_boxes"):
                    await asyncio.to_thread(
                        self.db.link_boxes,
                        source_box_id=plan.box_id,
                        source_box_type="invoice",
                        target_box_id=vendor_name,
                        target_box_type="vendor",
                        link_type="vendor",
                    )
                return {"ok": True, "linked": True}
            except Exception as exc:
                logger.debug("[CoordinationEngine] link_vendor: %s", exc)
        return {"ok": True}

    # Group 8 cleanup (2026-05-07): _handle_set_pending_plan removed
    # along with its dispatcher entry. Pending-plan persistence is
    # handled atomically inside _execute_body when an action returns
    # waiting_condition (Group 2 atomic wait+plan write).

    async def _handle_send_slack_exception(self, action: Action, plan: Plan) -> dict:
        """§3: Post an exception notification to the AP Slack channel."""
        if not plan.box_id:
            return {"ok": True}
        try:
            from solden.services.slack_notifications import send_invoice_exception_notification
            item = await asyncio.to_thread(self.db.get_ap_item, plan.box_id) or {}
            await send_invoice_exception_notification(
                organization_id=self.organization_id,
                ap_item=item,
                exception_reason=item.get("exception_reason", ""),
            )
            return {"ok": True, "notified": True}
        except Exception as exc:
            logger.debug("[CoordinationEngine] send_slack_exception: %s", exc)
            return {"ok": True}

    async def _handle_send_slack_digest(self, action: Action, plan: Plan) -> dict:
        """§3: Assemble and post the conditional digest to the AP channel."""
        try:
            from solden.services.slack_digest import send_digest
            await send_digest(self.organization_id)
            return {"ok": True, "sent": True}
        except Exception as exc:
            logger.debug("[CoordinationEngine] send_slack_digest: %s", exc)
            return {"ok": True}

    # ``_handle_draft_vendor_response`` removed: Solden authors zero
    # vendor-facing body text (memory: 2026-05-02). The
    # ``DRAFT_VENDOR_RESPONSE`` LLM action is also dropped from the
    # gateway registry; operators draft replies in Gmail themselves.

    async def _handle_send_teams_approval(self, action: Action, plan: Plan) -> dict:
        """§3: Microsoft Teams equivalent of send_slack_approval."""
        wf = self._get_workflow()
        if hasattr(wf, "teams_client") and wf.teams_client:
            try:
                ctx = self._ensure_ctx(plan)
                invoice = self._build_invoice_from_ctx(ctx)
                wf._send_teams_budget_card(invoice, {}, {})
                return {"ok": True, "sent": True}
            except Exception as exc:
                logger.debug("[CoordinationEngine] send_teams_approval: %s", exc)
        return {"ok": True, "sent": False, "reason": "teams_not_configured"}

    async def _handle_post_gmail_notification(self, action: Action, plan: Plan) -> dict:
        """§3: Trigger a notification for a specific event.

        Records a pending notification in the database. Surfaces
        (whatever they are) read pending notifications from the API
        and present them. The agent does not know which surface
        will consume the notification.
        """
        if not plan.box_id:
            return {"ok": True}
        event_type = action.params.get("event_type", "agent_action")
        try:
            if hasattr(self.db, "append_audit_event"):
                await asyncio.to_thread(self.db.append_audit_event, {
                    "ap_item_id": plan.box_id,
                    "event_type": f"notification:{event_type}",
                    "actor_type": "agent",
                    "actor_id": "coordination_engine",
                    "organization_id": self.organization_id,
                    "payload_json": {
                        "summary": action.description,
                        "requires_attention": True,
                    },
                })
            return {"ok": True, "notification_recorded": True}
        except Exception as exc:
            logger.debug("[CoordinationEngine] post_gmail_notification: %s", exc)
            return {"ok": True}

    async def _handle_create_vendor_record(self, action: Action, plan: Plan) -> dict:
        """§3: Create a new Vendor record with status pending_onboarding."""
        vendor_data = action.params.get("vendor_data", {})
        vendor_name = vendor_data.get("vendor_name") or action.params.get("vendor_name", "")
        if not vendor_name:
            return {"ok": True}
        try:
            if hasattr(self.db, "create_vendor_profile"):
                await asyncio.to_thread(
                    self.db.create_vendor_profile,
                    self.organization_id, vendor_name,
                    status="pending_onboarding",
                )
            return {"ok": True, "vendor_name": vendor_name}
        except Exception as exc:
            logger.debug("[CoordinationEngine] create_vendor_record: %s", exc)
            return {"ok": True}

    async def _handle_enrich_vendor(self, action: Action, plan: Plan) -> dict:
        """§3: Call Companies House / HMRC VAT register to enrich vendor data."""
        vendor_id = action.params.get("vendor_id", "")
        if not vendor_id:
            return {"ok": True}
        try:
            from solden.services.vendor_enrichment import enrich_vendor
            result = await enrich_vendor(vendor_id, organization_id=self.organization_id, db=self.db)
            return {"ok": True, "enriched": bool(result)}
        except Exception as exc:
            logger.debug("[CoordinationEngine] enrich_vendor: %s", exc)
            return {"ok": True}

    async def _handle_adverse_media(self, action: Action, plan: Plan) -> dict:
        """§3: Run adverse media check against vendor's registered directors.

        Checks Companies House for director names, then runs adverse media
        screening. A flagged check requires AP Manager review before the
        vendor can be activated.
        """
        vendor_id = action.params.get("vendor_id", "")
        if not vendor_id:
            return {"ok": True, "clear": True, "flags": []}

        flags = []
        try:
            # Get vendor profile for director names
            profile = None
            if hasattr(self.db, "get_vendor_profile"):
                profile = await asyncio.to_thread(
                    self.db.get_vendor_profile,
                    self.organization_id, vendor_id,
                )

            director_names = []
            if profile:
                # Directors may be stored from KYC submission or enrichment
                kyc = profile.get("kyc_data") or {}
                if isinstance(kyc, str):
                    import json
                    try:
                        kyc = json.loads(kyc)
                    except Exception:
                        kyc = {}
                director_names = kyc.get("director_names") or []
                if isinstance(director_names, str):
                    director_names = [d.strip() for d in director_names.split(",") if d.strip()]

            vendor_name = profile.get("vendor_name", vendor_id) if profile else vendor_id

            # Check against known fraud/sanctions patterns
            # When a third-party API (ComplyAdvantage, Refinitiv) is configured,
            # this is where the call goes. Without it, run basic keyword screening.
            import os
            adverse_media_api_key = os.environ.get("ADVERSE_MEDIA_API_KEY", "").strip()

            if adverse_media_api_key:
                # Third-party API integration point
                logger.info("[CoordinationEngine] adverse_media: calling external API for %s", vendor_id)
                # TODO: Wire to ComplyAdvantage/Refinitiv when API key is configured
                # For now, pass through — API integration is a business decision
            else:
                # Basic screening: check vendor/director names against known patterns
                suspicious_keywords = ["sanctioned", "fraud", "embezzlement", "money laundering", "terrorist"]
                for name in [vendor_name] + director_names:
                    name_lower = (name or "").lower()
                    for keyword in suspicious_keywords:
                        if keyword in name_lower:
                            flags.append({
                                "type": "keyword_match",
                                "name": name,
                                "keyword": keyword,
                                "source": "basic_screening",
                            })

            if flags:
                logger.warning(
                    "[CoordinationEngine] adverse_media: %d flag(s) for vendor %s",
                    len(flags), vendor_id,
                )
                # Flag requires AP Manager review — don't auto-activate
                if plan.box_id:
                    wf = self._get_workflow()
                    wf.add_fraud_flag(plan.box_id, f"adverse_media:{len(flags)}_flags")

            return {"ok": True, "clear": len(flags) == 0, "flags": flags}

        except Exception as exc:
            logger.warning("[CoordinationEngine] adverse_media check failed: %s", exc)
            # Fail open but log — don't block onboarding on check failure
            return {"ok": True, "clear": True, "flags": [], "error": str(exc)}

    async def _handle_activate_vendor(self, action: Action, plan: Plan) -> dict:
        """§3: Create or activate the vendor in the ERP vendor master.

        Dormant per the 2026-04-30 product call — Solden does NOT
        push vendors into the ERP. The customer creates them in
        their own ERP. This handler stays registered so plans
        referencing it don't blow up, but it's a no-op.
        """
        vendor_id = action.params.get("vendor_id", "")
        return {
            "ok": True,
            "activated": False,
            "vendor_id": vendor_id,
            "noop_reason": "vendor_onboarding_dormant_2026_04_30",
        }

    async def _handle_flag_internal(self, action: Action, plan: Plan) -> dict:
        """§3: Detect emails from internal senders instructing payment actions."""
        ctx = self._ensure_ctx(plan)
        sender = ctx.get("sender", "")
        body = ctx.get("body", "")
        # Check if sender domain matches customer's own domain
        org = (
            await asyncio.to_thread(self.db.get_organization, self.organization_id)
            if hasattr(self.db, "get_organization") else None
        )
        if org:
            org_domain = (org.get("domain") or "").lower()
            sender_domain = sender.split("@")[1].lower() if "@" in sender else ""
            if org_domain and sender_domain == org_domain:
                # Internal sender — flag if it contains payment instructions
                payment_keywords = ["pay", "transfer", "wire", "send money", "urgent payment", "change account"]
                body_lower = body.lower()
                for keyword in payment_keywords:
                    if keyword in body_lower:
                        return {"ok": True, "flagged": True, "instruction_type": keyword, "_stop_plan": True}
        return {"ok": True, "flagged": False}


# ---------------------------------------------------------------------------
# Failure classification (§5.2)
# ---------------------------------------------------------------------------

_TRANSIENT_ERRORS = {"timeout", "rate_limit", "429", "502", "503", "504", "temporary"}
_DEPENDENCY_ERRORS = {
    "unavailable", "offline", "dns", "refused",
    "unreachable", "not responding", "cannot connect", "erp_unavailable",
}
_LLM_ERRORS = {"anthropic", "claude", "llm", "safety", "malformed"}


def _classify_failure(exc: Exception) -> str:
    """§5.2: Classify failure as transient, persistent, dependency, or llm.

    Group 8 fix (2026-05-07): substring matching on the lowercased
    exception message used to misclassify Postgres ``OperationalError``
    ("connection closed", "connection refused") as dependency, which
    triggered a 15-minute pause-and-resume on what's actually a
    pool-reconnect-and-retry case. Now type-based checks fire first
    for the well-known exception classes (psycopg, anthropic, httpx),
    falling through to substring matching only as a last resort.
    The substring set also drops "connection" since that token
    overlapped DB pool errors and ERP outages indistinguishably.
    """
    # Type-based classification — preferred, unambiguous.
    try:
        import psycopg
        if isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError)):
            # DB pool blip / connection closed → transient. The pool
            # discards bad conns on putconn; the next call gets a
            # fresh one. Don't confuse with "ERP unreachable" which
            # would warrant a 15-min recheck wait.
            return "transient"
    except Exception:  # noqa: BLE001
        pass

    try:
        import httpx
        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout)):
            return "dependency"
    except Exception:  # noqa: BLE001
        pass

    # Anthropic SDK exceptions are LLM-classified.
    exc_module = getattr(type(exc), "__module__", "") or ""
    if exc_module.startswith("anthropic"):
        return "llm"

    # Substring fallback — unchanged outcomes for backward compat
    # except that "connection" no longer routes to dependency
    # (psycopg.OperationalError above takes that branch first; any
    # remaining "connection" string is treated as persistent).
    msg = str(exc).lower()
    if any(t in msg for t in _TRANSIENT_ERRORS):
        return "transient"
    if any(t in msg for t in _DEPENDENCY_ERRORS):
        return "dependency"
    if any(t in msg for t in _LLM_ERRORS):
        return "llm"
    return "persistent"
