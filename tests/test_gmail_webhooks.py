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

def _fake_request(headers=None):
    """Build a FastAPI-Request-like object whose .headers.get returns strings."""
    from unittest.mock import MagicMock
    hdrs = {k: v for k, v in (headers or {}).items()}
    req = MagicMock()
    req.headers.get = lambda name, default="": hdrs.get(name, default)
    return req


class TestEnforcePushVerifier:
    def test_no_secret_no_oidc_in_dev_passes(self, monkeypatch):
        monkeypatch.setenv("ENV", "dev")
        monkeypatch.delenv("GMAIL_PUSH_SHARED_SECRET", raising=False)
        monkeypatch.delenv("GMAIL_PUSH_AUDIENCE", raising=False)
        monkeypatch.delenv("GMAIL_PUSH_INVOKER_SA", raising=False)
        _enforce_push_verifier(_fake_request())  # should not raise

    def test_no_secret_no_oidc_in_prod_raises_503(self, monkeypatch):
        monkeypatch.setenv("ENV", "production")
        monkeypatch.delenv("GMAIL_PUSH_SHARED_SECRET", raising=False)
        monkeypatch.delenv("GMAIL_PUSH_AUDIENCE", raising=False)
        monkeypatch.delenv("GMAIL_PUSH_INVOKER_SA", raising=False)
        monkeypatch.delenv("GMAIL_PUSH_ALLOW_UNVERIFIED_IN_PROD", raising=False)
        with pytest.raises(HTTPException) as exc_info:
            _enforce_push_verifier(_fake_request())
        assert exc_info.value.status_code == 503

    def test_shared_secret_correct_token_passes(self, monkeypatch):
        monkeypatch.setenv("GMAIL_PUSH_SHARED_SECRET", "my-token")
        monkeypatch.delenv("GMAIL_PUSH_AUDIENCE", raising=False)
        monkeypatch.delenv("GMAIL_PUSH_INVOKER_SA", raising=False)
        _enforce_push_verifier(_fake_request({"X-Gmail-Push-Token": "my-token"}))

    def test_shared_secret_wrong_token_raises_401(self, monkeypatch):
        monkeypatch.setenv("GMAIL_PUSH_SHARED_SECRET", "my-token")
        monkeypatch.delenv("GMAIL_PUSH_AUDIENCE", raising=False)
        monkeypatch.delenv("GMAIL_PUSH_INVOKER_SA", raising=False)
        with pytest.raises(HTTPException) as exc_info:
            _enforce_push_verifier(_fake_request({"X-Gmail-Push-Token": "wrong"}))
        assert exc_info.value.status_code == 401


