"""AP-domain data-access mixin for ClearledgrDB.

``APStore`` is a **mixin class** -- it has no ``__init__`` of its own and
expects the concrete class that inherits it to provide:

* ``self.connect()``      -- returns a DB connection (context manager)
* ``self._prepare_sql()`` -- adapts ``?`` placeholders for the active engine
* ``self.initialize()``   -- ensures tables exist
* ``self._decode_json()`` -- safely parses a JSON string or returns ``{}``
* ``self._parse_iso()``   -- parses an ISO-8601 string into a datetime
* ``self._safe_float()``  -- safe float coercion
* ``self._exception_severity_rank()`` -- maps severity label to int rank
* ``self.use_postgres``   -- bool flag for Postgres vs SQLite dialect

All methods are copied verbatim from ``clearledgr/core/database.py`` so that
``ClearledgrDB(APStore, ...)`` inherits them without any behavioural change.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

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
    })

    # ------------------------------------------------------------------
    # AP items
    # ------------------------------------------------------------------

    def create_ap_item(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.initialize()
        import uuid
        now = datetime.now(timezone.utc).isoformat()
        item_id = payload.get("id") or f"AP-{uuid.uuid4().hex}"
        metadata = json.dumps(payload.get("metadata") or {})
        # Serialize field_confidences to JSON if provided as a dict
        raw_fc = payload.get("field_confidences")
        field_confidences_json: Optional[str] = None
        if isinstance(raw_fc, dict):
            field_confidences_json = json.dumps(raw_fc)
        elif isinstance(raw_fc, str):
            field_confidences_json = raw_fc

        sql = self._prepare_sql("""
            INSERT INTO ap_items
            (id, invoice_key, thread_id, message_id, subject, sender, vendor_name, amount, currency,
            invoice_number, invoice_date, due_date, state, confidence, approval_required,
             approved_by, approved_at, rejected_by, rejected_at, rejection_reason,
             supersedes_ap_item_id, supersedes_invoice_key, superseded_by_ap_item_id, resubmission_reason, erp_reference,
             erp_posted_at, workflow_id, run_id, approval_surface, approval_policy_version, post_attempted_at,
             last_error, organization_id, user_id, created_at, updated_at, metadata, field_confidences)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """)
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
            payload.get("organization_id"),
            payload.get("user_id"),
            now,
            now,
            metadata,
            field_confidences_json,
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

        # Validate column names against whitelist
        invalid_cols = set(kwargs.keys()) - self._AP_ITEM_ALLOWED_COLUMNS
        if invalid_cols:
            raise ValueError(f"Disallowed columns in update_ap_item: {invalid_cols}")

        now = datetime.now(timezone.utc).isoformat()
        kwargs["updated_at"] = now
        if "metadata" in kwargs and isinstance(kwargs["metadata"], dict):
            kwargs["metadata"] = json.dumps(kwargs["metadata"])  # type: ignore

        # --- State machine enforcement ---
        prev_state: Optional[str] = None
        new_state: Optional[str] = kwargs.get("state")
        current: Optional[Dict[str, Any]] = None
        if new_state is not None:
            from clearledgr.core.ap_states import transition_or_raise, normalize_state

            current = self.get_ap_item(ap_item_id)
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

        set_clause = ", ".join(f"{k} = ?" for k in kwargs.keys())
        sql = self._prepare_sql(f"UPDATE ap_items SET {set_clause} WHERE id = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (*kwargs.values(), ap_item_id))

            # --- Atomic audit write on state transitions ---
            if prev_state is not None and new_state is not None:
                import uuid as _uuid

                org_id = kwargs.get("organization_id") or (
                    current.get("organization_id") if current else None
                ) or ""
                audit_sql = self._prepare_sql(
                    """INSERT INTO audit_events
                    (id, ap_item_id, event_type, prev_state, new_state,
                     actor_type, actor_id, payload_json, source, correlation_id,
                     workflow_id, run_id, decision_reason, organization_id, ts)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
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
                        ap_item_id,
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
            return cur.rowcount > 0

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
        sql_select = self._prepare_sql("SELECT metadata FROM ap_items WHERE id = ?")
        sql_update = self._prepare_sql(
            "UPDATE ap_items SET metadata = ?, updated_at = ? WHERE id = ?"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            # BEGIN EXCLUSIVE serialises concurrent read-modify-write on SQLite
            if not self.use_postgres:
                cur.execute("BEGIN EXCLUSIVE")
            cur.execute(sql_select, (ap_item_id,))
            row = cur.fetchone()
            if not row:
                conn.rollback() if not self.use_postgres else None
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
            self.append_ap_audit_event(
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
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM ap_items WHERE id = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (ap_item_id,))
            row = cur.fetchone()
        return dict(row) if row else None

    # ---- Invoice-status bridge methods ----
    # invoice_workflow.py uses gmail_id-based methods (save_invoice_status,
    # update_invoice_status, get_invoice_status).  These bridge to the
    # canonical ap_items table using thread_id = gmail_id.

    def get_invoice_status(self, gmail_id: str) -> Optional[Dict[str, Any]]:
        """Look up an AP item by its Gmail thread/message ID."""
        self.initialize()
        sql = self._prepare_sql(
            "SELECT * FROM ap_items WHERE thread_id = ? ORDER BY created_at DESC LIMIT 1"
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
        ap_item_id: str | None = None,
        max_retries: int = 5,
    ) -> str:
        """Insert a notification into the retry queue."""
        self.initialize()
        import uuid as _uuid
        now = datetime.now(timezone.utc).isoformat()
        notif_id = f"notif-{_uuid.uuid4().hex[:12]}"
        sql = self._prepare_sql("""
            INSERT INTO pending_notifications
            (id, organization_id, ap_item_id, channel, payload_json,
             retry_count, max_retries, next_retry_at, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 0, ?, ?, 'pending', ?, ?)
        """)
        with self.connect() as conn:
            conn.cursor().execute(sql, (
                notif_id, organization_id, ap_item_id, channel,
                json.dumps(payload), max_retries, now, now, now,
            ))
            conn.commit()
        return notif_id

    def get_pending_notifications(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return notifications that are due for retry."""
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        sql = self._prepare_sql(
            "SELECT * FROM pending_notifications "
            "WHERE status = 'pending' AND next_retry_at <= ? "
            "ORDER BY next_retry_at ASC LIMIT ?"
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
        sql = self._prepare_sql(
            "UPDATE pending_notifications SET status = 'sent', updated_at = ? WHERE id = ?"
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
        sql_read = self._prepare_sql(
            "SELECT retry_count, max_retries FROM pending_notifications WHERE id = ?"
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
            else:
                status = "pending"
                idx = min(retry_count - 1, len(backoff_seconds) - 1)
                from datetime import timedelta
                next_retry = (now + timedelta(seconds=backoff_seconds[idx])).isoformat()
            sql_update = self._prepare_sql(
                "UPDATE pending_notifications SET retry_count = ?, next_retry_at = ?, "
                "last_error = ?, status = ?, updated_at = ? WHERE id = ?"
            )
            cur.execute(sql_update, (retry_count, next_retry, error, status, now.isoformat(), notif_id))
            conn.commit()

    def get_ap_item_by_invoice_key(self, organization_id: str, invoice_key: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql(
            "SELECT * FROM ap_items WHERE organization_id = ? AND invoice_key = ?"
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
        sql = self._prepare_sql(
            "SELECT * FROM ap_items WHERE organization_id = ? AND invoice_key LIKE ? ESCAPE '\\' "
            "ORDER BY created_at DESC LIMIT ?"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, f"{prefix}%", limit))
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def get_ap_item_by_vendor_invoice(
        self, organization_id: str, vendor_name: str, invoice_number: str
    ) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql(
            "SELECT * FROM ap_items WHERE organization_id = ? AND vendor_name = ? AND invoice_number = ? "
            "ORDER BY created_at DESC LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, vendor_name, invoice_number))
            row = cur.fetchone()
        return dict(row) if row else None

    def get_rejected_ap_item_by_vendor_invoice(
        self, organization_id: str, vendor_name: str, invoice_number: str
    ) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql(
            "SELECT * FROM ap_items WHERE organization_id = ? AND vendor_name = ? AND invoice_number = ? "
            "AND state = 'rejected' ORDER BY created_at DESC LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, vendor_name, invoice_number))
            row = cur.fetchone()
        return dict(row) if row else None

    def get_ap_item_by_thread(self, organization_id: str, thread_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql(
            """
            SELECT * FROM ap_items
            WHERE organization_id = ?
              AND (
                thread_id = ?
                OR id IN (
                  SELECT ap_item_id
                  FROM ap_item_sources
                  WHERE source_type = 'gmail_thread' AND source_ref = ?
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
        sql = self._prepare_sql(
            """
            SELECT * FROM ap_items
            WHERE organization_id = ?
              AND (
                message_id = ?
                OR id IN (
                  SELECT ap_item_id
                  FROM ap_item_sources
                  WHERE source_type = 'gmail_message' AND source_ref = ?
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
        sql = self._prepare_sql(
            "SELECT * FROM ap_items WHERE organization_id = ? AND erp_reference = ? "
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
        sql = self._prepare_sql(
            "SELECT * FROM ap_items WHERE organization_id = ? AND invoice_number = ? "
            "ORDER BY created_at DESC LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, invoice_number))
            row = cur.fetchone()
        return dict(row) if row else None

    def get_ap_item_by_workflow_id(self, organization_id: str, workflow_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql(
            "SELECT * FROM ap_items WHERE organization_id = ? AND workflow_id = ? "
            "ORDER BY created_at DESC LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, workflow_id))
            row = cur.fetchone()
        return dict(row) if row else None

    def list_ap_items_by_thread(self, organization_id: str, thread_id: str) -> List[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql(
            """
            SELECT * FROM ap_items
            WHERE organization_id = ?
              AND (
                thread_id = ?
                OR id IN (
                  SELECT ap_item_id
                  FROM ap_item_sources
                  WHERE source_type = 'gmail_thread' AND source_ref = ?
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
            return self._safe_float(explicit, 0.0)

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
        limit: int = 200,
        prioritized: bool = False,
    ) -> List[Dict[str, Any]]:
        self.initialize()
        safe_limit = max(1, min(int(limit or 200), 10000))

        if prioritized:
            # Pull a larger window before in-memory priority sort so older high-severity
            # exceptions can surface ahead of recent low-risk items.
            fetch_limit = max(500, safe_limit * 8)
            if state:
                sql = self._prepare_sql(
                    "SELECT * FROM ap_items WHERE organization_id = ? AND state = ? ORDER BY created_at DESC LIMIT ?"
                )
                params = (organization_id, state, fetch_limit)
            else:
                sql = self._prepare_sql(
                    "SELECT * FROM ap_items WHERE organization_id = ? ORDER BY created_at DESC LIMIT ?"
                )
                params = (organization_id, fetch_limit)
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, params)
                rows = cur.fetchall()
            items = [dict(row) for row in rows]
            items.sort(key=self._worklist_sort_key)
            return items[:safe_limit]

        if state:
            sql = self._prepare_sql(
                "SELECT * FROM ap_items WHERE organization_id = ? AND state = ? ORDER BY created_at DESC LIMIT ?"
            )
            params = (organization_id, state, safe_limit)
        else:
            sql = self._prepare_sql(
                "SELECT * FROM ap_items WHERE organization_id = ? ORDER BY created_at DESC LIMIT ?"
            )
            params = (organization_id, safe_limit)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
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
            sql = self._prepare_sql(
                "SELECT * FROM ap_items WHERE organization_id = ? AND state = ? "
                "ORDER BY created_at DESC LIMIT ?"
            )
            params: tuple = (organization_id, state, limit)
        else:
            sql = self._prepare_sql(
                "SELECT * FROM ap_items WHERE organization_id = ? "
                "ORDER BY created_at DESC LIMIT ?"
            )
            params = (organization_id, limit)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def get_overdue_approvals(self, organization_id: str, min_hours: float) -> List[Dict[str, Any]]:
        """Return ap_items stuck in needs_approval longer than min_hours.

        Uses ``updated_at`` as a proxy for when approval was requested (set on
        every state transition, so the value at state=needs_approval represents
        the moment the item entered that state).
        """
        self.initialize()
        if self.use_postgres:
            sql = self._prepare_sql(
                "SELECT * FROM ap_items "
                "WHERE organization_id = ? AND state = 'needs_approval' "
                "AND updated_at < NOW() - INTERVAL '? hours' "
                "ORDER BY updated_at ASC LIMIT 50"
            )
            params: tuple = (organization_id, min_hours)
        else:
            sql = self._prepare_sql(
                "SELECT * FROM ap_items "
                "WHERE organization_id = ? AND state = 'needs_approval' "
                "AND datetime(updated_at) < datetime('now', ? || ' hours') "
                "ORDER BY updated_at ASC LIMIT 50"
            )
            params = (organization_id, f"-{min_hours}")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def get_pending_approver_ids(self, ap_item_id: str) -> List[str]:
        """Return Slack user IDs of pending approvers for an AP item.

        Reads ``approval_sent_to`` from the item's JSON metadata — populated by
        ``send_invoice_approval_notification`` when it dispatches the Slack DM.
        """
        self.initialize()
        sql = self._prepare_sql("SELECT metadata FROM ap_items WHERE id = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (ap_item_id,))
            row = cur.fetchone()
        if not row:
            return []
        try:
            meta = json.loads(row[0] or "{}")
            sent_to = meta.get("approval_sent_to", [])
            if isinstance(sent_to, list):
                return [str(uid) for uid in sent_to if uid]
            if isinstance(sent_to, str) and sent_to:
                return [sent_to]
        except Exception:
            pass
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

        if self.use_postgres:
            sql = self._prepare_sql(
                """
                INSERT INTO ap_item_sources
                (id, ap_item_id, source_type, source_ref, subject, sender, detected_at, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (ap_item_id, source_type, source_ref) DO NOTHING
                """
            )
        else:
            sql = self._prepare_sql(
                """
                INSERT OR IGNORE INTO ap_item_sources
                (id, ap_item_id, source_type, source_ref, subject, sender, detected_at, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            row_sql = self._prepare_sql(
                "SELECT * FROM ap_item_sources WHERE ap_item_id = ? AND source_type = ? AND source_ref = ? LIMIT 1"
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
            sql = self._prepare_sql(
                "SELECT * FROM ap_item_sources WHERE ap_item_id = ? AND source_type = ? ORDER BY detected_at ASC, created_at ASC"
            )
            params = (ap_item_id, source_type)
        else:
            sql = self._prepare_sql(
                "SELECT * FROM ap_item_sources WHERE ap_item_id = ? ORDER BY detected_at ASC, created_at ASC"
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

    def list_ap_item_sources_by_ref(self, source_type: str, source_ref: str) -> List[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql(
            "SELECT * FROM ap_item_sources WHERE source_type = ? AND source_ref = ? ORDER BY created_at DESC"
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
        sql = self._prepare_sql(
            "DELETE FROM ap_item_sources WHERE ap_item_id = ? AND source_type = ? AND source_ref = ?"
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

        if self.use_postgres:
            sql = self._prepare_sql(
                """
                INSERT INTO ap_item_context_cache (ap_item_id, context_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT (ap_item_id)
                DO UPDATE SET context_json = EXCLUDED.context_json, updated_at = EXCLUDED.updated_at
                """
            )
        else:
            sql = self._prepare_sql(
                """
                INSERT OR REPLACE INTO ap_item_context_cache (ap_item_id, context_json, updated_at)
                VALUES (?, ?, ?)
                """
            )

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (ap_item_id, json.dumps(context_json or {}), now))
            conn.commit()

    def get_ap_item_context_cache(self, ap_item_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM ap_item_context_cache WHERE ap_item_id = ?")
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

        if self.use_postgres:
            sql = self._prepare_sql("""
                INSERT INTO channel_threads
                (id, ap_item_id, channel, conversation_id, message_id, activity_id,
                 service_url, state, last_action, updated_by, reason, organization_id,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            """)
        else:
            sql = self._prepare_sql("""
                INSERT INTO channel_threads
                (id, ap_item_id, channel, conversation_id, message_id, activity_id,
                 service_url, state, last_action, updated_by, reason, organization_id,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (ap_item_id, channel, conversation_id)
                DO UPDATE SET
                    message_id = excluded.message_id,
                    activity_id = excluded.activity_id,
                    service_url = excluded.service_url,
                    state = excluded.state,
                    last_action = excluded.last_action,
                    updated_by = excluded.updated_by,
                    reason = excluded.reason,
                    updated_at = excluded.updated_at
            """)

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
        sql = self._prepare_sql(
            "SELECT * FROM channel_threads WHERE ap_item_id = ? ORDER BY updated_at DESC"
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

    def append_ap_audit_event(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        self.initialize()
        import uuid
        now = payload.get("ts") or datetime.now(timezone.utc).isoformat()
        event_id = payload.get("id") or f"EVT-{uuid.uuid4().hex}"

        if payload.get("idempotency_key"):
            existing = self.get_ap_audit_event_by_key(payload.get("idempotency_key"))
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

        sql = self._prepare_sql("""
            INSERT INTO audit_events
            (id, ap_item_id, event_type, prev_state, new_state, actor_type, actor_id,
             payload_json, external_refs, idempotency_key, source, correlation_id, workflow_id, run_id,
             decision_reason, organization_id, ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                event_id,
                payload.get("ap_item_id"),
                payload.get("event_type"),
                payload.get("from_state"),
                payload.get("to_state"),
                payload.get("actor_type"),
                payload.get("actor_id"),
                json.dumps(payload_json or {}),
                json.dumps(external_refs or {}),
                payload.get("idempotency_key"),
                payload.get("source"),
                payload.get("correlation_id"),
                payload.get("workflow_id"),
                payload.get("run_id"),
                payload.get("decision_reason") or payload.get("reason"),
                payload.get("organization_id"),
                now,
            ))
            conn.commit()
        return self.get_ap_audit_event(event_id)

    def get_ap_audit_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM audit_events WHERE id = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (event_id,))
            row = cur.fetchone()
        return self._deserialize_audit_event(dict(row)) if row else None

    def get_ap_audit_event_by_key(self, idempotency_key: Optional[str]) -> Optional[Dict[str, Any]]:
        if not idempotency_key:
            return None
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM audit_events WHERE idempotency_key = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (idempotency_key,))
            row = cur.fetchone()
        return self._deserialize_audit_event(dict(row)) if row else None

    def list_ap_audit_events(self, ap_item_id: str) -> List[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql(
            "SELECT * FROM audit_events WHERE ap_item_id = ? ORDER BY ts ASC"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (ap_item_id,))
            rows = cur.fetchall()
        return [self._deserialize_audit_event(dict(row)) for row in rows]

    def list_recent_ap_audit_events(self, organization_id: str, limit: int = 30) -> List[Dict[str, Any]]:
        """Return recent AP audit events for an organization (newest first)."""
        self.initialize()
        safe_limit = max(1, min(int(limit or 30), 500))
        sql = self._prepare_sql(
            """
            SELECT ae.*,
                   ai.vendor_name AS vendor_name,
                   ai.amount AS amount,
                   ai.currency AS currency,
                   ai.invoice_number AS invoice_number
            FROM audit_events ae
            LEFT JOIN ap_items ai ON ae.ap_item_id = ai.id
            WHERE ae.organization_id = ?
               OR (ae.organization_id IS NULL AND ai.organization_id = ?)
            ORDER BY ae.ts DESC
            LIMIT ?
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, organization_id, safe_limit))
            rows = cur.fetchall()
        return [self._deserialize_audit_event(dict(row)) for row in rows]

    # ------------------------------------------------------------------
    # Workflow runs (durable local runtime)
    # ------------------------------------------------------------------

    def create_workflow_run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        run_id = str(payload.get("id") or f"WFR-{uuid.uuid4().hex}")
        sql = self._prepare_sql("""
            INSERT INTO workflow_runs
            (id, workflow_name, workflow_type, organization_id, ap_item_id, status,
             runtime_backend, task_queue, input_json, result_json, error_json, metadata_json,
             created_at, started_at, completed_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    run_id,
                    str(payload.get("workflow_name") or ""),
                    payload.get("workflow_type"),
                    str(payload.get("organization_id") or "default"),
                    payload.get("ap_item_id"),
                    str(payload.get("status") or "queued"),
                    payload.get("runtime_backend") or "local_db",
                    payload.get("task_queue"),
                    json.dumps(payload.get("input_json") or payload.get("input") or {}),
                    json.dumps(payload.get("result_json") or payload.get("result") or {}),
                    json.dumps(payload.get("error_json") or payload.get("error") or {}),
                    json.dumps(payload.get("metadata_json") or payload.get("metadata") or {}),
                    payload.get("created_at") or now,
                    payload.get("started_at"),
                    payload.get("completed_at"),
                    payload.get("updated_at") or now,
                ),
            )
            conn.commit()
        return self.get_workflow_run(run_id) or {"id": run_id}

    _WORKFLOW_RUN_ALLOWED_COLUMNS = frozenset({
        "status", "current_step", "ap_item_id", "started_at",
        "input", "input_json", "result", "result_json",
        "error", "error_json", "metadata", "metadata_json",
        "completed_at", "updated_at",
    })

    def update_workflow_run(self, workflow_run_id: str, **kwargs: Any) -> bool:
        self.initialize()
        if not kwargs:
            return False
        bad_keys = set(kwargs.keys()) - self._WORKFLOW_RUN_ALLOWED_COLUMNS
        if bad_keys:
            raise ValueError(f"Disallowed columns for workflow_run update: {bad_keys}")
        payload = dict(kwargs)
        for key in ("input_json", "result_json", "error_json", "metadata_json"):
            if key in payload and isinstance(payload[key], dict):
                payload[key] = json.dumps(payload[key])
        if "input" in payload and "input_json" not in payload:
            payload["input_json"] = json.dumps(payload.pop("input") or {})
        if "result" in payload and "result_json" not in payload:
            payload["result_json"] = json.dumps(payload.pop("result") or {})
        if "error" in payload and "error_json" not in payload:
            payload["error_json"] = json.dumps(payload.pop("error") or {})
        if "metadata" in payload and "metadata_json" not in payload:
            payload["metadata_json"] = json.dumps(payload.pop("metadata") or {})
        payload["updated_at"] = payload.get("updated_at") or datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in payload.keys())
        sql = self._prepare_sql(f"UPDATE workflow_runs SET {set_clause} WHERE id = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (*payload.values(), workflow_run_id))
            conn.commit()
            return cur.rowcount > 0

    def get_workflow_run(self, workflow_run_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM workflow_runs WHERE id = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (workflow_run_id,))
            row = cur.fetchone()
        return self._deserialize_workflow_run(dict(row)) if row else None

    def list_workflow_runs(
        self,
        organization_id: str,
        *,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        self.initialize()
        safe_limit = max(1, min(int(limit or 100), 1000))
        if status:
            sql = self._prepare_sql(
                "SELECT * FROM workflow_runs WHERE organization_id = ? AND status = ? ORDER BY created_at DESC LIMIT ?"
            )
            params = (organization_id, status, safe_limit)
        else:
            sql = self._prepare_sql(
                "SELECT * FROM workflow_runs WHERE organization_id = ? ORDER BY created_at DESC LIMIT ?"
            )
            params = (organization_id, safe_limit)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [self._deserialize_workflow_run(dict(row)) for row in rows]

    # ------------------------------------------------------------------
    # Durable agent retry jobs
    # ------------------------------------------------------------------

    def create_agent_retry_job(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        job_id = str(payload.get("id") or f"ARJ-{uuid.uuid4().hex}")
        idem_key = payload.get("idempotency_key")
        if idem_key:
            existing = self.get_agent_retry_job_by_key(str(idem_key))
            if existing:
                return existing

        sql = self._prepare_sql("""
            INSERT INTO agent_retry_jobs
            (id, organization_id, ap_item_id, gmail_id, job_type, status,
             retry_count, max_retries, next_retry_at, last_attempt_at, last_error,
             payload_json, result_json, idempotency_key, correlation_id,
             locked_by, locked_at, created_at, updated_at, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    job_id,
                    str(payload.get("organization_id") or "default"),
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
        sql = self._prepare_sql("SELECT * FROM agent_retry_jobs WHERE id = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (job_id,))
            row = cur.fetchone()
        return self._deserialize_agent_retry_job(dict(row)) if row else None

    def get_agent_retry_job_by_key(self, idempotency_key: Optional[str]) -> Optional[Dict[str, Any]]:
        if not idempotency_key:
            return None
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM agent_retry_jobs WHERE idempotency_key = ?")
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
        sql = self._prepare_sql(
            """
            SELECT * FROM agent_retry_jobs
            WHERE organization_id = ? AND ap_item_id = ? AND job_type = ?
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
            sql = self._prepare_sql(
                """
                SELECT * FROM agent_retry_jobs
                WHERE organization_id = ? AND ap_item_id = ? AND status = ?
                ORDER BY created_at DESC LIMIT ?
                """
            )
            params = (organization_id, ap_item_id, status, safe_limit)
        elif ap_item_id:
            sql = self._prepare_sql(
                """
                SELECT * FROM agent_retry_jobs
                WHERE organization_id = ? AND ap_item_id = ?
                ORDER BY created_at DESC LIMIT ?
                """
            )
            params = (organization_id, ap_item_id, safe_limit)
        elif status:
            sql = self._prepare_sql(
                """
                SELECT * FROM agent_retry_jobs
                WHERE organization_id = ? AND status = ?
                ORDER BY created_at DESC LIMIT ?
                """
            )
            params = (organization_id, status, safe_limit)
        else:
            sql = self._prepare_sql(
                "SELECT * FROM agent_retry_jobs WHERE organization_id = ? ORDER BY created_at DESC LIMIT ?"
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
            sql = self._prepare_sql(
                """
                SELECT * FROM agent_retry_jobs
                WHERE organization_id = ? AND job_type = ? AND status = 'pending' AND next_retry_at <= ?
                ORDER BY next_retry_at ASC LIMIT ?
                """
            )
            params = (organization_id, job_type, due_at, safe_limit)
        elif organization_id:
            sql = self._prepare_sql(
                """
                SELECT * FROM agent_retry_jobs
                WHERE organization_id = ? AND status = 'pending' AND next_retry_at <= ?
                ORDER BY next_retry_at ASC LIMIT ?
                """
            )
            params = (organization_id, due_at, safe_limit)
        elif job_type:
            sql = self._prepare_sql(
                """
                SELECT * FROM agent_retry_jobs
                WHERE job_type = ? AND status = 'pending' AND next_retry_at <= ?
                ORDER BY next_retry_at ASC LIMIT ?
                """
            )
            params = (job_type, due_at, safe_limit)
        else:
            sql = self._prepare_sql(
                """
                SELECT * FROM agent_retry_jobs
                WHERE status = 'pending' AND next_retry_at <= ?
                ORDER BY next_retry_at ASC LIMIT ?
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
        sql = self._prepare_sql(
            """
            UPDATE agent_retry_jobs
            SET status = 'running',
                retry_count = COALESCE(retry_count, 0) + 1,
                locked_by = ?,
                locked_at = ?,
                last_attempt_at = ?,
                updated_at = ?
            WHERE id = ? AND status = 'pending' AND next_retry_at <= ?
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
        sql = self._prepare_sql(
            """
            UPDATE agent_retry_jobs
            SET status = ?, result_json = ?, last_error = ?, completed_at = ?, updated_at = ?
            WHERE id = ?
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
        sql = self._prepare_sql(
            """
            UPDATE agent_retry_jobs
            SET status = ?, next_retry_at = ?, last_error = ?, result_json = ?, updated_at = ?
            WHERE id = ?
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

        if self.use_postgres:
            sql = self._prepare_sql("""
                INSERT INTO approvals
                (id, ap_item_id, channel_id, message_ts, source_channel, source_message_ref,
                 decision_idempotency_key, decision_payload, status, approved_by, approved_at,
                 rejected_by, rejected_at, rejection_reason, organization_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            """)
        else:
            sql = self._prepare_sql("""
                INSERT OR REPLACE INTO approvals
                (id, ap_item_id, channel_id, message_ts, source_channel, source_message_ref,
                 decision_idempotency_key, decision_payload, status, approved_by, approved_at,
                 rejected_by, rejected_at, rejection_reason, organization_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """)

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
        sql = self._prepare_sql(
            "SELECT * FROM approvals WHERE ap_item_id = ? ORDER BY created_at DESC LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (ap_item_id,))
            row = cur.fetchone()
        return dict(row) if row else None

    def get_approval_by_decision_key(self, ap_item_id: str, decision_idempotency_key: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql(
            "SELECT * FROM approvals WHERE ap_item_id = ? AND decision_idempotency_key = ? ORDER BY created_at DESC LIMIT 1"
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
        sql = self._prepare_sql(
            """
            UPDATE approvals
            SET status = ?, approved_by = ?, approved_at = ?, rejected_by = ?,
                rejected_at = ?, rejection_reason = ?
            WHERE id = ?
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
            sql = self._prepare_sql(
                "SELECT * FROM approvals WHERE organization_id = ? AND status = ? ORDER BY created_at DESC LIMIT ?"
            )
            params = (organization_id, status, limit)
        else:
            sql = self._prepare_sql(
                "SELECT * FROM approvals WHERE organization_id = ? ORDER BY created_at DESC LIMIT ?"
            )
            params = (organization_id, limit)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def list_approvals_by_item(self, ap_item_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql(
            "SELECT * FROM approvals WHERE ap_item_id = ? ORDER BY created_at DESC LIMIT ?"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (ap_item_id, limit))
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def list_ap_audit_events_by_thread(self, organization_id: str, thread_id: str) -> List[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql(
            """
            SELECT ae.* FROM audit_events ae
            JOIN ap_items ai ON ae.ap_item_id = ai.id
            WHERE ai.organization_id = ? AND ai.thread_id = ?
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

    def _deserialize_workflow_run(self, row: Dict[str, Any]) -> Dict[str, Any]:
        for key in ("input_json", "result_json", "error_json", "metadata_json"):
            value = row.get(key)
            if isinstance(value, str):
                try:
                    row[key] = json.loads(value)
                except json.JSONDecodeError:
                    row[key] = {}
        if "input_json" in row and "input" not in row:
            row["input"] = row.get("input_json") or {}
        if "result_json" in row and "result" not in row:
            row["result"] = row.get("result_json") or {}
        if "error_json" in row and "error" not in row:
            row["error"] = row.get("error_json") or {}
        if "metadata_json" in row and "metadata" not in row:
            row["metadata"] = row.get("metadata_json") or {}
        return row
