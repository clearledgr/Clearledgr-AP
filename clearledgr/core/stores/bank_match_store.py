"""BankMatchStore — CRUD for the ``bank_match_boxes`` BoxType.

The second BoxType in Solden's registry. AP-subordinate: every row
carries ``parent_ap_item_id`` so the bank_match Box hangs off its
parent AP item in the export graph.

State transitions go through :func:`update_bank_match_state` which
validates the edge against ``VALID_BANK_MATCH_TRANSITIONS`` and
appends a typed audit_events row. The same audit-event funnel used
for AP transitions (:func:`ApStore.append_audit_event`) records the
write with ``box_type='bank_match'`` so the export endpoint can
treat both BoxTypes uniformly.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from clearledgr.core.bank_match_states import (
    BANK_MATCH_TERMINAL_STATES,
    BankMatchState,
    CURRENT_BANK_MATCH_POLICY_VERSION,
    validate_bank_match_transition,
)

logger = logging.getLogger(__name__)


_BANK_MATCH_ALLOWED_UPDATE_COLUMNS = frozenset({
    "state",
    "payment_confirmation_id",
    "bank_statement_line_id",
    "confidence",
    "decided_by",
    "decided_at",
    "rejection_reason",
    "metadata_json",
    "updated_at",
})


class IllegalBankMatchTransitionError(ValueError):
    """Raised when a bank_match state transition violates the state machine."""


class BankMatchStore:
    """Mixin: bank_match Box CRUD. Combined into ClearledgrDB."""

    def create_bank_match(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Insert a new bank_match Box in state=proposed."""
        self.initialize()
        organization_id = payload.get("organization_id")
        parent_ap_item_id = payload.get("parent_ap_item_id")
        if not organization_id or not parent_ap_item_id:
            raise ValueError(
                "create_bank_match requires organization_id and parent_ap_item_id"
            )
        box_id = payload.get("id") or f"BM-{uuid.uuid4().hex[:16]}"
        now = datetime.now(timezone.utc).isoformat()
        state = str(payload.get("state") or BankMatchState.PROPOSED.value)
        if state != BankMatchState.PROPOSED.value:
            raise ValueError(
                f"bank_match Boxes must enter via state='proposed' (got {state!r})"
            )

        metadata_json = payload.get("metadata_json")
        if isinstance(metadata_json, dict):
            metadata_json = json.dumps(metadata_json)

        sql = """
            INSERT INTO bank_match_boxes
            (id, organization_id, parent_ap_item_id,
             payment_confirmation_id, bank_statement_line_id,
             state, confidence, proposed_by, proposed_at,
             metadata_json, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                box_id,
                organization_id,
                parent_ap_item_id,
                payload.get("payment_confirmation_id"),
                payload.get("bank_statement_line_id"),
                state,
                payload.get("confidence"),
                payload.get("proposed_by") or "bank_reconciliation_matcher",
                payload.get("proposed_at") or now,
                metadata_json,
                now,
                now,
            ))
            conn.commit()

        # Audit row — bank_match Box has the same audit treatment as
        # ap_item. policy_version defaults to the bank_match constant.
        if hasattr(self, "append_audit_event"):
            self.append_audit_event({
                "box_id": box_id,
                "box_type": "bank_match",
                "event_type": "bank_match_proposed",
                "to_state": state,
                "actor_type": "system",
                "actor_id": payload.get("proposed_by") or "bank_reconciliation_matcher",
                "organization_id": organization_id,
                "policy_version": CURRENT_BANK_MATCH_POLICY_VERSION,
                "payload_json": {
                    "parent_ap_item_id": parent_ap_item_id,
                    "payment_confirmation_id": payload.get("payment_confirmation_id"),
                    "bank_statement_line_id": payload.get("bank_statement_line_id"),
                    "confidence": payload.get("confidence"),
                },
                "idempotency_key": f"bm-proposed:{box_id}",
            })

        return self.get_bank_match(box_id)  # type: ignore[return-value]

    def get_bank_match(self, box_id: str) -> Optional[Dict[str, Any]]:
        """Return one bank_match Box by id, or None."""
        self.initialize()
        sql = "SELECT * FROM bank_match_boxes WHERE id = %s"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (box_id,))
            row = cur.fetchone()
        if not row:
            return None
        return self._deserialize_bank_match(dict(row))

    def list_bank_matches_for_ap(
        self,
        ap_item_id: str,
        *,
        organization_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """All bank_match Boxes hanging off this AP item."""
        self.initialize()
        if organization_id:
            sql = (
                "SELECT * FROM bank_match_boxes "
                "WHERE parent_ap_item_id = %s AND organization_id = %s "
                "ORDER BY created_at ASC"
            )
            params: tuple = (ap_item_id, organization_id)
        else:
            sql = (
                "SELECT * FROM bank_match_boxes "
                "WHERE parent_ap_item_id = %s "
                "ORDER BY created_at ASC"
            )
            params = (ap_item_id,)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
        return [self._deserialize_bank_match(r) for r in rows]

    def update_bank_match_state(
        self,
        box_id: str,
        target_state: str,
        *,
        actor_id: str,
        reason: str = "",
    ) -> Dict[str, Any]:
        """Advance a bank_match Box to a new state, validating the edge.

        Raises :class:`IllegalBankMatchTransitionError` if the edge is
        invalid. Writes the audit_events row atomically.
        """
        existing = self.get_bank_match(box_id)
        if not existing:
            raise IllegalBankMatchTransitionError(
                f"bank_match {box_id!r} not found"
            )
        current_state = str(existing.get("state") or "")
        if not validate_bank_match_transition(current_state, target_state):
            raise IllegalBankMatchTransitionError(
                f"Illegal bank_match transition: {current_state!r} -> "
                f"{target_state!r} (box_id={box_id})"
            )
        now = datetime.now(timezone.utc).isoformat()
        kwargs: Dict[str, Any] = {
            "state": target_state,
            "decided_by": actor_id,
            "decided_at": now,
            "updated_at": now,
        }
        if target_state == BankMatchState.REJECTED.value:
            kwargs["rejection_reason"] = reason or "no_reason_provided"

        self._patch_bank_match(box_id, kwargs)

        if hasattr(self, "append_audit_event"):
            self.append_audit_event({
                "box_id": box_id,
                "box_type": "bank_match",
                "event_type": (
                    "bank_match_accepted"
                    if target_state == BankMatchState.ACCEPTED.value
                    else "bank_match_rejected"
                ),
                "from_state": current_state,
                "to_state": target_state,
                "actor_type": "user",
                "actor_id": actor_id,
                "organization_id": existing.get("organization_id"),
                "policy_version": CURRENT_BANK_MATCH_POLICY_VERSION,
                "decision_reason": reason or None,
                "payload_json": {
                    "rejection_reason": kwargs.get("rejection_reason"),
                    "parent_ap_item_id": existing.get("parent_ap_item_id"),
                },
                "idempotency_key": f"bm-{target_state}:{box_id}:{now}",
            })

        return self.get_bank_match(box_id)  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _patch_bank_match(self, box_id: str, kwargs: Dict[str, Any]) -> None:
        bad = set(kwargs.keys()) - _BANK_MATCH_ALLOWED_UPDATE_COLUMNS
        if bad:
            raise ValueError(f"Disallowed columns for bank_match update: {bad}")
        if not kwargs:
            return
        set_clause = ", ".join(f"{k} = %s" for k in kwargs.keys())
        sql = f"UPDATE bank_match_boxes SET {set_clause} WHERE id = %s"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (*kwargs.values(), box_id))
            conn.commit()

    def _deserialize_bank_match(self, row: Dict[str, Any]) -> Dict[str, Any]:
        raw_meta = row.get("metadata_json")
        if isinstance(raw_meta, str):
            try:
                row["metadata_json"] = json.loads(raw_meta)
            except json.JSONDecodeError:
                row["metadata_json"] = {}
        return row


def is_bank_match_terminal(state: Optional[str]) -> bool:
    """Predicate: is a bank_match in a terminal state?"""
    if not state:
        return False
    try:
        return BankMatchState(state) in BANK_MATCH_TERMINAL_STATES
    except ValueError:
        return False