class TestOIDCPushVerifier:
    """OIDC path — Google Pub/Sub signs requests with a JWT bearer.

    We patch google.oauth2.id_token.verify_oauth2_token to simulate signature
    validation without actually fetching Google's public keys.
    """

    _EXPECTED_AUDIENCE = "https://api.clearledgr.com/gmail/push"
    _EXPECTED_INVOKER = "pubsub-invoker@clearledgr.iam.gserviceaccount.com"

    def _enable_oidc(self, monkeypatch):
        monkeypatch.setenv("GMAIL_PUSH_AUDIENCE", self._EXPECTED_AUDIENCE)
        monkeypatch.setenv("GMAIL_PUSH_INVOKER_SA", self._EXPECTED_INVOKER)
        monkeypatch.delenv("GMAIL_PUSH_SHARED_SECRET", raising=False)

    def _patch_verify(self, monkeypatch, claims=None, raises=None):
        from unittest.mock import MagicMock
        mock_verify = MagicMock()
        if raises is not None:
            mock_verify.side_effect = raises
        else:
            mock_verify.return_value = claims
        monkeypatch.setattr(
            "google.oauth2.id_token.verify_oauth2_token",
            mock_verify,
        )
        return mock_verify

    def test_valid_oidc_bearer_passes(self, monkeypatch):
        self._enable_oidc(monkeypatch)
        mock_verify = self._patch_verify(monkeypatch, claims={
            "iss": "https://accounts.google.com",
            "aud": self._EXPECTED_AUDIENCE,
            "email": self._EXPECTED_INVOKER,
            "email_verified": True,
        })
        req = _fake_request({"Authorization": "Bearer eyFAKE.TOKEN.HERE"})
        _enforce_push_verifier(req)  # should not raise
        # verify_oauth2_token was called with audience enforcement
        assert mock_verify.called
        args, kwargs = mock_verify.call_args
        assert kwargs.get("audience") == self._EXPECTED_AUDIENCE

    def test_oidc_wrong_email_raises_401(self, monkeypatch):
        self._enable_oidc(monkeypatch)
        self._patch_verify(monkeypatch, claims={
            "iss": "https://accounts.google.com",
            "aud": self._EXPECTED_AUDIENCE,
            "email": "attacker@evil.com",
            "email_verified": True,
        })
        with pytest.raises(HTTPException) as exc_info:
            _enforce_push_verifier(_fake_request({"Authorization": "Bearer token"}))
        assert exc_info.value.status_code == 401
        assert "bad_principal" in str(exc_info.value.detail)

    def test_oidc_email_unverified_raises_401(self, monkeypatch):
        self._enable_oidc(monkeypatch)
        self._patch_verify(monkeypatch, claims={
            "iss": "https://accounts.google.com",
            "aud": self._EXPECTED_AUDIENCE,
            "email": self._EXPECTED_INVOKER,
            "email_verified": False,
        })
        with pytest.raises(HTTPException) as exc_info:
            _enforce_push_verifier(_fake_request({"Authorization": "Bearer token"}))
        assert exc_info.value.status_code == 401

    def test_oidc_wrong_issuer_raises_401(self, monkeypatch):
        self._enable_oidc(monkeypatch)
        self._patch_verify(monkeypatch, claims={
            "iss": "https://evil.example/",
            "aud": self._EXPECTED_AUDIENCE,
            "email": self._EXPECTED_INVOKER,
            "email_verified": True,
        })
        with pytest.raises(HTTPException) as exc_info:
            _enforce_push_verifier(_fake_request({"Authorization": "Bearer token"}))
        assert exc_info.value.status_code == 401
        assert "bad_issuer" in str(exc_info.value.detail)

    def test_oidc_invalid_signature_raises_401(self, monkeypatch):
        self._enable_oidc(monkeypatch)
        self._patch_verify(monkeypatch, raises=ValueError("Token expired"))
        with pytest.raises(HTTPException) as exc_info:
            _enforce_push_verifier(_fake_request({"Authorization": "Bearer expired"}))
        assert exc_info.value.status_code == 401
        assert "oidc_invalid" in str(exc_info.value.detail)

    def test_missing_auth_header_falls_through_to_shared_secret(self, monkeypatch):
        # Even with OIDC configured, a request with no Authorization header
        # should fall through to the shared-secret path (useful for local
        # testing or manual curl). If neither is set in prod, 503.
        self._enable_oidc(monkeypatch)
        monkeypatch.setenv("GMAIL_PUSH_SHARED_SECRET", "dev-token")
        _enforce_push_verifier(_fake_request({"X-Gmail-Push-Token": "dev-token"}))

    def test_oidc_not_configured_does_not_call_verify(self, monkeypatch):
        # If GMAIL_PUSH_AUDIENCE or GMAIL_PUSH_INVOKER_SA is missing, OIDC
        # path is skipped entirely — no signature validation attempted.
        monkeypatch.delenv("GMAIL_PUSH_AUDIENCE", raising=False)
        monkeypatch.delenv("GMAIL_PUSH_INVOKER_SA", raising=False)
        monkeypatch.setenv("GMAIL_PUSH_SHARED_SECRET", "dev-token")
        mock_verify = self._patch_verify(monkeypatch, claims={})
        _enforce_push_verifier(_fake_request({
            "Authorization": "Bearer anything",
            "X-Gmail-Push-Token": "dev-token",
        }))
        # Shared secret accepted without consulting OIDC
        assert not mock_verify.called

    def test_empty_bearer_token_falls_through(self, monkeypatch):
        self._enable_oidc(monkeypatch)
        monkeypatch.setenv("GMAIL_PUSH_SHARED_SECRET", "dev")
        mock_verify = self._patch_verify(monkeypatch, claims={})
        # "Bearer " with no token → treated as absent, shared secret used
        with pytest.raises(HTTPException) as exc_info:
            _enforce_push_verifier(_fake_request({
                "Authorization": "Bearer ",
                "X-Gmail-Push-Token": "wrong",
            }))
        assert exc_info.value.status_code == 401
        assert not mock_verify.called


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
