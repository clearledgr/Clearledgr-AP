"""Tests for Group 8: majors + minors triage close-out.

Six fixes covered:

  1. ``_classify_failure``: psycopg.OperationalError now classifies
     as ``transient`` (pool blip, reconnect-and-retry) instead of
     ``dependency`` (15-min pause). Type-based check runs before
     substring fallback.

  2. ``attempt_self_recovery``: drops ``current_state == "ready_to_post"``
     from the trigger condition. ``ready_to_post`` is a healthy
     pre-post state, not a failure — a successful skill response on
     such a box used to fire ``resume_workflow`` and race with the
     engine's queued post action.

  3. ``_run_exception_flow``: breaks the cascade on ``_abort``.
     Previously a failed ``move_box_stage`` (e.g. terminal-state
     transition) would still fire ``send_slack_exception``,
     producing a Slack card for a state move that never happened.

  4. ``_handle_post_bill``: belt-and-suspenders ``erp_reference``
     write to the AP item after a successful ERP post. Closes a
     hypothetical regression where ``_post_to_erp`` returns success
     without persisting the column.

  5. Dead handler cleanup: ``apply_label_matched`` and
     ``set_pending_plan`` removed from the handlers registry. No
     planner emits them; pending_plan persistence now lives inside
     ``_execute_body``.

  6. Planner ``_get_db``: no longer caches the resolved singleton
     so a DB pool reset doesn't leave the engine on a dead handle.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from solden.core import database as db_module  # noqa: E402
from solden.core.coordination_engine import (  # noqa: E402
    CoordinationEngine,
    _classify_failure,
)
from solden.core.plan import Action, Plan  # noqa: E402
from solden.core.planning_engine import (  # noqa: E402
    DeterministicPlanningEngine,
    get_planning_engine,
)


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgTri", organization_name="Triage Test")
    return inst


def _make_engine(db) -> CoordinationEngine:
    return CoordinationEngine(db=db, organization_id="orgTri")


def _seed_box(db, *, item_id: str, state: str = "received", erp_reference: str = "") -> dict:
    item = db.create_ap_item({
        "id": item_id,
        "organization_id": "orgTri",
        "vendor_name": "Vendor",
        "amount": 100.0,
        "currency": "USD",
        "invoice_number": f"INV-{item_id}",
        "state": "received",
    })
    if state != "received":
        for s in ("validated", "needs_approval", "approved", "ready_to_post"):
            db.update_ap_item(item["id"], state=s)
            if s == state:
                break
    if erp_reference:
        db.update_ap_item(item["id"], erp_reference=erp_reference)
    return db.get_ap_item(item["id"])


# ─── Fix #1: _classify_failure ─────────────────────────────────────


class TestClassifyFailureTypeBased:
    def test_psycopg_operational_error_is_transient(self):
        """The audit's headline scenario: a pool reconnect blip
        used to be misclassified as ``dependency`` and trigger a
        15-min pause. Now type-based check returns ``transient``."""
        import psycopg
        exc = psycopg.OperationalError("connection closed unexpectedly")
        assert _classify_failure(exc) == "transient"

    def test_psycopg_interface_error_is_transient(self):
        import psycopg
        exc = psycopg.InterfaceError("the connection is closed")
        assert _classify_failure(exc) == "transient"

    def test_httpx_connect_error_is_dependency(self):
        import httpx
        exc = httpx.ConnectError("ERP host refused connection")
        assert _classify_failure(exc) == "dependency"

    def test_httpx_read_timeout_is_dependency(self):
        import httpx
        exc = httpx.ReadTimeout("ERP read timed out")
        assert _classify_failure(exc) == "dependency"

    def test_anthropic_module_error_is_llm(self):
        """Anything from the anthropic SDK module classifies as llm."""
        class FakeAnthropicError(Exception):
            pass
        FakeAnthropicError.__module__ = "anthropic._exceptions"
        exc = FakeAnthropicError("rate limit")
        assert _classify_failure(exc) == "llm"

    def test_substring_fallback_unchanged_for_transient(self):
        assert _classify_failure(Exception("request timeout")) == "transient"
        assert _classify_failure(Exception("503 Service Unavailable")) == "transient"

    def test_substring_fallback_dependency_drops_connection_keyword(self):
        """The bare ``connection`` substring used to route to
        dependency, sweeping psycopg connection errors into a
        15-min pause. Now ``connection`` alone falls through to
        persistent — but a real httpx ConnectError still routes
        correctly via the type check above."""
        # A bare RuntimeError mentioning "connection" does NOT
        # auto-classify as dependency.
        assert _classify_failure(RuntimeError("connection lost mid-call")) == "persistent"
        # Other dependency tokens still work.
        assert _classify_failure(Exception("ERP unreachable")) == "dependency"

    def test_unknown_exception_is_persistent(self):
        assert _classify_failure(ValueError("bad input")) == "persistent"


# ─── Fix #2: attempt_self_recovery trigger ─────────────────────────


class TestAttemptSelfRecoveryTrigger:
    """Run the recovery only on actual failure signals, not on a
    healthy box that happens to be in ``ready_to_post``."""

    def _make_runtime(self, db):
        # Minimal stub — attempt_self_recovery only reads
        # runtime.organization_id (lazily, inside the workflow
        # branch which we never reach in these tests).
        return MagicMock(organization_id="orgTri", db=db)

    def test_success_response_on_ready_to_post_does_not_recover(self, db):
        """Ready-to-post is a normal pre-post state. A successful
        skill response on such a box must NOT trigger
        ``resume_workflow`` — that would race with the engine's
        own queued post action."""
        from solden.services.finance_agent_governance import attempt_self_recovery

        runtime = self._make_runtime(db)
        item = {
            "id": "AP-tri-1",
            "state": "ready_to_post",
            "last_error": None,
        }
        response = {"status": "ok", "ap_item_id": "AP-tri-1"}

        with patch(
            "solden.services.invoice_workflow.get_invoice_workflow",
        ) as mock_workflow:
            result = asyncio.run(
                attempt_self_recovery(
                    runtime, request=MagicMock(payload={}), response=response, ap_item=item,
                )
            )

        # The workflow factory should NOT be touched on a healthy box.
        mock_workflow.assert_not_called()
        assert result.get("attempted") is False or "strategy" not in result

    def test_failed_post_state_still_triggers_recovery(self, db):
        """The genuine failure case: box already in failed_post →
        run resume_workflow as before."""
        from solden.services.finance_agent_governance import attempt_self_recovery

        runtime = self._make_runtime(db)
        item = {"id": "AP-tri-2", "state": "failed_post"}
        response = {"status": "ok"}

        async def fake_resume(_id):
            return {"status": "posted_to_erp"}

        fake_workflow = MagicMock()
        fake_workflow.resume_workflow = fake_resume

        with patch(
            "solden.services.invoice_workflow.get_invoice_workflow",
            return_value=fake_workflow,
        ):
            result = asyncio.run(
                attempt_self_recovery(
                    runtime, request=MagicMock(payload={}), response=response, ap_item=item,
                )
            )

        assert result["attempted"] is True
        assert result["strategy"] == "resume_workflow"

    def test_transient_failure_triggers_recovery(self, db):
        """Response status mentions transient-failure tokens →
        recovery fires regardless of current_state."""
        from solden.services.finance_agent_governance import attempt_self_recovery

        runtime = self._make_runtime(db)
        item = {"id": "AP-tri-3", "state": "approved"}  # not failed_post
        response = {
            "status": "error", "reason": "temporary network failure",
        }

        async def fake_resume(_id):
            return {"status": "posted_to_erp"}

        fake_workflow = MagicMock()
        fake_workflow.resume_workflow = fake_resume

        with patch(
            "solden.services.invoice_workflow.get_invoice_workflow",
            return_value=fake_workflow,
        ):
            result = asyncio.run(
                attempt_self_recovery(
                    runtime, request=MagicMock(payload={}), response=response, ap_item=item,
                )
            )

        assert result["attempted"] is True


# ─── Fix #3: _run_exception_flow break on abort ────────────────────


class TestExceptionFlowBreaksOnAbort:
    def test_abort_at_move_stage_skips_send_slack_exception(self, db):
        """When ``move_box_stage(needs_info)`` aborts (e.g. illegal
        transition from a terminal state), the cascade must NOT
        proceed to ``send_slack_exception`` — that would surface a
        misleading "this box needs info" Slack card for a state
        move that never happened."""
        engine = _make_engine(db)
        item = _seed_box(db, item_id="AP-tri-flow-1")

        # Stub each step. move_box_stage aborts; the rest record
        # whether they ran.
        ran = {"label": False, "stage": False, "slack": False, "exception": False}

        async def fake_generate_exception(action, plan):
            ran["exception"] = True
            return {"ok": True}

        async def fake_apply_label(action, plan):
            ran["label"] = True
            return {"ok": True}

        async def fake_stage(action, plan):
            ran["stage"] = True
            return {"_abort": True, "error": "illegal_transition"}

        async def fake_slack(action, plan):
            ran["slack"] = True
            return {"ok": True}

        engine._handlers["generate_exception_reason"] = fake_generate_exception
        engine._handlers["apply_label"] = fake_apply_label
        engine._handlers["move_box_stage"] = fake_stage
        engine._handlers["send_slack_exception"] = fake_slack

        plan = Plan(
            event_type="email_received",
            actions=[],
            box_id=item["id"],
            organization_id="orgTri",
            correlation_id="evt-tri-flow-1",
        )
        asyncio.run(engine._run_exception_flow(plan, ctx={}, match_result={}))

        # Steps before the abort ran; the abort step ran; the
        # send_slack_exception step did NOT run.
        assert ran["exception"] is True
        assert ran["label"] is True
        assert ran["stage"] is True
        assert ran["slack"] is False, (
            "send_slack_exception must not fire after move_box_stage aborts"
        )


# ─── Fix #4: _handle_post_bill erp_reference backstop ──────────────


class TestPostBillErpReferenceBackstop:
    def test_post_bill_writes_erp_reference_to_ap_item(self, db):
        """After a successful ERP post, the engine's handler must
        ensure ``erp_reference`` lands on the AP item — even if
        the workflow's own state-transition path didn't persist it.
        Backstop is idempotent so it's safe when both paths write."""
        engine = _make_engine(db)
        item = _seed_box(db, item_id="AP-tri-bill-1", state="ready_to_post")

        async def fake_post_to_erp(self_arg, invoice):
            return {"status": "posted", "reference_id": "EXT-BACKSTOP-1"}

        with patch(
            "solden.services.invoice_workflow.InvoiceWorkflowService._post_to_erp",
            new=fake_post_to_erp,
        ):
            plan = Plan(
                event_type="approval_received",
                actions=[Action("post_bill", "DET", {}, "test")],
                box_id=item["id"],
                organization_id="orgTri",
                correlation_id="evt-tri-bill-1",
            )
            result = asyncio.run(
                engine._handle_post_bill(plan.actions[0], plan)
            )

        assert result["ok"] is True
        assert result["erp_reference"] == "EXT-BACKSTOP-1"

        # The AP item should now have erp_reference set.
        refreshed = db.get_ap_item(item["id"])
        assert refreshed.get("erp_reference") == "EXT-BACKSTOP-1"


