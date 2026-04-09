from datetime import datetime, timezone, timedelta

from clearledgr.core.auth import TokenData, _reconcile_token_data


class _DummyDb:
    def __init__(self, *, by_id=None, by_email=None):
        self._by_id = by_id or {}
        self._by_email = by_email or {}

    def get_user(self, user_id):
        return self._by_id.get(user_id)

    def get_user_by_email(self, email):
        return self._by_email.get(str(email or "").lower())


def test_reconcile_token_data_prefers_canonical_user_role(monkeypatch):
    canonical = {
        "id": "USR-admin",
        "email": "mo@clearledgr.com",
        "organization_id": "default",
        "role": "admin",
    }
    monkeypatch.setattr(
        "clearledgr.core.auth._get_db",
        lambda: _DummyDb(by_id={"USR-admin": canonical}),
    )
    token_data = TokenData(
        user_id="USR-admin",
        email="mo@clearledgr.com",
        organization_id="default",
        role="operator",
        exp=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    resolved = _reconcile_token_data(token_data)

    assert resolved.user_id == "USR-admin"
    assert resolved.email == "mo@clearledgr.com"
    assert resolved.organization_id == "default"
    # Phase 2.3: roles are normalized to canonical thesis values on
    # every reconcile call. Legacy "admin" maps to "financial_controller".
    assert resolved.role == "financial_controller"


def test_reconcile_token_data_falls_back_to_email_when_user_id_is_stale(monkeypatch):
    canonical = {
        "id": "USR-admin",
        "email": "mo@clearledgr.com",
        "organization_id": "default",
        "role": "admin",
    }
    monkeypatch.setattr(
        "clearledgr.core.auth._get_db",
        lambda: _DummyDb(by_email={"mo@clearledgr.com": canonical}),
    )
    token_data = TokenData(
        user_id="legacy-stale-id",
        email="mo@clearledgr.com",
        organization_id="default",
        role="operator",
        exp=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    resolved = _reconcile_token_data(token_data)

    assert resolved.user_id == "USR-admin"
    assert resolved.email == "mo@clearledgr.com"
    assert resolved.organization_id == "default"
    # Phase 2.3: roles are normalized to canonical thesis values on
    # every reconcile call. Legacy "admin" maps to "financial_controller".
    assert resolved.role == "financial_controller"
