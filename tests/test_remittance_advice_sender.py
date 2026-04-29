"""Tests for the C5 carry-over — Gmail-token-resolved sender.

Covers:
  * No users in the org -> resolve returns None (existing
    'no_gmail' audit fallback applies).
  * Users in the org but none have Gmail tokens -> None.
  * Users with Gmail tokens -> resolver returns a callable; role
    preference (owner > admin > ap_clerk > others).
  * Sender callable wraps GmailAPIClient send_message; auth
    failure surfaces as a {status:error,reason:...} dict.
  * record_payment_confirmation auto-resolves Gmail sender on the
    confirmed path; the rendered email lands at the resolved
    user's connected Gmail.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.services.remittance_advice_sender import (  # noqa: E402
    _select_user_for_org,
    resolve_gmail_sender_for_org,
)


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgA", organization_name="Acme")
    return inst


def _create_user(db, *, name: str, role: str, org: str = "orgA") -> str:
    """Create a user via the auth_store API. Returns the auto-
    generated user_id (UUID) which is what oauth_tokens key against."""
    user = db.create_user(
        email=f"{name}@orgA.com",
        name=name,
        organization_id=org,
        role=role,
        password_hash=None,
        google_id=name,
        is_active=True,
    )
    return user["id"]


def _seed_gmail_token(db, *, user_id: str) -> None:
    """Insert a Gmail oauth token for the user. The store encrypts
    on store; we rely on the Fernet key being set in conftest."""
    db.save_oauth_token(
        user_id=user_id,
        provider="gmail",
        access_token="fake-access-token",
        refresh_token="fake-refresh-token",
        expires_at=(
            datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        ),
        email=f"{user_id}@gmail.example",
    )


# ─── Selector ─────────────────────────────────────────────────────


def test_select_returns_none_when_no_users(db):
    assert _select_user_for_org(db, "orgA") is None


def test_select_returns_none_when_no_gmail_tokens(db):
    _create_user(db, name="alice", role="owner")
    assert _select_user_for_org(db, "orgA") is None


def test_select_picks_owner_over_clerk(db):
    _create_user(db, name="alice", role="ap_clerk")
    bob_id = _create_user(db, name="bob", role="owner")
    _seed_gmail_token(db, user_id=_user_id_for(db, "alice"))
    _seed_gmail_token(db, user_id=bob_id)

    selected = _select_user_for_org(db, "orgA")
    assert selected is not None
    assert selected["id"] == bob_id  # owner wins over ap_clerk


def test_select_falls_back_when_no_preferred_role(db):
    """Role outside the preferred list still wins as fallback when
    it's the only Gmail-connected user."""
    cid = _create_user(db, name="charlie", role="cfo")
    _seed_gmail_token(db, user_id=cid)
    selected = _select_user_for_org(db, "orgA")
    assert selected is not None
    assert selected["id"] == cid


def test_select_skips_user_without_gmail(db):
    _create_user(db, name="alice", role="owner")
    bob_id = _create_user(db, name="bob", role="ap_clerk")
    _seed_gmail_token(db, user_id=bob_id)  # only bob has Gmail
    selected = _select_user_for_org(db, "orgA")
    assert selected is not None
    assert selected["id"] == bob_id


def _user_id_for(db, name: str) -> str:
    for u in db.get_users("orgA"):
        if u.get("name") == name:
            return u["id"]
    raise KeyError(name)


# ─── resolve_gmail_sender_for_org ─────────────────────────────────


def test_resolver_returns_none_when_no_eligible_user(db):
    sender = resolve_gmail_sender_for_org(db, "orgA")
    assert sender is None


def test_resolver_returns_callable_with_user(db):
    aid = _create_user(db, name="alice", role="owner")
    _seed_gmail_token(db, user_id=aid)
    sender = resolve_gmail_sender_for_org(db, "orgA")
    assert sender is not None
    assert callable(sender)


def test_sender_callable_calls_gmail_send(db):
    aid = _create_user(db, name="alice", role="owner")
    _seed_gmail_token(db, user_id=aid)
    sender = resolve_gmail_sender_for_org(db, "orgA")

    captured = {}

    async def fake_send_message(self, to, subject, body, **kwargs):
        captured.update({"to": to, "subject": subject, "body": body})
        return {"id": "msg-1"}

    async def fake_ensure(self):
        return True

    with patch(
        "clearledgr.services.gmail_api.GmailAPIClient.ensure_authenticated",
        new=fake_ensure,
    ), patch(
        "clearledgr.services.gmail_api.GmailAPIClient.send_message",
        new=fake_send_message,
    ):
        result = sender(
            to="vendor@vendor-x.com",
            subject="Remittance",
            body="paid",
        )

    assert result["id"] == "msg-1"
    assert captured["to"] == "vendor@vendor-x.com"
    assert captured["subject"] == "Remittance"


def test_sender_returns_error_when_token_not_authenticated(db):
    aid = _create_user(db, name="alice", role="owner")
    _seed_gmail_token(db, user_id=aid)
    sender = resolve_gmail_sender_for_org(db, "orgA")

    async def fake_ensure(self):
        return False  # token expired and refresh failed

    with patch(
        "clearledgr.services.gmail_api.GmailAPIClient.ensure_authenticated",
        new=fake_ensure,
    ):
        result = sender(to="x@x.com", subject="s", body="b")

    assert result["status"] == "error"
    assert result["reason"] == "gmail_token_not_authenticated"


# ─── End-to-end via record_payment_confirmation ──────────────────


def test_payment_tracking_resolves_gmail_sender_automatically(db):
    """The C5 hook calls resolve_gmail_sender_for_org and passes the
    sender into send_remittance_advice — so a confirmed payment
    actually sends when there's a Gmail-connected user."""
    from clearledgr.services.payment_tracking import (
        record_payment_confirmation,
    )

    aid = _create_user(db, name="alice", role="owner")
    _seed_gmail_token(db, user_id=aid)
    db.upsert_vendor_profile(
        "orgA", "Vendor X",
        primary_contact_email="ap@vendor-x.com",
    )
    item = db.create_ap_item({
        "id": "AP-c5-end-to-end",
        "organization_id": "orgA",
        "vendor_name": "Vendor X",
        "amount": 1500.0,
        "currency": "EUR",
        "state": "received",
    })
    for s in (
        "validated", "needs_approval", "approved",
        "ready_to_post", "posted_to_erp", "awaiting_payment",
    ):
        db.update_ap_item(item["id"], state=s)

    sent = []

    async def fake_send_message(self, to, subject, body, **kwargs):
        sent.append({"to": to, "subject": subject, "body": body})
        return {"id": "gm-1"}

    async def fake_ensure(self):
        return True

    with patch(
        "clearledgr.services.gmail_api.GmailAPIClient.ensure_authenticated",
        new=fake_ensure,
    ), patch(
        "clearledgr.services.gmail_api.GmailAPIClient.send_message",
        new=fake_send_message,
    ):
        record_payment_confirmation(
            db,
            organization_id="orgA",
            ap_item_id=item["id"],
            payment_id="PAY-C5-CO",
            source="manual",
            status="confirmed",
            amount=1500.0,
            currency="EUR",
        )

    assert len(sent) == 1
    assert sent[0]["to"] == "ap@vendor-x.com"
    assert "Remittance" in sent[0]["subject"]
