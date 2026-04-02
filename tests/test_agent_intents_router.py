from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

import clearledgr.api.agent_intents as agent_intents_module

from clearledgr.api.agent_intents import router
from clearledgr.core.auth import TokenData, get_current_user, require_ops_user


def _fake_user() -> TokenData:
    return TokenData(
        user_id="ops-user",
        email="ops@example.com",
        organization_id="default",
        role="owner",
        exp=datetime.now(timezone.utc) + timedelta(hours=1),
    )


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = _fake_user
    app.dependency_overrides[require_ops_user] = _fake_user
    return TestClient(app)


def test_preview_intent_returns_structured_unhandled_runtime_error(monkeypatch):
    class _FakeRuntime:
        def preview_intent(self, intent, input_payload):
            raise RuntimeError(f"boom:{intent}:{input_payload.get('ap_item_id')}")

    monkeypatch.setattr(agent_intents_module, "_runtime_for_request", lambda *args, **kwargs: _FakeRuntime())

    client = _build_client()
    response = client.post(
        "/api/agent/intents/preview",
        json={
            "intent": "review_ap_item",
            "input": {"ap_item_id": "ap-123"},
            "organization_id": "default",
        },
    )

    assert response.status_code == 500
    assert response.json() == {
        "detail": {
            "code": "agent_intent_runtime_error",
            "message": "Unexpected agent runtime failure",
        }
    }
