"""Team-invite email composition + send.

Lives outside the FastAPI router so the body composition and
delivery-state shape stay testable without the workspace_shell
import chain (which pulls in psycopg via core.auth → core.database).

Used by /api/workspace/team/invites POST. The handler creates the
invite row first, computes the invite_link, then calls
:func:`send_team_invite_email` to fire the SMTP message. The
returned dict gets merged into the API response so the SPA can
toast accurately ("Invite sent" vs "Invite created — email isn't
configured, copy the link below").
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from clearledgr.services.transactional_email import (
    EmailDeliveryResult,
    send_transactional_email,
)

logger = logging.getLogger(__name__)


_ROLE_DISPLAY: Dict[str, str] = {
    "ap_clerk": "AP Clerk",
    "ap_manager": "AP Manager",
    "financial_controller": "Financial Controller",
    "cfo": "CFO",
    "read_only": "Read Only",
}


def _role_label(role: str) -> str:
    return _ROLE_DISPLAY.get(role, str(role or "").replace("_", " ").title())


def build_invite_email(
    *,
    invite_link: str,
    inviter_email: str,
    org_name: str,
    role: str,
) -> Dict[str, str]:
    """Return ``{subject, body_text, body_html}`` for the invite mail.

    Pure function: no I/O, no side effects. Useful for tests +
    pre-flight preview. Both bodies include the invite link as the
    primary call-to-action so a mail client that strips HTML still
    produces a working invite. No tracking pixels, no analytics
    beacons — this is a transactional auth email.
    """
    role_label = _role_label(role)
    workspace_label = (org_name or "").strip() or "your Solden workspace"
    inviter_label = (inviter_email or "").strip() or "your team admin"

    subject = f"You're invited to {workspace_label} on Solden"
    body_text = (
        f"{inviter_label} invited you to join {workspace_label} on "
        f"Solden as {role_label}.\n\n"
        f"Accept the invite here:\n{invite_link}\n\n"
        "You can sign in with Google, Microsoft, or set a password "
        "for your email — whichever you prefer.\n\n"
        "This link expires in 7 days. If you weren't expecting this "
        "invite you can ignore this email.\n\n"
        "— The Solden team"
    )
    body_html = (
        "<p>"
        f"<strong>{inviter_label}</strong> invited you to join "
        f"<strong>{workspace_label}</strong> on Solden as "
        f"<strong>{role_label}</strong>."
        "</p>"
        "<p>"
        f'<a href="{invite_link}" '
        'style="display:inline-block;padding:10px 18px;'
        'background:#0A1F44;color:#fff;text-decoration:none;'
        'border-radius:6px;font-weight:600">Accept invite</a>'
        "</p>"
        "<p style=\"color:#5a6b80;font-size:13px\">"
        "Sign in with Google, Microsoft, or set a password for your "
        "email — whichever you prefer."
        "</p>"
        "<p style=\"color:#5a6b80;font-size:13px\">"
        "This link expires in 7 days. If you weren't expecting this "
        "invite you can ignore this email."
        "</p>"
    )
    return {
        "subject": subject,
        "body_text": body_text,
        "body_html": body_html,
    }


def send_team_invite_email(
    *,
    recipient: str,
    invite_link: str,
    inviter_email: str,
    org_name: str,
    role: str,
) -> Dict[str, Any]:
    """Compose + send the invite email. Returns a structured delivery
    report; never raises.

    Result shape::

        {"delivered": bool, "skipped": bool, "error": Optional[str]}

    * ``delivered=True`` means SMTP actually accepted the message.
    * ``skipped=True`` means SMTP isn't configured (deployment
      state, not a failure). The invite row exists; the admin
      shares the link manually.
    * ``error`` carries the underlying SMTP error when delivery
      failed. ``None`` on success or skip.

    The handler is responsible for failing fast on missing
    ``recipient`` / ``invite_link``; this function returns a
    structured error rather than raising so the surrounding HTTP
    response stays predictable.
    """
    if not recipient or "@" not in str(recipient):
        return {
            "delivered": False,
            "skipped": False,
            "error": "invalid_recipient",
        }
    if not invite_link:
        return {
            "delivered": False,
            "skipped": False,
            "error": "missing_invite_link",
        }

    parts = build_invite_email(
        invite_link=invite_link,
        inviter_email=inviter_email,
        org_name=org_name,
        role=role,
    )

    try:
        result: EmailDeliveryResult = send_transactional_email(
            to_addr=recipient,
            subject=parts["subject"],
            body_text=parts["body_text"],
            body_html=parts["body_html"],
        )
    except Exception as exc:
        # send_transactional_email shouldn't raise across its public
        # boundary, but a misconfigured deployment can still surface
        # a stdlib SSL error during context creation. Catch defensively.
        logger.warning(
            "[team_invite_email] unexpected error sending to %s: %s",
            recipient, exc,
        )
        return {"delivered": False, "skipped": False, "error": str(exc)}

    return {
        "delivered": bool(result.ok),
        "skipped": bool(result.skipped),
        "error": result.error_message,
    }
