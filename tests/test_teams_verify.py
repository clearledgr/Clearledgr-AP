from __future__ import annotations

import sys
from pathlib import Path

import httpx
import jwt
import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import teams_verify as teams_verify_module


class _SigningKey:
    key = "fake-public-key"


class _JWKSClientStub:
    def get_signing_key_from_jwt(self, token: str):
        assert token
        return _SigningKey()


@pytest.fixture(autouse=True)
def _reset_jwks_cache():
    teams_verify_module._jwks_cache.clear()
    yield
    teams_verify_module._jwks_cache.clear()


def test_verify_teams_token_rejects_malformed_authorization_header(monkeypatch):
    monkeypatch.setenv("TEAMS_APP_ID", "teams-app-id")

    with pytest.raises(HTTPException) as exc:
        teams_verify_module.verify_teams_token("not-bearer token")
    assert exc.value.status_code == 401
    assert "malformed" in str(exc.value.detail).lower()

    with pytest.raises(HTTPException) as exc2:
        teams_verify_module.verify_teams_token("Bearer   ")
    assert exc2.value.status_code == 401
    assert "empty bearer token" in str(exc2.value.detail).lower()


def test_verify_teams_token_requires_teams_app_id(monkeypatch):
    monkeypatch.delenv("TEAMS_APP_ID", raising=False)
    with pytest.raises(HTTPException) as exc:
        teams_verify_module.verify_teams_token("Bearer token")
    assert exc.value.status_code == 503
    assert "not configured" in str(exc.value.detail).lower()


def test_verify_teams_token_maps_jwks_fetch_error(monkeypatch):
    monkeypatch.setenv("TEAMS_APP_ID", "teams-app-id")

    def _raise_http_error():
        request = httpx.Request("GET", "https://login.botframework.com")
        raise httpx.ConnectError("boom", request=request)

    monkeypatch.setattr(teams_verify_module, "_get_jwks_client", _raise_http_error)
    with pytest.raises(HTTPException) as exc:
        teams_verify_module.verify_teams_token("Bearer fake-token")
    assert exc.value.status_code == 502


def test_verify_teams_token_maps_invalid_issuer(monkeypatch):
    monkeypatch.setenv("TEAMS_APP_ID", "teams-app-id")
    monkeypatch.setattr(teams_verify_module, "_get_jwks_client", lambda: _JWKSClientStub())
    monkeypatch.setattr(
        teams_verify_module.jwt,
        "decode",
        lambda *args, **kwargs: (_ for _ in ()).throw(jwt.InvalidIssuerError("bad issuer")),
    )
    with pytest.raises(HTTPException) as exc:
        teams_verify_module.verify_teams_token("Bearer fake-token")
    assert exc.value.status_code == 401
    assert "issuer" in str(exc.value.detail).lower()


def test_verify_teams_token_maps_invalid_audience(monkeypatch):
    monkeypatch.setenv("TEAMS_APP_ID", "teams-app-id")
    monkeypatch.setattr(teams_verify_module, "_get_jwks_client", lambda: _JWKSClientStub())
    monkeypatch.setattr(
        teams_verify_module.jwt,
        "decode",
        lambda *args, **kwargs: (_ for _ in ()).throw(jwt.InvalidAudienceError("bad aud")),
    )
    with pytest.raises(HTTPException) as exc:
        teams_verify_module.verify_teams_token("Bearer fake-token")
    assert exc.value.status_code == 401
    assert "audience" in str(exc.value.detail).lower()


def test_verify_teams_token_maps_invalid_token(monkeypatch):
    monkeypatch.setenv("TEAMS_APP_ID", "teams-app-id")
    monkeypatch.setattr(teams_verify_module, "_get_jwks_client", lambda: _JWKSClientStub())
    monkeypatch.setattr(
        teams_verify_module.jwt,
        "decode",
        lambda *args, **kwargs: (_ for _ in ()).throw(jwt.InvalidTokenError("bad token")),
    )
    with pytest.raises(HTTPException) as exc:
        teams_verify_module.verify_teams_token("Bearer fake-token")
    assert exc.value.status_code == 401
    assert "invalid teams token" in str(exc.value.detail).lower()


def test_verify_teams_token_success_returns_claims(monkeypatch):
    monkeypatch.setenv("TEAMS_APP_ID", "teams-app-id")
    monkeypatch.setattr(teams_verify_module, "_get_jwks_client", lambda: _JWKSClientStub())
    expected = {"iss": "https://api.botframework.com", "aud": "teams-app-id", "oid": "user-1"}
    monkeypatch.setattr(teams_verify_module.jwt, "decode", lambda *args, **kwargs: expected)

    claims = teams_verify_module.verify_teams_token("Bearer fake-token")
    assert claims == expected


def test_get_jwks_client_fetches_metadata_and_caches(monkeypatch):
    calls = {"get": 0, "pyjwk": 0}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"jwks_uri": "https://login.botframework.com/keys"}

    class _HttpxClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, timeout=10):
            calls["get"] += 1
            assert "openidconfiguration" in url
            return _Response()

    class _PyJWKClientFake:
        def __init__(self, uri, cache_keys=True):
            calls["pyjwk"] += 1
            self.uri = uri
            self.cache_keys = cache_keys

    monkeypatch.setattr(teams_verify_module.httpx, "Client", _HttpxClient)
    monkeypatch.setattr(teams_verify_module, "PyJWKClient", _PyJWKClientFake)
    times = iter([1000.0, 1001.0])
    monkeypatch.setattr(teams_verify_module.time, "time", lambda: next(times))

    first = teams_verify_module._get_jwks_client()
    second = teams_verify_module._get_jwks_client()

    assert isinstance(first, _PyJWKClientFake)
    assert first is second
    assert first.uri == "https://login.botframework.com/keys"
    assert calls["get"] == 1
    assert calls["pyjwk"] == 1
