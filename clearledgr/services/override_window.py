"""OverrideWindowService — business logic for Phase 1.4 reversal windows.

Per DESIGN_THESIS.md §8, every autonomous ERP post opens a time-bounded
override window during which a human can reverse the post via Slack,
Teams, or the API. This service is the single owner of that logic:

  - computing when a window starts and expires
  - opening a window after a successful post (called from the
    ``OverrideWindowObserver`` state observer)
  - attempting a reversal (called from the Slack action handler, the
    Teams handler, and the ``POST /ap-items/{id}/reverse`` API)
  - expiring a window (called from the background reaper)
  - reading the configured duration from org settings
  - computing seconds remaining for UI rendering

All external calls to ERP reversal go through
``clearledgr.integrations.erp_router.reverse_bill`` (Phase 1.3). This
service owns the workflow, state-transition, and persistence concerns
on top of that ERP substrate.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


DEFAULT_OVERRIDE_WINDOW_MINUTES = 15
MIN_OVERRIDE_WINDOW_MINUTES = 1
MAX_OVERRIDE_WINDOW_MINUTES = 60 * 24  # 24 hours as a sanity cap

# Default action type — used when callers don't supply one. Phase 1.4
# only emits "erp_post" because that's the only autonomous action type
# in V1 scope. Future action types register their own strings:
#   - "erp_post"            (Phase 1.4)
#   - "payment_execution"   (Q4 thesis sequence)
#   - "vendor_onboarding"   (when bank-detail change freezes ship)
DEFAULT_ACTION_TYPE = "erp_post"

# Special key in the per-action config that supplies the fallback value
# for any action type without an explicit entry. The thesis says
# "configurable per action type" — this gives orgs the ergonomics of a
# single global default plus per-action overrides without forcing them
# to enumerate every action type they don't care about.
DEFAULT_ACTION_KEY = "default"


@dataclass(frozen=True)
class ReversalOutcome:
    """Structured result of an attempt to reverse a posted bill."""

    status: str  # "reversed" | "already_reversed" | "expired" | "not_found" | "failed" | "skipped"
    window_id: Optional[str]
    ap_item_id: Optional[str]
    reversal_ref: Optional[str] = None
    reversal_method: Optional[str] = None
    erp: Optional[str] = None
    reason: Optional[str] = None  # failure reason code
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "window_id": self.window_id,
            "ap_item_id": self.ap_item_id,
            "reversal_ref": self.reversal_ref,
            "reversal_method": self.reversal_method,
            "erp": self.erp,
            "reason": self.reason,
            "message": self.message,
        }


class OverrideWindowService:
    """Owns all override-window workflow logic for a single organization."""

    def __init__(self, organization_id: str, db: Any = None) -> None:
        from clearledgr.core.database import get_db
        self.organization_id = organization_id
        self.db = db or get_db()

    # ------------------------------------------------------------------
    # Duration configuration
    # ------------------------------------------------------------------

    def get_window_duration_minutes(
        self, action_type: str = DEFAULT_ACTION_TYPE
    ) -> int:
        """Return the configured override window duration for an action type.

        Reads ``settings_json["workflow_controls"]["override_window_minutes"]``
        which MUST be a dict mapping action_type → minutes. Lookup order:

          1. Exact match: ``override_window_minutes[action_type]``
          2. ``override_window_minutes["default"]`` if no exact match
          3. ``DEFAULT_OVERRIDE_WINDOW_MINUTES`` (15 min) if no dict
             configured at all

        Values outside the ``[MIN, MAX]`` range are clamped with a warning.
        Per the no-backwards-compat policy, the legacy flat-int shape is
        not accepted — orgs must migrate to the dict shape.
        """
        normalized_action = str(action_type or DEFAULT_ACTION_TYPE).strip() or DEFAULT_ACTION_TYPE

        try:
            org = self.db.get_organization(self.organization_id)
        except Exception as exc:
            logger.warning(
                "[OverrideWindow] get_organization failed for %s: %s — using default %d min",
                self.organization_id, exc, DEFAULT_OVERRIDE_WINDOW_MINUTES,
            )
            return DEFAULT_OVERRIDE_WINDOW_MINUTES

        if not org:
            return DEFAULT_OVERRIDE_WINDOW_MINUTES

        settings = org.get("settings") or org.get("settings_json") or {}
        if isinstance(settings, str):
            try:
                settings = json.loads(settings)
            except json.JSONDecodeError:
                settings = {}

        if not isinstance(settings, dict):
            return DEFAULT_OVERRIDE_WINDOW_MINUTES

        workflow_controls = settings.get("workflow_controls") or {}
        if not isinstance(workflow_controls, dict):
            return DEFAULT_OVERRIDE_WINDOW_MINUTES

        raw = workflow_controls.get("override_window_minutes")
        if raw is None:
            return DEFAULT_OVERRIDE_WINDOW_MINUTES

        if not isinstance(raw, dict):
            logger.warning(
                "[OverrideWindow] override_window_minutes must be a dict mapping "
                "action_type→minutes (got %s) — using default %d. The flat-int "
                "shape is no longer accepted; migrate to "
                "{\"erp_post\": 15, \"default\": 15} or similar.",
                type(raw).__name__, DEFAULT_OVERRIDE_WINDOW_MINUTES,
            )
            return DEFAULT_OVERRIDE_WINDOW_MINUTES

        # Lookup: exact action_type → "default" key → DEFAULT constant
        if normalized_action in raw:
            picked = raw[normalized_action]
            picked_source = normalized_action
        elif DEFAULT_ACTION_KEY in raw:
            picked = raw[DEFAULT_ACTION_KEY]
            picked_source = DEFAULT_ACTION_KEY
        else:
            return DEFAULT_OVERRIDE_WINDOW_MINUTES

        try:
            minutes = int(picked)
        except (TypeError, ValueError):
            logger.warning(
                "[OverrideWindow] override_window_minutes[%r] is not an int "
                "(got %r) — using default %d",
                picked_source, picked, DEFAULT_OVERRIDE_WINDOW_MINUTES,
            )
            return DEFAULT_OVERRIDE_WINDOW_MINUTES

        if minutes < MIN_OVERRIDE_WINDOW_MINUTES:
            logger.warning(
                "[OverrideWindow] action %r configured %d min is below minimum %d; clamping",
                normalized_action, minutes, MIN_OVERRIDE_WINDOW_MINUTES,
            )
            return MIN_OVERRIDE_WINDOW_MINUTES
        if minutes > MAX_OVERRIDE_WINDOW_MINUTES:
            logger.warning(
                "[OverrideWindow] action %r configured %d min is above maximum %d; clamping",
                normalized_action, minutes, MAX_OVERRIDE_WINDOW_MINUTES,
            )
            return MAX_OVERRIDE_WINDOW_MINUTES
        return minutes

    # ------------------------------------------------------------------
    # Window lifecycle — open
    # ------------------------------------------------------------------

    def open_window(
        self,
        *,
        ap_item_id: str,
        erp_reference: str,
        erp_type: Optional[str] = None,
        action_type: str = DEFAULT_ACTION_TYPE,
        posted_at: Optional[datetime] = None,
        confidence: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Open a new override window for a freshly-posted AP item.

        Called by the ``OverrideWindowObserver`` immediately after the
        ``posted_to_erp`` state transition. The duration is resolved
        per action type — Phase 1.4 only emits ``erp_post`` but the
        column is open for future tiers (payment_execution, etc.) per
        DESIGN_THESIS.md §8 "configurable per action type".

        §7.4 Confidence Model: medium confidence shortens the window
        to 15 minutes so reasoning is surfaced prominently.

        Returns the newly created window row.
        """
        normalized_action = str(action_type or DEFAULT_ACTION_TYPE).strip() or DEFAULT_ACTION_TYPE
        now = posted_at or datetime.now(timezone.utc)
        duration = self.get_window_duration_minutes(normalized_action)

        # §7.4: medium confidence → shorten override window to 15 minutes
        if confidence is not None and 0.7 <= confidence < 0.95:
            duration = min(duration, 15)
            logger.info(
                "[OverrideWindow] Medium confidence %.2f — window shortened to %d min for %s",
                confidence, duration, ap_item_id,
            )
        expires = now + timedelta(minutes=duration)
        window = self.db.create_override_window(
            ap_item_id=ap_item_id,
            organization_id=self.organization_id,
            erp_reference=erp_reference,
            erp_type=erp_type,
            action_type=normalized_action,
            posted_at=now.isoformat(),
            expires_at=expires.isoformat(),
        )
        logger.info(
            "[OverrideWindow] Opened window %s for ap_item=%s "
            "(action=%s, erp=%s, ref=%s, expires=%s, duration=%dm)",
            window["id"], ap_item_id, normalized_action, erp_type,
            erp_reference, window["expires_at"], duration,
        )
        return window

    # ------------------------------------------------------------------
    # Time calculations
    # ------------------------------------------------------------------

    @staticmethod
    def is_window_expired(window: Dict[str, Any], *, as_of: Optional[datetime] = None) -> bool:
        """Return True if ``window`` has passed its expiry, regardless of state."""
        if not window:
            return True
        expires_iso = window.get("expires_at")
        if not expires_iso:
            return True
        try:
            expires_dt = datetime.fromisoformat(str(expires_iso).replace("Z", "+00:00"))
        except ValueError:
            return True
        if expires_dt.tzinfo is None:
            expires_dt = expires_dt.replace(tzinfo=timezone.utc)
        now = as_of or datetime.now(timezone.utc)
        return now >= expires_dt

    @staticmethod
    def time_remaining_seconds(
        window: Dict[str, Any], *, as_of: Optional[datetime] = None
    ) -> int:
        """Return seconds remaining in the window (>= 0, clamped)."""
        if not window:
            return 0
        expires_iso = window.get("expires_at")
        if not expires_iso:
            return 0
        try:
            expires_dt = datetime.fromisoformat(str(expires_iso).replace("Z", "+00:00"))
        except ValueError:
            return 0
        if expires_dt.tzinfo is None:
            expires_dt = expires_dt.replace(tzinfo=timezone.utc)
        now = as_of or datetime.now(timezone.utc)
        delta = (expires_dt - now).total_seconds()
        return max(0, int(delta))

    # ------------------------------------------------------------------
    # Window lifecycle — attempt reversal
    # ------------------------------------------------------------------

    async def attempt_reversal(
        self,
        *,
        window_id: str,
        actor_id: str,
        reason: str,
    ) -> ReversalOutcome:
        """Try to reverse the posted bill tracked by ``window_id``.

        This is the canonical reversal entry point shared by the Slack
        button handler, the Teams handler, and the ``/ap-items/{id}/reverse``
        API. It:

          1. Loads the window and validates it's still pending
          2. Verifies the window has not expired
          3. Calls ``reverse_bill`` (Phase 1.3) with an idempotency key
             derived from the window id
          4. On success: marks the window reversed, transitions the AP
             item state posted_to_erp → reversed → closed, records the
             reversal on the workflow audit trail
          5. On failure: marks the window failed, leaves the AP item in
             ``posted_to_erp`` for human intervention
        """
        window = self.db.get_override_window(window_id)
        if not window:
            return ReversalOutcome(
                status="not_found",
                window_id=window_id,
                ap_item_id=None,
                reason="window_not_found",
                message="No override window exists for this id.",
            )

        ap_item_id = window.get("ap_item_id")
        current_state = str(window.get("state") or "").lower()

        if current_state == "reversed":
            return ReversalOutcome(
                status="already_reversed",
                window_id=window_id,
                ap_item_id=ap_item_id,
                reversal_ref=window.get("reversal_ref"),
                message="This post has already been reversed.",
            )

        if current_state in {"expired", "failed"}:
            return ReversalOutcome(
                status="expired" if current_state == "expired" else "failed",
                window_id=window_id,
                ap_item_id=ap_item_id,
                reason=window.get("failure_reason") or current_state,
                message=(
                    "The override window has already expired."
                    if current_state == "expired"
                    else "A previous reversal attempt failed on this window."
                ),
            )

        if current_state != "pending":
            return ReversalOutcome(
                status="failed",
                window_id=window_id,
                ap_item_id=ap_item_id,
                reason="unexpected_window_state",
                message=f"Window is in unexpected state: {current_state!r}",
            )

        if self.is_window_expired(window):
            # The reaper should have already marked this as expired, but
            # we catch the race here and finalize it now.
            self.db.mark_override_window_expired(window_id)
            self._transition_to_closed_safely(ap_item_id)
            return ReversalOutcome(
                status="expired",
                window_id=window_id,
                ap_item_id=ap_item_id,
                reason="window_expired",
                message="The override window has expired; this post is final.",
            )

        # ------------------------------------------------------------------
        # Call the Phase 1.3 ERP reversal substrate
        # ------------------------------------------------------------------
        from clearledgr.integrations.erp_router import reverse_bill

        erp_reference = window.get("erp_reference")
        idempotency_key = f"override_window:{window_id}"

        try:
            erp_result = await reverse_bill(
                organization_id=self.organization_id,
                erp_reference=erp_reference,
                reason=reason,
                ap_item_id=ap_item_id,
                idempotency_key=idempotency_key,
                actor_id=actor_id,
            )
        except Exception as exc:
            logger.error(
                "[OverrideWindow] reverse_bill raised unexpectedly: %s", exc,
            )
            self.db.mark_override_window_failed(window_id, f"unexpected_error:{exc}")
            return ReversalOutcome(
                status="failed",
                window_id=window_id,
                ap_item_id=ap_item_id,
                reason="unexpected_error",
                message=str(exc),
            )

        erp_status = (erp_result or {}).get("status")

        if erp_status in {"success", "already_reversed"}:
            self.db.mark_override_window_reversed(
                window_id,
                reversed_by=actor_id,
                reversal_reason=reason,
                reversal_ref=(erp_result or {}).get("reversal_ref")
                or (erp_result or {}).get("reference_id"),
            )
            self._transition_ap_item_to_reversed(
                ap_item_id=ap_item_id,
                actor_id=actor_id,
                reason=reason,
                reversal_ref=(erp_result or {}).get("reversal_ref"),
                erp=(erp_result or {}).get("erp") or window.get("erp_type"),
            )
            return ReversalOutcome(
                status=(
                    "already_reversed"
                    if erp_status == "already_reversed"
                    else "reversed"
                ),
                window_id=window_id,
                ap_item_id=ap_item_id,
                reversal_ref=(erp_result or {}).get("reversal_ref"),
                reversal_method=(erp_result or {}).get("reversal_method"),
                erp=(erp_result or {}).get("erp") or window.get("erp_type"),
            )

        if erp_status == "skipped":
            self.db.mark_override_window_failed(window_id, "no_erp_connected")
            return ReversalOutcome(
                status="skipped",
                window_id=window_id,
                ap_item_id=ap_item_id,
                reason="no_erp_connected",
                message="No ERP is connected for this organization.",
            )

        # Any other status is a hard failure at the ERP layer.
        failure_reason = (erp_result or {}).get("reason") or "erp_reversal_failed"
        self.db.mark_override_window_failed(window_id, failure_reason)
        logger.warning(
            "[OverrideWindow] Reversal failed window=%s ap_item=%s erp_result=%s",
            window_id, ap_item_id, erp_result,
        )
        return ReversalOutcome(
            status="failed",
            window_id=window_id,
            ap_item_id=ap_item_id,
            reason=failure_reason,
            erp=(erp_result or {}).get("erp") or window.get("erp_type"),
            message=(erp_result or {}).get("erp_error_detail")
            or f"ERP reversal failed: {failure_reason}",
        )

    # ------------------------------------------------------------------
    # Window lifecycle — expire (reaper path)
    # ------------------------------------------------------------------

    def expire_window(self, window_id: str) -> bool:
        """Mark a pending window as expired and close out the AP item.

        Called by the background reaper when ``expires_at`` has passed.
        Returns True if the window was expired by this call, False if
        it was already in a terminal state.
        """
        window = self.db.get_override_window(window_id)
        if not window:
            return False
        if str(window.get("state") or "").lower() != "pending":
            return False
        # Race guard: between the read above and the conditional UPDATE
        # below, a user may have clicked Undo. mark_override_window_expired
        # is `WHERE state='pending'` so it returns False if it lost the
        # race — in that case do NOT close the AP item or claim success
        # to the reaper (otherwise we'd overwrite the Reversed Slack card
        # with Expired and fire OVERRIDE_WINDOW_EXPIRED for a reversed item).
        if not self.db.mark_override_window_expired(window_id):
            return False
        self._transition_to_closed_safely(window.get("ap_item_id"))
        logger.info(
            "[OverrideWindow] Expired window %s for ap_item=%s",
            window_id, window.get("ap_item_id"),
        )
        return True

    # ------------------------------------------------------------------
    # Workflow hooks — state transitions
    # ------------------------------------------------------------------

    def _transition_ap_item_to_reversed(
        self,
        *,
        ap_item_id: Optional[str],
        actor_id: str,
        reason: str,
        reversal_ref: Optional[str],
        erp: Optional[str],
    ) -> None:
        """Transition AP item ``posted_to_erp`` → ``reversed`` → ``closed``.

        Uses the canonical workflow transition method so observers fire
        (audit trail, notifications, learning). If the transition fails
        (e.g., item already closed by another path), log and move on —
        the ERP-level reversal already succeeded, so state drift is
        non-fatal.
        """
        if not ap_item_id:
            return
        try:
            from clearledgr.services.invoice_workflow import get_invoice_workflow
            workflow = get_invoice_workflow(self.organization_id)
        except Exception as exc:
            logger.warning(
                "[OverrideWindow] Could not load workflow for state transition: %s",
                exc,
            )
            return

        # The AP item is keyed by id in our store but the workflow's
        # transition method uses gmail_id (thread_id). Look it up.
        try:
            ap_item = self.db.get_ap_item(ap_item_id) or {}
        except Exception as exc:
            logger.warning(
                "[OverrideWindow] Could not load AP item %s: %s",
                ap_item_id, exc,
            )
            return

        gmail_id = (
            ap_item.get("thread_id")
            or ap_item.get("gmail_id")
            or ap_item_id
        )

        metadata_update = {
            "reversed_by": actor_id,
            "reversal_reason": reason,
            "reversal_ref": reversal_ref,
            "reversal_erp": erp,
        }

        # Transition posted_to_erp → reversed
        try:
            workflow._transition_invoice_state(
                gmail_id,
                "reversed",
                actor_id=actor_id,
                metadata_update=metadata_update,
            )
        except TypeError:
            # Older transition signatures may not accept metadata_update.
            try:
                workflow._transition_invoice_state(gmail_id, "reversed")
            except Exception as exc:
                logger.warning(
                    "[OverrideWindow] State transition to reversed failed: %s", exc,
                )
                return
        except Exception as exc:
            logger.warning(
                "[OverrideWindow] State transition to reversed failed: %s", exc,
            )
            return

        # Then transition reversed → closed to clean up
        try:
            workflow._transition_invoice_state(gmail_id, "closed", actor_id=actor_id)
        except TypeError:
            try:
                workflow._transition_invoice_state(gmail_id, "closed")
            except Exception as exc:
                logger.debug(
                    "[OverrideWindow] State transition to closed failed: %s", exc,
                )
        except Exception as exc:
            logger.debug(
                "[OverrideWindow] State transition to closed failed: %s", exc,
            )

    def _transition_to_closed_safely(self, ap_item_id: Optional[str]) -> None:
        """Best-effort posted_to_erp → closed transition after window expires.

        Never raises — used from the reaper which must remain resilient.
        """
        if not ap_item_id:
            return
        try:
            ap_item = self.db.get_ap_item(ap_item_id) or {}
            current_state = str(ap_item.get("state") or "").lower()
            if current_state != "posted_to_erp":
                # Nothing to do — the item has already moved on.
                return
            gmail_id = (
                ap_item.get("thread_id")
                or ap_item.get("gmail_id")
                or ap_item_id
            )
            from clearledgr.services.invoice_workflow import get_invoice_workflow
            workflow = get_invoice_workflow(self.organization_id)
            try:
                workflow._transition_invoice_state(gmail_id, "closed")
            except TypeError:
                workflow._transition_invoice_state(gmail_id, "closed")
        except Exception as exc:
            logger.debug(
                "[OverrideWindow] Best-effort close after expiry failed: %s", exc,
            )


def get_override_window_service(
    organization_id: str, db: Any = None
) -> OverrideWindowService:
    """Factory so callers don't pin to the class path directly."""
    return OverrideWindowService(organization_id, db=db)
