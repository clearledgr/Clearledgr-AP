"""Common Slack/Teams approval action normalization (PLAN.md Section 5.4)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple


STALE_ACTION_MAX_AGE_SECONDS = 86_400  # 24h default


class ApprovalActionContractError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code = str(code or "invalid_action")
        self.message = str(message or self.code)
        self.status_code = int(status_code)
        super().__init__(self.message)


@dataclass
class NormalizedApprovalAction:
    ap_item_id: Optional[str]
    run_id: Optional[str]
    action: str  # approve | reject | request_info
    actor_id: str
    actor_display: str
    reason: Optional[str]
    source_channel: str  # slack | teams
    source_channel_id: Optional[str]
    source_message_ref: Optional[str]
    request_ts: Optional[str]
    idempotency_key: str
    gmail_id: str
    organization_id: str
    correlation_id: Optional[str] = None
    action_variant: Optional[str] = None
    raw_action: Optional[str] = None
    raw_payload: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ap_item_id": self.ap_item_id,
            "run_id": self.run_id,
            "action": self.action,
            "actor_id": self.actor_id,
            "actor_display": self.actor_display,
            "reason": self.reason,
            "source_channel": self.source_channel,
            "source_channel_id": self.source_channel_id,
            "source_message_ref": self.source_message_ref,
            "request_ts": self.request_ts,
            "idempotency_key": self.idempotency_key,
            "gmail_id": self.gmail_id,
            "organization_id": self.organization_id,
            "correlation_id": self.correlation_id,
            "action_variant": self.action_variant,
            "raw_action": self.raw_action,
        }


@dataclass(frozen=True)
class ApprovalActionPrecedenceResult:
    status: str  # dispatch | duplicate | stale | blocked
    reason: Optional[str] = None

    @property
    def should_dispatch(self) -> bool:
        return self.status == "dispatch"


# H18: Pre-flight policy check at approval action boundary (PLAN.md §B)
# Maps action → set of valid AP item states the action may be dispatched from.
_ACTION_VALID_STATES = {
    "approve": {"needs_approval"},
    "reject": {"needs_approval"},
    "request_info": {"needs_approval", "validated"},
}

_SUPERSEDED_STATES = {
    "approved",
    "ready_to_post",
    "posted_to_erp",
    "closed",
    "rejected",
    "failed_post",
}


def validate_action_state_preflight(
    action: NormalizedApprovalAction,
    ap_item: Optional[Dict[str, Any]],
) -> Optional[str]:
    """Return a block reason if the action cannot proceed, else None.

    Checks that the AP item exists and is in a valid state for the
    requested action. This is a boundary guard called before dispatching
    to the workflow service.
    """
    if not ap_item:
        return None  # New items — let workflow handle
    current_state = str(ap_item.get("state") or "").strip().lower()
    valid_states = _ACTION_VALID_STATES.get(action.action)
    if valid_states and current_state and current_state not in valid_states:
        return f"action_{action.action}_invalid_for_state_{current_state}"
    return None


def resolve_action_precedence(
    action: NormalizedApprovalAction,
    ap_item: Optional[Dict[str, Any]],
    *,
    already_processed: bool = False,
    now_ts: Optional[int] = None,
) -> ApprovalActionPrecedenceResult:
    """Resolve the canonical callback precedence before dispatch.

    The contract is intentionally explicit so Slack and Teams share one outcome
    model instead of open-coding slightly different callback behavior.

    Precedence order:
    1. Duplicate of an already-processed callback -> duplicate
    2. Expired callback window -> stale
    3. Workflow already moved beyond the approval window -> stale
    4. Illegal state for the action -> blocked
    5. Otherwise -> dispatch
    """

    if already_processed:
        return ApprovalActionPrecedenceResult("duplicate", "duplicate_callback")

    if is_stale_action(action, now_ts=now_ts):
        return ApprovalActionPrecedenceResult("stale", "stale_action")

    if not ap_item:
        return ApprovalActionPrecedenceResult("dispatch")

    current_state = str(ap_item.get("state") or "").strip().lower()
    preflight_block = validate_action_state_preflight(action, ap_item)
    if not preflight_block:
        return ApprovalActionPrecedenceResult("dispatch")

    if current_state in _SUPERSEDED_STATES:
        return ApprovalActionPrecedenceResult(
            "stale",
            f"superseded_by_state_{current_state}",
        )

    return ApprovalActionPrecedenceResult("blocked", preflight_block)


def _epoch_from_any(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def is_stale_action(action: NormalizedApprovalAction, *, now_ts: Optional[int] = None) -> bool:
    request_epoch = _epoch_from_any(action.request_ts)
    if request_epoch is None:
        return False
    current = int(now_ts or datetime.now(timezone.utc).timestamp())
    return (current - request_epoch) > STALE_ACTION_MAX_AGE_SECONDS


def _stable_action_key(seed: Dict[str, Any]) -> str:
    payload = json.dumps(seed, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"approval_action:{digest}"


def _extract_slack_gmail_id(action: Dict[str, Any]) -> str:
    value = str(action.get("value") or "")
    if value:
        if value.startswith("{"):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    candidate = parsed.get("gmail_id") or parsed.get("email_id") or parsed.get("invoice_id")
                    if candidate:
                        return str(candidate)
            except Exception:
                pass
        return value
    action_id = str(action.get("action_id") or "")
    prefixes = (
        "approve_invoice_",
        "post_to_erp_",
        "post_to_sap_",
        "reject_invoice_",
        "approve_budget_override_",
        "request_budget_adjustment_",
        "request_info_",
        "reject_budget_",
    )
    for prefix in prefixes:
        if action_id.startswith(prefix):
            return action_id[len(prefix) :]
    if "_" in action_id:
        return action_id.rsplit("_", 1)[-1]
    return action_id


def _canonical_slack_action(action_id: str) -> Tuple[str, Optional[str]]:
    action_id = str(action_id or "").strip()
    if action_id.startswith(("approve_invoice_", "post_to_erp_", "post_to_sap_")):
        return "approve", None
    if action_id.startswith("approve_budget_override_"):
        return "approve", "budget_override"
    if action_id.startswith(("request_budget_adjustment_", "request_info_")):
        return "request_info", "budget_adjustment"
    if action_id.startswith(("reject_invoice_", "reject_budget_")):
        return "reject", ("budget_reject" if action_id.startswith("reject_budget_") else None)
    raise ApprovalActionContractError("unsupported_action", f"Unsupported Slack action: {action_id}", 400)


def normalize_slack_action(
    payload: Dict[str, Any],
    *,
    request_ts: Optional[str],
    organization_id: str,
) -> NormalizedApprovalAction:
    actions = payload.get("actions") or []
    action_obj = actions[0] if actions and isinstance(actions[0], dict) else {}
    action_id = str(action_obj.get("action_id") or "").strip()
    if not action_id:
        raise ApprovalActionContractError("missing_action", "Slack action_id is required", 400)

    gmail_id = _extract_slack_gmail_id(action_obj).strip()
    if not gmail_id:
        raise ApprovalActionContractError("missing_email_id", "Slack action missing email identifier", 400)

    action, variant = _canonical_slack_action(action_id)

    user = payload.get("user") or {}
    actor_id = str(user.get("id") or user.get("username") or user.get("name") or "slack_user")
    actor_display = str(user.get("username") or user.get("name") or actor_id)
    channel = payload.get("channel") or {}
    message = payload.get("message") or {}
    source_channel_id = str(channel.get("id") or "").strip() or None
    source_message_ref = str(message.get("ts") or "").strip() or None
    run_id = (
        str(payload.get("callback_id") or "").strip()
        or str((payload.get("container") or {}).get("message_ts") or "").strip()
        or None
    )

    reason: Optional[str] = None
    value = str(action_obj.get("value") or "")
    if value.startswith("{"):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                reason = str(parsed.get("justification") or parsed.get("reason") or "").strip() or None
        except Exception:
            pass
    if action == "reject" and not reason:
        reason = "rejected_over_budget_in_slack" if variant == "budget_reject" else "rejected_in_slack"
    if action == "request_info" and not reason:
        reason = "budget_adjustment_requested_in_slack"
    if action == "approve" and variant == "budget_override" and not reason:
        reason = "Approved over budget in Slack"

    if action == "reject" and not reason:
        raise ApprovalActionContractError("reason_required", "Reject action requires reason", 400)

    key_seed = {
        "channel": "slack",
        "organization_id": organization_id,
        "gmail_id": gmail_id,
        "action": action,
        "variant": variant,
        "actor_id": actor_id,
        "source_message_ref": source_message_ref,
        "source_channel_id": source_channel_id,
        "request_ts": str(request_ts or ""),
        "reason": reason or "",
    }

    return NormalizedApprovalAction(
        ap_item_id=None,
        run_id=run_id,
        action=action,
        actor_id=actor_id,
        actor_display=actor_display,
        reason=reason,
        source_channel="slack",
        source_channel_id=source_channel_id,
        source_message_ref=source_message_ref,
        request_ts=str(request_ts or "") or None,
        idempotency_key=_stable_action_key(key_seed),
        gmail_id=gmail_id,
        organization_id=organization_id,
        action_variant=variant,
        raw_action=action_id,
        raw_payload={"action_id": action_id},
    )


def _canonical_teams_action(action: str) -> Tuple[str, Optional[str]]:
    action = str(action or "").strip().lower()
    if action in {"approve", "approve_invoice", "post_to_erp"}:
        return "approve", None
    if action in {"approve_budget_override", "approve_override"}:
        return "approve", "budget_override"
    if action in {"request_info", "request_budget_adjustment", "request_adjustment"}:
        return "request_info", "budget_adjustment"
    if action in {"reject", "reject_invoice"}:
        return "reject", None
    if action in {"reject_budget"}:
        return "reject", "budget_reject"
    raise ApprovalActionContractError("unsupported_action", f"Unsupported Teams action: {action}", 400)


def normalize_teams_action(
    payload: Dict[str, Any],
    *,
    claims: Optional[Dict[str, Any]],
    organization_id: str,
) -> NormalizedApprovalAction:
    raw_action = str(payload.get("action") or "").strip().lower()
    if not raw_action:
        raise ApprovalActionContractError("missing_action", "Teams action is required", 400)
    action, variant = _canonical_teams_action(raw_action)

    gmail_id = str(payload.get("email_id") or payload.get("gmail_id") or "").strip()
    if not gmail_id:
        raise ApprovalActionContractError("missing_email_id", "Teams action missing email_id", 400)

    actor_id = str(
        payload.get("actor")
        or payload.get("user_email")
        or (claims or {}).get("oid")
        or (claims or {}).get("appid")
        or "teams_user"
    ).strip()
    actor_display = str(payload.get("actor_display") or payload.get("actor") or payload.get("user_email") or actor_id)
    source_channel_id = str(payload.get("conversation_id") or payload.get("channel_id") or "").strip() or None
    source_message_ref = str(payload.get("message_id") or payload.get("activity_id") or "").strip() or None
    run_id = str(payload.get("run_id") or payload.get("correlation_id") or "").strip() or None

    reason = str(payload.get("justification") or payload.get("reason") or "").strip() or None
    if action == "reject" and not reason:
        reason = "rejected_over_budget_in_teams" if variant == "budget_reject" else "rejected_in_teams"
    if action == "request_info" and not reason:
        reason = "budget_adjustment_requested_in_teams"
    if action == "approve" and variant == "budget_override" and not reason:
        reason = "Approved over budget in Teams"

    if action == "reject" and not reason:
        raise ApprovalActionContractError("reason_required", "Reject action requires reason", 400)

    request_ts = (
        str(payload.get("request_ts") or payload.get("timestamp") or "").strip()
        or (str((claims or {}).get("iat")) if (claims or {}).get("iat") else None)
    )
    key_seed = {
        "channel": "teams",
        "organization_id": organization_id,
        "gmail_id": gmail_id,
        "action": action,
        "variant": variant,
        "actor_id": actor_id,
        "source_message_ref": source_message_ref,
        "source_channel_id": source_channel_id,
        "request_ts": str(request_ts or ""),
        "reason": reason or "",
    }

    return NormalizedApprovalAction(
        ap_item_id=None,
        run_id=run_id,
        action=action,
        actor_id=actor_id,
        actor_display=actor_display,
        reason=reason,
        source_channel="teams",
        source_channel_id=source_channel_id,
        source_message_ref=source_message_ref,
        request_ts=request_ts,
        idempotency_key=_stable_action_key(key_seed),
        gmail_id=gmail_id,
        organization_id=organization_id,
        action_variant=variant,
        raw_action=raw_action,
        raw_payload={"action": raw_action},
    )
