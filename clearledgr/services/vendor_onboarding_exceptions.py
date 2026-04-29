"""Synthesize exception rows from stuck/blocked vendor onboarding sessions.

Module 4 Pass C surfaces vendor onboarding sessions as first-class
exceptions in Module 2's queue without bloating the canonical
``box_exceptions`` table with synthetic rows. The signal is:

  * Any session in the ``BLOCKED`` state → high-severity exception
    (a human downgraded the session and it sits there until
    someone unblocks).
  * Any session in a pre-active state whose ``last_activity_at`` is
    older than ``stall_hours`` (default 48h) → medium-severity
    exception. The vendor stopped responding mid-flow.

Synthetic rows are computed at read time and merged into the
``list_exceptions`` response. Their ``id`` is prefixed ``vos:`` so
the resolve endpoint can recognise them as read-only — resolving
the underlying signal happens via the vendor onboarding surface
(state-machine actions on the session itself), not via the
exception queue.

This module is pure: takes a DB handle + org id, returns a list of
exception-row dicts. No writes.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from clearledgr.core.vendor_onboarding_states import VendorOnboardingState

logger = logging.getLogger(__name__)


# Severity assignment per (state, stall) — kept declarative so the
# threshold tuning happens in one place.
DEFAULT_STALL_HOURS = 48
STATE_NAMES_NEEDING_RESPONSE = {
    VendorOnboardingState.INVITED.value: "Vendor has not started KYC",
    VendorOnboardingState.KYC.value: "Vendor stalled mid-KYC",
    VendorOnboardingState.BANK_VERIFY.value: "Bank verification incomplete",
    VendorOnboardingState.BANK_VERIFIED.value: "Bank verified, waiting for ERP push",
    VendorOnboardingState.READY_FOR_ERP.value: "Ready to push to ERP, not yet active",
}


def _parse_iso(value: Any) -> Optional[datetime]:
    """Parse an ISO timestamp into a UTC datetime; return None on bad input."""
    if not value:
        return None
    try:
        s = str(value).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def synthesize_onboarding_exceptions(
    db,
    organization_id: str,
    *,
    stall_hours: int = DEFAULT_STALL_HOURS,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Return synthetic exception rows for stuck/blocked sessions.

    Rows match the ``box_exceptions`` JSON shape so the existing
    ``ExceptionsPage`` UI renders them without branching. Each row
    carries:
      * ``id`` prefixed ``vos:<session_id>`` (read-only marker).
      * ``box_type='vendor_onboarding_session'``.
      * ``box_id`` set to the session id (clickthrough target).
      * ``exception_type`` — ``vendor_onboarding_blocked`` or
        ``vendor_onboarding_stalled_<state>``.
      * ``severity`` — ``high`` for BLOCKED, ``medium`` for stalls.
      * ``reason`` — human-readable explanation.
      * ``raised_at`` — the ``last_activity_at`` of the session
        (when the staleness started ticking).
      * ``raised_by='system'`` — these are derived signals.
      * ``synthetic=True`` — distinguishes from canonical rows.
    """
    if not hasattr(db, "list_pending_onboarding_sessions"):
        return []

    try:
        sessions = db.list_pending_onboarding_sessions(
            organization_id, limit=500,
        )
    except Exception as exc:
        logger.warning(
            "[onboarding_exceptions] list_pending_onboarding_sessions failed: %s",
            exc,
        )
        return []

    # Also pull the BLOCKED state explicitly — list_pending defaults
    # to PRE_ACTIVE which excludes BLOCKED. We want both surfaces
    # in the queue.
    try:
        blocked_sessions = db.list_pending_onboarding_sessions(
            organization_id,
            states=[VendorOnboardingState.BLOCKED.value],
            limit=500,
        )
    except Exception as exc:
        logger.warning(
            "[onboarding_exceptions] blocked-list fetch failed: %s", exc,
        )
        blocked_sessions = []

    # Combine, dedupe by id (PRE_ACTIVE may overlap on edge state
    # tokens depending on the spec).
    seen_ids: set = set()
    combined: List[Dict[str, Any]] = []
    for sess in (*sessions, *blocked_sessions):
        sid = str(sess.get("id") or "")
        if not sid or sid in seen_ids:
            continue
        seen_ids.add(sid)
        combined.append(sess)

    now_dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = now_dt - timedelta(hours=int(stall_hours))
    out: List[Dict[str, Any]] = []

    for sess in combined:
        state = str(sess.get("state") or "").strip().lower()
        session_id = str(sess.get("id") or "")
        if not session_id:
            continue
        vendor_name = str(sess.get("vendor_name") or "").strip() or "(unnamed vendor)"
        last_dt = (
            _parse_iso(sess.get("last_activity_at"))
            or _parse_iso(sess.get("invited_at"))
            or _parse_iso(sess.get("created_at"))
        )

        if state == VendorOnboardingState.BLOCKED.value:
            out.append(_make_synthetic_row(
                session_id=session_id,
                organization_id=organization_id,
                exception_type="vendor_onboarding_blocked",
                severity="high",
                reason=(
                    f"Vendor onboarding session for '{vendor_name}' is "
                    f"BLOCKED — needs an admin to either restart the "
                    f"flow or close it as unsuccessful."
                ),
                raised_at=last_dt,
                vendor_name=vendor_name,
                state=state,
            ))
            continue

        # Non-terminal stall check — ignore sessions whose last
        # activity is more recent than the stall window.
        if last_dt is None or last_dt > cutoff:
            continue

        # We only synthesise stall exceptions for the well-known
        # pre-active states. Anything outside this set (e.g. an
        # exotic state from a future migration) is silently skipped
        # rather than generating a half-formed exception row.
        state_label = STATE_NAMES_NEEDING_RESPONSE.get(state)
        if state_label is None:
            continue

        hours_stuck = int((now_dt - last_dt).total_seconds() / 3600)
        out.append(_make_synthetic_row(
            session_id=session_id,
            organization_id=organization_id,
            exception_type=f"vendor_onboarding_stalled_{state}",
            severity="medium",
            reason=(
                f"{state_label} for '{vendor_name}' — no activity in "
                f"{hours_stuck} hour{'s' if hours_stuck != 1 else ''} "
                f"(threshold: {stall_hours}h)."
            ),
            raised_at=last_dt,
            vendor_name=vendor_name,
            state=state,
        ))

    return out


def _make_synthetic_row(
    *,
    session_id: str,
    organization_id: str,
    exception_type: str,
    severity: str,
    reason: str,
    raised_at: Optional[datetime],
    vendor_name: str,
    state: str,
) -> Dict[str, Any]:
    """Build one synthetic exception dict in the canonical shape."""
    return {
        "id": f"vos:{session_id}",
        "organization_id": organization_id,
        "box_type": "vendor_onboarding_session",
        "box_id": session_id,
        "exception_type": exception_type,
        "severity": severity,
        "reason": reason,
        "raised_at": (
            raised_at.isoformat()
            if raised_at else datetime.now(timezone.utc).isoformat()
        ),
        "raised_by": "system",
        "resolved_at": None,
        "metadata": {
            "vendor_name": vendor_name,
            "session_state": state,
            "synthetic": True,
        },
        # Top-level flag so the UI can render a "View vendor"
        # affordance instead of the canonical Resolve dialog.
        "synthetic": True,
    }
