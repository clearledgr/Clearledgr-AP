"""Tests for clearledgr.api.gmail_webhooks — Pub/Sub validation and OAuth state."""

import base64
import hashlib
import hmac
import json
import time

import pytest
from fastapi import HTTPException

from clearledgr.api import workspace_shell as workspace_shell_module
from clearledgr.api.gmail_webhooks import (
    _validate_push_payload,
    _unsign_oauth_state,
    _resolve_user_org_id,
    _enforce_push_verifier,
    _is_prod_like_env,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pubsub_body(email="user@test.com", history_id="12345"):
    notification = {"emailAddress": email, "historyId": history_id}
    encoded = base64.urlsafe_b64encode(json.dumps(notification).encode()).decode()
    return {"message": {"data": encoded}}


def _sign_state(payload: dict, secret: str) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


# ---------------------------------------------------------------------------
# _validate_push_payload
# ---------------------------------------------------------------------------

class TestValidatePushPayload:
    def test_valid_payload(self):
        body = _make_pubsub_body("user@acme.com", "99999")
        result = _validate_push_payload(body)
        assert result["email_address"] == "user@acme.com"
        assert result["history_id"] == "99999"

    def test_missing_message(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_push_payload({})
        assert exc_info.value.status_code == 400

    def test_message_not_dict(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_push_payload({"message": "not-a-dict"})
        assert exc_info.value.status_code == 400

    def test_missing_data_field(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_push_payload({"message": {}})
        assert exc_info.value.status_code == 400

    def test_empty_data_field(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_push_payload({"message": {"data": "  "}})
        assert exc_info.value.status_code == 400

    def test_invalid_base64(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_push_payload({"message": {"data": "not-base64!!!"}})
        assert exc_info.value.status_code == 400

    def test_valid_base64_but_not_json(self):
        encoded = base64.urlsafe_b64encode(b"not-json").decode()
        with pytest.raises(HTTPException) as exc_info:
            _validate_push_payload({"message": {"data": encoded}})
        assert exc_info.value.status_code == 400

    def test_missing_email_address(self):
        notification = {"historyId": "123"}
        encoded = base64.urlsafe_b64encode(json.dumps(notification).encode()).decode()
        with pytest.raises(HTTPException) as exc_info:
            _validate_push_payload({"message": {"data": encoded}})
        assert exc_info.value.status_code == 400

    def test_missing_history_id(self):
        notification = {"emailAddress": "user@test.com"}
        encoded = base64.urlsafe_b64encode(json.dumps(notification).encode()).decode()
        with pytest.raises(HTTPException) as exc_info:
            _validate_push_payload({"message": {"data": encoded}})
        assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# _unsign_oauth_state
# ---------------------------------------------------------------------------

class TestUnsignOAuthState:
    def test_valid_state(self, monkeypatch):
        monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret")
        payload = {"user_id": "u1", "org_id": "acme", "iat": int(time.time())}
        state = _sign_state(payload, "test-secret")
        result = _unsign_oauth_state(state)
        assert result["user_id"] == "u1"
        assert result["org_id"] == "acme"

    def test_empty_state(self, monkeypatch):
        monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret")
        with pytest.raises(HTTPException) as exc_info:
            _unsign_oauth_state("")
        assert exc_info.value.status_code == 400

    def test_no_dot_separator(self, monkeypatch):
        monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret")
        with pytest.raises(HTTPException) as exc_info:
            _unsign_oauth_state("nodot")
        assert exc_info.value.status_code == 400

    def test_tampered_signature(self, monkeypatch):
        monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret")
        payload = {"user_id": "u1", "iat": int(time.time())}
        state = _sign_state(payload, "test-secret")
        tampered = state[:-4] + "0000"
        with pytest.raises(HTTPException) as exc_info:
            _unsign_oauth_state(tampered)
        assert "signature" in exc_info.value.detail

    def test_expired_state(self, monkeypatch):
        monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret")
        payload = {"user_id": "u1", "iat": int(time.time()) - 2000}
        state = _sign_state(payload, "test-secret")
        with pytest.raises(HTTPException) as exc_info:
            _unsign_oauth_state(state)
        assert "expired" in exc_info.value.detail

    def test_accepts_workspace_signed_state_with_dev_secret_fallback(self, monkeypatch):
        monkeypatch.setenv("ENV", "dev")
        monkeypatch.delenv("CLEARLEDGR_SECRET_KEY", raising=False)
        payload = {"user_id": "u1", "org_id": "acme", "iat": int(time.time())}
        state = workspace_shell_module._sign_state(payload)
        result = _unsign_oauth_state(state)
        assert result["user_id"] == "u1"
        assert result["org_id"] == "acme"


# ---------------------------------------------------------------------------
# _enforce_push_verifier
# ---------------------------------------------------------------------------

class TestEnforcePushVerifier:
    def test_no_secret_in_dev_passes(self, monkeypatch):
        monkeypatch.setenv("ENV", "dev")
        monkeypatch.delenv("GMAIL_PUSH_SHARED_SECRET", raising=False)
        from unittest.mock import MagicMock
        request = MagicMock()
        _enforce_push_verifier(request)  # should not raise

    def test_no_secret_in_prod_raises_503(self, monkeypatch):
        monkeypatch.setenv("ENV", "production")
        monkeypatch.delenv("GMAIL_PUSH_SHARED_SECRET", raising=False)
        monkeypatch.delenv("GMAIL_PUSH_ALLOW_UNVERIFIED_IN_PROD", raising=False)
        from unittest.mock import MagicMock
        request = MagicMock()
        with pytest.raises(HTTPException) as exc_info:
            _enforce_push_verifier(request)
        assert exc_info.value.status_code == 503

    def test_correct_token_passes(self, monkeypatch):
        monkeypatch.setenv("GMAIL_PUSH_SHARED_SECRET", "my-token")
        from unittest.mock import MagicMock
        request = MagicMock()
        request.headers.get.side_effect = lambda h: "my-token" if h == "X-Gmail-Push-Token" else None
        _enforce_push_verifier(request)  # should not raise

    def test_wrong_token_raises_401(self, monkeypatch):
        monkeypatch.setenv("GMAIL_PUSH_SHARED_SECRET", "my-token")
        from unittest.mock import MagicMock
        request = MagicMock()
        request.headers.get.return_value = "wrong-token"
        with pytest.raises(HTTPException) as exc_info:
            _enforce_push_verifier(request)
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# _resolve_user_org_id
# ---------------------------------------------------------------------------

class TestResolveUserOrgId:
    def test_returns_user_org(self, monkeypatch):
        from unittest.mock import MagicMock, patch
        mock_db = MagicMock()
        mock_db.get_user.return_value = {"organization_id": "acme-corp"}
        with patch("clearledgr.api.gmail_webhooks.get_db", return_value=mock_db):
            assert _resolve_user_org_id("user@test.com") == "acme-corp"

    def test_returns_default_on_missing_user(self, monkeypatch):
        from unittest.mock import MagicMock, patch
        mock_db = MagicMock()
        mock_db.get_user.return_value = None
        with patch("clearledgr.api.gmail_webhooks.get_db", return_value=mock_db):
            assert _resolve_user_org_id("unknown@test.com") == "default"

    def test_returns_default_on_db_error(self, monkeypatch):
        from unittest.mock import MagicMock, patch
        mock_db = MagicMock()
        mock_db.get_user.side_effect = Exception("DB down")
        with patch("clearledgr.api.gmail_webhooks.get_db", return_value=mock_db):
            assert _resolve_user_org_id("user@test.com") == "default"


# ---------------------------------------------------------------------------
# _is_prod_like_env
# ---------------------------------------------------------------------------

class TestIsProdLikeEnv:
    @pytest.mark.parametrize("env_val", ["prod", "production", "stage", "staging"])
    def test_prod_like(self, monkeypatch, env_val):
        monkeypatch.setenv("ENV", env_val)
        assert _is_prod_like_env() is True

    @pytest.mark.parametrize("env_val", ["dev", "test", "local"])
    def test_not_prod(self, monkeypatch, env_val):
        monkeypatch.setenv("ENV", env_val)
        assert _is_prod_like_env() is False
