"""OverrideWindowStore mixin — CRUD for Phase 1.4 override-window rows.

Per DESIGN_THESIS.md §8, every autonomous ERP post opens a time-bounded
window during which a human can reverse the post at the ERP level via
the Phase 1.3 ``reverse_bill`` API. The override window is the last
human escape hatch before a post is considered final.

This store owns the ``override_windows`` table which tracks:
  - which posted AP item a window belongs to
  - when the window expires
  - where the Slack/Teams undo card is (for updates/deletion)
  - whether the window is still pending, was reversed, expired
    naturally, or failed

The background reaper (``agent_background.py``) queries
``list_expired_override_windows`` to finalize stale pending windows and
update their Slack cards.

The Slack/API action handlers use ``get_override_window_by_ap_item_id``
(for button clicks) and ``mark_override_window_reversed`` /
``mark_override_window_failed`` to record the outcome.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# Valid override window states
OVERRIDE_WINDOW_PENDING = "pending"
OVERRIDE_WINDOW_REVERSED = "reversed"
OVERRIDE_WINDOW_EXPIRED = "expired"
OVERRIDE_WINDOW_FAILED = "failed"

_VALID_STATES = frozenset(
    {
        OVERRIDE_WINDOW_PENDING,
        OVERRIDE_WINDOW_REVERSED,
        OVERRIDE_WINDOW_EXPIRED,
        OVERRIDE_WINDOW_FAILED,
    }
)


# Default action type — used when callers don't supply one. The thesis
# scopes V1 to ERP posts; future action types (payment_execution,
# vendor_onboarding, etc.) register themselves with a distinct string.
DEFAULT_ACTION_TYPE = "erp_post"


OVERRIDE_WINDOWS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS override_windows (
    id TEXT PRIMARY KEY,
    ap_item_id TEXT NOT NULL,
    organization_id TEXT NOT NULL,
    erp_reference TEXT NOT NULL,
    erp_type TEXT,
    action_type TEXT NOT NULL DEFAULT 'erp_post',
    posted_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    slack_channel TEXT,
    slack_message_ts TEXT,
    reversed_at TEXT,
    reversed_by TEXT,
    reversal_reason TEXT,
    reversal_ref TEXT,
    failure_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

OVERRIDE_WINDOWS_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_override_windows_state_expiry ON override_windows(state, expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_override_windows_ap_item ON override_windows(ap_item_id)",
    "CREATE INDEX IF NOT EXISTS idx_override_windows_org ON override_windows(organization_id)",
    "CREATE INDEX IF NOT EXISTS idx_override_windows_action_type ON override_windows(action_type, state, expires_at)",
]


class OverrideWindowStore:
    """Mixin providing override-window persistence for ClearledgrDB."""

    OVERRIDE_WINDOWS_TABLE_SQL = OVERRIDE_WINDOWS_TABLE_SQL
    OVERRIDE_WINDOWS_INDEXES_SQL = OVERRIDE_WINDOWS_INDEXES_SQL

    # ------------------------------------------------------------------
    # Row shape helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: Any) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        try:
            return dict(row)
        except Exception:
            return row if isinstance(row, dict) else None

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create_override_window(
        self,
        *,
        ap_item_id: str,
        organization_id: str,
        erp_reference: str,
        erp_type: Optional[str],
        expires_at: str,
        action_type: str = DEFAULT_ACTION_TYPE,
        posted_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new override-window row in the ``pending`` state.

        ``posted_at`` defaults to now if not supplied. ``expires_at`` must
        already be computed by the caller (the OverrideWindowService
        knows the configured duration). ``action_type`` defaults to
        ``erp_post`` — the only action type Phase 1.4 emits — but the
        column is open for future tiers (payment_execution, etc.).
        """
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        window_id = f"ovw_{uuid.uuid4().hex[:16]}"
        posted_ts = posted_at or now
        normalized_action = str(action_type or DEFAULT_ACTION_TYPE).strip() or DEFAULT_ACTION_TYPE

        sql = (
            """
            INSERT INTO override_windows
            (id, ap_item_id, organization_id, erp_reference, erp_type,
             action_type, posted_at, expires_at, state, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s)
            """
        )
        params = (
            window_id,
            ap_item_id,
            organization_id,
            erp_reference,
            erp_type,
            normalized_action,
            posted_ts,
            expires_at,
            now,
            now,
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()

        return {
            "id": window_id,
            "ap_item_id": ap_item_id,
            "organization_id": organization_id,
            "erp_reference": erp_reference,
            "erp_type": erp_type,
            "action_type": normalized_action,
            "posted_at": posted_ts,
            "expires_at": expires_at,
            "state": OVERRIDE_WINDOW_PENDING,
            "slack_channel": None,
            "slack_message_ts": None,
            "reversed_at": None,
            "reversed_by": None,
            "reversal_reason": None,
            "reversal_ref": None,
            "failure_reason": None,
            "created_at": now,
            "updated_at": now,
        }

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_override_window(self, window_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a window by its id."""
        self.initialize()
        sql = "SELECT * FROM override_windows WHERE id = %s"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (window_id,))
            row = cur.fetchone()
        return self._row_to_dict(row)

    def get_override_window_by_ap_item_id(
        self, ap_item_id: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch the most recent window for an AP item."""
        self.initialize()
        sql = (
            """
            SELECT * FROM override_windows
            WHERE ap_item_id = %s
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (ap_item_id,))
            row = cur.fetchone()
        return self._row_to_dict(row)

    def list_override_windows_for_org(
        self,
        organization_id: str,
        *,
        state: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List windows for an organization, optionally filtered by state."""
        self.initialize()
        safe_limit = max(1, min(int(limit or 100), 1000))
        if state:
            sql = (
                """
                SELECT * FROM override_windows
                WHERE organization_id = %s AND state = %s
                ORDER BY created_at DESC
                LIMIT %s
                """
            )
            params = (organization_id, state, safe_limit)
        else:
            sql = (
                """
                SELECT * FROM override_windows
                WHERE organization_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """
            )
            params = (organization_id, safe_limit)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            return [self._row_to_dict(r) for r in cur.fetchall() if r is not None]

    def list_expired_override_windows(
        self, *, as_of: Optional[str] = None, limit: int = 500
    ) -> List[Dict[str, Any]]:
        """Return pending windows whose ``expires_at`` is <= ``as_of``.

        This is the reaper's canonical query. ``as_of`` defaults to now.
        """
        self.initialize()
        cutoff = as_of or datetime.now(timezone.utc).isoformat()
        safe_limit = max(1, min(int(limit or 500), 5000))
        sql = (
            """
            SELECT * FROM override_windows
            WHERE state = 'pending' AND expires_at <= %s
            ORDER BY expires_at ASC
            LIMIT %s
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (cutoff, safe_limit))
            return [self._row_to_dict(r) for r in cur.fetchall() if r is not None]

    # ------------------------------------------------------------------
    # Write — state transitions
    # ------------------------------------------------------------------

    def update_override_window_slack_refs(
        self,
        window_id: str,
        *,
        slack_channel: Optional[str],
        slack_message_ts: Optional[str],
    ) -> bool:
        """Record where the Slack undo card was posted."""
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        sql = (
            """
            UPDATE override_windows
            SET slack_channel = %s, slack_message_ts = %s, updated_at = %s
            WHERE id = %s
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (slack_channel, slack_message_ts, now, window_id))
            changed = cur.rowcount
            conn.commit()
        return bool(changed)

    def mark_override_window_reversed(
        self,
        window_id: str,
        *,
        reversed_by: str,
        reversal_reason: str,
        reversal_ref: Optional[str] = None,
    ) -> bool:
        """Record a successful reversal on the window."""
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        sql = (
            """
            UPDATE override_windows
            SET state = 'reversed',
                reversed_at = %s,
                reversed_by = %s,
                reversal_reason = %s,
                reversal_ref = %s,
                updated_at = %s
            WHERE id = %s AND state = 'pending'
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (now, reversed_by, reversal_reason, reversal_ref, now, window_id),
            )
            changed = cur.rowcount
            conn.commit()
        return bool(changed)

    def mark_override_window_expired(self, window_id: str) -> bool:
        """Mark a pending window as expired (used by the reaper)."""
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        sql = (
            """
            UPDATE override_windows
            SET state = 'expired', updated_at = %s
            WHERE id = %s AND state = 'pending'
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (now, window_id))
            changed = cur.rowcount
            conn.commit()
        return bool(changed)

    def mark_override_window_failed(
        self, window_id: str, failure_reason: str
    ) -> bool:
        """Mark a window as failed (reversal attempt errored at the ERP)."""
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        sql = (
            """
            UPDATE override_windows
            SET state = 'failed', failure_reason = %s, updated_at = %s
            WHERE id = %s AND state = 'pending'
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (failure_reason, now, window_id))
            changed = cur.rowcount
            conn.commit()
        return bool(changed)
