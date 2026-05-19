"""Coverage for the team-invite email helper.

Tests the composition layer (``build_invite_email``) directly,
and the send-and-translate layer (``send_team_invite_email``) by
mocking ``send_transactional_email``. The helper is module-local
to solden.services so these tests don't pull in the
workspace_shell → core.auth → core.database → psycopg chain.
"""

from __future__ import annotations

from unittest.mock import patch

from solden.services import team_invite_email as tie_module
from solden.services.team_invite_email import (
    build_invite_email,
    send_team_invite_email,
)
from solden.services.transactional_email import EmailDeliveryResult


# ─── build_invite_email: pure composition ──────────────────────────


def test_build_email_contains_invite_link_in_both_bodies() -> None:
    """If a mail client strips HTML, the plain-text fallback must
    still produce a working invite. Both bodies need the link."""
    parts = build_invite_email(
        invite_link="https://workspace.solden.example/accept?token=abc",
        inviter_email="alice@example.com",
        org_name="Acme",
        role="ap_clerk",
    )
    assert parts["body_text"].count(
        "https://workspace.solden.example/accept?token=abc"
    ) >= 1
    assert parts["body_html"].count(
        "https://workspace.solden.example/accept?token=abc"
    ) >= 1


def test_build_email_subject_uses_workspace_name() -> None:
    parts = build_invite_email(
        invite_link="https://x", inviter_email="alice@example.com",
        org_name="Acme Corp", role="ap_clerk",
    )
    assert "Acme Corp" in parts["subject"]


def test_build_email_subject_falls_back_when_org_name_missing() -> None:
    """A freshly-created org sometimes has an empty name; the
    subject must still be readable."""
    parts = build_invite_email(
        invite_link="https://x", inviter_email="alice@example.com",
        org_name="", role="ap_clerk",
    )
    assert "Solden" in parts["subject"]


def test_build_email_maps_canonical_role_to_display() -> None:
    """Display labels must match what the SPA shows so the invitee
    isn't surprised when they see the role on first login."""
    parts = build_invite_email(
        invite_link="https://x", inviter_email="alice@example.com",
        org_name="Acme", role="financial_controller",
    )
    assert "Financial Controller" in parts["body_text"]
    assert "Financial Controller" in parts["body_html"]


def test_build_email_titlecases_unknown_roles() -> None:
    """A future role not in the display map shouldn't crash — fall
    back to title-cased underscores."""
    parts = build_invite_email(
        invite_link="https://x", inviter_email="alice@example.com",
        org_name="Acme", role="vendor_onboarder",
    )
    assert "Vendor Onboarder" in parts["body_text"]


def test_build_email_inviter_label_falls_back_when_empty() -> None:
    parts = build_invite_email(
        invite_link="https://x", inviter_email="",
        org_name="Acme", role="ap_clerk",
    )
    assert "your team admin" in parts["body_text"]


# ─── send_team_invite_email: delivery-state translation ────────────


def test_send_returns_delivered_on_smtp_success() -> None:
    """SMTP accepted the message → delivered=True, skipped=False,
    error=None. This is the happy path: the SPA toasts 'Invite sent'."""
    with patch.object(
        tie_module, "send_transactional_email",
        return_value=EmailDeliveryResult(ok=True),
    ):
        result = send_team_invite_email(
            recipient="new@example.com",
            invite_link="https://workspace.example/accept?token=t",
            inviter_email="alice@example.com",
            org_name="Acme",
            role="ap_clerk",
        )
    assert result == {"delivered": True, "skipped": False, "error": None}


def test_send_returns_skipped_when_smtp_not_configured() -> None:
    """No SMTP env vars set → transactional_email returns skipped=True.
    The invite row still exists; the SPA toasts 'Invite created — copy
    the link'."""
    with patch.object(
        tie_module, "send_transactional_email",
        return_value=EmailDeliveryResult(ok=False, skipped=True),
    ):
        result = send_team_invite_email(
            recipient="new@example.com",
            invite_link="https://x",
            inviter_email="alice@example.com",
            org_name="Acme",
            role="ap_clerk",
        )
    assert result == {"delivered": False, "skipped": True, "error": None}


def test_send_returns_error_when_smtp_fails() -> None:
    """SMTP connect / auth / send failure → delivered=False,
    skipped=False, error=<smtp message>. SPA toasts 'Email delivery
    failed — copy the link'."""
    with patch.object(
        tie_module, "send_transactional_email",
        return_value=EmailDeliveryResult(
            ok=False, error_message="connection refused",
        ),
    ):
        result = send_team_invite_email(
            recipient="new@example.com",
            invite_link="https://x",
            inviter_email="alice@example.com",
            org_name="Acme",
            role="ap_clerk",
        )
    assert result == {
        "delivered": False, "skipped": False, "error": "connection refused",
    }


def test_send_swallows_unexpected_exceptions() -> None:
    """An exception escaping send_transactional_email (rare but
    possible: SSL context errors) must not propagate — the invite
    row exists and the HTTP response shouldn't 500."""
    with patch.object(
        tie_module, "send_transactional_email",
        side_effect=RuntimeError("ssl context init failed"),
    ):
        result = send_team_invite_email(
            recipient="new@example.com",
            invite_link="https://x",
            inviter_email="alice@example.com",
            org_name="Acme",
            role="ap_clerk",
        )
    assert result["delivered"] is False
    assert result["skipped"] is False
    assert "ssl context init failed" in (result["error"] or "")


def test_send_rejects_invalid_recipient_without_attempting_smtp() -> None:
    """A guard at the boundary: bad email shouldn't trigger an SMTP
    connection. Returns a structured error instead of None / raising."""
    with patch.object(tie_module, "send_transactional_email") as mock_send:
        result = send_team_invite_email(
            recipient="not-an-email",
            invite_link="https://x",
            inviter_email="alice@example.com",
            org_name="Acme",
            role="ap_clerk",
        )
    assert result == {
        "delivered": False, "skipped": False, "error": "invalid_recipient",
    }
    mock_send.assert_not_called()


def test_send_rejects_missing_invite_link() -> None:
    """If somehow the caller passes no link, fail loud rather than
    sending an email with no call-to-action."""
    with patch.object(tie_module, "send_transactional_email") as mock_send:
        result = send_team_invite_email(
            recipient="new@example.com",
            invite_link="",
            inviter_email="alice@example.com",
            org_name="Acme",
            role="ap_clerk",
        )
    assert result == {
        "delivered": False, "skipped": False, "error": "missing_invite_link",
    }
    mock_send.assert_not_called()


def test_send_passes_composed_parts_to_smtp() -> None:
    """The send helper composes via build_invite_email and forwards
    every part. Verify the SMTP call gets the right kwargs."""
    captured = {}

    def fake_send(**kwargs):
        captured.update(kwargs)
        return EmailDeliveryResult(ok=True)

    with patch.object(tie_module, "send_transactional_email", side_effect=fake_send):
        send_team_invite_email(
            recipient="new@example.com",
            invite_link="https://workspace.example/accept?token=t9",
            inviter_email="alice@example.com",
            org_name="Acme",
            role="cfo",
        )
    assert captured["to_addr"] == "new@example.com"
    assert "Acme" in captured["subject"]
    assert "https://workspace.example/accept?token=t9" in captured["body_text"]
    assert "https://workspace.example/accept?token=t9" in captured["body_html"]
    assert "CFO" in captured["body_text"]