# ─── Fix #5: dead handler cleanup ──────────────────────────────────


class TestDeadHandlersRemoved:
    def test_apply_label_matched_alias_removed(self, db):
        engine = _make_engine(db)
        assert "apply_label_matched" not in engine._handlers, (
            "Dead alias 'apply_label_matched' should be removed; "
            "no planner emits it."
        )

    def test_set_pending_plan_removed(self, db):
        engine = _make_engine(db)
        assert "set_pending_plan" not in engine._handlers, (
            "Dead handler 'set_pending_plan' should be removed; "
            "pending_plan is persisted automatically by _execute_body."
        )

    def test_apply_label_canonical_handler_still_registered(self, db):
        """Sanity: removing the alias didn't break the real handler."""
        engine = _make_engine(db)
        assert "apply_label" in engine._handlers


# ─── Fix #6: planner _get_db re-resolves ───────────────────────────


class TestPlannerDbReresolves:
    def test_get_db_does_not_cache_resolved_singleton(self):
        """After a pool reset, ``_get_db`` must pick up the new
        instance instead of holding the dead one."""
        engine = DeterministicPlanningEngine(db=None)

        # First call resolves via get_db.
        first_db = engine._get_db()
        assert first_db is not None

        # Now simulate a reset: get_db returns a different object.
        sentinel = MagicMock(name="fresh-db-after-reset")
        with patch("solden.core.database.get_db", return_value=sentinel):
            second_db = engine._get_db()
        assert second_db is sentinel, (
            "_get_db cached the first db; pool reset would leave it on a dead handle"
        )

    def test_explicit_db_arg_still_takes_priority(self):
        """When a caller provides an explicit db at construction,
        ``_get_db`` honors it (doesn't re-resolve via get_db)."""
        custom = MagicMock(name="custom-db")
        engine = DeterministicPlanningEngine(db=custom)
        assert engine._get_db() is custom
