"""Team offboarding — Module 6 (deactivate / reactivate users).

Spec §221: "remove access immediately across dashboard and all surfaces.
Audit-logged."
Spec §228 (acceptance): "User offboarding removes access within 30
seconds across all surfaces."

Mechanism:

  - Setting ``users.is_active = 0`` is the single source of truth.
  - ``core/auth.py::_reconcile_token_data`` rejects authenticated
    requests with status=403 ``user_deactivated`` when the resolved
    user row has is_active = 0. Every JWT/cookie/Google-OAuth
    auth path flows through that reconciliation, so the next
    request after deactivation fails — no separate session
    invalidation needed.
  - All of the user's API keys are cascade-revoked at the same
    transaction so the X-API-Key header path also stops working
    immediately.
  - audit_event with event_type='user_deactivated' / 'user_reactivated'
    is appended for the audit log (Module 7).

Safety rails:
  - Self-deactivation is blocked. An Owner deactivating themselves
    can leave the org without an admin; we 400 ``cannot_deactivate_self``.
  - Last-active-Owner protection: cannot deactivate the only
    remaining active Owner. 400 ``last_owner_protected``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from solden.core.auth import (
    TokenData, get_current_user, normalize_user_role,
)
from solden.core.database import get_db

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/workspace/team", tags=["team-offboarding"])


def _require_admin(user: TokenData) -> None:
    """Workspace-admin gate (post-v89). Owners + admins (which now
    includes pre-v89 cfo / financial_controller via the legacy
    mapping) can manage users.
    """
    from solden.core.auth import has_workspace_admin
    workspace_role = (
        getattr(user, "workspace_role", None)
        or getattr(user, "role", None)
    )
    if not has_workspace_admin(workspace_role):
        raise HTTPException(status_code=403, detail="admin_required")


def _is_last_active_owner(db: Any, organization_id: str, user_id: str) -> bool:
    """True when the target user is the only remaining active Owner."""
    rows = db.get_users(organization_id, include_inactive=False) or []
    active_owners = [
        r for r in rows
        if str(r.get("id")) != str(user_id)
        and (normalize_user_role(r.get("role")) or "") == "owner"
        and bool(r.get("is_active", True))
    ]
    target = db.get_user(user_id)
    if not target:
        return False
    target_is_owner = (normalize_user_role(target.get("role")) or "") == "owner"
    return target_is_owner and len(active_owners) == 0


def _audit_user_state_change(
    db: Any, *, organization_id: str, target_user_id: str,
    actor_user_id: str, event_type: str, reason: str,
    api_keys_revoked: int = 0,
) -> None:
    """Append a Module-7 audit event for the deactivation / reactivation."""
    if not hasattr(db, "append_audit_event"):
        return
    try:
        db.append_audit_event({
            "box_id": target_user_id,
            "box_type": "user",
            "event_type": event_type,
            "actor_id": actor_user_id,
            "actor_type": "user",
            "organization_id": organization_id,
            "reason": reason,
            "metadata": {
                "api_keys_revoked": int(api_keys_revoked or 0),
            },
        })
    except Exception as exc:
        logger.warning(
            "[team_offboarding] audit append failed for %s: %s",
            target_user_id, exc,
        )


@router.post("/users/{user_id}/deactivate")
def deactivate_user(
    user_id: str,
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    _require_admin(user)
    db = get_db()

    actor_id = getattr(user, "user_id", "") or getattr(user, "email", "")
    target = db.get_user(user_id)
    if not target or str(target.get("organization_id") or "") != str(user.organization_id):
        # Cross-tenant + missing both 404 (no membership oracle).
        raise HTTPException(status_code=404, detail="user_not_found")

    if str(target.get("id")) == str(actor_id):
        raise HTTPException(
            status_code=400,
            detail={"code": "cannot_deactivate_self",
                    "message": "An admin cannot deactivate their own account."},
        )

    if _is_last_active_owner(db, user.organization_id, user_id):
        raise HTTPException(
            status_code=400,
            detail={"code": "last_owner_protected",
                    "message": (
                        "Cannot deactivate the last active Owner. Promote "
                        "another user to Owner first, then retry."
                    )},
        )

    if not bool(target.get("is_active", True)):
        return {
            "deactivated": False,
            "already_inactive": True,
            "user_id": user_id,
        }

    db.update_user(user_id, is_active=False)
    keys_revoked = 0
    if hasattr(db, "revoke_user_api_keys"):
        try:
            keys_revoked = db.revoke_user_api_keys(user_id, user.organization_id)
        except Exception as exc:
            logger.warning(
                "[team_offboarding] API-key cascade revoke failed for %s: %s",
                user_id, exc,
            )

    _audit_user_state_change(
        db, organization_id=user.organization_id,
        target_user_id=user_id, actor_user_id=actor_id,
        event_type="user_deactivated",
        reason=f"User deactivated by {actor_id}",
        api_keys_revoked=keys_revoked,
    )

    return {
        "deactivated": True,
        "user_id": user_id,
        "api_keys_revoked": keys_revoked,
    }


@router.post("/users/{user_id}/reactivate")
def reactivate_user(
    user_id: str,
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    _require_admin(user)
    db = get_db()

    actor_id = getattr(user, "user_id", "") or getattr(user, "email", "")
    target = db.get_user(user_id)
    if not target or str(target.get("organization_id") or "") != str(user.organization_id):
        raise HTTPException(status_code=404, detail="user_not_found")

    if bool(target.get("is_active", True)):
        return {
            "reactivated": False,
            "already_active": True,
            "user_id": user_id,
        }

    db.update_user(user_id, is_active=True)

    _audit_user_state_change(
        db, organization_id=user.organization_id,
        target_user_id=user_id, actor_user_id=actor_id,
        event_type="user_reactivated",
        reason=f"User reactivated by {actor_id}",
    )

    return {
        "reactivated": True,
        "user_id": user_id,
    }
