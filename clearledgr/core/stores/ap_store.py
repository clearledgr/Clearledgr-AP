"""AP-domain data-access mixin for ClearledgrDB.

``APStore`` is a **mixin class** -- it has no ``__init__`` of its own and
expects the concrete class that inherits it to provide:

* ``self.connect()``      -- returns a DB connection (context manager)
* ``self.initialize()``   -- ensures tables exist
* ``self._decode_json()`` -- safely parses a JSON string or returns ``{}``
* ``self._parse_iso()``   -- parses an ISO-8601 string into a datetime
* ``self._exception_severity_rank()`` -- maps severity label to int rank

All methods are copied verbatim from ``clearledgr/core/database.py`` so that
``ClearledgrDB(APStore, ...)`` inherits them without any behavioural change.

Gmail ID mapping
~~~~~~~~~~~~~~~~
- **gmail_id** (Python/service layer) maps to the ``thread_id`` column in
  ``ap_items``.  It is the Gmail *thread* identifier (e.g. ``18e3f...``).
- **message_id** is the individual message within that thread.
  A single thread_id / gmail_id may contain multiple message_ids.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from clearledgr.core.utils import safe_float


def _is_unique_violation(exc: Exception) -> bool:
    """Detect a UNIQUE-constraint violation across sqlite3 + psycopg.

    We can't `except sqlite3.IntegrityError` directly in store code
    because the same module also runs against Postgres in prod — those
    raise `psycopg.errors.UniqueViolation` instead. Sniff by class name
    + message so we don't need to import either backend here.
    """
    name = type(exc).__name__
    if name in {"IntegrityError", "UniqueViolation"}:
        return True
    msg = str(exc).lower()
    return "unique" in msg and ("constraint" in msg or "violat" in msg)

logger = logging.getLogger(__name__)


class APStore:
    """Mixin providing all AP-domain persistence methods."""

    # Whitelist of columns that may be updated on ap_items.
    # Any column not in this set is rejected to prevent SQL injection
    # via dynamic column names.
    _AP_ITEM_ALLOWED_COLUMNS = frozenset({
        "state", "vendor_name", "amount", "currency", "invoice_number",
        "invoice_key", "invoice_date", "due_date", "subject", "sender",
        "confidence", "approval_required",
        "approved_by", "approved_at", "rejected_by", "rejected_at",
        "rejection_reason", "supersedes_ap_item_id", "supersedes_invoice_key", "superseded_by_ap_item_id",
        "resubmission_reason", "erp_reference", "erp_posted_at",
        "last_error", "metadata", "updated_at",
        "workflow_id", "run_id", "approval_surface",
        "approval_policy_version", "post_attempted_at",
        "organization_id", "user_id",
        "thread_id", "message_id", "po_number", "attachment_url",
        # Slack/Teams refs stored on the item
        "slack_channel_id", "slack_thread_id", "slack_message_ts",
        # Gap #10: exception fields as first-class indexed columns
        "exception_code", "exception_severity",
        # Extraction accuracy: per-field confidence JSON for trend analysis
        "field_confidences",
        # Multi-entity routing: which legal entity this AP item belongs to
        "entity_id",
        # Document classification type (invoice, subscription_notification, credit_note, etc.)
        "document_type",
        # Phase 2.1.a: Fernet-encrypted bank details — never plaintext.
        # Use set_ap_item_bank_details() to encrypt-and-write atomically;
        # the column itself is whitelisted here for direct update_ap_item
        # callers, but they must pass the already-encrypted ciphertext.
        "bank_details_encrypted",
        # §5.5 Agent Columns: first-class fields populated by the agent
        "grn_reference",
        "match_status",
        "exception_reason",
        # §6 Agent Design Spec: Box state fields for plan persistence and waiting
        "pending_plan",
        "waiting_condition",
        "fraud_flags",
        "payment_reference",
        # Wave 1 / A1 — link to the SOX-archived original-PDF row.
        # Foreign reference to invoice_originals.content_hash; the
        # archive table is append-only at the trigger level so the
        # link is never invalidated by a mutation.
        "attachment_content_hash",
        # Wave 1 / A2 — auditor-traceable GL journal entry id.
        # Distinct from ``erp_reference`` (bill id) in ERPs where
        # bill and JE are separate records (SAP B1, Xero). For
        # QBO/NetSuite the bill IS the journal record, so the two
        # values coincide — but we still store under this column
        # so callers query one field uniformly.
        "erp_journal_entry_id",
        # Wave 3 / E2 — VAT split + treatment. amount stays as the
        # gross/total (what the operator and matcher rely on);
        # net_amount + vat_amount carry the split for JE building
        # and VAT-return rollup.
        "net_amount",
        "vat_amount",
        "vat_rate",
        "vat_code",
        "tax_treatment",
        "bill_country",
    })

    # ------------------------------------------------------------------
    # AP items
    # ------------------------------------------------------------------

    def create_ap_item(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.initialize()
        import uuid
        now = datetime.now(timezone.utc).isoformat()
        item_id = payload.get("id") or f"AP-{uuid.uuid4().hex}"

        # Validate amount: flag invalid amounts rather than persisting silently
        raw_amount = payload.get("amount")
        if raw_amount is not None:
            try:
                amount_val = float(raw_amount)
            except (TypeError, ValueError):
                amount_val = 0.0
            if amount_val <= 0:
                logger.warning(
                    "AP item %s has non-positive amount %s; flagging as invalid_amount",
                    item_id,
                    raw_amount,
                )
                if not payload.get("exception_code"):
                    payload["exception_code"] = "invalid_amount"

        # ---- Phase 2.1.a: bank details tokenisation (DESIGN_THESIS.md §19) ----
        # Bank details are NEVER stored in the metadata JSON blob. They go to
        # bank_details_encrypted as Fernet ciphertext. We accept the value
        # at three levels for caller ergonomics, in priority order:
        #   1. payload["bank_details"]                — typed top-level field
        #   2. payload["metadata"]["bank_details"]    — defensive strip from
        #                                              any caller still using
        #                                              the legacy shape
        # Then we encrypt it to bank_details_encrypted and remove every
        # plaintext copy from the metadata before persisting.
        from clearledgr.core.stores.bank_details import (
            encrypt_bank_details,
            normalize_bank_details,
        )

        raw_metadata = payload.get("metadata") or {}
        if isinstance(raw_metadata, str):
            try:
                raw_metadata = json.loads(raw_metadata) if raw_metadata else {}
            except json.JSONDecodeError:
                raw_metadata = {}
        if not isinstance(raw_metadata, dict):
            raw_metadata = {}

        bank_details_input = (
            payload.get("bank_details")
            or raw_metadata.pop("bank_details", None)
        )
        bank_details_normalized = normalize_bank_details(bank_details_input)
        bank_details_ciphertext: Optional[str] = None
        if bank_details_normalized:
            try:
                bank_details_ciphertext = encrypt_bank_details(
                    bank_details_normalized, encrypt_fn=self._encrypt_secret
                )
            except Exception as enc_exc:
                logger.error(
                    "AP item %s bank details encryption failed: %s",
                    item_id, enc_exc,
                )
                bank_details_ciphertext = None

        metadata = json.dumps(raw_metadata)
        # Serialize field_confidences to JSON if provided as a dict
        raw_fc = payload.get("field_confidences")
        field_confidences_json: Optional[str] = None
        if isinstance(raw_fc, dict):
            field_confidences_json = json.dumps(raw_fc)
        elif isinstance(raw_fc, str):
            field_confidences_json = raw_fc

        sql = """
            INSERT INTO ap_items
            (id, invoice_key, thread_id, message_id, subject, sender, vendor_name, amount, currency,
            invoice_number, invoice_date, due_date, state, confidence, approval_required,
             approved_by, approved_at, rejected_by, rejected_at, rejection_reason,
             supersedes_ap_item_id, supersedes_invoice_key, superseded_by_ap_item_id, resubmission_reason, erp_reference,
             erp_posted_at, workflow_id, run_id, approval_surface, approval_policy_version, post_attempted_at,
             last_error, po_number, attachment_url, attachment_content_hash, exception_code, exception_severity,
             organization_id, user_id, entity_id, created_at, updated_at, metadata, field_confidences, document_type,
             bank_details_encrypted)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        values = (
            item_id,
            payload.get("invoice_key"),
            payload.get("thread_id"),
            payload.get("message_id"),
            payload.get("subject"),
            payload.get("sender"),
            payload.get("vendor_name"),
            payload.get("amount"),
            payload.get("currency") or "USD",
            payload.get("invoice_number"),
            payload.get("invoice_date"),
            payload.get("due_date"),
            payload.get("state"),
            payload.get("confidence") or 0,
            1 if payload.get("approval_required", True) else 0,
            payload.get("approved_by"),
            payload.get("approved_at"),
            payload.get("rejected_by"),
            payload.get("rejected_at"),
            payload.get("rejection_reason"),
            payload.get("supersedes_ap_item_id"),
            payload.get("supersedes_invoice_key"),
            payload.get("superseded_by_ap_item_id"),
            payload.get("resubmission_reason"),
            payload.get("erp_reference"),
            payload.get("erp_posted_at"),
            payload.get("workflow_id"),
            payload.get("run_id"),
            payload.get("approval_surface") or "hybrid",
            payload.get("approval_policy_version"),
            payload.get("post_attempted_at"),
            payload.get("last_error"),
            payload.get("po_number"),
            payload.get("attachment_url"),
            payload.get("attachment_content_hash"),
            payload.get("exception_code"),
            payload.get("exception_severity"),
            payload.get("organization_id"),
            payload.get("user_id"),
            payload.get("entity_id"),
            now,
            now,
            metadata,
            field_confidences_json,
            payload.get("document_type") or "invoice",
            bank_details_ciphertext,
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, values)
            conn.commit()
        return self.get_ap_item(item_id)

    def update_ap_item(self, ap_item_id: str, **kwargs) -> bool:
        """Update an AP item with column-whitelist enforcement and state machine validation.

        If ``state`` is included in *kwargs*, the transition is validated
        against the canonical AP state machine (PLAN.md 2.1) and an
        audit event is written atomically within the same transaction.

        §11.2.5 Optimistic locking: pass ``_expected_updated_at`` to enable
        concurrent-write detection. If the row's ``updated_at`` has changed
        since the caller last read it, the update returns False (0 rows
        affected) and the caller must re-read and re-evaluate.

        Transaction semantics: the row UPDATE and the audit-event INSERT
        share a single ``conn.commit()`` call, so they are atomic in both
        SQLite and Postgres.  If either statement fails, neither is committed.

        Callers may pass ``_actor_type`` and ``_actor_id`` as kwargs to
        record who triggered the transition.  These keys are consumed
        (not stored on the row).
        """
        self.initialize()
        if not kwargs:
            return False

        # Extract audit metadata before column validation
        actor_type = kwargs.pop("_actor_type", "system")
        actor_id = kwargs.pop("_actor_id", "system")
        audit_source = kwargs.pop("_source", None)
        correlation_id = kwargs.pop("_correlation_id", None)
        workflow_id = kwargs.pop("_workflow_id", None)
        run_id = kwargs.pop("_run_id", None)
        decision_reason = kwargs.pop("_decision_reason", None)
        # §11.2.5: Optimistic locking — if provided, WHERE includes updated_at check
        expected_updated_at = kwargs.pop("_expected_updated_at", None)

        # Validate column names against whitelist
        invalid_cols = set(kwargs.keys()) - self._AP_ITEM_ALLOWED_COLUMNS
        if invalid_cols:
            raise ValueError(f"Disallowed columns in update_ap_item: {invalid_cols}")

        now = datetime.now(timezone.utc).isoformat()
        kwargs["updated_at"] = now
        # JSON-serialize dict/list values for TEXT columns
        for json_col in ("metadata", "field_confidences", "pending_plan", "waiting_condition", "fraud_flags"):
            if json_col in kwargs and isinstance(kwargs[json_col], (dict, list)):
                kwargs[json_col] = json.dumps(kwargs[json_col])  # type: ignore

        # --- State machine enforcement ---
        prev_state: Optional[str] = None
        new_state: Optional[str] = kwargs.get("state")
        current: Optional[Dict[str, Any]] = None
        # §8 Box lifecycle mirror: capture prev exception_code so the
        # post-commit hook can emit raise/resolve on box_exceptions.
        prev_exception_code: Optional[str] = None
        _will_update_exception = "exception_code" in kwargs
        if _will_update_exception or new_state is not None:
            current = self.get_ap_item(ap_item_id)
            if current:
                prev_exception_code = current.get("exception_code")
        if new_state is not None:
            from clearledgr.core.ap_states import transition_or_raise, normalize_state

            if current:
                prev_state = current.get("state")
                if prev_state:
                    try:
                        transition_or_raise(prev_state, new_state, ap_item_id)
                    except Exception as exc:
                        self._record_rejected_transition_attempt(
                            ap_item_id=ap_item_id,
                            prev_state=str(prev_state),
                            attempted_state=str(new_state),
                            actor_type=str(actor_type or "system"),
                            actor_id=str(actor_id or "system"),
                            organization_id=str((current or {}).get("organization_id") or kwargs.get("organization_id") or ""),
                            source=str(audit_source or "ap_store"),
                            correlation_id=(str(correlation_id) if correlation_id else None),
                            workflow_id=(str(workflow_id) if workflow_id else None),
                            run_id=(str(run_id) if run_id else None),
                            decision_reason=(str(decision_reason) if decision_reason else None),
                            error=str(exc),
                        )
                        logger.warning(
                            "Rejected illegal AP state transition for %s: %s -> %s (%s)",
                            ap_item_id,
                            prev_state,
                            new_state,
                            exc,
                        )
                        raise
            # Normalize to canonical state name
            kwargs["state"] = normalize_state(new_state)

        set_clause = ", ".join(f"{k} = %s" for k in kwargs.keys())
        if expected_updated_at:
            # §11.2.5: Optimistic locking — only update if updated_at hasn't changed
            sql = f"UPDATE ap_items SET {set_clause} WHERE id = %s AND updated_at = %s"
            params = (*kwargs.values(), ap_item_id, expected_updated_at)
        else:
            sql = f"UPDATE ap_items SET {set_clause} WHERE id = %s"
            params = (*kwargs.values(), ap_item_id)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)

            # --- Atomic audit write on state transitions ---
            if prev_state is not None and new_state is not None:
                import uuid as _uuid

                org_id = kwargs.get("organization_id") or (
                    current.get("organization_id") if current else None
                ) or ""
                audit_sql = (
                    """INSERT INTO audit_events
                    (id, box_id, box_type, event_type, prev_state, new_state,
                     actor_type, actor_id, payload_json, source, correlation_id,
                     workflow_id, run_id, decision_reason, organization_id, ts)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
                )
                audit_payload = {
                    k: v
                    for k, v in kwargs.items()
                    if k not in ("state", "updated_at", "metadata")
                }
                cur.execute(
                    audit_sql,
                    (
                        str(_uuid.uuid4()),
                        ap_item_id,   # box_id
                        "ap_item",    # box_type
                        "state_transition",
                        prev_state,
                        kwargs["state"],
                        actor_type,
                        actor_id,
                        json.dumps(audit_payload),
                        audit_source,
                        correlation_id,
                        workflow_id,
                        run_id,
                        decision_reason,
                        org_id,
                        now,
                    ),
                )

            conn.commit()
            updated = cur.rowcount > 0

        # --- Post-commit: emit webhook for state transitions (non-blocking) ---
        if updated and prev_state is not None and new_state is not None:
            try:
                import asyncio
                from clearledgr.services.webhook_delivery import emit_state_change_webhook

                org_id = kwargs.get("organization_id") or (
                    current.get("organization_id") if current else None
                ) or ""
                # Fire-and-forget: schedule on the running loop if available
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(
                        emit_state_change_webhook(
                            organization_id=org_id,
                            ap_item_id=ap_item_id,
                            new_state=kwargs["state"],
                            prev_state=str(prev_state),
                            item_data=current,
                        )
                    )
                except RuntimeError:
                    # No event loop — enqueue for the background loop to pick up
                    try:
                        self.enqueue_notification(
                            org_id,
                            "webhook",
                            {
                                "event_type": "ap_item.state_changed",
                                "box_id": ap_item_id,
                                "box_type": "ap_item",
                                "new_state": kwargs["state"],
                                "prev_state": str(prev_state),
                            },
                        )
                    except Exception as enq_exc:
                        logger.debug("Webhook enqueue fallback failed: %s", enq_exc)
            except Exception as wh_exc:
                logger.warning("[APStore] Webhook emission failed for %s (will retry via queue): %s", ap_item_id, wh_exc)

        # --- §8 Box lifecycle mirror ---
        # Keep box_exceptions + box_outcomes in sync with ap_items control-
        # plane columns. The legacy columns drive the state machine; these
        # tables are the first-class audit/attribution record. Without this
        # mirror, surfaces that read from box_exceptions/box_outcomes see
        # empty tables while the workflow happily updates ap_items.
        if updated:
            org_id = (
                kwargs.get("organization_id")
                or (current.get("organization_id") if current else None)
                or ""
            )
            # Exception mirror.
            if _will_update_exception:
                new_exception_code = kwargs.get("exception_code")
                try:
                    if new_exception_code and new_exception_code != prev_exception_code:
                        if hasattr(self, "raise_box_exception"):
                            self.raise_box_exception(
                                box_id=ap_item_id,
                                box_type="ap_item",
                                organization_id=org_id,
                                exception_type=str(new_exception_code),
                                severity=str(kwargs.get("exception_severity") or "medium"),
                                reason=str(kwargs.get("exception_reason") or new_exception_code),
                                metadata={"source": "update_ap_item"},
                                raised_by=str(actor_id or "system"),
                                raised_actor_type=str(actor_type or "system"),
                                idempotency_key=f"ap_excp:{ap_item_id}:{new_exception_code}:{now}",
                            )
                    elif new_exception_code in (None, "") and prev_exception_code:
                        if hasattr(self, "list_box_exceptions") and hasattr(self, "resolve_box_exception"):
                            unresolved = self.list_box_exceptions(
                                box_type="ap_item",
                                box_id=ap_item_id,
                                only_unresolved=True,
                            )
                            for exc_row in unresolved:
                                self.resolve_box_exception(
                                    exception_id=exc_row["id"],
                                    resolved_by=str(actor_id or "system"),
                                    resolved_actor_type=str(actor_type or "system"),
                                    resolution_note="Cleared via ap_items.exception_code",
                                )
                except Exception as mirror_exc:
                    logger.warning(
                        "[APStore] box_exception mirror failed for %s: %s",
                        ap_item_id, mirror_exc,
                    )

            # Outcome mirror.
            _OUTCOME_STATES = {"posted_to_erp", "rejected", "reversed"}
            if new_state in _OUTCOME_STATES and hasattr(self, "record_box_outcome"):
                try:
                    # Re-read to get the now-committed erp_reference etc.
                    post = self.get_ap_item(ap_item_id) or {}
                    self.record_box_outcome(
                        box_id=ap_item_id,
                        box_type="ap_item",
                        organization_id=org_id,
                        outcome_type=str(new_state),
                        recorded_by=str(actor_id or "system"),
                        recorded_actor_type=str(actor_type or "system"),
                        data={
                            "erp_reference": post.get("erp_reference"),
                            "invoice_number": post.get("invoice_number"),
                            "amount": post.get("amount"),
                            "currency": post.get("currency"),
                            "vendor_name": post.get("vendor_name"),
                        },
                    )
                except Exception as mirror_exc:
                    logger.warning(
                        "[APStore] box_outcome mirror failed for %s: %s",
                        ap_item_id, mirror_exc,
                    )

        return updated

    def update_ap_item_metadata_merge(self, ap_item_id: str, patch: Dict[str, Any]) -> bool:
        """Merge *patch* into an AP item's existing metadata JSON column.

        Reads the current metadata, applies a shallow merge (patch keys
        overwrite matching top-level keys; nested dicts are also merged one
        level deep), then writes back atomically.  Never clobbers unrelated
        metadata keys.

        Returns True if the row was updated, False if the item was not found.
        """
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        # FOR UPDATE locks the row for the duration of the txn so
        # concurrent patch_ap_item_metadata() calls serialize on the
        # row read instead of racing on the read-modify-write window.
        sql_select = (
            "SELECT metadata FROM ap_items WHERE id = %s FOR UPDATE"
        )
        sql_update = (
            "UPDATE ap_items SET metadata = %s, updated_at = %s WHERE id = %s"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql_select, (ap_item_id,))
            row = cur.fetchone()
            if not row:
                conn.rollback()
                return False
            try:
                existing: Dict[str, Any] = json.loads(row[0] or "{}")
            except Exception:
                existing = {}
            # Shallow merge: for dict values merge one level deep
            for k, v in patch.items():
                if isinstance(v, dict) and isinstance(existing.get(k), dict):
                    existing[k] = {**existing[k], **v}
                else:
                    existing[k] = v
            cur.execute(sql_update, (json.dumps(existing), now, ap_item_id))
            conn.commit()
            return cur.rowcount > 0

    def _record_rejected_transition_attempt(
        self,
        *,
        ap_item_id: str,
        prev_state: str,
        attempted_state: str,
        actor_type: str,
        actor_id: str,
        organization_id: str,
        source: str,
        correlation_id: Optional[str],
        workflow_id: Optional[str],
        run_id: Optional[str],
        decision_reason: Optional[str],
        error: str,
    ) -> None:
        """Best-effort audit/log evidence for rejected state transitions.

        This is intentionally best-effort and must not mask the original exception.
        """
        try:
            self.append_audit_event(
                {
                    "ap_item_id": ap_item_id,
                    "event_type": "state_transition_rejected",
                    "from_state": prev_state,
                    "to_state": attempted_state,
                    "actor_type": actor_type,
                    "actor_id": actor_id,
                    "reason": "illegal_transition",
                    "decision_reason": decision_reason,
                    "source": source,
                    "correlation_id": correlation_id,
                    "workflow_id": workflow_id,
                    "run_id": run_id,
                    "metadata": {
                        "error": error,
                    },
                    "organization_id": organization_id,
                    "idempotency_key": (
                        f"state_transition_rejected:{ap_item_id}:{prev_state}:{attempted_state}:"
                        f"{actor_type}:{actor_id}:{correlation_id or ''}:{workflow_id or ''}:{run_id or ''}"
                    ),
                }
            )
        except Exception as exc:  # pragma: no cover - best effort
            logger.error("Could not audit rejected AP state transition for %s: %s", ap_item_id, exc)

    def get_ap_item(self, ap_item_id: str) -> Optional[Dict[str, Any]]:
        """Fetch an AP item row by id.

        Returns the raw row dict including the ``bank_details_encrypted``
        ciphertext column. Callers that need the plaintext bank-details
        dict must use ``get_ap_item_bank_details`` — the regular
        ``get_ap_item`` deliberately does NOT decrypt, so a stray
        ``logger.info(ap_item)`` cannot leak the plaintext.
        """
        self.initialize()
        sql = "SELECT * FROM ap_items WHERE id = %s"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (ap_item_id,))
            row = cur.fetchone()
        return dict(row) if row else None

    # ---- Phase 2.1.a: Bank-details typed accessors ----
    #
    # The four methods below are the ONLY supported read/write paths for
    # bank account details on an AP item. Direct manipulation of the
    # bank_details_encrypted column via update_ap_item is allowed (the
    # column is in the whitelist), but callers must encrypt the value
    # themselves; the helpers below do the encryption + masking +
    # tombstone-stripping correctly.

    def get_ap_item_bank_details(
        self, ap_item_id: str
    ) -> Optional[Dict[str, str]]:
        """Decrypt and return the bank-details dict for an AP item.

        Returns the canonical normalized shape (subset of
        ``BANK_DETAIL_FIELDS``) or ``None`` when no bank details are
        stored. Caller is responsible for masking before returning to
        any user-facing surface — see ``get_ap_item_bank_details_masked``
        for the API-safe variant.
        """
        from clearledgr.core.stores.bank_details import decrypt_bank_details

        self.initialize()
        sql = (
            "SELECT bank_details_encrypted FROM ap_items WHERE id = %s"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (ap_item_id,))
            row = cur.fetchone()
        if not row:
            return None
        ciphertext = (
            row["bank_details_encrypted"]
            if hasattr(row, "keys")
            else (row[0] if row else None)
        )
        return decrypt_bank_details(ciphertext, decrypt_fn=self._decrypt_secret)

    def get_ap_item_bank_details_masked(
        self, ap_item_id: str
    ) -> Optional[Dict[str, str]]:
        """Return the masked-for-display bank-details dict for an AP item.

        This is the API-safe accessor — its output is what every
        outbound API surface SHOULD return when surfacing bank details.
        ``mask_bank_details`` produces shapes like
        ``{"iban": "GB82 **** **** **** 5432", "sort_code": "**-**-00", ...}``.
        Returns ``None`` when no bank details are stored.
        """
        from clearledgr.core.stores.bank_details import mask_bank_details

        plaintext = self.get_ap_item_bank_details(ap_item_id)
        return mask_bank_details(plaintext)

    def set_ap_item_bank_details(
        self,
        ap_item_id: str,
        bank_details: Optional[Dict[str, Any]],
        *,
        actor_id: Optional[str] = None,
    ) -> bool:
        """Encrypt + persist bank details on an AP item.

        ``bank_details`` is the plaintext dict — encryption is handled
        here. Pass ``None`` to clear the column. Returns True on success.
        """
        from clearledgr.core.stores.bank_details import (
            encrypt_bank_details,
            normalize_bank_details,
        )

        cleaned = normalize_bank_details(bank_details)
        if cleaned is None:
            ciphertext = None
        else:
            try:
                ciphertext = encrypt_bank_details(
                    cleaned, encrypt_fn=self._encrypt_secret
                )
            except Exception as exc:
                logger.error(
                    "set_ap_item_bank_details encryption failed for %s: %s",
                    ap_item_id, exc,
                )
                return False
        return self.update_ap_item(
            ap_item_id,
            bank_details_encrypted=ciphertext,
            _actor_id=actor_id or "system",
        )

    def clear_ap_item_bank_details(self, ap_item_id: str) -> bool:
        """Convenience: clear the bank-details column."""
        return self.set_ap_item_bank_details(ap_item_id, None)

    # ---- Invoice-status bridge methods ----
    # invoice_workflow.py uses gmail_id-based methods (save_invoice_status,
    # update_invoice_status, get_invoice_status).  These bridge to the
    # canonical ap_items table using thread_id = gmail_id.

    def get_invoice_status(self, gmail_id: str) -> Optional[Dict[str, Any]]:
        """Look up an AP item by its Gmail thread/message ID."""
        self.initialize()
        sql = (
            "SELECT * FROM ap_items WHERE thread_id = %s ORDER BY created_at DESC LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (gmail_id,))
            row = cur.fetchone()
        return dict(row) if row else None

    def save_invoice_status(self, **kwargs) -> str:
        """Create a new AP item from invoice data.

        Accepts the kwargs historically used by invoice_workflow
        and maps them to the ap_items schema.
        """
        from clearledgr.core.ap_states import normalize_state

        gmail_id = kwargs.get("gmail_id", "")
        status_raw = kwargs.get("status", "received")
        state = normalize_state(status_raw)

        # Derive invoice_key so the UNIQUE(organization_id, invoice_key) constraint
        # prevents duplicate AP items.  NULL invoice_key bypasses the constraint in
        # SQLite, so we always generate one from available identifiers.
        invoice_key = kwargs.get("invoice_key")
        if not invoice_key:
            inv_num = kwargs.get("invoice_number") or ""
            vendor = kwargs.get("vendor") or ""
            if inv_num and vendor:
                invoice_key = f"{vendor}::{inv_num}"
            elif gmail_id:
                invoice_key = f"gmail::{gmail_id}"

        payload = {
            "invoice_key": invoice_key,
            "thread_id": gmail_id,
            "message_id": kwargs.get("message_id"),
            "subject": kwargs.get("email_subject") or kwargs.get("subject"),
            "sender": kwargs.get("sender"),
            "vendor_name": kwargs.get("vendor"),
            "amount": kwargs.get("amount"),
            "currency": kwargs.get("currency", "USD"),
            "invoice_number": kwargs.get("invoice_number"),
            "due_date": kwargs.get("due_date"),
            "state": state,
            "confidence": kwargs.get("confidence", 0),
            "field_confidences": kwargs.get("field_confidences"),
            "organization_id": kwargs.get("organization_id"),
            "user_id": kwargs.get("user_id"),
            # Phase 2.1.a: forward bank_details so create_ap_item can
            # encrypt it. The workflow caller passes invoice.bank_details
            # which is the in-memory dict from the LLM extractor.
            "bank_details": kwargs.get("bank_details"),
        }
        result = self.create_ap_item(payload)
        return result.get("id", gmail_id) if result else gmail_id

    def update_invoice_status(self, gmail_id: str = "", **kwargs) -> bool:
        """Update an AP item looked up by Gmail thread/message ID."""
        gmail_id = gmail_id or kwargs.pop("gmail_id", "")
        if not gmail_id:
            return False
        item = self.get_invoice_status(gmail_id)
        if not item:
            return False
        # Map 'status' kwarg to 'state' column
        if "status" in kwargs:
            from clearledgr.core.ap_states import normalize_state

            kwargs["state"] = normalize_state(kwargs.pop("status"))
        # Strip keys that aren't in the AP item schema
        kwargs.pop("gmail_id", None)
        kwargs.pop("email_subject", None)
        if not kwargs:
            return False
        return self.update_ap_item(item["id"], **kwargs)

    def get_slack_thread(self, gmail_id: str) -> Optional[Dict[str, Any]]:
        """Return Slack thread info for an AP item by gmail_id."""
        item = self.get_invoice_status(gmail_id)
        if not item:
            return None
        channel = item.get("slack_channel_id")
        ts = item.get("slack_message_ts")
        if not channel and not ts:
            return None
        return {
            "channel_id": channel,
            "thread_ts": ts,
            "thread_id": item.get("slack_thread_id"),
        }

    def save_slack_thread(
        self,
        gmail_id: str,
        channel_id: str = "",
        thread_ts: str = "",
        **kwargs,
    ) -> str:
        """Store Slack thread info on the AP item."""
        item = self.get_invoice_status(gmail_id)
        if item:
            self.update_ap_item(
                item["id"],
                slack_channel_id=channel_id,
                slack_message_ts=thread_ts,
                slack_thread_id=kwargs.get("thread_id", ""),
            )
        return thread_ts

    def update_slack_thread_status(self, gmail_id: str, **kwargs) -> bool:
        """Update Slack-related fields on the AP item."""
        item = self.get_invoice_status(gmail_id)
        if not item:
            return False
        update_kwargs = {}
        # Accept transport-style aliases used by workflow/channel code.
        if "channel_id" in kwargs and "slack_channel_id" not in kwargs:
            kwargs["slack_channel_id"] = kwargs.get("channel_id")
        if "thread_ts" in kwargs and "slack_message_ts" not in kwargs:
            kwargs["slack_message_ts"] = kwargs.get("thread_ts")
        if "thread_id" in kwargs and "slack_thread_id" not in kwargs:
            kwargs["slack_thread_id"] = kwargs.get("thread_id")
        if "status" in kwargs:
            # Don't update AP state from slack thread status
            kwargs.pop("status")
        for k in ("slack_channel_id", "slack_message_ts", "slack_thread_id"):
            if k in kwargs:
                update_kwargs[k] = kwargs[k]
        if update_kwargs:
            return self.update_ap_item(item["id"], **update_kwargs)
        return False

    # ------------------------------------------------------------------
    # Notification retry queue
    # ------------------------------------------------------------------

    def enqueue_notification(
        self,
        organization_id: str,
        channel: str,
        payload: dict,
        box_id: str | None = None,
        box_type: str | None = None,
        max_retries: int = 5,
    ) -> str:
        """Insert a notification into the retry queue."""
        self.initialize()
        import uuid as _uuid
        now = datetime.now(timezone.utc).isoformat()
        notif_id = f"notif-{_uuid.uuid4().hex[:12]}"
        sql = """
            INSERT INTO pending_notifications
            (id, organization_id, box_id, box_type, channel, payload_json,
             retry_count, max_retries, next_retry_at, status, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, 0, %s, %s, 'pending', %s, %s)
        """
        with self.connect() as conn:
            conn.cursor().execute(sql, (
                notif_id, organization_id, box_id, box_type, channel,
                json.dumps(payload), max_retries, now, now, now,
            ))
            conn.commit()
        return notif_id

    def get_pending_notifications(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return notifications that are due for retry."""
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        sql = (
            "SELECT * FROM pending_notifications "
            "WHERE status = 'pending' AND next_retry_at <= %s "
            "ORDER BY next_retry_at ASC LIMIT %s"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (now, limit))
            rows = cur.fetchall()
        return [dict(r) for r in rows]

    def mark_notification_sent(self, notif_id: str) -> None:
        """Mark a notification as successfully sent."""
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        sql = (
            "UPDATE pending_notifications SET status = 'sent', updated_at = %s WHERE id = %s"
        )
        with self.connect() as conn:
            conn.cursor().execute(sql, (now, notif_id))
            conn.commit()

    def mark_notification_failed(self, notif_id: str, error: str) -> None:
        """Increment retry count and schedule next retry with exponential backoff."""
        self.initialize()
        now = datetime.now(timezone.utc)
        # Backoff schedule: 1m, 5m, 15m, 1h, 4h
        backoff_seconds = [60, 300, 900, 3600, 14400]
        sql_read = (
            "SELECT retry_count, max_retries FROM pending_notifications WHERE id = %s"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql_read, (notif_id,))
            row = cur.fetchone()
            if not row:
                return
            retry_count = (dict(row)["retry_count"] or 0) + 1
            max_retries = dict(row)["max_retries"] or 5
            if retry_count >= max_retries:
                status = "dead_letter"
                next_retry = now.isoformat()
                logger.critical(
                    "Notification %s entered dead_letter after %d retries. Last error: %s. "
                    "Manual intervention required.",
                    notif_id, retry_count, error,
                )
            else:
                status = "pending"
                idx = min(retry_count - 1, len(backoff_seconds) - 1)
                from datetime import timedelta
                next_retry = (now + timedelta(seconds=backoff_seconds[idx])).isoformat()
            sql_update = (
                "UPDATE pending_notifications SET retry_count = %s, next_retry_at = %s, "
                "last_error = %s, status = %s, updated_at = %s WHERE id = %s"
            )
            cur.execute(sql_update, (retry_count, next_retry, error, status, now.isoformat(), notif_id))
            conn.commit()

    def get_ap_item_by_invoice_key(self, organization_id: str, invoice_key: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = (
            "SELECT * FROM ap_items WHERE organization_id = %s AND invoice_key = %s"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, invoice_key))
            row = cur.fetchone()
        return dict(row) if row else None

    def list_ap_items_by_invoice_key_prefix(
        self, organization_id: str, invoice_key_prefix: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        self.initialize()
        prefix = invoice_key_prefix.replace("%", "\\%").replace("_", "\\_")
        sql = (
            "SELECT * FROM ap_items WHERE organization_id = %s AND invoice_key LIKE %s ESCAPE '\\' "
            "ORDER BY created_at DESC LIMIT %s"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, f"{prefix}%", limit))
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _normalize_invoice_number(raw: str) -> str:
        """Normalize invoice number for dedup: lowercase, strip whitespace and common prefixes."""
        import re as _re
        val = str(raw or "").strip().lower()
        # Strip leading '#'
        val = val.lstrip("#")
        # Remove common prefixes like 'inv-', 'inv', 'invoice-', 'invoice'
        val = _re.sub(r"^inv(?:oice)?[-\s]*", "", val)
        return val.strip()

    def get_ap_item_by_vendor_invoice(
        self, organization_id: str, vendor_name: str, invoice_number: str
    ) -> Optional[Dict[str, Any]]:
        vendor_name = str(vendor_name or "").strip()
        invoice_number = str(invoice_number or "").strip()
        normalized_invoice = self._normalize_invoice_number(invoice_number)
        self.initialize()
        # First try exact LOWER match (most common, uses index)
        sql = (
            "SELECT * FROM ap_items WHERE organization_id = %s "
            "AND LOWER(vendor_name) = LOWER(%s) AND LOWER(invoice_number) = LOWER(%s) "
            "ORDER BY created_at DESC LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, vendor_name, invoice_number))
            row = cur.fetchone()
        if row:
            return dict(row)
        # Fallback: load recent vendor items and compare normalized forms
        if normalized_invoice:
            sql2 = (
                "SELECT * FROM ap_items WHERE organization_id = %s "
                "AND LOWER(vendor_name) = LOWER(%s) "
                "ORDER BY created_at DESC LIMIT 50"
            )
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql2, (organization_id, vendor_name))
                rows = cur.fetchall()
            for r in rows:
                row_inv = str(dict(r).get("invoice_number") or "").strip()
                if row_inv and self._normalize_invoice_number(row_inv) == normalized_invoice:
                    return dict(r)
        return None

    def get_rejected_ap_item_by_vendor_invoice(
        self, organization_id: str, vendor_name: str, invoice_number: str
    ) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = (
            "SELECT * FROM ap_items WHERE organization_id = %s AND vendor_name = %s AND invoice_number = %s "
            "AND state = 'rejected' ORDER BY created_at DESC LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, vendor_name, invoice_number))
            row = cur.fetchone()
        return dict(row) if row else None

    def get_ap_item_by_thread(self, organization_id: str, thread_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = (
            """
            SELECT * FROM ap_items
            WHERE organization_id = %s
              AND (
                thread_id = %s
                OR id IN (
                  SELECT ap_item_id
                  FROM ap_item_sources
                  WHERE source_type = 'gmail_thread' AND source_ref = %s
                )
              )
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, thread_id, thread_id))
            row = cur.fetchone()
        return dict(row) if row else None

    def get_ap_item_by_message_id(self, organization_id: str, message_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = (
            """
            SELECT * FROM ap_items
            WHERE organization_id = %s
              AND (
                message_id = %s
                OR id IN (
                  SELECT ap_item_id
                  FROM ap_item_sources
                  WHERE source_type = 'gmail_message' AND source_ref = %s
                )
              )
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, message_id, message_id))
            row = cur.fetchone()
        return dict(row) if row else None

    def get_ap_item_by_erp_reference(self, organization_id: str, erp_reference: str) -> Optional[Dict[str, Any]]:
        """Look up AP item by its ERP reference (indexed)."""
        self.initialize()
        sql = (
            "SELECT * FROM ap_items WHERE organization_id = %s AND erp_reference = %s "
            "ORDER BY created_at DESC LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, erp_reference))
            row = cur.fetchone()
        return dict(row) if row else None

    def get_ap_item_by_invoice_number(self, organization_id: str, invoice_number: str) -> Optional[Dict[str, Any]]:
        """Look up AP item by invoice number."""
        self.initialize()
        sql = (
            "SELECT * FROM ap_items WHERE organization_id = %s AND invoice_number = %s "
            "ORDER BY created_at DESC LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, invoice_number))
            row = cur.fetchone()
        return dict(row) if row else None

    def get_ap_item_by_workflow_id(self, organization_id: str, workflow_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = (
            "SELECT * FROM ap_items WHERE organization_id = %s AND workflow_id = %s "
            "ORDER BY created_at DESC LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, workflow_id))
            row = cur.fetchone()
        return dict(row) if row else None

    def list_ap_items_by_thread(self, organization_id: str, thread_id: str) -> List[Dict[str, Any]]:
        self.initialize()
        sql = (
            """
            SELECT * FROM ap_items
            WHERE organization_id = %s
              AND (
                thread_id = %s
                OR id IN (
                  SELECT ap_item_id
                  FROM ap_item_sources
                  WHERE source_type = 'gmail_thread' AND source_ref = %s
                )
              )
            ORDER BY created_at DESC
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, thread_id, thread_id))
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def _worklist_priority_score(self, item: Dict[str, Any]) -> float:
        metadata = self._decode_json(item.get("metadata"))
        explicit = metadata.get("priority_score")
        if explicit is not None:
            return safe_float(explicit, 0.0)

        severity_rank = self._exception_severity_rank(
            metadata.get("exception_severity") or item.get("exception_severity")
        )
        score = float(severity_rank * 100)

        state = str(item.get("state") or "").strip().lower()
        if state == "failed_post":
            score += 45.0
        elif state == "needs_info":
            score += 40.0
        elif state == "needs_approval":
            score += 30.0
        elif state == "approved":
            score += 20.0

        due_date = self._parse_iso(item.get("due_date"))
        if due_date:
            now = datetime.now(timezone.utc)
            hours_to_due = (due_date - now).total_seconds() / 3600.0
            if hours_to_due <= 24:
                score += 25.0
            elif hours_to_due <= 72:
                score += 10.0
        return score

    def _worklist_sort_key(self, item: Dict[str, Any]) -> tuple:
        metadata = self._decode_json(item.get("metadata"))
        severity_rank = self._exception_severity_rank(
            metadata.get("exception_severity") or item.get("exception_severity")
        )
        priority_score = self._worklist_priority_score(item)
        created_at = self._parse_iso(item.get("created_at")) or self._parse_iso(item.get("updated_at"))
        created_ts = created_at.timestamp() if created_at else 0.0
        return (-priority_score, -severity_rank, -created_ts)

    def list_ap_items(
        self,
        organization_id: str,
        state: Optional[str] = None,
        entity_id: Optional[str] = None,
        limit: int = 200,
        prioritized: bool = False,
    ) -> List[Dict[str, Any]]:
        self.initialize()
        safe_limit = max(1, min(int(limit or 200), 10000))

        # Build WHERE clause dynamically — entity_id filter is optional (§3 multi-entity)
        where_parts = ["organization_id = %s"]
        params_list: list = [organization_id]
        if state:
            where_parts.append("state = %s")
            params_list.append(state)
        if entity_id:
            where_parts.append("entity_id = %s")
            params_list.append(entity_id)
        where_clause = " AND ".join(where_parts)

        if prioritized:
            fetch_limit = max(500, safe_limit * 8)
            sql = (
                f"SELECT * FROM ap_items WHERE {where_clause} ORDER BY created_at DESC LIMIT %s"
            )
            params_list.append(fetch_limit)
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, tuple(params_list))
                rows = cur.fetchall()
            items = [dict(row) for row in rows]
            items.sort(key=self._worklist_sort_key)
            return items[:safe_limit]

        sql = (
            f"SELECT * FROM ap_items WHERE {where_clause} ORDER BY created_at DESC LIMIT %s"
        )
        params_list.append(safe_limit)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, tuple(params_list))
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def list_ap_items_all(
        self, organization_id: str, state: Optional[str] = None, limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """List AP items scoped to a single organization.

        Cross-tenant access is prevented by requiring organization_id.
        """
        self.initialize()
        if not organization_id:
            raise ValueError("organization_id is required for list_ap_items_all")
        if state:
            sql = (
                "SELECT * FROM ap_items WHERE organization_id = %s AND state = %s "
                "ORDER BY created_at DESC LIMIT %s"
            )
            params: tuple = (organization_id, state, limit)
        else:
            sql = (
                "SELECT * FROM ap_items WHERE organization_id = %s "
                "ORDER BY created_at DESC LIMIT %s"
            )
            params = (organization_id, limit)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def get_overdue_approvals(self, organization_id: str, min_hours: float) -> List[Dict[str, Any]]:
        """Return ap_items stuck in needs_approval longer than min_hours.

        Prefer the explicit ``approval_requested_at`` timestamp stored in item
        metadata and fall back to ``updated_at`` for legacy rows.
        """
        self.initialize()
        # metadata is stored as TEXT (for SQLite parity) so we must
        # cast to jsonb before the ->> operator or psycopg raises
        # "operator does not exist: text ->> unknown". The COALESCE
        # result is TEXT (both branches are text), so cast the
        # result to timestamptz before comparing against NOW() —
        # otherwise PG errors with "text < timestamp with time zone".
        sql = (
            "SELECT * FROM ap_items "
            "WHERE organization_id = %s AND state = 'needs_approval' "
            "AND COALESCE(NULLIF(metadata::jsonb->>'approval_requested_at', ''), updated_at)::timestamptz "
            "< (NOW() - (%s * INTERVAL '1 hour')) "
            "ORDER BY updated_at ASC LIMIT 50"
        )
        params: tuple = (organization_id, min_hours)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def get_pending_approver_ids(self, ap_item_id: str) -> List[str]:
        """Return Slack user IDs of pending approvers for an AP item.

        Prefer ``approval_delivery_targets`` from the item's JSON metadata,
        then Slack-style IDs from ``approval_sent_to``, then fall back to any
        pending approval-chain step approvers stored in
        ``approval_steps``.
        """
        self.initialize()
        sql = "SELECT metadata FROM ap_items WHERE id = %s"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (ap_item_id,))
            row = cur.fetchone()
        if not row:
            return []
        meta: Dict[str, Any] = {}
        try:
            meta = json.loads(row[0] or "{}")
            direct_targets = meta.get("approval_delivery_targets", [])
            if isinstance(direct_targets, list):
                direct = [str(uid).strip() for uid in direct_targets if str(uid).strip()]
                if direct:
                    return direct
            if isinstance(direct_targets, str) and str(direct_targets).strip():
                return [str(direct_targets).strip()]

            sent_to = meta.get("approval_sent_to", [])
            if isinstance(sent_to, list):
                direct = [
                    str(uid).strip()
                    for uid in sent_to
                    if str(uid).strip() and str(uid).strip()[0] in {"U", "W"}
                ]
                if direct:
                    return direct
            if isinstance(sent_to, str) and str(sent_to).strip() and str(sent_to).strip()[0] in {"U", "W"}:
                return [str(sent_to).strip()]
        except Exception:
            meta = {}
        chain_id = str(meta.get("approval_chain_id") or "").strip()
        if not chain_id:
            return []
        steps_sql = (
            "SELECT approvers FROM approval_steps "
            "WHERE chain_id = %s AND status = 'pending' "
            "ORDER BY step_index ASC"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(steps_sql, (chain_id,))
            rows = cur.fetchall()
        pending: List[str] = []
        for step_row in rows:
            try:
                raw_approvers = json.loads((step_row[0] if not isinstance(step_row, dict) else step_row.get("approvers")) or "[]")
            except Exception:
                raw_approvers = []
            if isinstance(raw_approvers, list):
                pending.extend(str(uid).strip() for uid in raw_approvers if str(uid).strip())
        deduped: List[str] = []
        seen = set()
        for approver_id in pending:
            if approver_id in seen:
                continue
            seen.add(approver_id)
            deduped.append(approver_id)
        if deduped:
            return deduped
        return []

    def link_ap_item_source(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.initialize()
        import uuid

        now = datetime.now(timezone.utc).isoformat()
        source_id = payload.get("id") or f"SRC-{uuid.uuid4().hex}"
        ap_item_id = payload.get("ap_item_id")
        source_type = str(payload.get("source_type") or "").strip()
        source_ref = str(payload.get("source_ref") or "").strip()
        if not source_type or not source_ref:
            raise ValueError("source_type_and_source_ref_required")

        sql = (
            """
            INSERT INTO ap_item_sources
            (id, ap_item_id, source_type, source_ref, subject, sender, detected_at, metadata, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ap_item_id, source_type, source_ref) DO NOTHING
            """
        )

        detected_at = payload.get("detected_at") or now
        metadata_json = json.dumps(payload.get("metadata") or {})
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    source_id,
                    ap_item_id,
                    source_type,
                    source_ref,
                    payload.get("subject"),
                    payload.get("sender"),
                    detected_at,
                    metadata_json,
                    now,
                ),
            )
            row_sql = (
                "SELECT * FROM ap_item_sources WHERE ap_item_id = %s AND source_type = %s AND source_ref = %s LIMIT 1"
            )
            cur.execute(row_sql, (ap_item_id, source_type, source_ref))
            row = cur.fetchone()
            conn.commit()

        if row:
            data = dict(row)
            raw_metadata = data.get("metadata")
            if isinstance(raw_metadata, str):
                try:
                    data["metadata"] = json.loads(raw_metadata)
                except json.JSONDecodeError:
                    data["metadata"] = {}
            return data

        # Fallback should be unreachable, but preserves prior return contract.
        return {
            "id": source_id,
            "ap_item_id": ap_item_id,
            "source_type": source_type,
            "source_ref": source_ref,
            "subject": payload.get("subject"),
            "sender": payload.get("sender"),
            "detected_at": detected_at,
            "metadata": payload.get("metadata") or {},
            "created_at": now,
        }

    def list_ap_item_sources(self, ap_item_id: str, source_type: Optional[str] = None) -> List[Dict[str, Any]]:
        self.initialize()
        if source_type:
            sql = (
                "SELECT * FROM ap_item_sources WHERE ap_item_id = %s AND source_type = %s ORDER BY detected_at ASC, created_at ASC"
            )
            params = (ap_item_id, source_type)
        else:
            sql = (
                "SELECT * FROM ap_item_sources WHERE ap_item_id = %s ORDER BY detected_at ASC, created_at ASC"
            )
            params = (ap_item_id,)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()

        results: List[Dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            meta = data.get("metadata")
            if isinstance(meta, str):
                try:
                    data["metadata"] = json.loads(meta)
                except json.JSONDecodeError:
                    data["metadata"] = {}
            results.append(data)
        return results

    def list_ap_item_sources_bulk(self, ap_item_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
        """Return source rows keyed by AP item id for a set of items.

        This is the bulk companion to ``list_ap_item_sources`` and is meant for
        list/read surfaces that would otherwise issue one source query per AP
        item.
        """
        self.initialize()
        normalized_ids = [
            str(value or "").strip()
            for value in (ap_item_ids or [])
            if str(value or "").strip()
        ]
        if not normalized_ids:
            return {}

        placeholders = ", ".join("%s" for _ in normalized_ids)
        sql = (
            "SELECT * FROM ap_item_sources "
            f"WHERE ap_item_id IN ({placeholders}) "
            "ORDER BY ap_item_id ASC, detected_at ASC, created_at ASC"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, tuple(normalized_ids))
            rows = cur.fetchall()

        grouped: Dict[str, List[Dict[str, Any]]] = {item_id: [] for item_id in normalized_ids}
        for row in rows:
            data = dict(row)
            meta = data.get("metadata")
            if isinstance(meta, str):
                try:
                    data["metadata"] = json.loads(meta)
                except json.JSONDecodeError:
                    data["metadata"] = {}
            item_id = str(data.get("ap_item_id") or "").strip()
            grouped.setdefault(item_id, []).append(data)
        return grouped

    def list_ap_item_sources_by_ref(self, source_type: str, source_ref: str) -> List[Dict[str, Any]]:
        self.initialize()
        sql = (
            "SELECT * FROM ap_item_sources WHERE source_type = %s AND source_ref = %s ORDER BY created_at DESC"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (source_type, source_ref))
            rows = cur.fetchall()
        results: List[Dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            meta = data.get("metadata")
            if isinstance(meta, str):
                try:
                    data["metadata"] = json.loads(meta)
                except json.JSONDecodeError:
                    data["metadata"] = {}
            results.append(data)
        return results

    def unlink_ap_item_source(self, ap_item_id: str, source_type: str, source_ref: str) -> bool:
        self.initialize()
        sql = (
            "DELETE FROM ap_item_sources WHERE ap_item_id = %s AND source_type = %s AND source_ref = %s"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (ap_item_id, source_type, source_ref))
            conn.commit()
            return cur.rowcount > 0

    def move_ap_item_source(
        self,
        from_ap_item_id: str,
        to_ap_item_id: str,
        source_type: str,
        source_ref: str,
    ) -> Optional[Dict[str, Any]]:
        self.initialize()
        source_type = str(source_type or "").strip()
        source_ref = str(source_ref or "").strip()
        if not source_type or not source_ref:
            return None

        current_rows = self.list_ap_item_sources(from_ap_item_id, source_type=source_type)
        current = next((row for row in current_rows if row.get("source_ref") == source_ref), None)
        if not current:
            return None

        moved = self.link_ap_item_source(
            {
                "ap_item_id": to_ap_item_id,
                "source_type": source_type,
                "source_ref": source_ref,
                "subject": current.get("subject"),
                "sender": current.get("sender"),
                "detected_at": current.get("detected_at"),
                "metadata": current.get("metadata") or {},
            }
        )
        self.unlink_ap_item_source(from_ap_item_id, source_type, source_ref)
        return moved

    def upsert_ap_item_context_cache(self, ap_item_id: str, context_json: Dict[str, Any]) -> None:
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()

        sql = (
            """
            INSERT INTO ap_item_context_cache (ap_item_id, context_json, updated_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (ap_item_id)
            DO UPDATE SET context_json = EXCLUDED.context_json, updated_at = EXCLUDED.updated_at
            """
        )

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (ap_item_id, json.dumps(context_json or {}), now))
            conn.commit()

    def get_ap_item_context_cache(self, ap_item_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = "SELECT * FROM ap_item_context_cache WHERE ap_item_id = %s"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (ap_item_id,))
            row = cur.fetchone()
        if not row:
            return None
        data = dict(row)
        raw = data.get("context_json")
        if isinstance(raw, str):
            try:
                data["context_json"] = json.loads(raw)
            except json.JSONDecodeError:
                data["context_json"] = {}
        return data

    def list_organizations_with_ap_items(self) -> List[str]:
        self.initialize()
        sql = "SELECT DISTINCT organization_id FROM ap_items WHERE organization_id IS NOT NULL AND organization_id != ''"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall()
        orgs = []
        for row in rows:
            if isinstance(row, dict):
                org = row.get("organization_id")
            elif hasattr(row, '__getitem__'):
                try:
                    org = row["organization_id"]
                except (KeyError, IndexError):
                    org = row[0] if row else None
            else:
                org = row[0] if row else None
            if org:
                orgs.append(str(org))
        return orgs

    # ------------------------------------------------------------------
    # Channel threads (Gap #11 — symmetric storage for Slack and Teams)
    # ------------------------------------------------------------------

    def upsert_channel_thread(
        self,
        *,
        ap_item_id: str,
        channel: str,
        conversation_id: Optional[str],
        message_id: Optional[str] = None,
        activity_id: Optional[str] = None,
        service_url: Optional[str] = None,
        state: Optional[str] = None,
        last_action: Optional[str] = None,
        updated_by: Optional[str] = None,
        reason: Optional[str] = None,
        organization_id: Optional[str] = None,
    ) -> None:
        """Insert or update a channel thread record for Slack or Teams.

        Uses ``UNIQUE(ap_item_id, channel, conversation_id)`` for upsert
        semantics so repeated callback calls are idempotent.
        """
        self.initialize()
        import uuid as _uuid

        now = datetime.now(timezone.utc).isoformat()
        thread_id = f"CT-{_uuid.uuid4().hex}"

        sql = """
            INSERT INTO channel_threads
            (id, ap_item_id, channel, conversation_id, message_id, activity_id,
             service_url, state, last_action, updated_by, reason, organization_id,
             created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ap_item_id, channel, conversation_id)
            DO UPDATE SET
                message_id = EXCLUDED.message_id,
                activity_id = EXCLUDED.activity_id,
                service_url = EXCLUDED.service_url,
                state = EXCLUDED.state,
                last_action = EXCLUDED.last_action,
                updated_by = EXCLUDED.updated_by,
                reason = EXCLUDED.reason,
                updated_at = EXCLUDED.updated_at
        """

        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (
                    thread_id, ap_item_id, channel, conversation_id or "",
                    message_id, activity_id, service_url,
                    state, last_action, updated_by, reason, organization_id,
                    now, now,
                ))
                conn.commit()
        except Exception as exc:
            logger.error("upsert_channel_thread failed (non-fatal): %s", exc)

    def get_channel_threads(self, ap_item_id: str) -> List[Dict[str, Any]]:
        """Return all channel thread records for an AP item."""
        self.initialize()
        sql = (
            "SELECT * FROM channel_threads WHERE ap_item_id = %s ORDER BY updated_at DESC"
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (ap_item_id,))
                return [dict(row) for row in cur.fetchall()]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Audit events
    # ------------------------------------------------------------------

    def append_audit_event(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Funnel for all audit event writes. Every row is Box-keyed:
        ``box_id`` + ``box_type`` identify which Box this event is
        about. Callers must pass either the pair explicitly or the
        AP-convenience single ``ap_item_id`` field (which is
        normalised to ``box_id``/``box_type='ap_item'``).
        """
        self.initialize()
        import uuid
        now = payload.get("ts") or datetime.now(timezone.utc).isoformat()
        event_id = payload.get("id") or f"EVT-{uuid.uuid4().hex}"

        idempotency_key = payload.get("idempotency_key")
        if idempotency_key:
            existing = self.get_ap_audit_event_by_key(idempotency_key)
            if existing:
                return existing

        payload_json = payload.get("payload_json")
        if payload_json is None:
            payload_json = {}
            reason = payload.get("reason")
            if reason:
                payload_json["reason"] = reason
            metadata = payload.get("metadata") or {}
            if isinstance(metadata, dict):
                payload_json.update(metadata)
        external_refs = payload.get("external_refs") or {}

        # Resolve (box_id, box_type). Explicit kwargs win; a caller
        # may also pass the AP-convenience ``ap_item_id`` alone, in
        # which case it's the box_id for type ``ap_item``.
        box_id = payload.get("box_id") or payload.get("ap_item_id")
        box_type = payload.get("box_type")
        if box_type is None and box_id is not None:
            box_type = "ap_item"
        if box_id is None or box_type is None:
            raise ValueError(
                "append_audit_event requires (box_id, box_type) or ap_item_id"
            )

        # P4 (audit 2026-04-28): governance_verdict + agent_confidence are
        # structured columns now (migration v50). Callers can pass them
        # at the top level of ``payload`` OR nested in ``payload_json`` —
        # accept either so writers don't have to re-thread the kwarg.
        governance_verdict = payload.get("governance_verdict")
        agent_confidence = payload.get("agent_confidence")
        if governance_verdict is None and isinstance(payload_json, dict):
            verdict_block = payload_json.get("governance_verdict")
            if isinstance(verdict_block, dict):
                if verdict_block.get("should_execute") is False:
                    governance_verdict = "vetoed"
                elif "should_execute" in verdict_block:
                    governance_verdict = "should_execute"
        if agent_confidence is None and isinstance(payload_json, dict):
            for key in ("agent_confidence", "confidence_score", "confidence"):
                value = payload_json.get(key)
                if value is None:
                    continue
                try:
                    agent_confidence = float(value)
                    break
                except (TypeError, ValueError):
                    continue

        sql = """
            INSERT INTO audit_events
            (id, box_id, box_type, event_type, prev_state, new_state,
             actor_type, actor_id, payload_json, external_refs,
             idempotency_key, source, correlation_id, workflow_id, run_id,
             decision_reason, governance_verdict, agent_confidence,
             organization_id, ts)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (
                    event_id,
                    box_id,
                    box_type,
                    payload.get("event_type"),
                    payload.get("from_state"),
                    payload.get("to_state"),
                    payload.get("actor_type"),
                    payload.get("actor_id"),
                    json.dumps(payload_json or {}),
                    json.dumps(external_refs or {}),
                    idempotency_key,
                    payload.get("source"),
                    payload.get("correlation_id"),
                    payload.get("workflow_id"),
                    payload.get("run_id"),
                    payload.get("decision_reason") or payload.get("reason"),
                    governance_verdict,
                    agent_confidence,
                    payload.get("organization_id"),
                    now,
                ))
                conn.commit()
        except Exception as exc:
            # audit_events.idempotency_key has a UNIQUE constraint. The
            # pre-check above catches the common case, but two concurrent
            # callers with the same idempotency_key can both see "no row"
            # and race to INSERT — one wins, the other trips the UNIQUE
            # and raises IntegrityError (sqlite3) or UniqueViolation
            # (psycopg). Treat that as "someone else already wrote this
            # exact event" and return the winner's row, so the caller's
            # idempotent path stays idempotent instead of bubbling a 500.
            if idempotency_key and _is_unique_violation(exc):
                winner = self.get_ap_audit_event_by_key(idempotency_key)
                if winner:
                    return winner
            raise

        # Module 7 v1 Pass 3 — webhook fan-out. After the canonical
        # audit_events INSERT commits, fire-and-forget enqueue a
        # Celery task that fans this event out to every webhook
        # subscription matching its event_type. Decouples audit-write
        # latency from webhook delivery latency: a slow SIEM
        # endpoint never slows the canonical audit write.
        #
        # Best-effort: a Celery dispatch failure (broker outage,
        # import error during dev) logs + swallows so the audit
        # write itself stays committed. The audit log is the source
        # of truth; webhook delivery is downstream observability.
        try:
            from clearledgr.services.celery_tasks import dispatch_audit_webhooks
            dispatch_audit_webhooks.delay(event_id)
        except Exception as fanout_exc:
            logger.warning(
                "[append_audit_event] webhook fan-out enqueue failed for %s: %s",
                event_id, fanout_exc,
            )

        return self.get_ap_audit_event(event_id)

    def get_ap_audit_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = "SELECT * FROM audit_events WHERE id = %s"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (event_id,))
            row = cur.fetchone()
        return self._deserialize_audit_event(dict(row)) if row else None

    def get_ap_audit_event_by_key(self, idempotency_key: Optional[str]) -> Optional[Dict[str, Any]]:
        if not idempotency_key:
            return None
        self.initialize()
        sql = "SELECT * FROM audit_events WHERE idempotency_key = %s"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (idempotency_key,))
            row = cur.fetchone()
        return self._deserialize_audit_event(dict(row)) if row else None

    def list_ap_audit_events(
        self,
        ap_item_id: str,
        limit: Optional[int] = None,
        order: str = "asc",
    ) -> List[Dict[str, Any]]:
        """List audit events for one AP Box. Thin wrapper over
        :meth:`list_box_audit_events` — the AP item's id is the
        box_id for type ``ap_item``.
        """
        return self.list_box_audit_events(
            box_type="ap_item",
            box_id=ap_item_id,
            limit=limit,
            order=order,
        )

    def list_box_audit_events(
        self,
        box_type: str,
        box_id: str,
        limit: Optional[int] = None,
        order: str = "asc",
    ) -> List[Dict[str, Any]]:
        """Generic reader for any Box type's audit trail."""
        self.initialize()
        direction = "DESC" if str(order).lower() == "desc" else "ASC"
        sql = (
            "SELECT * FROM audit_events "
            "WHERE box_id = %s AND box_type = %s "
            f"ORDER BY ts {direction}"
        )
        params: Tuple[Any, ...] = (box_id, box_type)
        if limit is not None:
            sql += " LIMIT %s"
            params = (box_id, box_type, int(limit))
        sql = sql
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [self._deserialize_audit_event(dict(row)) for row in rows]

    def list_recent_ap_audit_events(self, organization_id: str, limit: int = 30) -> List[Dict[str, Any]]:
        """Return recent AP audit events for an organization (newest first)."""
        self.initialize()
        safe_limit = max(1, min(int(limit or 30), 500))
        sql = (
            """
            SELECT ae.*,
                   ai.vendor_name AS vendor_name,
                   ai.amount AS amount,
                   ai.currency AS currency,
                   ai.invoice_number AS invoice_number
            FROM audit_events ae
            LEFT JOIN ap_items ai
                   ON ae.box_type = 'ap_item' AND ae.box_id = ai.id
            WHERE ae.organization_id = %s
               OR (ae.organization_id IS NULL AND ai.organization_id = %s)
            ORDER BY ae.ts DESC
            LIMIT %s
"""
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, organization_id, safe_limit))
            rows = cur.fetchall()
        return [self._deserialize_audit_event(dict(row)) for row in rows]

    def list_recent_ap_audit_events_with_retention(
        self,
        organization_id: str,
        limit: int = 30,
        retention_days: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """§13 tier-gated Agent Activity feed retention.

        Returns recent audit events for the org, filtered to within
        the tier's retention window. ``audit_events`` itself is
        append-only (§7.6 — the audit trail is the evidence of trust
        and cannot be mutated), so retention is a query-time filter
        rather than a delete.

        ``retention_days`` of None or <= 0 returns the full feed —
        used by internal ops / audit-export paths that need the
        complete record regardless of the customer-facing tier cap.
        Customer-facing callers must pass the tier's retention_days
        so Starter workspaces see a 30-day window while Pro/Enterprise
        see the full 7-year statutory window.
        """
        self.initialize()
        safe_limit = max(1, min(int(limit or 30), 500))

        days = int(retention_days or 0)
        if days <= 0:
            return self.list_recent_ap_audit_events(organization_id, limit=safe_limit)

        from datetime import datetime, timedelta, timezone
        cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        sql = (
            """
            SELECT ae.*,
                   ai.vendor_name AS vendor_name,
                   ai.amount AS amount,
                   ai.currency AS currency,
                   ai.invoice_number AS invoice_number
            FROM audit_events ae
            LEFT JOIN ap_items ai
                   ON ae.box_type = 'ap_item' AND ae.box_id = ai.id
            WHERE (ae.organization_id = %s
                   OR (ae.organization_id IS NULL AND ai.organization_id = %s))
              AND ae.ts >= %s
            ORDER BY ae.ts DESC
            LIMIT %s
"""
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, organization_id, cutoff_iso, safe_limit))
            rows = cur.fetchall()
        return [self._deserialize_audit_event(dict(row)) for row in rows]

    # ------------------------------------------------------------------
    # Org-level audit search (Module 7 v1 — Dashboard build spec)
    # ------------------------------------------------------------------

    def search_audit_events(
        self,
        *,
        organization_id: str,
        from_ts: Optional[str] = None,
        to_ts: Optional[str] = None,
        event_types: Optional[List[str]] = None,
        actor_id: Optional[str] = None,
        box_type: Optional[str] = None,
        box_id: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[Tuple[str, str]] = None,
    ) -> Dict[str, Any]:
        """Org-scoped audit-event search with composite-cursor pagination.

        Returns ``{events: [...], next_cursor: (ts, id) | None}``.
        Newest-first. ``next_cursor`` is None when the page is the last
        one. Cursor is a (ts, id) pair so two events written at the
        same millisecond never skip or duplicate across pages.

        Filter semantics:
          * ``from_ts`` / ``to_ts`` — inclusive at both ends, ISO 8601.
          * ``event_types`` — IN-list match. Empty list ignored.
          * ``actor_id`` — exact match on ``actor_id`` column.
          * ``box_type`` / ``box_id`` — narrow to a single Box's trail.

        Tenant scope is enforced by the ``organization_id`` filter.
        Cross-tenant rows are excluded at the SQL level — there is no
        application-side trust.
        """
        self.initialize()
        safe_limit = max(1, min(int(limit or 100), 500))
        # Fetch one extra so we can detect "is there a next page".
        sql_limit = safe_limit + 1

        clauses = ["ae.organization_id = %s"]
        params: List[Any] = [organization_id]

        if from_ts:
            clauses.append("ae.ts >= %s")
            params.append(from_ts)
        if to_ts:
            clauses.append("ae.ts <= %s")
            params.append(to_ts)
        if event_types:
            placeholders = ",".join(["%s"] * len(event_types))
            clauses.append(f"ae.event_type IN ({placeholders})")
            params.extend(event_types)
        if actor_id:
            clauses.append("ae.actor_id = %s")
            params.append(actor_id)
        if box_type:
            clauses.append("ae.box_type = %s")
            params.append(box_type)
        if box_id:
            clauses.append("ae.box_id = %s")
            params.append(box_id)

        # Cursor-based pagination: rows STRICTLY older than (cursor_ts,
        # cursor_id) come next when sorting newest-first. The composite
        # comparison `(ts, id) < (cursor_ts, cursor_id)` makes the
        # window unambiguous when timestamps tie.
        if cursor and len(cursor) == 2:
            cursor_ts, cursor_id = cursor
            clauses.append("(ae.ts, ae.id) < (%s, %s)")
            params.extend([cursor_ts, cursor_id])

        where_clause = " AND ".join(clauses)
        sql = f"""
            SELECT ae.*,
                   ai.vendor_name AS vendor_name,
                   ai.amount AS amount,
                   ai.currency AS currency,
                   ai.invoice_number AS invoice_number
              FROM audit_events ae
              LEFT JOIN ap_items ai
                     ON ae.box_type = 'ap_item' AND ae.box_id = ai.id
             WHERE {where_clause}
             ORDER BY ae.ts DESC, ae.id DESC
             LIMIT %s
        """
        params.append(sql_limit)

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()

        events = [self._deserialize_audit_event(dict(row)) for row in rows]

        next_cursor: Optional[Tuple[str, str]] = None
        if len(events) > safe_limit:
            # Drop the look-ahead row; emit a cursor pointing at the
            # last row that's actually returned to the caller.
            events = events[:safe_limit]
            tail = events[-1]
            next_cursor = (str(tail.get("ts") or ""), str(tail.get("id") or ""))

        return {"events": events, "next_cursor": next_cursor}

    # ------------------------------------------------------------------
    # Audit-export jobs (Module 7 v1 Pass 2)
    # ------------------------------------------------------------------

    def create_audit_export(
        self,
        *,
        organization_id: str,
        requested_by: str,
        filters_json: str,
        export_format: str = "csv",
        retention_hours: int = 24,
    ) -> Dict[str, Any]:
        """Create an audit_exports row in 'queued' state.

        Returns the row dict including a generated UUID-based id.
        Caller dispatches the Celery task with ``id`` after; the task
        flips status to 'running' → 'done' / 'failed' as it runs.
        """
        self.initialize()
        import uuid as _uuid

        export_id = f"AEX-{_uuid.uuid4().hex[:24]}"
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        expires_at = (now_dt + timedelta(hours=int(retention_hours))).isoformat()
        sql = (
            """
            INSERT INTO audit_exports
            (id, organization_id, requested_by, filters_json, format, status,
             created_at, expires_at)
            VALUES (%s, %s, %s, %s, %s, 'queued', %s, %s)
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (export_id, organization_id, requested_by, filters_json,
                 export_format, now, expires_at),
            )
            conn.commit()
        return self.get_audit_export(export_id) or {
            "id": export_id,
            "organization_id": organization_id,
            "requested_by": requested_by,
            "filters_json": filters_json,
            "format": export_format,
            "status": "queued",
            "created_at": now,
            "expires_at": expires_at,
        }

    def get_audit_export(
        self,
        export_id: str,
        *,
        include_content: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Fetch an audit_exports row.

        ``include_content=False`` (default) skips the ``content`` BYTEA
        column so the status-poll endpoint doesn't ship multi-MB
        payloads on every refresh. The download endpoint passes
        ``True`` to actually serve the file.
        """
        self.initialize()
        cols = (
            "id, organization_id, requested_by, filters_json, format, status, "
            "total_rows, content_filename, content_size_bytes, error_message, "
            "created_at, started_at, completed_at, expires_at"
        )
        if include_content:
            cols = cols + ", content"
        sql = f"SELECT {cols} FROM audit_exports WHERE id = %s"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (export_id,))
            row = cur.fetchone()
        return dict(row) if row else None

    def update_audit_export_status(
        self,
        export_id: str,
        *,
        status: str,
        started_at: Optional[str] = None,
        completed_at: Optional[str] = None,
        error_message: Optional[str] = None,
        total_rows: Optional[int] = None,
    ) -> bool:
        """Atomic status transition: queued → running → done | failed.

        Only the columns the caller passes are updated — the SQL
        builds a SET clause from non-None kwargs. Audit_exports are
        durable but ephemeral (24h retention); we don't write an
        audit_event for status transitions because the export job
        itself isn't a Box-mutating action.
        """
        self.initialize()
        updates: Dict[str, Any] = {"status": status}
        if started_at is not None:
            updates["started_at"] = started_at
        if completed_at is not None:
            updates["completed_at"] = completed_at
        if error_message is not None:
            updates["error_message"] = error_message
        if total_rows is not None:
            updates["total_rows"] = int(total_rows)
        set_clause = ", ".join(f"{k} = %s" for k in updates.keys())
        sql = f"UPDATE audit_exports SET {set_clause} WHERE id = %s"
        params = (*updates.values(), export_id)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()
            return (cur.rowcount or 0) > 0

    def set_audit_export_content(
        self,
        export_id: str,
        *,
        content: bytes,
        content_filename: str,
    ) -> bool:
        """Attach the rendered CSV bytes to a 'done' export row."""
        self.initialize()
        sql = (
            "UPDATE audit_exports "
            "SET content = %s, content_filename = %s, content_size_bytes = %s "
            "WHERE id = %s"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (content, content_filename, len(content), export_id))
            conn.commit()
            return (cur.rowcount or 0) > 0

    def reap_expired_audit_exports(self) -> int:
        """Delete export rows past their expires_at. Idempotent.

        Called by the same orphan-task-runs sweeper at startup
        (clearledgr/services/app_startup.py). Returns count deleted.
        """
        self.initialize()
        now_iso = datetime.now(timezone.utc).isoformat()
        sql = "DELETE FROM audit_exports WHERE expires_at < %s"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (now_iso,))
            deleted = cur.rowcount or 0
            conn.commit()
        if deleted:
            logger.info("[ap_store] reaped %d expired audit_exports", deleted)
        return int(deleted)

    # ------------------------------------------------------------------
    # Connection health (Module 5 Pass B)
    # ------------------------------------------------------------------
    #
    # Derived view over the existing audit_events + webhook_deliveries
    # tables. No new persistence — health is a function of recent
    # observations. The derivation classifies every audit event by
    # which integration kind it belongs to (gmail/slack/teams/erp)
    # using source + event_type heuristics, then aggregates counts
    # over a configurable window (default 24h).

    def get_connection_health_aggregates(
        self,
        *,
        organization_id: str,
        window_hours: int = 24,
    ) -> Dict[str, Any]:
        """Return per-integration audit-event aggregates for the window.

        Output shape::

            {
              "window_hours": 24,
              "by_kind": {
                "gmail":  {"events": 142, "errors": 0,  "latest_event_at": "..."},
                "slack":  {"events": 14,  "errors": 0,  "latest_event_at": "..."},
                "teams":  {"events": 0,   "errors": 0,  "latest_event_at": null},
                "erp":    {"events": 12,  "errors": 1,  "latest_event_at": "..."},
              },
              "latest_error_by_kind": {
                "erp": {"ts": "...", "event_type": "erp_post_failed",
                        "payload_json": {...}}
              },
              "webhooks": {"delivered": 4, "failed": 0, "retrying": 0}
            }

        The classifier is deliberately permissive — anything that doesn't
        match a known integration prefix lands in "other" and is dropped
        from the response. This avoids a Module-7-renamed-it-tomorrow
        regression silently zeroing out gmail counts.
        """
        self.initialize()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=int(window_hours))).isoformat()

        # Single CASE expression so PG can use the index on (organization_id, ts).
        # event_type LIKE patterns expanded explicitly so the tier of which-kind
        # is auditable from the SQL alone — no Python pre/post-processing.
        # Note: psycopg3 requires literal `%` to be doubled in any query that
        # also carries `%s` placeholders, otherwise the parser tries to read
        # the `%` as the start of a placeholder name.
        kind_expr = (
            "CASE "
            "  WHEN source LIKE 'gmail%%' OR event_type LIKE 'gmail\\_%%' ESCAPE '\\' THEN 'gmail' "
            "  WHEN source LIKE 'slack%%' OR event_type LIKE 'slack\\_%%' ESCAPE '\\' THEN 'slack' "
            "  WHEN source LIKE 'teams%%' OR event_type LIKE 'teams\\_%%' ESCAPE '\\' THEN 'teams' "
            "  WHEN event_type LIKE 'erp\\_%%' ESCAPE '\\' OR source LIKE 'erp\\_%%' ESCAPE '\\' THEN 'erp' "
            "  ELSE 'other' "
            "END"
        )

        aggregate_sql = (
            f"SELECT {kind_expr} AS kind, "
            "       COUNT(*) AS events, "
            "       COUNT(*) FILTER ("
            "          WHERE event_type LIKE '%%failed%%' OR event_type LIKE '%%error%%'"
            "       ) AS errors, "
            "       MAX(ts) AS latest_event_at "
            "FROM audit_events "
            "WHERE organization_id = %s AND ts >= %s "
            "GROUP BY kind"
        )

        # Latest-error per kind via DISTINCT ON. Only events where
        # event_type itself signals failure count — we want the actual
        # error row, not the latest event of any sort.
        latest_error_sql = (
            f"SELECT DISTINCT ON (kind_col) "
            f"       {kind_expr} AS kind_col, "
            "        ts, event_type, source, payload_json "
            "FROM audit_events "
            "WHERE organization_id = %s AND ts >= %s "
            "  AND (event_type LIKE '%%failed%%' OR event_type LIKE '%%error%%') "
            f"ORDER BY kind_col, ts DESC"
        )

        webhook_sql = (
            "SELECT status, COUNT(*) AS n "
            "FROM webhook_deliveries "
            "WHERE organization_id = %s AND attempted_at >= %s "
            "GROUP BY status"
        )

        by_kind: Dict[str, Dict[str, Any]] = {
            k: {"events": 0, "errors": 0, "latest_event_at": None}
            for k in ("gmail", "slack", "teams", "erp")
        }
        latest_error_by_kind: Dict[str, Dict[str, Any]] = {}
        webhooks = {"delivered": 0, "failed": 0, "retrying": 0}

        with self.connect() as conn:
            cur = conn.cursor()

            cur.execute(aggregate_sql, (organization_id, cutoff))
            for row in cur.fetchall():
                rd = dict(row)
                kind = str(rd.get("kind") or "other")
                if kind == "other":
                    continue
                by_kind[kind] = {
                    "events": int(rd.get("events") or 0),
                    "errors": int(rd.get("errors") or 0),
                    "latest_event_at": rd.get("latest_event_at"),
                }

            cur.execute(latest_error_sql, (organization_id, cutoff))
            for row in cur.fetchall():
                rd = dict(row)
                kind = str(rd.get("kind_col") or "other")
                if kind == "other":
                    continue
                payload = rd.get("payload_json")
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except Exception:
                        payload = None
                latest_error_by_kind[kind] = {
                    "ts": rd.get("ts"),
                    "event_type": rd.get("event_type"),
                    "source": rd.get("source"),
                    "payload_json": payload,
                }

            cur.execute(webhook_sql, (organization_id, cutoff))
            for row in cur.fetchall():
                rd = dict(row)
                status = str(rd.get("status") or "").lower()
                if status == "success":
                    webhooks["delivered"] = int(rd.get("n") or 0)
                elif status == "failed":
                    webhooks["failed"] = int(rd.get("n") or 0)
                elif status == "retrying":
                    webhooks["retrying"] = int(rd.get("n") or 0)

        return {
            "window_hours": int(window_hours),
            "by_kind": by_kind,
            "latest_error_by_kind": latest_error_by_kind,
            "webhooks": webhooks,
        }
        return int(deleted)

    # ------------------------------------------------------------------
    # Webhook delivery log (Module 7 v1 Pass 3)
    # ------------------------------------------------------------------

    def insert_webhook_delivery(
        self,
        *,
        organization_id: str,
        webhook_subscription_id: str,
        event_type: str,
        request_url: str,
        status: str,
        attempt_number: int = 1,
        audit_event_id: Optional[str] = None,
        http_status_code: Optional[int] = None,
        response_snippet: Optional[str] = None,
        error_message: Optional[str] = None,
        request_signature_prefix: Optional[str] = None,
        payload_size_bytes: Optional[int] = None,
        duration_ms: Optional[int] = None,
        next_retry_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record a single webhook delivery attempt.

        One row per attempt — retries insert NEW rows so the chain
        of attempts for a given (webhook, event) pair is recoverable.
        Returns the inserted row.
        """
        self.initialize()
        import uuid as _uuid

        delivery_id = f"WHD-{_uuid.uuid4().hex[:24]}"
        # Truncate response_snippet defensively — a misbehaving
        # subscriber's 5MB response body should not bloat our table.
        if response_snippet and len(response_snippet) > 2000:
            response_snippet = response_snippet[:2000] + "...[truncated]"
        if error_message and len(error_message) > 1000:
            error_message = error_message[:1000] + "...[truncated]"

        sql = (
            """
            INSERT INTO webhook_deliveries
            (id, organization_id, webhook_subscription_id, audit_event_id,
             event_type, attempt_number, status, http_status_code,
             response_snippet, error_message, request_url,
             request_signature_prefix, payload_size_bytes, duration_ms,
             attempted_at, next_retry_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
        )
        attempted_at = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    delivery_id, organization_id, webhook_subscription_id,
                    audit_event_id, event_type, int(attempt_number), status,
                    http_status_code, response_snippet, error_message,
                    request_url, request_signature_prefix, payload_size_bytes,
                    duration_ms, attempted_at, next_retry_at,
                ),
            )
            conn.commit()
        return {
            "id": delivery_id,
            "organization_id": organization_id,
            "webhook_subscription_id": webhook_subscription_id,
            "audit_event_id": audit_event_id,
            "event_type": event_type,
            "attempt_number": int(attempt_number),
            "status": status,
            "http_status_code": http_status_code,
            "request_url": request_url,
            "attempted_at": attempted_at,
            "next_retry_at": next_retry_at,
        }

    def list_webhook_deliveries(
        self,
        *,
        organization_id: str,
        webhook_subscription_id: Optional[str] = None,
        audit_event_id: Optional[str] = None,
        status: Optional[str] = None,
        from_ts: Optional[str] = None,
        to_ts: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Read recent webhook deliveries with filter/scope.

        Tenant-scoped by ``organization_id``. Newest-first.
        """
        self.initialize()
        clauses = ["organization_id = %s"]
        params: List[Any] = [organization_id]
        if webhook_subscription_id:
            clauses.append("webhook_subscription_id = %s")
            params.append(webhook_subscription_id)
        if audit_event_id:
            clauses.append("audit_event_id = %s")
            params.append(audit_event_id)
        if status:
            clauses.append("status = %s")
            params.append(status)
        if from_ts:
            clauses.append("attempted_at >= %s")
            params.append(from_ts)
        if to_ts:
            clauses.append("attempted_at <= %s")
            params.append(to_ts)

        safe_limit = max(1, min(int(limit or 50), 500))
        where_clause = " AND ".join(clauses)
        sql = (
            f"SELECT * FROM webhook_deliveries WHERE {where_clause} "
            f"ORDER BY attempted_at DESC, id DESC LIMIT %s"
        )
        params.append(safe_limit)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Durable agent retry jobs
    # ------------------------------------------------------------------

    def reap_completed_agent_retry_jobs(self, *, older_than_days: int = 90) -> int:
        """Delete agent_retry_jobs rows that are terminal + older than N days.

        agent_retry_jobs.idempotency_key has a UNIQUE index — if the
        table grows forever, both the index and the
        get_agent_retry_job_by_key lookup degrade. Old completed /
        failed / dead-letter rows have no business value (the actual
        outcome is in audit_events, which IS append-only by design),
        so we can safely drop them after a generous retention window.

        Default 90 days mirrors the longest reasonable lookback for
        debugging an incident; older than that, the operational value
        is gone. Returns the number of rows deleted.
        """
        self.initialize()
        days = max(1, int(older_than_days))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        # Only delete terminal rows so an in-flight retry that's been
        # paused for a long time is never collected. completed_at IS
        # NOT NULL implies the row reached a terminal state (success,
        # exhausted, dead-letter).
        sql = (
            """
            DELETE FROM agent_retry_jobs
             WHERE completed_at IS NOT NULL
               AND completed_at < %s
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (cutoff,))
            deleted = cur.rowcount or 0
            conn.commit()
        if deleted:
            logger.info(
                "[ap_store] reaped %d completed agent_retry_jobs older than %d days",
                deleted, days,
            )
        return int(deleted)

    def create_agent_retry_job(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        job_id = str(payload.get("id") or f"ARJ-{uuid.uuid4().hex}")
        _arj_org_id = payload.get("organization_id")
        if not _arj_org_id:
            logger.warning("organization_id missing in create_agent_retry_job payload (job_id=%s), falling back to 'default'", job_id)
        _arj_org_id = str(_arj_org_id or "default")
        idem_key = payload.get("idempotency_key")
        if idem_key:
            existing = self.get_agent_retry_job_by_key(str(idem_key))
            if existing:
                return existing

        sql = """
            INSERT INTO agent_retry_jobs
            (id, organization_id, ap_item_id, gmail_id, job_type, status,
             retry_count, max_retries, next_retry_at, last_attempt_at, last_error,
             payload_json, result_json, idempotency_key, correlation_id,
             locked_by, locked_at, created_at, updated_at, completed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    job_id,
                    _arj_org_id,
                    str(payload.get("ap_item_id") or ""),
                    payload.get("gmail_id"),
                    str(payload.get("job_type") or "erp_post_retry"),
                    str(payload.get("status") or "pending"),
                    int(payload.get("retry_count") or 0),
                    int(payload.get("max_retries") or 3),
                    str(payload.get("next_retry_at") or now),
                    payload.get("last_attempt_at"),
                    payload.get("last_error"),
                    json.dumps(payload.get("payload_json") or payload.get("payload") or {}),
                    json.dumps(payload.get("result_json") or payload.get("result") or {}),
                    idem_key,
                    payload.get("correlation_id"),
                    payload.get("locked_by"),
                    payload.get("locked_at"),
                    payload.get("created_at") or now,
                    payload.get("updated_at") or now,
                    payload.get("completed_at"),
                ),
            )
            conn.commit()
        return self.get_agent_retry_job(job_id) or {"id": job_id}

    def get_agent_retry_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = "SELECT * FROM agent_retry_jobs WHERE id = %s"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (job_id,))
            row = cur.fetchone()
        return self._deserialize_agent_retry_job(dict(row)) if row else None

    def get_agent_retry_job_by_key(self, idempotency_key: Optional[str]) -> Optional[Dict[str, Any]]:
        if not idempotency_key:
            return None
        self.initialize()
        sql = "SELECT * FROM agent_retry_jobs WHERE idempotency_key = %s"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (idempotency_key,))
            row = cur.fetchone()
        return self._deserialize_agent_retry_job(dict(row)) if row else None

    def get_active_agent_retry_job(
        self,
        organization_id: str,
        ap_item_id: str,
        *,
        job_type: str = "erp_post_retry",
    ) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = (
            """
            SELECT * FROM agent_retry_jobs
            WHERE organization_id = %s AND ap_item_id = %s AND job_type = %s
              AND status IN ('pending', 'running')
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, ap_item_id, job_type))
            row = cur.fetchone()
        return self._deserialize_agent_retry_job(dict(row)) if row else None

    def list_agent_retry_jobs(
        self,
        organization_id: str,
        *,
        ap_item_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        self.initialize()
        safe_limit = max(1, min(int(limit or 100), 1000))
        if ap_item_id and status:
            sql = (
                """
                SELECT * FROM agent_retry_jobs
                WHERE organization_id = %s AND ap_item_id = %s AND status = %s
                ORDER BY created_at DESC LIMIT %s
                """
            )
            params = (organization_id, ap_item_id, status, safe_limit)
        elif ap_item_id:
            sql = (
                """
                SELECT * FROM agent_retry_jobs
                WHERE organization_id = %s AND ap_item_id = %s
                ORDER BY created_at DESC LIMIT %s
                """
            )
            params = (organization_id, ap_item_id, safe_limit)
        elif status:
            sql = (
                """
                SELECT * FROM agent_retry_jobs
                WHERE organization_id = %s AND status = %s
                ORDER BY created_at DESC LIMIT %s
                """
            )
            params = (organization_id, status, safe_limit)
        else:
            sql = (
                "SELECT * FROM agent_retry_jobs WHERE organization_id = %s ORDER BY created_at DESC LIMIT %s"
            )
            params = (organization_id, safe_limit)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [self._deserialize_agent_retry_job(dict(row)) for row in rows]

    def list_due_agent_retry_jobs(
        self,
        *,
        organization_id: Optional[str] = None,
        job_type: Optional[str] = None,
        limit: int = 25,
        now_iso: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        self.initialize()
        safe_limit = max(1, min(int(limit or 25), 500))
        due_at = now_iso or datetime.now(timezone.utc).isoformat()
        if organization_id and job_type:
            sql = (
                """
                SELECT * FROM agent_retry_jobs
                WHERE organization_id = %s AND job_type = %s AND status = 'pending' AND next_retry_at <= %s
                ORDER BY next_retry_at ASC LIMIT %s
                """
            )
            params = (organization_id, job_type, due_at, safe_limit)
        elif organization_id:
            sql = (
                """
                SELECT * FROM agent_retry_jobs
                WHERE organization_id = %s AND status = 'pending' AND next_retry_at <= %s
                ORDER BY next_retry_at ASC LIMIT %s
                """
            )
            params = (organization_id, due_at, safe_limit)
        elif job_type:
            sql = (
                """
                SELECT * FROM agent_retry_jobs
                WHERE job_type = %s AND status = 'pending' AND next_retry_at <= %s
                ORDER BY next_retry_at ASC LIMIT %s
                """
            )
            params = (job_type, due_at, safe_limit)
        else:
            sql = (
                """
                SELECT * FROM agent_retry_jobs
                WHERE status = 'pending' AND next_retry_at <= %s
                ORDER BY next_retry_at ASC LIMIT %s
                """
            )
            params = (due_at, safe_limit)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [self._deserialize_agent_retry_job(dict(row)) for row in rows]

    def claim_agent_retry_job(self, job_id: str, *, worker_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        sql = (
            """
            UPDATE agent_retry_jobs
            SET status = 'running',
                retry_count = COALESCE(retry_count, 0) + 1,
                locked_by = %s,
                locked_at = %s,
                last_attempt_at = %s,
                updated_at = %s
            WHERE id = %s AND status = 'pending' AND next_retry_at <= %s
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (worker_id, now, now, now, job_id, now))
            conn.commit()
            if cur.rowcount <= 0:
                return None
        return self.get_agent_retry_job(job_id)

    def complete_agent_retry_job(
        self,
        job_id: str,
        *,
        status: str = "completed",
        result: Optional[Dict[str, Any]] = None,
        last_error: Optional[str] = None,
    ) -> bool:
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        sql = (
            """
            UPDATE agent_retry_jobs
            SET status = %s, result_json = %s, last_error = %s, completed_at = %s, updated_at = %s
            WHERE id = %s
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    status,
                    json.dumps(result or {}),
                    last_error,
                    now,
                    now,
                    job_id,
                ),
            )
            conn.commit()
            return cur.rowcount > 0

    def reschedule_agent_retry_job(
        self,
        job_id: str,
        *,
        next_retry_at: str,
        last_error: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
        status: str = "pending",
    ) -> bool:
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        sql = (
            """
            UPDATE agent_retry_jobs
            SET status = %s, next_retry_at = %s, last_error = %s, result_json = %s, updated_at = %s
            WHERE id = %s
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    status,
                    next_retry_at,
                    last_error,
                    json.dumps(result or {}),
                    now,
                    job_id,
                ),
            )
            conn.commit()
            return cur.rowcount > 0

    def _deserialize_agent_retry_job(self, row: Dict[str, Any]) -> Dict[str, Any]:
        for key in ("payload_json", "result_json"):
            value = row.get(key)
            if isinstance(value, str):
                try:
                    row[key] = json.loads(value)
                except json.JSONDecodeError:
                    row[key] = {}
        if "payload_json" in row and "payload" not in row:
            row["payload"] = row.get("payload_json") or {}
        if "result_json" in row and "result" not in row:
            row["result"] = row.get("result_json") or {}
        return row

    # ------------------------------------------------------------------
    # Approvals
    # ------------------------------------------------------------------

    def save_approval(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.initialize()
        import uuid
        now = datetime.now(timezone.utc).isoformat()
        approval_id = payload.get("id") or f"APR-{uuid.uuid4().hex}"

        sql = """
            INSERT INTO approvals
            (id, ap_item_id, channel_id, message_ts, source_channel, source_message_ref,
             decision_idempotency_key, decision_payload, status, approved_by, approved_at,
             rejected_by, rejected_at, rejection_reason, organization_id, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ap_item_id, channel_id, message_ts)
            DO UPDATE SET status = EXCLUDED.status,
                          source_channel = EXCLUDED.source_channel,
                          source_message_ref = EXCLUDED.source_message_ref,
                          decision_idempotency_key = EXCLUDED.decision_idempotency_key,
                          decision_payload = EXCLUDED.decision_payload,
                          approved_by = EXCLUDED.approved_by,
                          approved_at = EXCLUDED.approved_at,
                          rejected_by = EXCLUDED.rejected_by,
                          rejected_at = EXCLUDED.rejected_at,
                          rejection_reason = EXCLUDED.rejection_reason
        """

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                approval_id,
                payload.get("ap_item_id"),
                payload.get("channel_id"),
                payload.get("message_ts"),
                payload.get("source_channel"),
                payload.get("source_message_ref"),
                payload.get("decision_idempotency_key"),
                json.dumps(payload.get("decision_payload") or {}),
                payload.get("status") or "pending",
                payload.get("approved_by"),
                payload.get("approved_at"),
                payload.get("rejected_by"),
                payload.get("rejected_at"),
                payload.get("rejection_reason"),
                payload.get("organization_id"),
                payload.get("created_at") or now,
            ))
            conn.commit()
        return {"id": approval_id, **payload}

    def get_latest_approval(self, ap_item_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = (
            "SELECT * FROM approvals WHERE ap_item_id = %s ORDER BY created_at DESC LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (ap_item_id,))
            row = cur.fetchone()
        return dict(row) if row else None

    def get_approval_by_decision_key(self, ap_item_id: str, decision_idempotency_key: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = (
            "SELECT * FROM approvals WHERE ap_item_id = %s AND decision_idempotency_key = %s ORDER BY created_at DESC LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (ap_item_id, decision_idempotency_key))
            row = cur.fetchone()
        return dict(row) if row else None

    def update_approval_status(
        self,
        ap_item_id: str,
        status: str,
        approved_by: Optional[str] = None,
        approved_at: Optional[str] = None,
        rejected_by: Optional[str] = None,
        rejected_at: Optional[str] = None,
        rejection_reason: Optional[str] = None,
    ) -> None:
        self.initialize()
        latest = self.get_latest_approval(ap_item_id)
        if not latest:
            return
        sql = (
            """
            UPDATE approvals
            SET status = %s, approved_by = %s, approved_at = %s, rejected_by = %s,
                rejected_at = %s, rejection_reason = %s
            WHERE id = %s
            """
        )
        params = (
            status,
            approved_by,
            approved_at,
            rejected_by,
            rejected_at,
            rejection_reason,
            latest["id"],
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()

    def list_approvals(self, organization_id: str, status: Optional[str] = None, limit: int = 1000) -> List[Dict[str, Any]]:
        self.initialize()
        if status:
            sql = (
                "SELECT * FROM approvals WHERE organization_id = %s AND status = %s ORDER BY created_at DESC LIMIT %s"
            )
            params = (organization_id, status, limit)
        else:
            sql = (
                "SELECT * FROM approvals WHERE organization_id = %s ORDER BY created_at DESC LIMIT %s"
            )
            params = (organization_id, limit)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def list_approvals_by_item(self, ap_item_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        self.initialize()
        sql = (
            "SELECT * FROM approvals WHERE ap_item_id = %s ORDER BY created_at DESC LIMIT %s"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (ap_item_id, limit))
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def list_ap_audit_events_by_thread(self, organization_id: str, thread_id: str) -> List[Dict[str, Any]]:
        self.initialize()
        sql = (
            """
            SELECT ae.* FROM audit_events ae
            JOIN ap_items ai
                 ON ae.box_type = 'ap_item' AND ae.box_id = ai.id
            WHERE ai.organization_id = %s AND ai.thread_id = %s
            ORDER BY ae.ts ASC
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, thread_id))
            rows = cur.fetchall()
        return [self._deserialize_audit_event(dict(row)) for row in rows]

    def _deserialize_audit_event(self, row: Dict[str, Any]) -> Dict[str, Any]:
        payload = row.get("payload_json")
        refs = row.get("external_refs")
        if isinstance(payload, str):
            try:
                row["payload_json"] = json.loads(payload)
            except json.JSONDecodeError:
                row["payload_json"] = {}
        if isinstance(refs, str):
            try:
                row["external_refs"] = json.loads(refs)
            except json.JSONDecodeError:
                row["external_refs"] = {}
        if "prev_state" in row and "from_state" not in row:
            row["from_state"] = row.get("prev_state")
        if "new_state" in row and "to_state" not in row:
            row["to_state"] = row.get("new_state")
        return row

    # ------------------------------------------------------------------ #
    # Ancillary query helpers (vendor spending, upcoming due)             #
    # ------------------------------------------------------------------ #

    def get_ap_items_by_vendor(
        self, organization_id: str, vendor_name: str, days: int = 90, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """AP items for a vendor within a date window."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).isoformat()
        sql = (
            "SELECT * FROM ap_items WHERE organization_id = %s AND vendor_name = %s "
            "AND created_at >= %s ORDER BY created_at DESC LIMIT %s"
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (organization_id, vendor_name, cutoff, limit))
                return [dict(r) for r in cur.fetchall()]
        except Exception as exc:
            logger.warning("[APStore] get_ap_items_by_vendor failed: %s", exc)
            return []

    def get_spending_by_vendor(
        self, organization_id: str, days: int = 30
    ) -> Dict[str, float]:
        """Spending grouped by vendor for a time window. Returns {vendor: total}."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).isoformat()
        sql = (
            "SELECT vendor_name, SUM(amount) as total "
            "FROM ap_items WHERE organization_id = %s "
            "AND created_at >= %s AND amount IS NOT NULL "
            "GROUP BY vendor_name ORDER BY total DESC"
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (organization_id, cutoff))
                return {row[0]: float(row[1] or 0) for row in cur.fetchall() if row[0]}
        except Exception as exc:
            logger.warning("[APStore] get_spending_by_vendor failed: %s", exc)
            return {}

    def get_spending_for_period(
        self, organization_id: str, days_ago_start: int, days_ago_end: int
    ) -> Dict[str, float]:
        """Spending grouped by vendor for a specific past period."""
        now = datetime.now(timezone.utc)
        start = (now - timedelta(days=max(1, days_ago_start))).isoformat()
        end = (now - timedelta(days=max(0, days_ago_end))).isoformat()
        sql = (
            "SELECT vendor_name, SUM(amount) as total "
            "FROM ap_items WHERE organization_id = %s "
            "AND created_at >= %s AND created_at < %s AND amount IS NOT NULL "
            "GROUP BY vendor_name ORDER BY total DESC"
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (organization_id, start, end))
                return {row[0]: float(row[1] or 0) for row in cur.fetchall() if row[0]}
        except Exception as exc:
            logger.warning("[APStore] get_spending_for_period failed: %s", exc)
            return {}

    def get_upcoming_due(
        self, organization_id: str, days: int = 7
    ) -> List[Dict[str, Any]]:
        """AP items with due_date within N days that aren't yet posted."""
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        horizon = (now + timedelta(days=max(1, days))).strftime("%Y-%m-%d")
        sql = (
            "SELECT * FROM ap_items WHERE organization_id = %s "
            "AND due_date IS NOT NULL AND due_date >= %s AND due_date <= %s "
            "AND state NOT IN ('posted_to_erp', 'rejected', 'closed') "
            "ORDER BY due_date ASC LIMIT 50"
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (organization_id, today, horizon))
                return [dict(r) for r in cur.fetchall()]
        except Exception as exc:
            logger.warning("[APStore] get_upcoming_due failed: %s", exc)
            return []
