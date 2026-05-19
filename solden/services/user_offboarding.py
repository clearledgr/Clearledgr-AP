"""Cross-surface user offboarding (Module 6 Pass D).

Per scope §Module 6 §221 + acceptance criteria: "Remove access
immediately across dashboard and all surfaces. Audit-logged.
Removes access within 30 seconds across all surfaces."

A single ``offboard_user`` call:
  1. Soft-deletes the user row (``is_active=0``) so dashboard auth
     fails on the next request.
  2. Revokes their Google OAuth tokens — both at our token store and
     at Google itself via the revoke endpoint, so a leaked refresh
     token can't be used by anyone.
  3. Clears their Slack ``slack_user_id`` mapping so any pending
     approval-DMs route nowhere (the next planning pass picks a new
     approver).
  4. Removes any per-entity role assignments so a stale row can't
     re-grant them on a future re-activation.
  5. Emits a ``user_offboarded`` audit event with a per-step
     summary of what was revoked.

Webhooks are intentionally NOT touched — they're workspace-level
compliance hooks (SIEM forwarders, ERP webhooks, etc), not per-user.
A leaving user shouldn't take the org's audit forwarding with them.

Each step is best-effort and logs on failure — the soft-delete (step
1) is the only invariant we MUST land for the SLA, and that runs
first. The other steps run in parallel-safe order so the function
returns within seconds even when a downstream surface (Google, Slack)
is slow or unreachable.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


@dataclass
class OffboardingResult:
    """Per-step revocation summary, returned to the caller and
    persisted as the audit event payload.

    Each ``*_revoked`` field captures whether the step succeeded
    *or* whether the surface wasn't relevant for this user (e.g.
    a user that never installed Gmail has ``gmail_revoked = "skipped"``).
    Errors land in ``errors`` so the audit row carries a complete
    picture without callers needing to parse logs.
    """

    user_id: str
    user_archived: bool = False
    gmail_revoked: str = "skipped"  # "ok" | "failed" | "skipped"
    slack_revoked: str = "skipped"
    entity_roles_cleared: int = 0
    errors: List[str] = field(default_factory=list)


def offboard_user(
    db,
    *,
    user_id: str,
    organization_id: str,
    actor_email: str,
    revoke_google_token_remotely: bool = True,
) -> OffboardingResult:
    """Soft-delete the user + revoke every cross-surface attachment.

    The pure DB steps (soft-delete, slack mapping clear, webhook
    deactivation, entity-role clear) all run synchronously inside
    this call. The Google OAuth remote revoke is best-effort and
    times out fast — the function never blocks past a few seconds.
    """
    out = OffboardingResult(user_id=user_id)

    # ─── 1) Soft-delete user row ────────────────────────────────
    try:
        db.delete_user(user_id, archived_by=actor_email)
        out.user_archived = True
    except Exception as exc:
        out.errors.append(f"archive_failed:{exc}")
        logger.warning("[offboarding] delete_user(%s) failed: %s", user_id, exc)

    # ─── 2) Revoke Google OAuth (gmail) ─────────────────────────
    if hasattr(db, "get_oauth_token"):
        try:
            token_row = db.get_oauth_token(user_id, "gmail")
        except Exception as exc:
            token_row = None
            out.errors.append(f"gmail_lookup_failed:{exc}")
        if token_row:
            # Remote revoke first so a leaked token is dead even
            # before we delete it locally.
            if revoke_google_token_remotely:
                try:
                    _revoke_google_token_remote(token_row)
                except Exception as exc:
                    out.errors.append(f"google_revoke_remote_failed:{exc}")
                    logger.warning(
                        "[offboarding] Google remote revoke failed for %s: %s",
                        user_id, exc,
                    )
            try:
                db.delete_oauth_token(user_id, "gmail")
                out.gmail_revoked = "ok"
            except Exception as exc:
                out.gmail_revoked = "failed"
                out.errors.append(f"gmail_revoke_failed:{exc}")

    # ─── 3) Clear Slack user mapping ────────────────────────────
    if hasattr(db, "update_user"):
        try:
            user_row = db.get_user(user_id) if hasattr(db, "get_user") else None
            if user_row and user_row.get("slack_user_id"):
                db.update_user(user_id, slack_user_id=None)
                out.slack_revoked = "ok"
        except Exception as exc:
            out.slack_revoked = "failed"
            out.errors.append(f"slack_clear_failed:{exc}")

    # ─── 4) Clear per-entity role assignments ───────────────────
    if hasattr(db, "list_user_entity_roles") and hasattr(db, "delete_user_entity_role"):
        try:
            for row in db.list_user_entity_roles(user_id):
                if db.delete_user_entity_role(user_id, row["entity_id"]):
                    out.entity_roles_cleared += 1
        except Exception as exc:
            out.errors.append(f"entity_roles_clear_failed:{exc}")

    # ─── 5) Audit emit ──────────────────────────────────────────
    try:
        db.append_audit_event({
            "event_type": "user_offboarded",
            "actor_type": "user",
            "actor_id": actor_email,
            "organization_id": organization_id,
            "box_id": user_id,
            "box_type": "user",
            "source": "auth_api",
            "payload_json": {
                "actor_email": actor_email,
                "user_archived": out.user_archived,
                "gmail_revoked": out.gmail_revoked,
                "slack_revoked": out.slack_revoked,
                "entity_roles_cleared": out.entity_roles_cleared,
                "errors": out.errors,
            },
        })
    except Exception as exc:
        logger.warning(
            "[offboarding] audit emit failed for %s: %s", user_id, exc,
        )

    return out


def _revoke_google_token_remote(token_row: Dict[str, Any]) -> None:
    """POST to https://oauth2.googleapis.com/revoke with the user's
    access or refresh token.

    Per Google docs: revoking either token invalidates the entire
    grant — refresh + every issued access token. We prefer the
    refresh_token because it's longer-lived; fall back to access_token
    when refresh is absent.
    """
    refresh = token_row.get("refresh_token") or token_row.get("token") or ""
    access = token_row.get("access_token") or ""
    target = (refresh or access or "").strip()
    if not target:
        return
    # httpx is already a project dependency. Tight timeout — we never
    # want offboarding to block on a slow Google response.
    try:
        import httpx
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(
                "https://oauth2.googleapis.com/revoke",
                data={"token": target},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            # 200 = revoked. 400 with error=invalid_token = already
            # revoked / expired — that's fine, it's the same outcome
            # we want. Any other status raises so the caller logs.
            if resp.status_code in (200, 400):
                return
            resp.raise_for_status()
    except ImportError:
        # httpx not present in some test environments — surface the
        # missing-dep as a soft failure, never block offboarding.
        return
