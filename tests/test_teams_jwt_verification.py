import asyncio
import hmac
import hashlib
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.services import teams_api as teams_api_module


def _sign_teams(body: bytes, timestamp: str, secret: str) -> str:
    sig_base = f"v1:{timestamp}:{body.decode()}"
    digest = hmac.new(secret.encode(), sig_base.encode(), hashlib.sha256).hexdigest()
    return f"v1={digest}"


def test_verify_teams_request_accepts_valid_jwt(monkeypatch):
    async def _jwt_ok(headers):
        return True

    monkeypatch.setattr(teams_api_module, "verify_teams_jwt", _jwt_ok)
    monkeypatch.setattr(teams_api_module, "TEAMS_LEGACY_HMAC_ALLOWED", False)

    result = asyncio.run(teams_api_module.verify_teams_request(b'{"ok": true}', {"authorization": "Bearer token"}))
    assert result is True


def test_verify_teams_request_rejects_invalid_jwt_without_legacy(monkeypatch):
    async def _jwt_fail(headers):
        return False

    monkeypatch.setattr(teams_api_module, "verify_teams_jwt", _jwt_fail)
    monkeypatch.setattr(teams_api_module, "TEAMS_LEGACY_HMAC_ALLOWED", False)

    result = asyncio.run(teams_api_module.verify_teams_request(b'{"ok": true}', {"authorization": "Bearer token"}))
    assert result is False


def test_verify_teams_request_legacy_hmac_fallback(monkeypatch):
    async def _jwt_fail(headers):
        return False

    monkeypatch.setattr(teams_api_module, "verify_teams_jwt", _jwt_fail)
    monkeypatch.setattr(teams_api_module, "TEAMS_LEGACY_HMAC_ALLOWED", True)
    monkeypatch.setattr(teams_api_module, "TEAMS_SIGNING_SECRET", "teams-secret")

    body = b'{"action":"approve","ap_item_id":"AP-1"}'
    ts = str(int(time.time()))
    signature = _sign_teams(body, ts, "teams-secret")

    result = asyncio.run(
        teams_api_module.verify_teams_request(
            body,
            {
                "x-teams-request-timestamp": ts,
                "x-teams-signature": signature,
            },
        )
    )
    assert result is True


def test_verify_teams_signature_rejects_replay(monkeypatch):
    monkeypatch.setattr(teams_api_module, "TEAMS_SIGNING_SECRET", "teams-secret")
    body = b'{"action":"approve"}'
    stale_ts = str(int(time.time()) - 301)
    signature = _sign_teams(body, stale_ts, "teams-secret")
    assert teams_api_module.verify_teams_signature(body, stale_ts, signature) is False

