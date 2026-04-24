"""Onboarding magic-link single-use enforcement.

Threat model: a vendor's onboarding magic-link can be forwarded,
leaked into a shared inbox, or logged somewhere unexpected. Once
the onboarding session reaches a terminal state (ACTIVE or
CLOSED_UNSUCCESSFUL) the link must stop working at the **token
layer**, not only at the session ``is_active`` guard.

Defense-in-depth: if a future refactor bypasses or weakens the
``is_active`` check in :func:`clearledgr.core.portal_auth.require_portal_token`,
the revoked-token guard must still fail the request.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import database as db_module  # noqa: E402


@pytest.fixture()
def db(tmp_path, monkeypatch):
    inst = db_module.get_db()
    inst.initialize()
    return inst


def _seed_session_and_token(db) -> tuple[str, str, str]:
    """Create an active onboarding session + issue one token.

    Returns ``(session_id, raw_token, token_id)``.
    """
    session = db.create_vendor_onboarding_session(
        organization_id="default",
        vendor_name="Acme Inc",
        invited_by="ap@default",
    )
    session_id = session["id"]
    result = db.generate_onboarding_token(
        session_id=session_id,
        issued_by="ap@default",
    )
    assert result is not None, "token issue returned None"
    raw_token, token_row = result
    return session_id, raw_token, token_row["id"]


class TestTerminalStateAutoRevokesTokens:
    def test_active_state_revokes_live_token(self, db):
        session_id, raw_token, token_id = _seed_session_and_token(db)
        assert db.validate_onboarding_token(raw_token) is not None

        # Walk the full happy path: invited → kyc → bank_verify
        # → bank_verified → active. Validate the token is still alive
        # at every intermediate step.
        for target in ("kyc", "bank_verify", "bank_verified", "ready_for_erp"):
            db.transition_onboarding_session_state(
                session_id, target_state=target, actor_id="agent",
            )
            assert db.validate_onboarding_token(raw_token) is not None, (
                f"token should remain valid mid-flow at state {target!r}"
            )

        # Terminal edge — token must be revoked by the time
        # transition_onboarding_session_state returns.
        db.transition_onboarding_session_state(
            session_id, target_state="active", actor_id="agent",
        )
        assert db.validate_onboarding_token(raw_token) is None, (
            "token must be revoked the moment the session reaches ACTIVE"
        )
        row = db.get_onboarding_token_by_id(token_id)
        assert row is not None
        assert row.get("revoked_at"), "token row must carry a revoked_at timestamp"
        assert "session_terminal:active" in (row.get("revoke_reason") or "")

    def test_closed_unsuccessful_revokes_live_token(self, db):
        session_id, raw_token, token_id = _seed_session_and_token(db)
        assert db.validate_onboarding_token(raw_token) is not None

        db.transition_onboarding_session_state(
            session_id,
            target_state="closed_unsuccessful",
            actor_id="ap@default",
            reason="vendor declined",
        )
        assert db.validate_onboarding_token(raw_token) is None
        row = db.get_onboarding_token_by_id(token_id)
        assert row.get("revoked_at")
        assert "session_terminal:closed_unsuccessful" in (row.get("revoke_reason") or "")

    def test_non_terminal_blocked_state_keeps_token_alive(self, db):
        # BLOCKED is not terminal — a session can recover from it.
        # Auto-revoke in that state would break the recovery flow.
        session_id, raw_token, token_id = _seed_session_and_token(db)
        db.transition_onboarding_session_state(
            session_id, target_state="kyc", actor_id="agent",
        )
        db.transition_onboarding_session_state(
            session_id, target_state="blocked", actor_id="agent",
        )
        assert db.validate_onboarding_token(raw_token) is not None, (
            "BLOCKED is recoverable — token must remain usable"
        )


class TestForwardedLinkAfterCompletion:
    """Realistic threat model: vendor forwards their onboarding email
    to a colleague or accidentally leaves the link in a shared doc
    AFTER completing onboarding. Any click must fail closed."""

    def test_post_completion_forwarded_link_is_rejected(self, db):
        session_id, raw_token, _token_id = _seed_session_and_token(db)
        # Complete onboarding fully.
        for target in ("kyc", "bank_verify", "bank_verified", "ready_for_erp", "active"):
            db.transition_onboarding_session_state(
                session_id, target_state=target, actor_id="agent",
            )

        # A third party clicks the forwarded link. Every layer of
        # defense must reject:
        #   1. Token-layer: validate_onboarding_token returns None
        #      because the token was revoked on the active transition.
        #   2. Session-layer (belt-and-braces): is_active == 0.
        assert db.validate_onboarding_token(raw_token) is None
        session = db.get_onboarding_session_by_id(session_id)
        assert not session.get("is_active"), (
            "terminal transition must flip is_active off"
        )


class TestTokenRotationMidFlow:
    """When a new token is issued mid-flow, the OLD token must die.

    This already existed as a behaviour (generate_onboarding_token
    calls revoke_session_tokens internally), but codifying as a test
    prevents a regression that would bring us back to "two live
    tokens, both work"."""

    def test_reissuing_token_revokes_the_old_one(self, db):
        session_id, first_raw, first_id = _seed_session_and_token(db)
        assert db.validate_onboarding_token(first_raw) is not None

        # Issue a fresh token for the same session.
        result = db.generate_onboarding_token(
            session_id=session_id,
            issued_by="ap@default",
        )
        assert result is not None
        second_raw, _ = result

        assert db.validate_onboarding_token(first_raw) is None, (
            "re-issuing must revoke the prior live token — otherwise "
            "an attacker with the old link retains access"
        )
        assert db.validate_onboarding_token(second_raw) is not None, (
            "the freshly-issued token must be valid"
        )
