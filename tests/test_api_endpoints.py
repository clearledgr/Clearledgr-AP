"""
Tests for API Endpoints

Tests the FastAPI endpoints for the Clearledgr API.
"""

from datetime import datetime, timedelta, timezone
import asyncio
import importlib
import os
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock

from clearledgr.core.auth import TokenData, get_current_user

os.environ.setdefault("CLEARLEDGR_SKIP_DEFERRED_STARTUP", "true")


class _LazyProxy:
    def __init__(self, loader):
        object.__setattr__(self, "_lazy_loader", loader)
        object.__setattr__(self, "_lazy_target", None)

    def _load(self):
        target = object.__getattribute__(self, "_lazy_target")
        if target is None:
            target = object.__getattribute__(self, "_lazy_loader")()
            object.__setattr__(self, "_lazy_target", target)
        return target

    def __getattr__(self, name):
        if name == "__test__":
            return False
        return getattr(self._load(), name)

    def __setattr__(self, name, value):
        if name in {"_lazy_loader", "_lazy_target"}:
            object.__setattr__(self, name, value)
            return
        setattr(self._load(), name, value)

    def __delattr__(self, name):
        if name in {"_lazy_loader", "_lazy_target"}:
            raise AttributeError(name)
        delattr(self._load(), name)

    def __call__(self, *args, **kwargs):
        return self._load()(*args, **kwargs)


def _lazy_module(name: str) -> _LazyProxy:
    return _LazyProxy(lambda: importlib.import_module(name))


main_module = _lazy_module("main")
app = _LazyProxy(lambda: main_module.app)
gmail_extension_module = _lazy_module("clearledgr.api.gmail_extension")
agent_intents_module = _lazy_module("clearledgr.api.agent_intents")
gmail_webhooks_module = _lazy_module("clearledgr.api.gmail_webhooks")
workspace_shell_module = _lazy_module("clearledgr.api.workspace_shell")
auth_module = _lazy_module("clearledgr.api.auth")

client = _LazyProxy(lambda: TestClient(main_module.app))


@pytest.fixture(autouse=True)
def _reset_test_client_state():
    client.cookies.clear()
    app.dependency_overrides.clear()
    try:
        yield
    finally:
        client.cookies.clear()
        app.dependency_overrides.clear()


class TestHealthEndpoints:
    """Test health check endpoints."""
    
    def test_health_check(self):
        """Test main health endpoint."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
    
    def test_v1_health(self):
        """Test v1 API health."""
        response = client.get("/v1/health")
        assert response.status_code == 200


class TestAuthEndpoints:
    """Test authentication endpoints."""

    def _admin_override(self):
        """Override get_current_user with an admin TokenData."""
        from clearledgr.core.auth import get_current_user
        app.dependency_overrides[get_current_user] = lambda: TokenData(
            user_id="admin-user", email="admin@test.com", role="admin",
            organization_id="test-org",
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    def test_register_requires_auth(self):
        """Registration without auth returns 401."""
        response = client.post("/auth/register", json={
            "email": "test@example.com",
            "password": "StrongPass123!",
            "name": "Test User",
            "organization_id": "test-org",
        })
        assert response.status_code == 401

    def test_register_validation(self):
        """Test registration validates password strength."""
        self._admin_override()
        # Weak password should fail
        response = client.post("/auth/register", json={
            "email": "test@example.com",
            "password": "weak",
            "name": "Test User",
            "organization_id": "test-org",
        })
        assert response.status_code == 422  # Validation error

    def test_register_success(self):
        """Test successful registration."""
        self._admin_override()
        response = client.post("/auth/register", json={
            "email": "newuser@example.com",
            "password": "StrongPass123!",
            "name": "New User",
            "organization_id": "test-org",
        })
        # May fail if user exists, but should be 200 or 400
        assert response.status_code in [200, 400]
    
    def test_google_identity_endpoint_removed(self):
        """The /google-identity endpoint was a security backdoor — it minted
        JWTs from self-reported email without validating a Google token.
        Verify it no longer exists."""
        response = client.post("/auth/google-identity", json={
            "email": "user@company.com",
            "google_id": "google-123456",
        })
        assert response.status_code in (404, 405)

    def test_google_callback_uses_one_time_auth_code_exchange(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-google-client")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-google-secret")
        monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
        monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-token-key")

        state = auth_module._sign_google_state(
            {
                "organization_id": "default",
                "redirect_path": "/workspace",
                "iat": int(datetime.now(timezone.utc).timestamp()),
                "nonce": "nonce-1",
            }
        )

        class _Resp:
            def __init__(self, status_code, payload):
                self.status_code = status_code
                self._payload = payload
                self.content = b"{}"

            def json(self):
                return self._payload

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url, data=None, headers=None):
                if "oauth2.googleapis.com/token" in url:
                    return _Resp(200, {"access_token": "google-access-token"})
                return _Resp(404, {})

            async def get(self, url, headers=None):
                if "www.googleapis.com/oauth2/v2/userinfo" in url:
                    return _Resp(200, {"email": "user@company.com", "id": "google-uid-1"})
                return _Resp(404, {})

        monkeypatch.setattr(auth_module.httpx, "AsyncClient", _FakeAsyncClient)

        fake_user = SimpleNamespace(
            id="user-123",
            email="user@company.com",
            organization_id="default",
            role="user",
        )
        monkeypatch.setattr("clearledgr.core.auth.get_user_by_email", lambda _email: None)
        monkeypatch.setattr("clearledgr.core.auth.create_user_from_google", lambda **_kwargs: fake_user)
        response = client.get(
            "/auth/google/callback",
            params={"code": "google-code-1", "state": state},
            follow_redirects=False,
        )
        assert response.status_code in {302, 307}
        location = response.headers.get("location") or ""
        assert "auth_code=" in location
        assert "token=" not in location
        assert "refresh_token=" not in location

        from urllib.parse import parse_qs, urlparse

        parsed = parse_qs(urlparse(location).query)
        auth_code = str(parsed.get("auth_code", [""])[0])
        assert auth_code

        exchange = client.post("/auth/google/exchange", json={"auth_code": auth_code})
        assert exchange.status_code == 200
        payload = exchange.json()
        assert payload.get("access_token")
        assert payload.get("refresh_token")

        reused = client.post("/auth/google/exchange", json={"auth_code": auth_code})
        assert reused.status_code == 400
        assert reused.json().get("detail") == "invalid_auth_code"


class TestAPRetryPostEndpoint:
    @staticmethod
    def _fake_user():
        return TokenData(
            user_id="ap-user-1",
            email="ap-user@example.com",
            organization_id="default",
            role="operator",
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    def test_retry_post_uses_runtime_retry_intent_success(self):
        fake_db = MagicMock()
        fake_db.get_ap_item.return_value = {
            "id": "ap-1",
            "organization_id": "default",
            "state": "failed_post",
            "thread_id": "gmail-thread-retry-1",
        }
        captured = {}

        async def _runtime_execute(self, intent, payload=None, *, idempotency_key=None):
            captured["intent"] = intent
            captured["payload"] = dict(payload or {})
            captured["idempotency_key"] = idempotency_key
            return {
                "status": "posted",
                "ap_item_id": "ap-1",
                "erp_reference": "ERP-RET-1",
                "result": {"status": "recovered", "erp_reference": "ERP-RET-1"},
            }

        app.dependency_overrides[get_current_user] = self._fake_user
        try:
            with patch("clearledgr.services.ap_item_service.get_db", return_value=fake_db):
                with patch(
                    "clearledgr.services.finance_agent_runtime.FinanceAgentRuntime.execute_intent",
                    _runtime_execute,
                ):
                    response = client.post("/api/ap/items/ap-1/retry-post?organization_id=default")
        finally:
            app.dependency_overrides.pop(get_current_user, None)

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "posted"
        assert payload["erp_reference"] == "ERP-RET-1"
        assert captured["intent"] == "retry_recoverable_failures"
        assert captured["payload"]["ap_item_id"] == "ap-1"
        assert captured["payload"]["email_id"] == "gmail-thread-retry-1"

    def test_retry_post_returns_502_when_runtime_retry_still_failing(self):
        fake_db = MagicMock()
        fake_db.get_ap_item.return_value = {
            "id": "ap-2",
            "organization_id": "default",
            "state": "failed_post",
            "thread_id": "gmail-thread-retry-2",
        }

        async def _runtime_execute(self, intent, payload=None, *, idempotency_key=None):
            return {"status": "error", "reason": "connector_timeout"}

        app.dependency_overrides[get_current_user] = self._fake_user
        try:
            with patch("clearledgr.services.ap_item_service.get_db", return_value=fake_db):
                with patch(
                    "clearledgr.services.finance_agent_runtime.FinanceAgentRuntime.execute_intent",
                    _runtime_execute,
                ):
                    response = client.post("/api/ap/items/ap-2/retry-post?organization_id=default")
        finally:
            app.dependency_overrides.pop(get_current_user, None)

        assert response.status_code == 502
        assert "connector_timeout" in str(response.json().get("detail") or "")


class TestGmailWebhooks:
    """Test Gmail Pub/Sub webhook endpoints."""

    @staticmethod
    def _fake_user(user_id: str = "gmail-user-1", role: str = "user"):
        return TokenData(
            user_id=user_id,
            email=f"{user_id}@example.com",
            organization_id="default",
            role=role,
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    
    def test_gmail_push_accepts_message(self):
        """Test that push endpoint accepts Pub/Sub messages."""
        import base64
        import json
        
        # Simulate Pub/Sub message
        notification = {
            "emailAddress": "test@example.com",
            "historyId": "12345",
        }
        encoded = base64.urlsafe_b64encode(json.dumps(notification).encode()).decode()
        
        response = client.post("/gmail/push", json={
            "message": {
                "data": encoded,
            },
            "subscription": "projects/test/subscriptions/test-sub",
        })
        
        # Should always return 200 to acknowledge
        assert response.status_code == 200
        assert response.json().get("status") == "ok"

    def test_gmail_push_rejects_invalid_payload(self):
        response = client.post("/gmail/push", json={})
        assert response.status_code == 400
        assert response.json().get("detail") == "invalid_pubsub_payload"

    def test_gmail_push_requires_shared_secret_when_configured(self, monkeypatch):
        monkeypatch.setenv("GMAIL_PUSH_SHARED_SECRET", "secret-123")
        response = client.post("/gmail/push", json={})
        assert response.status_code == 401
        assert response.json().get("detail") == "gmail_push_verification_failed"

    def test_gmail_push_prod_requires_verifier_secret_by_default(self, monkeypatch):
        monkeypatch.setenv("ENV", "production")
        monkeypatch.delenv("GMAIL_PUSH_SHARED_SECRET", raising=False)
        monkeypatch.delenv("GMAIL_PUSH_ALLOW_UNVERIFIED_IN_PROD", raising=False)
        response = client.post("/gmail/push", json={})
        assert response.status_code == 503
        assert response.json().get("detail") == "gmail_push_verifier_not_configured"

    def test_gmail_push_prod_can_allow_unverified_with_explicit_flag(self, monkeypatch):
        import base64
        import json

        monkeypatch.setenv("ENV", "production")
        monkeypatch.delenv("GMAIL_PUSH_SHARED_SECRET", raising=False)
        monkeypatch.setenv("GMAIL_PUSH_ALLOW_UNVERIFIED_IN_PROD", "true")

        notification = {"emailAddress": "test@example.com", "historyId": "12345"}
        encoded = base64.urlsafe_b64encode(json.dumps(notification).encode()).decode()
        response = client.post(
            "/gmail/push",
            json={"message": {"data": encoded}, "subscription": "projects/test/subscriptions/test-sub"},
        )
        assert response.status_code == 200
        assert response.json().get("status") == "ok"
    
    def test_gmail_status_requires_auth(self):
        response = client.get("/gmail/status/nonexistent-user")
        assert response.status_code == 401

    def test_gmail_status_not_connected_with_auth(self):
        """Test Gmail status for non-connected user with authenticated identity."""
        app.dependency_overrides[gmail_webhooks_module.get_current_user] = lambda: self._fake_user("nonexistent-user")
        try:
            response = client.get("/gmail/status/nonexistent-user")
        finally:
            app.dependency_overrides.pop(gmail_webhooks_module.get_current_user, None)
        assert response.status_code == 200
        data = response.json()
        assert data["connected"] is False

    def test_gmail_disconnect_requires_auth(self):
        response = client.post("/gmail/disconnect?user_id=gmail-user-1")
        assert response.status_code == 401

    def test_gmail_disconnect_blocks_cross_user_access(self):
        app.dependency_overrides[gmail_webhooks_module.get_current_user] = lambda: self._fake_user("gmail-user-1")
        try:
            response = client.post("/gmail/disconnect?user_id=another-user")
        finally:
            app.dependency_overrides.pop(gmail_webhooks_module.get_current_user, None)
        assert response.status_code == 403

    def test_gmail_authorize_route_removed(self):
        response = client.get(
            "/gmail/authorize",
            params={"user_id": "gmail-user-1", "redirect_url": "https://app.test/callback"},
        )
        assert response.status_code == 404

    def test_gmail_callback_requires_oauth_state(self):
        response = client.get("/gmail/callback?code=test-code")
        assert response.status_code == 400
        assert response.json().get("detail") == "missing_oauth_state"

    def test_gmail_callback_rejects_tampered_oauth_state(self, monkeypatch):
        monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
        response = client.get(
            "/gmail/callback",
            params={"code": "test-code", "state": "tampered-state-without-signature"},
        )
        assert response.status_code == 400
        assert response.json().get("detail") == "invalid_oauth_state"

    def test_gmail_callback_redirect_appends_success_with_existing_query(self, monkeypatch):
        monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
        state = workspace_shell_module._sign_state(
            {
                "organization_id": "default",
                "user_id": "gmail-user-1",
                "redirect_url": "/gmail/connected?source=oauth",
                "iat": int(datetime.now(timezone.utc).timestamp()),
                "nonce": "test-nonce",
            }
        )
        fake_token = SimpleNamespace(
            user_id="gmail-user-1",
            access_token="access-token",
            refresh_token="refresh-token",
            expires_at=int(datetime.now(timezone.utc).timestamp()) + 3600,
            email="ops@example.com",
        )
        fake_db = MagicMock()

        with patch.object(gmail_webhooks_module, "exchange_code_for_tokens", AsyncMock(return_value=fake_token)):
            with patch.object(gmail_webhooks_module, "token_store", MagicMock(store=MagicMock())):
                with patch.object(gmail_webhooks_module, "_should_setup_watch", return_value=False):
                    with patch.object(gmail_webhooks_module, "get_db", return_value=fake_db):
                        response = client.get(
                            "/gmail/callback",
                            params={"code": "test-code", "state": state},
                            follow_redirects=False,
                        )

        assert response.status_code in {302, 307}
        location = str(response.headers.get("location") or "")
        assert "/gmail/connected" in location
        assert "source=oauth" in location
        assert "success=true" in location

    def test_gmail_connected_page_renders_success_message(self):
        response = client.get("/gmail/connected?success=true")
        assert response.status_code == 200
        assert "Gmail connected" in response.text
        assert "Return to Gmail" in response.text
        assert "Monitoring active" in response.text

    def test_gmail_callback_preserves_existing_refresh_token_when_google_omits_one(self, monkeypatch):
        monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
        state = workspace_shell_module._sign_state(
            {
                "organization_id": "default",
                "user_id": "gmail-user-1",
                "redirect_url": "/workspace?page=integrations",
                "iat": int(datetime.now(timezone.utc).timestamp()),
                "nonce": "test-nonce-refresh",
            }
        )
        fake_token = SimpleNamespace(
            user_id="gmail-user-1",
            access_token="access-token",
            refresh_token="",
            expires_at=int(datetime.now(timezone.utc).timestamp()) + 3600,
            email="ops@example.com",
        )
        stored = {}

        class _TokenStore:
            def get(self, _user_id):
                return SimpleNamespace(refresh_token="preserved-refresh-token")

            def store(self, token):
                stored["token"] = token

        with patch.object(gmail_webhooks_module, "exchange_code_for_tokens", AsyncMock(return_value=fake_token)):
            with patch.object(gmail_webhooks_module, "token_store", _TokenStore()):
                with patch.object(gmail_webhooks_module, "_should_setup_watch", return_value=False):
                    with patch.object(gmail_webhooks_module, "GmailAPIClient", MagicMock()):
                        with patch.object(gmail_webhooks_module, "get_db", return_value=MagicMock()):
                            response = client.get(
                                "/gmail/callback",
                                params={"code": "test-code", "state": state},
                                follow_redirects=False,
                            )

        assert response.status_code in {200, 302, 307}
        assert stored["token"].refresh_token == "preserved-refresh-token"

    def test_process_single_email_propagates_org_to_invoice_handler(self):
        class _FakeClient:
            async def get_message(self, _message_id):
                return SimpleNamespace(
                    id="msg-1",
                    thread_id="thread-1",
                    subject="Invoice INV-1",
                    sender="billing@acme.test",
                    recipient="ap@company.test",
                    date=datetime.now(timezone.utc),
                    snippet="Invoice attached",
                    body_text="Please pay invoice INV-1 for $125.00",
                    body_html="",
                    labels=[],
                    attachments=[],
                )

            async def list_labels(self):
                return [{"id": "label-1", "name": "Clearledgr/Processed"}]

            async def create_label(self, _name):
                return {"id": "label-1", "name": "Clearledgr/Processed"}

            async def add_label(self, _message_id, _label_ids):
                return None

        class _FakeDB:
            def get_finance_email_by_gmail_id(self, _gmail_id):
                return None

            def save_finance_email(self, _email):
                return _email

        seen = {}

        async def _fake_process_invoice_email(*, organization_id: str, **_kwargs):
            seen["organization_id"] = organization_id
            return {"status": "ok"}

        with patch.object(
            gmail_webhooks_module,
            "classify_email_with_llm",
            AsyncMock(return_value={"type": "invoice", "confidence": 0.95}),
        ):
            with patch.object(
                gmail_webhooks_module,
                "process_invoice_email",
                AsyncMock(side_effect=_fake_process_invoice_email),
            ):
                with patch.object(
                    gmail_webhooks_module,
                    "process_payment_request_email",
                    AsyncMock(return_value={"status": "skipped"}),
                ):
                    with patch.object(gmail_webhooks_module, "get_db", return_value=_FakeDB()):
                        asyncio.run(
                            gmail_webhooks_module.process_single_email(
                                client=_FakeClient(),
                                message_id="msg-1",
                                user_id="gmail-user-1",
                                organization_id="tenant-42",
                            )
                        )

        assert seen.get("organization_id") == "tenant-42"

    def test_process_single_email_reprocesses_detected_email_without_ap_item(self):
        class _FakeClient:
            async def get_message(self, _message_id):
                return SimpleNamespace(
                    id="msg-retry-1",
                    thread_id="thread-retry-1",
                    subject="Invoice INV-RETRY-1",
                    sender="billing@acme.test",
                    recipient="ap@company.test",
                    date=datetime.now(timezone.utc),
                    snippet="Invoice attached",
                    body_text="Please pay invoice INV-RETRY-1 for $125.00",
                    body_html="",
                    labels=["CLEARLEDGR_PROCESSED"],
                    attachments=[],
                )

            async def list_labels(self):
                return [{"id": "label-1", "name": "Clearledgr/Processed"}]

            async def create_label(self, _name):
                return {"id": "label-1", "name": "Clearledgr/Processed"}

            async def add_label(self, _message_id, _label_ids):
                return None

        class _FakeDB:
            def __init__(self):
                self.saved = None

            def get_finance_email_by_gmail_id(self, _gmail_id):
                return SimpleNamespace(id="finance-email-1")

            def get_ap_item_by_thread(self, _organization_id, _thread_id):
                return None

            def get_ap_item_by_message_id(self, _organization_id, _message_id):
                return None

            def save_finance_email(self, email):
                self.saved = email
                return email

        fake_db = _FakeDB()
        seen = {}

        async def _fake_process_invoice_email(*, message, organization_id: str, **_kwargs):
            seen["organization_id"] = organization_id
            seen["message_id"] = message.id
            return {"status": "ok"}

        with patch.object(
            gmail_webhooks_module,
            "classify_email_with_llm",
            AsyncMock(return_value={"type": "invoice", "confidence": 0.95}),
        ):
            with patch.object(
                gmail_webhooks_module,
                "process_invoice_email",
                AsyncMock(side_effect=_fake_process_invoice_email),
            ):
                with patch.object(
                    gmail_webhooks_module,
                    "process_payment_request_email",
                    AsyncMock(return_value={"status": "skipped"}),
                ):
                    with patch.object(gmail_webhooks_module, "get_db", return_value=fake_db):
                        asyncio.run(
                            gmail_webhooks_module.process_single_email(
                                client=_FakeClient(),
                                message_id="msg-retry-1",
                                user_id="gmail-user-1",
                                organization_id="tenant-42",
                            )
                        )

        assert seen["organization_id"] == "tenant-42"
        assert seen["message_id"] == "msg-retry-1"
        assert fake_db.saved.id == "finance-email-1"

    def test_process_single_email_labels_receipt_without_ap_workflow(self):
        class _FakeClient:
            async def get_message(self, _message_id):
                return SimpleNamespace(
                    id="msg-receipt-1",
                    thread_id="thread-receipt-1",
                    subject="Your receipt from Replit #2462-2703",
                    sender="billing@replit.com",
                    recipient="ap@company.test",
                    date=datetime.now(timezone.utc),
                    snippet="Payment confirmed",
                    body_text="Thanks for your payment. Receipt attached.",
                    body_html="",
                    labels=[],
                    attachments=[],
                )

        class _FakeDB:
            def __init__(self):
                self.saved = []

            def get_finance_email_by_gmail_id(self, _gmail_id):
                return None

            def save_finance_email(self, email):
                self.saved.append(email)
                return email

        fake_db = _FakeDB()
        sync_labels = AsyncMock(return_value={"processed", "receipts"})

        with patch.object(
            gmail_webhooks_module,
            "classify_email_with_llm",
            AsyncMock(return_value={"type": "NOISE", "confidence": 0.91}),
        ):
            with patch.object(
                gmail_webhooks_module,
                "_label_only_document_parse",
                return_value={
                    "email_type": "receipt",
                    "vendor": "Replit",
                    "amount": 19.0,
                    "currency": "usd",
                    "confidence": 0.91,
                },
            ):
                with patch.object(gmail_webhooks_module, "sync_finance_labels", sync_labels):
                    with patch.object(gmail_webhooks_module, "get_db", return_value=fake_db):
                        asyncio.run(
                            gmail_webhooks_module.process_single_email(
                                client=_FakeClient(),
                                message_id="msg-receipt-1",
                                user_id="gmail-user-1",
                                organization_id="tenant-42",
                            )
                        )

        assert len(fake_db.saved) == 1
        assert fake_db.saved[0].email_type == "receipt"
        assert fake_db.saved[0].status == "processed"
        sync_labels.assert_awaited_once()

    def test_process_single_email_labels_payment_confirmation_without_ap_workflow(self):
        class _FakeClient:
            async def get_message(self, _message_id):
                return SimpleNamespace(
                    id="msg-payment-1",
                    thread_id="thread-payment-1",
                    subject="Payment confirmation for invoice INV-2048",
                    sender="billing@acme.com",
                    recipient="ap@company.test",
                    date=datetime.now(timezone.utc),
                    snippet="Payment processed",
                    body_text="Payment processed successfully for your prior invoice.",
                    body_html="",
                    labels=[],
                    attachments=[],
                )

        class _FakeDB:
            def __init__(self):
                self.saved = []

            def get_finance_email_by_gmail_id(self, _gmail_id):
                return None

            def save_finance_email(self, email):
                self.saved.append(email)
                return email

        fake_db = _FakeDB()
        sync_labels = AsyncMock(return_value={"processed", "payments"})

        with patch.object(
            gmail_webhooks_module,
            "classify_email_with_llm",
            AsyncMock(return_value={"type": "NOISE", "confidence": 0.91}),
        ):
            with patch.object(
                gmail_webhooks_module,
                "_label_only_document_parse",
                return_value={
                    "email_type": "payment",
                    "vendor": "Acme Corp",
                    "amount": 42.0,
                    "currency": "usd",
                    "confidence": 0.91,
                },
            ):
                with patch.object(gmail_webhooks_module, "sync_finance_labels", sync_labels):
                    with patch.object(gmail_webhooks_module, "get_db", return_value=fake_db):
                        asyncio.run(
                            gmail_webhooks_module.process_single_email(
                                client=_FakeClient(),
                                message_id="msg-payment-1",
                                user_id="gmail-user-1",
                                organization_id="tenant-42",
                            )
                        )

        assert len(fake_db.saved) == 1
        assert fake_db.saved[0].email_type == "payment"
        assert fake_db.saved[0].status == "processed"
        sync_labels.assert_awaited_once()

    def test_process_single_email_labels_refund_without_ap_workflow(self):
        class _FakeClient:
            async def get_message(self, _message_id):
                return SimpleNamespace(
                    id="msg-refund-1",
                    thread_id="thread-refund-1",
                    subject="Your refund from Cursor #3779-4144",
                    sender="billing@cursor.com",
                    recipient="ap@company.test",
                    date=datetime.now(timezone.utc),
                    snippet="Refund confirmed",
                    body_text="We processed your refund.",
                    body_html="",
                    labels=[],
                    attachments=[],
                )

        class _FakeDB:
            def __init__(self):
                self.saved = []

            def get_finance_email_by_gmail_id(self, _gmail_id):
                return None

            def save_finance_email(self, email):
                self.saved.append(email)
                return email

        fake_db = _FakeDB()
        sync_labels = AsyncMock(return_value={"processed", "refunds"})

        with patch.object(
            gmail_webhooks_module,
            "classify_email_with_llm",
            AsyncMock(return_value={"type": "NOISE", "confidence": 0.91}),
        ):
            with patch.object(
                gmail_webhooks_module,
                "_label_only_document_parse",
                return_value={
                    "email_type": "refund",
                    "vendor": "Cursor",
                    "amount": 20.0,
                    "currency": "usd",
                    "confidence": 0.91,
                },
            ):
                with patch.object(gmail_webhooks_module, "sync_finance_labels", sync_labels):
                    with patch.object(gmail_webhooks_module, "get_db", return_value=fake_db):
                        asyncio.run(
                            gmail_webhooks_module.process_single_email(
                                client=_FakeClient(),
                                message_id="msg-refund-1",
                                user_id="gmail-user-1",
                                organization_id="tenant-42",
                            )
                        )

        assert len(fake_db.saved) == 1
        assert fake_db.saved[0].email_type == "refund"
        assert fake_db.saved[0].status == "processed"
        sync_labels.assert_awaited_once()

    def test_process_invoice_email_persists_extracted_fields_and_sender_fallback(self):
        class _FakeDB:
            def __init__(self):
                self.saved = []

            def get_finance_email_by_gmail_id(self, _gmail_id):
                return SimpleNamespace(id="finance-email-1")

            def save_finance_email(self, email):
                self.saved.append(email)
                return email

        fake_db = _FakeDB()
        fake_runtime = MagicMock()
        fake_runtime.execute_ap_invoice_processing = AsyncMock(return_value={"status": "pending_approval"})
        message = SimpleNamespace(
            id="msg-invoice-1",
            thread_id="thread-invoice-1",
            subject="Google Workspace invoice",
            sender="Google Payments <payments-noreply@google.com>",
            snippet="Your invoice is available",
            body_text="Please find attached invoice.",
            attachments=[],
            date=datetime.now(timezone.utc),
        )

        with patch.object(gmail_webhooks_module, "get_db", return_value=fake_db):
            with patch(
                "clearledgr.workflows.gmail_activities.extract_email_data_activity",
                AsyncMock(
                    return_value={
                        "vendor": "Unknown",
                        "amount": 125.0,
                        "currency": "usd",
                        "invoice_number": "INV-123",
                        "confidence": 0.97,
                    }
                ),
            ):
                with patch(
                    "clearledgr.services.finance_agent_runtime.get_platform_finance_runtime",
                    return_value=fake_runtime,
                ):
                    with patch.object(
                        gmail_webhooks_module,
                        "_create_invoice_draft_summary",
                        AsyncMock(return_value=None),
                    ):
                        asyncio.run(
                            gmail_webhooks_module.process_invoice_email(
                                client=MagicMock(),
                                message=message,
                                user_id="gmail-user-1",
                                organization_id="default",
                                confidence=0.91,
                            )
                        )

        assert len(fake_db.saved) == 2
        assert fake_db.saved[0].id == "finance-email-1"
        assert fake_db.saved[0].vendor == "Google Payments"
        assert fake_db.saved[0].amount == 125.0
        assert fake_db.saved[0].currency == "USD"
        assert fake_db.saved[0].invoice_number == "INV-123"
        assert fake_db.saved[-1].status == "processed"
        runtime_payload = fake_runtime.execute_ap_invoice_processing.await_args.kwargs["invoice_payload"]
        assert runtime_payload["vendor_name"] == "Google Payments"
        assert runtime_payload["amount"] == 125.0

    def test_process_invoice_email_uses_extracted_document_type_for_metadata_and_labels(self):
        class _FakeDB:
            def __init__(self):
                self.saved = []

            def get_finance_email_by_gmail_id(self, _gmail_id):
                return SimpleNamespace(id="finance-email-doc-type-1")

            def save_finance_email(self, email):
                self.saved.append(email)
                return email

        fake_db = _FakeDB()
        fake_runtime = MagicMock()
        fake_runtime.execute_ap_invoice_processing = AsyncMock(return_value={"status": "pending_approval"})
        sync_mock = AsyncMock(return_value=None)
        message = SimpleNamespace(
            id="msg-doc-type-1",
            thread_id="thread-doc-type-1",
            subject="Your refund from Cursor #3779-4144",
            sender="Cursor <billing@cursor.com>",
            snippet="Refund processed",
            body_text="Refund receipt attached.",
            attachments=[],
            date=datetime.now(timezone.utc),
        )

        with patch.object(gmail_webhooks_module, "get_db", return_value=fake_db):
            with patch(
                "clearledgr.workflows.gmail_activities.extract_email_data_activity",
                AsyncMock(
                    return_value={
                        "vendor": "Cursor",
                        "amount": 0.0,
                        "currency": "usd",
                        "invoice_number": "CR-3779-4144",
                        "confidence": 0.97,
                        "document_type": "refund",
                        "email_type": "refund",
                    }
                ),
            ):
                with patch(
                    "clearledgr.services.finance_agent_runtime.get_platform_finance_runtime",
                    return_value=fake_runtime,
                ):
                    with patch.object(
                        gmail_webhooks_module,
                        "_sync_message_finance_labels",
                        sync_mock,
                    ):
                        with patch.object(
                            gmail_webhooks_module,
                            "_create_invoice_draft_summary",
                            AsyncMock(return_value=None),
                        ):
                            asyncio.run(
                                gmail_webhooks_module.process_invoice_email(
                                    client=MagicMock(),
                                    message=message,
                                    user_id="gmail-user-1",
                                    organization_id="default",
                                    confidence=0.91,
                                )
                            )

        assert len(fake_db.saved) == 2
        assert fake_db.saved[-1].email_type == "refund"
        assert fake_db.saved[-1].metadata["document_type"] == "refund"
        assert fake_db.saved[-1].metadata["email_type"] == "refund"
        fake_runtime.execute_ap_invoice_processing.assert_not_awaited()
        assert sync_mock.await_args.kwargs["document_type"] == "refund"

    def test_process_invoice_email_allows_label_resolver_override_for_invoice_shaped_refund_subjects(self):
        class _FakeDB:
            def __init__(self):
                self.saved = []

            def get_finance_email_by_gmail_id(self, _gmail_id):
                return SimpleNamespace(id="finance-email-refund-1")

            def save_finance_email(self, email):
                self.saved.append(email)
                return email

        fake_db = _FakeDB()
        fake_runtime = MagicMock()
        fake_runtime.execute_ap_invoice_processing = AsyncMock(return_value={"status": "pending_approval"})
        sync_mock = AsyncMock(return_value=None)
        message = SimpleNamespace(
            id="msg-refund-1",
            thread_id="thread-refund-1",
            subject="Your refund from Cursor #3779-4144",
            sender="Cursor <billing@cursor.com>",
            snippet="Refund processed",
            body_text="Refund receipt attached.",
            attachments=[],
            date=datetime.now(timezone.utc),
        )

        with patch.object(gmail_webhooks_module, "get_db", return_value=fake_db):
            with patch(
                "clearledgr.workflows.gmail_activities.extract_email_data_activity",
                AsyncMock(
                    return_value={
                        "vendor": "Cursor",
                        "amount": 0.0,
                        "currency": "usd",
                        "invoice_number": "CR-3779-4144",
                        "confidence": 0.97,
                        "document_type": "invoice",
                        "email_type": "invoice",
                    }
                ),
            ):
                with patch(
                    "clearledgr.services.finance_agent_runtime.get_platform_finance_runtime",
                    return_value=fake_runtime,
                ):
                    with patch.object(
                        gmail_webhooks_module,
                        "_sync_message_finance_labels",
                        sync_mock,
                    ):
                        with patch.object(
                            gmail_webhooks_module,
                            "_create_invoice_draft_summary",
                            AsyncMock(return_value=None),
                        ):
                            asyncio.run(
                                gmail_webhooks_module.process_invoice_email(
                                    client=MagicMock(),
                                    message=message,
                                    user_id="gmail-user-1",
                                    organization_id="default",
                                    confidence=0.91,
                                )
                            )

        assert len(fake_db.saved) == 2
        assert fake_db.saved[-1].email_type == "refund"
        assert sync_mock.await_args.kwargs["document_type"] == "refund"

    def test_process_invoice_email_subject_refund_overrides_credit_note_extraction(self):
        class _FakeDB:
            def __init__(self):
                self.saved = []

            def get_finance_email_by_gmail_id(self, _gmail_id):
                return SimpleNamespace(id="finance-email-refund-override-1")

            def save_finance_email(self, email):
                self.saved.append(email)
                return email

        fake_db = _FakeDB()
        fake_runtime = MagicMock()
        fake_runtime.execute_ap_invoice_processing = AsyncMock(return_value={"status": "pending_approval"})
        sync_mock = AsyncMock(return_value=None)
        message = SimpleNamespace(
            id="msg-refund-override-1",
            thread_id="thread-refund-override-1",
            subject="Your refund from Cursor #3779-4144",
            sender="Cursor <billing@cursor.com>",
            snippet="Refund processed",
            body_text="A credit note has been issued.",
            attachments=[],
            date=datetime.now(timezone.utc),
        )

        with patch.object(gmail_webhooks_module, "get_db", return_value=fake_db):
            with patch(
                "clearledgr.workflows.gmail_activities.extract_email_data_activity",
                AsyncMock(
                    return_value={
                        "vendor": "Cursor",
                        "amount": 0.0,
                        "currency": "usd",
                        "invoice_number": "CR-3779-4144",
                        "confidence": 0.97,
                        "document_type": "credit_note",
                        "email_type": "credit_note",
                    }
                ),
            ):
                with patch(
                    "clearledgr.services.finance_agent_runtime.get_platform_finance_runtime",
                    return_value=fake_runtime,
                ):
                    with patch.object(
                        gmail_webhooks_module,
                        "_sync_message_finance_labels",
                        sync_mock,
                    ):
                        with patch.object(
                            gmail_webhooks_module,
                            "_create_invoice_draft_summary",
                            AsyncMock(return_value=None),
                        ):
                            result = asyncio.run(
                                gmail_webhooks_module.process_invoice_email(
                                    client=MagicMock(),
                                    message=message,
                                    user_id="gmail-user-1",
                                    organization_id="default",
                                    confidence=0.91,
                                )
                            )

        assert result["status"] == "processed_non_invoice"
        assert result["document_type"] == "refund"
        assert len(fake_db.saved) == 2
        assert fake_db.saved[-1].email_type == "refund"
        assert fake_runtime.execute_ap_invoice_processing.assert_not_awaited() is None
        assert sync_mock.await_args.kwargs["document_type"] == "refund"

    def test_process_invoice_email_refresh_only_skips_runtime_execution_and_draft(self):
        class _FakeDB:
            def __init__(self):
                self.saved = []

            def get_finance_email_by_gmail_id(self, _gmail_id):
                return SimpleNamespace(id="finance-email-refresh-1")

            def save_finance_email(self, email):
                self.saved.append(email)
                return email

        fake_db = _FakeDB()
        fake_runtime = MagicMock()
        fake_runtime.execute_ap_invoice_processing = AsyncMock(side_effect=AssertionError("runtime execute should not run"))
        fake_runtime.refresh_invoice_record_from_extraction = MagicMock(
            return_value={"status": "refreshed", "execution_mode": "extraction_refresh"}
        )
        message = SimpleNamespace(
            id="msg-refresh-1",
            thread_id="thread-refresh-1",
            subject="Invoice INV-REFRESH-1",
            sender="billing@vendor.test",
            snippet="Invoice attached",
            body_text="Please find attached invoice.",
            attachments=[],
            date=datetime.now(timezone.utc),
        )
        draft_mock = AsyncMock(return_value=None)

        with patch.object(gmail_webhooks_module, "get_db", return_value=fake_db):
            with patch(
                "clearledgr.workflows.gmail_activities.extract_email_data_activity",
                AsyncMock(
                    return_value={
                        "vendor": "Vendor Refresh Co",
                        "amount": 451.23,
                        "currency": "usd",
                        "invoice_number": "INV-REFRESH-1",
                        "confidence": 0.97,
                        "primary_source": "attachment",
                    }
                ),
            ):
                with patch(
                    "clearledgr.services.finance_agent_runtime.get_platform_finance_runtime",
                    return_value=fake_runtime,
                ):
                    with patch.object(
                        gmail_webhooks_module,
                        "_create_invoice_draft_summary",
                        draft_mock,
                    ):
                        result = asyncio.run(
                            gmail_webhooks_module.process_invoice_email(
                                client=MagicMock(),
                                message=message,
                                user_id="gmail-user-1",
                                organization_id="default",
                                confidence=0.91,
                                run_runtime=False,
                                create_draft=False,
                                refresh_reason="repair_pass",
                            )
                        )

        assert result["status"] == "refreshed"
        assert len(fake_db.saved) == 2
        assert fake_runtime.refresh_invoice_record_from_extraction.called
        assert fake_runtime.refresh_invoice_record_from_extraction.call_args.kwargs["refresh_reason"] == "repair_pass"
        draft_mock.assert_not_awaited()

    def test_process_invoice_email_persists_field_review_gate_and_review_required_status(self):
        class _FakeDB:
            def __init__(self):
                self.saved = []

            def get_finance_email_by_gmail_id(self, _gmail_id):
                return SimpleNamespace(id="finance-email-review-1")

            def save_finance_email(self, email):
                self.saved.append(email)
                return email

        fake_db = _FakeDB()
        fake_runtime = MagicMock()
        fake_runtime.execute_ap_invoice_processing = AsyncMock(
            return_value={"status": "blocked", "reason": "field_review_required"}
        )
        message = SimpleNamespace(
            id="msg-review-1",
            thread_id="thread-review-1",
            subject="Invoice INV-REVIEW-1",
            sender="billing@vendor.test",
            snippet="Invoice attached",
            body_text="Please find attached invoice.",
            attachments=[],
            date=datetime.now(timezone.utc),
        )

        with patch.object(gmail_webhooks_module, "get_db", return_value=fake_db):
            with patch(
                "clearledgr.workflows.gmail_activities.extract_email_data_activity",
                AsyncMock(
                    return_value={
                        "vendor": "Vendor Review Co",
                        "amount": 451.23,
                        "currency": "usd",
                        "invoice_number": "INV-REVIEW-1",
                        "confidence": 0.97,
                        "primary_source": "attachment",
                        "requires_extraction_review": True,
                        "field_provenance": {"amount": {"source": "attachment", "value": 451.23}},
                        "field_evidence": {"amount": {"source": "attachment", "selected_value": 451.23}},
                        "source_conflicts": [
                            {
                                "field": "amount",
                                "blocking": True,
                                "reason": "source_value_mismatch",
                                "preferred_source": "attachment",
                                "values": {"email": 400.0, "attachment": 451.23},
                            }
                        ],
                        "conflict_actions": [{"action": "review_fields", "field": "amount", "blocking": True}],
                        "field_confidences": {"amount": 0.99},
                    }
                ),
            ):
                with patch(
                    "clearledgr.services.finance_agent_runtime.get_platform_finance_runtime",
                    return_value=fake_runtime,
                ):
                    with patch.object(
                        gmail_webhooks_module,
                        "_create_invoice_draft_summary",
                        AsyncMock(return_value=None),
                    ):
                        asyncio.run(
                            gmail_webhooks_module.process_invoice_email(
                                client=MagicMock(),
                                message=message,
                                user_id="gmail-user-1",
                                organization_id="default",
                                confidence=0.91,
                            )
                        )

        assert len(fake_db.saved) == 2
        assert fake_db.saved[-1].status == "review_required"
        assert fake_db.saved[0].metadata["requires_field_review"] is True
        assert fake_db.saved[0].metadata["confidence_gate"]["requires_field_review"] is True
        assert fake_db.saved[0].metadata["exception_code"] == "field_conflict"
        runtime_payload = fake_runtime.execute_ap_invoice_processing.await_args.kwargs["invoice_payload"]
        assert runtime_payload["requires_field_review"] is True
        assert runtime_payload["source_conflicts"][0]["field"] == "amount"

    def test_extension_by_thread_recovers_detected_finance_email(self):
        class _FakeDB:
            def __init__(self):
                self.items = {}
                self.sources = []

            def get_ap_item_by_thread(self, organization_id, thread_id):
                for item in self.items.values():
                    if item.get("organization_id") != organization_id:
                        continue
                    if item.get("thread_id") == thread_id:
                        return item
                    for source in self.sources:
                        if (
                            source.get("ap_item_id") == item.get("id")
                            and source.get("source_type") == "gmail_thread"
                            and source.get("source_ref") == thread_id
                        ):
                            return item
                return None

            def get_ap_item_by_message_id(self, organization_id, message_id):
                for item in self.items.values():
                    if item.get("organization_id") != organization_id:
                        continue
                    if item.get("message_id") == message_id:
                        return item
                    for source in self.sources:
                        if (
                            source.get("ap_item_id") == item.get("id")
                            and source.get("source_type") == "gmail_message"
                            and source.get("source_ref") == message_id
                        ):
                            return item
                return None

            def get_finance_email_by_gmail_id(self, gmail_id):
                if gmail_id != "msg-thread-1":
                    return None
                return SimpleNamespace(
                    id="finance-thread-1",
                    subject="Invoice INV-THREAD-1",
                    sender="billing@acme.test",
                    vendor="Acme Corp",
                    amount=125.0,
                    currency="USD",
                    invoice_number="INV-THREAD-1",
                    confidence=0.95,
                    user_id="extension-user-1",
                    email_type="invoice",
                )

            def create_ap_item(self, payload):
                item = {
                    "id": "ap-thread-1",
                    "organization_id": payload.get("organization_id"),
                    "thread_id": payload.get("thread_id"),
                    "message_id": payload.get("message_id"),
                    "state": payload.get("state"),
                    "vendor_name": payload.get("vendor_name"),
                    "invoice_number": payload.get("invoice_number"),
                    "amount": payload.get("amount"),
                    "currency": payload.get("currency"),
                    "subject": payload.get("subject"),
                    "sender": payload.get("sender"),
                    "confidence": payload.get("confidence"),
                    "metadata": payload.get("metadata") or {},
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                self.items[item["id"]] = item
                return item

            def get_ap_item(self, ap_item_id):
                return self.items.get(ap_item_id)

            def update_ap_item(self, ap_item_id, **kwargs):
                if ap_item_id not in self.items:
                    return False
                self.items[ap_item_id].update(kwargs)
                return True

            def link_ap_item_source(self, payload):
                self.sources.append(dict(payload or {}))
                return payload

            def list_ap_item_sources(self, ap_item_id):
                return [row for row in self.sources if row.get("ap_item_id") == ap_item_id]

        class _FakeGmailAPIClient:
            def __init__(self, _user_id):
                pass

            async def ensure_authenticated(self):
                return True

            async def get_thread(self, thread_id):
                assert thread_id == "thread-1"
                return [
                    SimpleNamespace(
                        id="msg-thread-1",
                        thread_id="thread-1",
                        subject="Invoice INV-THREAD-1",
                        sender="billing@acme.test",
                    )
                ]

        fake_db = _FakeDB()
        app.dependency_overrides[gmail_extension_module.get_current_user] = lambda: TokenData(
            user_id="extension-user-1",
            email="extension@example.com",
            organization_id="default",
            role="user",
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        try:
            with patch.object(gmail_extension_module, "get_db", return_value=fake_db):
                with patch.object(gmail_extension_module, "_gmail_api_client", _FakeGmailAPIClient):
                    lookup_response = client.get("/extension/by-thread/thread-1", params={"organization_id": "default"})
                    recover_response = client.post("/extension/by-thread/thread-1/recover", params={"organization_id": "default"})
        finally:
            app.dependency_overrides.pop(gmail_extension_module.get_current_user, None)

        assert lookup_response.status_code == 200
        lookup_payload = lookup_response.json()
        assert lookup_payload == {"found": False, "thread_id": "thread-1", "item": None}

        assert recover_response.status_code == 200
        payload = recover_response.json()
        assert payload["found"] is True
        assert payload["recovered"] is True
        assert payload["item"]["thread_id"] == "thread-1"
        assert payload["item"]["message_id"] == "msg-thread-1"
        assert payload["item"]["vendor_name"] == "Acme Corp"
        assert payload["item"]["primary_source"]["thread_id"] == "thread-1"
        assert payload["item"]["primary_source"]["message_id"] == "msg-thread-1"

    def test_recover_thread_processes_raw_gmail_message_when_finance_email_is_missing(self):
        class _FakeDB:
            def __init__(self):
                self.items = {}
                self.sources = []

            def get_ap_item_by_thread(self, organization_id, thread_id):
                for item in self.items.values():
                    if item.get("organization_id") == organization_id and item.get("thread_id") == thread_id:
                        return item
                return None

            def get_ap_item_by_message_id(self, organization_id, message_id):
                for item in self.items.values():
                    if item.get("organization_id") != organization_id:
                        continue
                    if item.get("message_id") == message_id:
                        return item
                    for source in self.sources:
                        if (
                            source.get("ap_item_id") == item.get("id")
                            and source.get("source_type") == "gmail_message"
                            and source.get("source_ref") == message_id
                        ):
                            return item
                return None

            def get_finance_email_by_gmail_id(self, gmail_id):
                return None

            def create_ap_item(self, payload):
                item = {
                    "id": "ap-thread-raw-1",
                    "organization_id": payload.get("organization_id"),
                    "thread_id": payload.get("thread_id"),
                    "message_id": payload.get("message_id"),
                    "state": payload.get("state"),
                    "vendor_name": payload.get("vendor_name"),
                    "invoice_number": payload.get("invoice_number"),
                    "amount": payload.get("amount"),
                    "currency": payload.get("currency"),
                    "subject": payload.get("subject"),
                    "sender": payload.get("sender"),
                    "confidence": payload.get("confidence"),
                    "metadata": payload.get("metadata") or {},
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                self.items[item["id"]] = item
                return item

            def get_ap_item(self, ap_item_id):
                return self.items.get(ap_item_id)

            def update_ap_item(self, ap_item_id, **kwargs):
                if ap_item_id not in self.items:
                    return False
                self.items[ap_item_id].update(kwargs)
                return True

            def link_ap_item_source(self, payload):
                self.sources.append(dict(payload or {}))
                return payload

            def list_ap_item_sources(self, ap_item_id):
                return [row for row in self.sources if row.get("ap_item_id") == ap_item_id]

        class _FakeGmailAPIClient:
            def __init__(self, _user_id):
                pass

            async def ensure_authenticated(self):
                return True

            async def get_thread(self, thread_id):
                assert thread_id == "thread-school-1"
                return [
                    SimpleNamespace(
                        id="msg-school-1",
                        thread_id="thread-school-1",
                        subject="Fwd: School fee invoice",
                        sender="mo@clearledgr.com",
                    )
                ]

        async def _fake_process_single_email(*, message, organization_id, **_kwargs):
            created = fake_db.create_ap_item({
                "organization_id": organization_id,
                "thread_id": "thread-school-1",
                "message_id": message.id,
                "state": "needs_approval",
                "vendor_name": "My Son's School",
                "invoice_number": "SCH-2026-04",
                "amount": 250.0,
                "currency": "USD",
                "subject": message.subject,
                "sender": message.sender,
                "confidence": 0.91,
                "metadata": {
                    "primary_source": {
                        "thread_id": "thread-school-1",
                        "message_id": message.id,
                    },
                },
            })
            fake_db.link_ap_item_source({
                "ap_item_id": created["id"],
                "source_type": "gmail_message",
                "source_ref": message.id,
                "source_label": "Source email",
                "metadata_json": {},
            })
            return {"status": "processed"}

        fake_db = _FakeDB()
        app.dependency_overrides[gmail_extension_module.get_current_user] = lambda: TokenData(
            user_id="extension-user-1",
            email="extension@example.com",
            organization_id="default",
            role="user",
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        try:
            with patch.object(gmail_extension_module, "get_db", return_value=fake_db):
                with patch.object(gmail_extension_module, "_gmail_api_client", _FakeGmailAPIClient):
                    with patch("clearledgr.api.gmail_webhooks.process_single_email", AsyncMock(side_effect=_fake_process_single_email)) as process_mock:
                        recover_response = client.post("/extension/by-thread/thread-school-1/recover", params={"organization_id": "default"})
        finally:
            app.dependency_overrides.pop(gmail_extension_module.get_current_user, None)

        assert recover_response.status_code == 200
        payload = recover_response.json()
        assert payload["found"] is True
        assert payload["recovered"] is True
        assert payload["item"]["thread_id"] == "thread-school-1"
        assert payload["item"]["message_id"] == "msg-school-1"
        assert payload["item"]["vendor_name"] == "My Son's School"
        assert process_mock.await_count == 1

    def test_exchange_gmail_code_preserves_existing_refresh_token_when_google_omits_one(self):
        fake_user = SimpleNamespace(
            id="gmail-user-1",
            email="ops@example.com",
            organization_id="default",
            role="user",
        )
        fake_token = SimpleNamespace(
            access_token="new-access-token",
            refresh_token="",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            email="ops@example.com",
        )
        stored = {}

        class _TokenStore:
            def get(self, _user_id):
                return SimpleNamespace(refresh_token="preserved-refresh-token")

            def store(self, token):
                stored["token"] = token

        with patch("clearledgr.services.gmail_api.exchange_code_for_tokens", AsyncMock(return_value=fake_token)):
            with patch.object(gmail_extension_module, "get_user_by_email", return_value=fake_user):
                with patch.object(gmail_extension_module, "_token_store", return_value=_TokenStore()):
                    with patch.object(gmail_extension_module, "_gmail_token_class", return_value=SimpleNamespace):
                        with patch.object(gmail_extension_module, "create_access_token", return_value="backend-access-token"):
                            with patch.object(gmail_extension_module, "get_db", return_value=MagicMock(save_gmail_autopilot_state=MagicMock())):
                                response = client.post(
                                    "/extension/gmail/exchange-code",
                                    json={"code": "gmail-code-1", "redirect_uri": "https://example.test/callback"},
                                )

        assert response.status_code == 200
        payload = response.json()
        assert payload["has_refresh_token"] is True
        assert payload["backend_expires_in"] == gmail_extension_module.EXTENSION_BACKEND_TOKEN_TTL_SECONDS
        assert stored["token"].refresh_token == "preserved-refresh-token"


class TestAdminConsoleIntegrations:
    @staticmethod
    def _fake_user(role: str):
        return TokenData(
            user_id="admin-user-1",
            email="admin@example.com",
            organization_id="default",
            role=role,
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    def test_start_gmail_connect_requires_admin(self):
        app.dependency_overrides[workspace_shell_module.get_current_user] = lambda: self._fake_user("viewer")
        try:
            response = client.post(
                "/api/workspace/integrations/gmail/connect/start",
                json={"organization_id": "default", "redirect_path": "/workspace?page=integrations"},
            )
        finally:
            app.dependency_overrides.pop(workspace_shell_module.get_current_user, None)
        assert response.status_code == 403

    def test_start_gmail_connect_returns_google_auth_url(self, monkeypatch):
        monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
        monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://127.0.0.1:8010/gmail/callback")
        captured = {}

        def _fake_auth_url(*, state):
            captured["state"] = state
            return f"https://accounts.google.com/o/oauth2/v2/auth?state={state}"

        app.dependency_overrides[workspace_shell_module.get_current_user] = lambda: self._fake_user("admin")
        try:
            with patch.object(workspace_shell_module, "_generate_auth_url", side_effect=_fake_auth_url):
                response = client.post(
                    "/api/workspace/integrations/gmail/connect/start",
                    json={"organization_id": "default", "redirect_path": "/gmail/connected"},
                )
        finally:
            app.dependency_overrides.pop(workspace_shell_module.get_current_user, None)

        assert response.status_code == 200
        payload = response.json()
        assert payload["organization_id"] == "default"
        assert payload["redirect_path"] == "/gmail/connected"
        assert payload["auth_url"].startswith("https://accounts.google.com/o/oauth2/v2/auth")
        signed_state = captured.get("state")
        assert isinstance(signed_state, str) and "." in signed_state
        decoded = gmail_webhooks_module._unsign_oauth_state(signed_state)
        assert decoded.get("user_id") == "admin-user-1"
        assert decoded.get("organization_id") == "default"
        assert decoded.get("redirect_url") == "/gmail/connected"
        assert decoded.get("oauth_redirect_uri") == "http://127.0.0.1:8010/gmail/callback"

    def test_generate_gmail_auth_url_requests_offline_consent_and_granted_scopes(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "google-client-id")
        monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://127.0.0.1:8010/gmail/callback")

        auth_url = workspace_shell_module._generate_auth_url(state="signed-state")

        assert "access_type=offline" in auth_url
        assert "prompt=consent" in auth_url
        assert "include_granted_scopes=true" in auth_url
        assert "state=signed-state" in auth_url

    def test_generate_gmail_auth_url_defaults_to_api_base_callback(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "google-client-id")
        monkeypatch.setenv("API_BASE_URL", "http://127.0.0.1:8010")
        monkeypatch.delenv("GOOGLE_REDIRECT_URI", raising=False)
        monkeypatch.delenv("GOOGLE_GMAIL_REDIRECT_URI", raising=False)

        auth_url = workspace_shell_module._generate_auth_url(state="signed-state")

        assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A8010%2Fgmail%2Fcallback" in auth_url

    def test_generate_gmail_auth_url_ignores_workspace_auth_callback_redirect(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "google-client-id")
        monkeypatch.setenv("API_BASE_URL", "https://api.clearledgr.com")
        monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://api.clearledgr.com/auth/google/callback")
        monkeypatch.delenv("GOOGLE_GMAIL_REDIRECT_URI", raising=False)

        auth_url = workspace_shell_module._generate_auth_url(state="signed-state")

        assert "redirect_uri=https%3A%2F%2Fapi.clearledgr.com%2Fgmail%2Fcallback" in auth_url

    def test_gmail_status_requires_reconnect_without_refresh_token(self):
        fake_db = MagicMock()
        fake_db.get_oauth_token.return_value = {
            "user_id": "admin-user-1",
            "provider": "gmail",
            "email": "admin@example.com",
            "access_token": "encrypted-access",
            "refresh_token": "",
        }
        fake_db.get_gmail_autopilot_state.return_value = {"last_scan_at": "2026-03-19T21:10:47+00:00"}

        user = self._fake_user("admin")
        with patch.object(workspace_shell_module, "get_db", return_value=fake_db):
            status = workspace_shell_module._gmail_status_for_org("default", user)

        assert status["connected"] is True
        assert status["durable"] is False
        assert status["has_refresh_token"] is False
        assert status["requires_reconnect"] is True
        assert status["status"] == "reconnect_required"
        assert status["watch_status"] == "reconnect_required"

    def test_slack_status_requires_runtime_connection_not_channel_only_config(self):
        fake_db = MagicMock()
        fake_db.get_organization.return_value = {
            "id": "default",
            "integration_mode": "shared",
            "settings_json": {"slack_channels": {"invoices": "cl-finance-ap"}},
        }
        fake_db.get_organization_integration.return_value = {
            "status": "connected",
            "mode": "shared",
            "last_sync_at": "2026-03-18T16:15:33.253180+00:00",
        }
        fake_db.get_slack_installation.return_value = None

        with patch.object(workspace_shell_module, "get_db", return_value=fake_db):
            with patch.object(
                workspace_shell_module,
                "_resolve_slack_runtime",
                return_value={"connected": False, "source": "shared_env_unconfigured"},
            ):
                status = workspace_shell_module._slack_status_for_org("default")

        assert status["connected"] is False
        assert status["status"] == "disconnected"
        assert status["approval_channel"] == "cl-finance-ap"
        assert status["approval_channel_configured"] is True
        assert status["install_recorded"] is False
        assert status["source"] == "shared_env_unconfigured"

    def test_set_slack_channel_does_not_mark_connected_without_runtime_token(self):
        fake_db = MagicMock()
        fake_db.ensure_organization.return_value = {
            "id": "default",
            "settings_json": {},
        }
        fake_db.get_organization.return_value = {
            "id": "default",
            "integration_mode": "shared",
        }
        fake_db.get_organization_integration.return_value = {
            "status": "connected",
            "mode": "shared",
            "metadata": {"team_id": "T123"},
        }

        app.dependency_overrides[workspace_shell_module.get_current_user] = lambda: self._fake_user("admin")
        try:
            with patch.object(workspace_shell_module, "get_db", return_value=fake_db):
                with patch.object(workspace_shell_module, "_save_org_settings") as save_settings:
                    with patch.object(
                        workspace_shell_module,
                        "_resolve_slack_runtime",
                        return_value={"connected": False, "source": "shared_env_unconfigured"},
                    ):
                        response = client.post(
                            "/api/workspace/integrations/slack/channel",
                            json={"organization_id": "default", "channel_id": "cl-finance-ap"},
                        )
        finally:
            app.dependency_overrides.pop(workspace_shell_module.get_current_user, None)

        assert response.status_code == 200
        payload = response.json()
        assert payload["slack_connected"] is False
        assert payload["slack_source"] == "shared_env_unconfigured"
        save_settings.assert_called_once()
        fake_db.upsert_organization_integration.assert_called_once()
        kwargs = fake_db.upsert_organization_integration.call_args.kwargs
        assert kwargs["organization_id"] == "default"
        assert kwargs["integration_type"] == "slack"
        assert kwargs["status"] == "disconnected"
        assert kwargs["mode"] == "shared"
        assert kwargs["metadata"] == {"team_id": "T123", "approval_channel": "cl-finance-ap"}
        assert isinstance(kwargs["last_sync_at"], str) and kwargs["last_sync_at"]

    def test_slack_test_endpoint_verifies_channel_without_posting_message(self):
        class _FakeSlackClient:
            def __init__(self, bot_token=None):
                self.bot_token = bot_token
                self.sent = False

            async def auth_test(self):
                return {"team": "Clearledgr", "user_id": "B123"}

            async def resolve_channel(self, channel):
                return {"id": "C123", "name": "cl-finance-ap"} if channel == "cl-finance-ap" else None

            async def send_message(self, *args, **kwargs):  # pragma: no cover - defensive
                self.sent = True
                raise AssertionError("Slack verification must not post a message")

        app.dependency_overrides[workspace_shell_module.get_current_user] = lambda: self._fake_user("admin")
        try:
            with patch.object(
                workspace_shell_module,
                "_resolve_slack_runtime",
                return_value={
                    "bot_token": "xoxb-live",
                    "mode": "per_org",
                    "approval_channel": "cl-finance-ap",
                },
            ):
                with patch.object(workspace_shell_module, "_slack_api_client_class", return_value=_FakeSlackClient):
                    response = client.post(
                        "/api/workspace/integrations/slack/test",
                        json={"organization_id": "default", "channel_id": "cl-finance-ap"},
                    )
        finally:
            app.dependency_overrides.pop(workspace_shell_module.get_current_user, None)

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["verification"] == "silent"
        assert payload["message_posted"] is False
        assert payload["channel_verified"] is True
        assert payload["channel"] == "#cl-finance-ap"
        assert payload["channel_id"] == "C123"

    def test_slack_install_start_requests_email_lookup_scope(self, monkeypatch):
        from urllib.parse import parse_qs, urlparse

        app.dependency_overrides[workspace_shell_module.get_current_user] = lambda: self._fake_user("admin")
        monkeypatch.setenv("SLACK_CLIENT_ID", "client-123")
        monkeypatch.setenv("SLACK_CLIENT_SECRET", "secret-123")
        monkeypatch.delenv("SLACK_OAUTH_SCOPES", raising=False)
        monkeypatch.setenv("APP_BASE_URL", "https://public.clearledgr.test")
        monkeypatch.delenv("SLACK_REDIRECT_URI", raising=False)
        try:
            response = client.post(
                "/api/workspace/integrations/slack/install/start",
                json={"organization_id": "default", "mode": "per_org", "redirect_path": "/"},
            )
        finally:
            app.dependency_overrides.pop(workspace_shell_module.get_current_user, None)

        assert response.status_code == 200
        auth_url = response.json()["auth_url"]
        parsed = parse_qs(urlparse(auth_url).query)
        scopes = set((parsed.get("scope") or [""])[0].split(","))
        user_scopes = set((parsed.get("user_scope") or [""])[0].split(","))
        redirect_uri = (parsed.get("redirect_uri") or [""])[0]
        assert "users:read" in scopes
        assert "users:read.email" in scopes
        assert "im:write" in scopes
        assert user_scopes in (set(), {""})
        assert redirect_uri == "https://public.clearledgr.test/api/workspace/integrations/slack/install/callback"

    def test_slack_status_flags_missing_email_lookup_scope_for_reauth(self):
        fake_db = MagicMock()
        fake_db.get_organization.return_value = {
            "id": "default",
            "integration_mode": "per_org",
            "settings_json": {"slack_channels": {"invoices": "cl-finance-ap"}},
        }
        fake_db.get_organization_integration.return_value = {
            "status": "connected",
            "mode": "per_org",
            "last_sync_at": "2026-04-06T21:00:00+00:00",
        }
        fake_db.get_slack_installation.return_value = {
            "team_id": "T123",
            "team_name": "Clearledgr",
            "scope_csv": "chat:write,commands,channels:read,groups:read,users:read",
        }

        with patch.object(workspace_shell_module, "get_db", return_value=fake_db):
            with patch.object(
                workspace_shell_module,
                "_resolve_slack_runtime",
                return_value={"connected": True, "source": "org_installation"},
            ):
                status = workspace_shell_module._slack_status_for_org("default")

        assert status["connected"] is True
        assert status["status"] == "reauthorization_required"
        assert status["approval_channel"] == "cl-finance-ap"
        assert status["requires_reauthorization"] is True
        assert status["email_lookup_ready"] is False
        assert "users:read.email" in status["missing_scopes"]
        assert "im:write" in status["missing_scopes"]

    def test_workspace_manifest_uses_invoice_interactive_callback(self, monkeypatch):
        app.dependency_overrides[workspace_shell_module.get_current_user] = lambda: self._fake_user("admin")
        monkeypatch.setenv("APP_BASE_URL", "https://public.clearledgr.test")
        monkeypatch.delenv("SLACK_REDIRECT_URI", raising=False)
        try:
            response = client.get("/api/workspace/integrations/slack/manifest?organization_id=default")
        finally:
            app.dependency_overrides.pop(workspace_shell_module.get_current_user, None)

        assert response.status_code == 200
        payload = response.json()
        request_url = payload["manifest"]["settings"]["interactivity"]["request_url"]
        redirect_urls = payload["manifest"]["oauth_config"]["redirect_urls"]
        assert request_url.endswith("/slack/invoices/interactive")
        assert request_url == "https://public.clearledgr.test/slack/invoices/interactive"
        assert redirect_urls == ["https://public.clearledgr.test/api/workspace/integrations/slack/install/callback"]
        bot_scopes = payload["manifest"]["oauth_config"]["scopes"]["bot"]
        user_scopes = payload["manifest"]["oauth_config"]["scopes"]["user"]
        assert "users:read.email" in bot_scopes
        assert "im:write" in bot_scopes
        assert user_scopes == []


class TestERPEndpoints:
    """Test canonical ERP integration surfaces."""
    
    def test_admin_integrations_includes_erp_status(self):
        app.dependency_overrides[workspace_shell_module.get_current_user] = lambda: TokenData(
            user_id="erp-user-1",
            email="erp-user@example.com",
            organization_id="default",
            role="owner",
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        try:
            response = client.get("/api/workspace/integrations?organization_id=default")
        finally:
            app.dependency_overrides.pop(workspace_shell_module.get_current_user, None)
        assert response.status_code == 200
        payload = response.json()
        assert any(row.get("name") == "erp" for row in payload.get("integrations", []))

    def test_admin_integrations_blocks_cross_org_for_non_admin(self):
        app.dependency_overrides[workspace_shell_module.get_current_user] = lambda: TokenData(
            user_id="erp-user-2",
            email="erp-user-2@example.com",
            organization_id="default",
            role="user",
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        try:
            response = client.get("/api/workspace/integrations?organization_id=other-org")
        finally:
            app.dependency_overrides.pop(workspace_shell_module.get_current_user, None)
        assert response.status_code == 403
        assert response.json().get("detail") == "org_access_denied"
    
    def test_oauth_status_route_not_mounted(self):
        """Legacy /oauth route family is not mounted in strict AP-v1 runtime."""
        response = client.get("/oauth/status")
        assert response.status_code == 404


class TestExtensionEndpoints:
    """Test Gmail extension API endpoints."""

    @staticmethod
    def _fake_user():
        return TokenData(
            user_id="extension-user-1",
            email="extension@example.com",
            organization_id="default",
            role="operator",
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    
    def test_triage_endpoint(self):
        """Test email triage endpoint runs inline (Temporal ripped out)."""
        app.dependency_overrides[gmail_extension_module.get_current_user] = self._fake_user
        try:
            with patch(
                "clearledgr.services.gmail_triage_service.run_inline_gmail_triage",
                AsyncMock(
                    return_value={
                        "email_id": "test-email-123",
                        "classification": {"type": "INVOICE", "confidence": 0.99},
                        "extraction": {"vendor": "Acme Corp", "amount": 1500.0},
                    }
                ),
            ):
                response = client.post("/extension/triage", json={
                    "email_id": "test-email-123",
                    "subject": "Invoice #12345 from Acme Corp",
                    "sender": "billing@acme.com",
                    "body": "Please find attached invoice for $1,500.00",
                    "organization_id": "default",
                })
        finally:
            app.dependency_overrides.pop(gmail_extension_module.get_current_user, None)
        assert response.status_code == 200
        data = response.json()
        assert "classification" in data or "category" in data

    def test_triage_endpoint_requires_auth(self):
        app.dependency_overrides.pop(gmail_extension_module.get_current_user, None)
        response = client.post("/extension/triage", json={
            "email_id": "test-email-unauth",
            "subject": "Invoice",
            "sender": "billing@acme.com",
            "body": "Invoice body",
            "organization_id": "default",
        })
        assert response.status_code == 401

    def test_process_endpoint_runs_inline_triage_without_legacy_audit_kwarg(self):
        fake_audit = self._FakeAuditService()
        app.dependency_overrides[gmail_extension_module.get_current_user] = self._fake_user
        app.dependency_overrides[gmail_extension_module.require_ops_user] = self._fake_user
        app.dependency_overrides[gmail_extension_module.get_audit_service] = lambda: fake_audit
        try:
            with patch(
                "clearledgr.services.gmail_triage_service.run_inline_gmail_triage",
                AsyncMock(
                    return_value={
                        "email_id": "process-inline-1",
                        "action": "triaged",
                        "classification": {"type": "INVOICE"},
                    }
                ),
            ) as triage_mock:
                response = client.post(
                    "/extension/process",
                    json={
                        "email_id": "process-inline-1",
                        "subject": "Invoice inline process",
                        "sender": "billing@acme.com",
                        "organization_id": "default",
                    },
                )
        finally:
            app.dependency_overrides.pop(gmail_extension_module.get_current_user, None)
            app.dependency_overrides.pop(gmail_extension_module.require_ops_user, None)
            app.dependency_overrides.pop(gmail_extension_module.get_audit_service, None)

        assert response.status_code == 200
        payload = response.json()
        # §2: Event queue is the canonical path — returns "processing" when
        # event is enqueued (even in-memory fallback in tests).
        # Falls back to "processed_inline" only if queue is completely unavailable.
        assert payload["status"] in ("processing", "processed_inline", "duplicate")

    def test_bulk_scan_endpoint_runs_inline_triage_without_legacy_audit_kwarg(self):
        fake_audit = self._FakeAuditService()
        app.dependency_overrides[gmail_extension_module.get_current_user] = self._fake_user
        app.dependency_overrides[gmail_extension_module.require_ops_user] = self._fake_user
        app.dependency_overrides[gmail_extension_module.get_audit_service] = lambda: fake_audit
        try:
            with patch(
                "clearledgr.services.gmail_triage_service.run_inline_gmail_triage",
                AsyncMock(
                    side_effect=[
                        {
                            "email_id": "scan-inline-1",
                            "action": "triaged",
                            "classification": {"type": "INVOICE"},
                        },
                        {
                            "email_id": "scan-inline-2",
                            "action": "skipped",
                            "classification": {"type": "NOISE"},
                        },
                    ]
                ),
            ) as triage_mock:
                response = client.post(
                    "/extension/scan",
                    json={
                        "email_ids": ["scan-inline-1", "scan-inline-2"],
                        "organization_id": "default",
                    },
                )
        finally:
            app.dependency_overrides.pop(gmail_extension_module.get_current_user, None)
            app.dependency_overrides.pop(gmail_extension_module.require_ops_user, None)
            app.dependency_overrides.pop(gmail_extension_module.get_audit_service, None)

        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 2
        assert payload["processed"] == 2
        assert payload["labeled"] == 1
        assert triage_mock.await_count == 2

    def test_approve_and_post_uses_runtime_with_canonical_ap_item_reference(self, monkeypatch):
        captured: dict = {}

        async def _runtime_execute(self, intent, payload=None, *, idempotency_key=None):
            captured["intent"] = intent
            captured["payload"] = dict(payload or {})
            captured["idempotency_key"] = idempotency_key
            return {"status": "approved", "ap_item_id": payload.get("ap_item_id"), "email_id": payload.get("email_id")}

        fake_db = self._FakeExtensionDB()
        monkeypatch.setattr(
            "clearledgr.services.finance_agent_runtime.FinanceAgentRuntime.execute_intent",
            _runtime_execute,
        )

        app.dependency_overrides[gmail_extension_module.get_current_user] = self._fake_user
        try:
            with patch.object(gmail_extension_module, "get_db", return_value=fake_db):
                response = client.post(
                    "/extension/approve-and-post",
                    json={
                        "email_id": "gmail-thread-1",
                        "ap_item_id": "ap-item-1",
                        "extraction": {
                            "vendor": "Acme Corp",
                            "amount": 1250.50,
                            "override_justification": "month_end_exception",
                        },
                        "override": True,
                        "organization_id": "default",
                    },
                )
        finally:
            app.dependency_overrides.pop(gmail_extension_module.get_current_user, None)

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "approved"
        assert captured["intent"] == "post_to_erp"
        assert captured["payload"]["ap_item_id"] == "ap-item-1"
        assert captured["payload"]["email_id"] == "gmail-thread-1"
        assert captured["payload"]["source_channel"] == "gmail_extension"
        assert captured["payload"]["source_message_ref"] == "gmail-thread-1"
        assert captured["payload"]["override"] is True

    def test_submit_for_approval_uses_runtime_invoice_processing_contract(self, monkeypatch):
        captured: dict = {}

        class _FakeTrail:
            def __init__(self):
                self.events = []

            def log(self, *args, **kwargs):
                self.events.append({"args": args, "kwargs": kwargs})

        async def _execute_processing(self, invoice_payload=None, attachments=None, *, idempotency_key=None, correlation_id=None):
            captured["invoice_payload"] = dict(invoice_payload or {})
            captured["idempotency_key"] = idempotency_key
            captured["actor_email"] = self.actor_email
            captured["attachments"] = list(attachments or [])
            captured["correlation_id"] = correlation_id
            return {
                "status": "pending_approval",
                "ap_item_id": "ap-item-1",
                "email_id": (invoice_payload or {}).get("gmail_id"),
            }

        monkeypatch.setattr(
            "clearledgr.services.finance_agent_runtime.FinanceAgentRuntime.execute_ap_invoice_processing",
            _execute_processing,
        )
        monkeypatch.setattr(
            "clearledgr.services.finance_agent_runtime.FinanceAgentRuntime.ap_auto_approve_threshold",
            lambda self: 0.95,
        )

        fake_db = self._FakeExtensionDB()
        fake_trail = _FakeTrail()
        fake_audit = self._FakeAuditService()
        app.dependency_overrides[gmail_extension_module.get_current_user] = self._fake_user
        app.dependency_overrides[gmail_extension_module.get_audit_service] = lambda: fake_audit
        try:
            with patch.object(gmail_extension_module, "get_db", return_value=fake_db):
                with patch.object(gmail_extension_module, "get_audit_trail", return_value=fake_trail):
                    response = client.post(
                        "/extension/submit-for-approval",
                        json={
                            "email_id": "gmail-thread-1",
                            "subject": "Invoice INV-1001 from Acme Corp",
                            "sender": "billing@acme.example",
                            "vendor": "Acme Corp",
                            "amount": 1250.50,
                            "currency": "USD",
                            "invoice_number": "INV-1001",
                            "confidence": 0.76,
                            "organization_id": "default",
                            "vendor_intelligence": {"risk": "low"},
                            "policy_compliance": {"compliant": True, "required_approvers": []},
                            "priority": {"priority": "normal", "priority_label": "Normal"},
                            "budget_impact": [],
                            "agent_decision": {"decision": "auto_approve", "confidence": 0.98},
                            "idempotency_key": "idem-submit-runtime-1",
                        },
                    )
        finally:
            app.dependency_overrides.pop(gmail_extension_module.get_current_user, None)
            app.dependency_overrides.pop(gmail_extension_module.get_audit_service, None)

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "pending_approval"
        assert captured["idempotency_key"] == "idem-submit-runtime-1"
        assert captured["actor_email"] == "extension@example.com"
        assert captured["invoice_payload"]["gmail_id"] == "gmail-thread-1"
        assert captured["invoice_payload"]["organization_id"] == "default"
        assert captured["invoice_payload"]["user_id"] == "extension-user-1"
        assert captured["invoice_payload"]["confidence"] >= 0.95
        assert not fake_db.audit_rows
        assert not fake_audit.events

    def test_escalate_endpoint_uses_runtime_contract(self, monkeypatch):
        captured: dict = {}

        async def _escalate(self, **kwargs):
            captured["kwargs"] = dict(kwargs or {})
            captured["actor_email"] = self.actor_email
            return {
                "email_id": kwargs.get("email_id"),
                "ap_item_id": "ap-item-escalate-1",
                "status": "escalated",
                "channel": kwargs.get("channel"),
                "message": kwargs.get("message") or "Runtime escalation",
                "delivery": {"status": "sent"},
                "audit_event_id": "audit-escalate-1",
            }

        monkeypatch.setattr(
            "clearledgr.services.finance_agent_runtime.FinanceAgentRuntime.escalate_invoice_review",
            _escalate,
        )

        fake_audit = self._FakeAuditService()
        app.dependency_overrides[gmail_extension_module.get_current_user] = self._fake_user
        app.dependency_overrides[gmail_extension_module.get_audit_service] = lambda: fake_audit
        try:
            response = client.post(
                "/extension/escalate",
                json={
                    "email_id": "gmail-thread-escalate-1",
                    "vendor": "Escalate Co",
                    "amount": 300.0,
                    "currency": "USD",
                    "confidence": 82,
                    "mismatches": [{"message": "Amount mismatch"}],
                    "channel": "#finance-escalations",
                    "organization_id": "default",
                },
            )
        finally:
            app.dependency_overrides.pop(gmail_extension_module.get_current_user, None)
            app.dependency_overrides.pop(gmail_extension_module.get_audit_service, None)

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "escalated"
        assert captured["actor_email"] == "extension@example.com"
        assert captured["kwargs"]["email_id"] == "gmail-thread-escalate-1"
        assert captured["kwargs"]["channel"] == "#finance-escalations"
        assert payload["audit_event_id"] == "audit-escalate-1"
        assert not fake_audit.events

    def test_extension_register_gmail_token_success(self, monkeypatch):
        stored = {}
        state_calls = []

        class _Resp:
            def __init__(self, status_code, payload):
                self.status_code = status_code
                self._payload = payload

            def json(self):
                return self._payload

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, url, headers=None):
                if "gmail.googleapis.com/gmail/v1/users/me/profile" in url:
                    return _Resp(200, {"emailAddress": "mo@clearledgr.com"})
                return _Resp(404, {})

        def _store(token):
            stored["token"] = token

        class _FakeTokenStore:
            def get(self, _user_id):
                return None

            def store(self, token):
                _store(token)

        class _FakeDB:
            def save_gmail_autopilot_state(self, **kwargs):
                state_calls.append(kwargs)

        monkeypatch.setattr(gmail_extension_module.httpx, "AsyncClient", _FakeAsyncClient)
        monkeypatch.setattr(gmail_extension_module, "_token_store", lambda: _FakeTokenStore())
        monkeypatch.setattr(gmail_extension_module, "_gmail_token_class", lambda: SimpleNamespace)
        monkeypatch.setattr(gmail_extension_module, "get_db", lambda: _FakeDB())
        monkeypatch.setattr(
            gmail_extension_module,
            "get_user_by_email",
            lambda _email: SimpleNamespace(
                id="user-123",
                email="mo@clearledgr.com",
                organization_id="default",
                role="user",
            ),
        )

        response = client.post(
            "/extension/gmail/register-token",
            json={
                "access_token": "test-access-token",
                "expires_in": 3600,
                "email": "mo@clearledgr.com",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["email"] == "mo@clearledgr.com"
        assert data["backend_expires_in"] == gmail_extension_module.EXTENSION_BACKEND_TOKEN_TTL_SECONDS
        assert stored["token"].email == "mo@clearledgr.com"
        assert stored["token"].access_token == "test-access-token"
        assert state_calls and state_calls[0]["email"] == "mo@clearledgr.com"

    def test_extension_register_gmail_token_rejects_invalid_token(self, monkeypatch):
        class _Resp:
            def __init__(self, status_code, payload):
                self.status_code = status_code
                self._payload = payload

            def json(self):
                return self._payload

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, url, headers=None):
                if "gmail.googleapis.com/gmail/v1/users/me/profile" in url:
                    return _Resp(401, {"error": "invalid_token"})
                if "www.googleapis.com/oauth2/v2/userinfo" in url:
                    return _Resp(401, {"error": "invalid_token"})
                return _Resp(404, {})

        monkeypatch.setattr(gmail_extension_module.httpx, "AsyncClient", _FakeAsyncClient)
        monkeypatch.setattr(
            gmail_extension_module,
            "get_user_by_email",
            lambda _email: SimpleNamespace(
                id="user-123",
                email="mo@clearledgr.com",
                organization_id="default",
                role="user",
            ),
        )

        response = client.post(
            "/extension/gmail/register-token",
            json={
                "access_token": "bad-token",
                "expires_in": 3600,
                "email": "mo@clearledgr.com",
            },
        )
        assert response.status_code == 400
        assert "invalid_google_access_token" in str(response.json().get("detail", ""))

    def test_extension_register_gmail_token_rejects_org_mismatch(self, monkeypatch):
        class _Resp:
            def __init__(self, status_code, payload):
                self.status_code = status_code
                self._payload = payload

            def json(self):
                return self._payload

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, url, headers=None):
                if "gmail.googleapis.com/gmail/v1/users/me/profile" in url:
                    return _Resp(200, {"emailAddress": "mo@clearledgr.com"})
                return _Resp(404, {})

        monkeypatch.setattr(gmail_extension_module.httpx, "AsyncClient", _FakeAsyncClient)
        monkeypatch.setattr(
            gmail_extension_module,
            "get_user_by_email",
            lambda _email: SimpleNamespace(
                id="user-123",
                email="mo@clearledgr.com",
                organization_id="default",
                role="user",
            ),
        )

        response = client.post(
            "/extension/gmail/register-token",
            json={
                "access_token": "test-access-token",
                "expires_in": 3600,
                "email": "mo@clearledgr.com",
                "organization_id": "other-org",
            },
        )
        assert response.status_code == 403
        assert response.json().get("detail") == "org_mismatch"

    def test_extension_register_gmail_token_requires_provisioned_user(self, monkeypatch):
        class _Resp:
            def __init__(self, status_code, payload):
                self.status_code = status_code
                self._payload = payload

            def json(self):
                return self._payload

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, url, headers=None):
                if "gmail.googleapis.com/gmail/v1/users/me/profile" in url:
                    return _Resp(200, {"emailAddress": "new-user@clearledgr.com"})
                return _Resp(404, {})

        monkeypatch.setattr(gmail_extension_module.httpx, "AsyncClient", _FakeAsyncClient)
        monkeypatch.setattr(gmail_extension_module, "get_user_by_email", lambda _email: None)

        response = client.post(
            "/extension/gmail/register-token",
            json={
                "access_token": "test-access-token",
                "expires_in": 3600,
                "email": "new-user@clearledgr.com",
            },
        )
        # Auto-provisioning: unknown users are created on first extension login
        assert response.status_code == 200
        assert response.json().get("success") is True
        assert response.json().get("email") == "new-user@clearledgr.com"

    def test_sensitive_extension_endpoints_require_auth(self):
        app.dependency_overrides.pop(gmail_extension_module.get_current_user, None)

        assert client.post(
            "/extension/verify-confidence",
            json={"email_id": "x", "extraction": {}, "organization_id": "default"},
        ).status_code == 401
        assert client.post(
            "/extension/match-bank",
            json={"extraction": {}, "organization_id": "default"},
        ).status_code == 401
        assert client.post(
            "/extension/match-erp",
            json={"extraction": {}, "organization_id": "default"},
        ).status_code == 401
        assert client.post(
            "/extension/suggestions/gl-code",
            json={"vendor_name": "Acme", "organization_id": "default"},
        ).status_code == 401
        assert client.post(
            "/extension/suggestions/vendor",
            json={"organization_id": "default", "extracted_vendor": "Acme"},
        ).status_code == 401
        assert client.post(
            "/extension/suggestions/amount-validation",
            json={"vendor_name": "Acme", "amount": 10.5, "organization_id": "default"},
        ).status_code == 401
        assert client.get("/extension/suggestions/form-prefill/email-1?organization_id=default").status_code == 401
        assert client.get("/extension/needs-info-draft/AP-1").status_code == 401
        assert client.get("/extension/pipeline?organization_id=default").status_code == 401
        assert client.get("/extension/invoice-pipeline/default").status_code == 401
        assert client.get("/extension/invoice-status/email-1").status_code == 401
        assert client.get("/extension/workflow/wf-1").status_code == 401
        assert client.get("/extension/ap/AP-1/explain").status_code == 401
        # Field-correction is a runtime-owned mutation route and still requires auth.
        assert client.post(
            "/extension/record-field-correction",
            json={
                "ap_item_id": "AP-1",
                "field": "vendor",
                "original_value": "Old",
                "corrected_value": "New",
            },
        ).status_code == 401

    def test_sensitive_extension_endpoints_enforce_org_scope(self):
        app.dependency_overrides[gmail_extension_module.get_current_user] = self._fake_user
        try:
            verify = client.post(
                "/extension/verify-confidence",
                json={
                    "email_id": "x",
                    "extraction": {},
                    "organization_id": "other-org",
                },
            )
            match_bank = client.post(
                "/extension/match-bank",
                json={"extraction": {}, "organization_id": "other-org"},
            )
            match_erp = client.post(
                "/extension/match-erp",
                json={"extraction": {}, "organization_id": "other-org"},
            )
            suggest_gl = client.post(
                "/extension/suggestions/gl-code",
                json={
                    "vendor_name": "Acme",
                    "organization_id": "other-org",
                },
            )
            suggest_vendor = client.post(
                "/extension/suggestions/vendor",
                json={
                    "sender_email": "billing@acme.test",
                    "organization_id": "other-org",
                },
            )
            validate_amount = client.post(
                "/extension/suggestions/amount-validation",
                json={
                    "vendor_name": "Acme",
                    "amount": 100,
                    "organization_id": "other-org",
                },
            )
            form_prefill = client.get(
                "/extension/suggestions/form-prefill/email-1?organization_id=other-org"
            )
        finally:
            app.dependency_overrides.pop(gmail_extension_module.get_current_user, None)

        assert verify.status_code == 403
        assert match_bank.status_code == 403
        assert match_erp.status_code == 403
        assert suggest_gl.status_code == 403
        assert suggest_vendor.status_code == 403
        assert validate_amount.status_code == 403
        assert form_prefill.status_code == 403

    def test_extension_match_endpoints_return_results_for_authorized_user(self):
        app.dependency_overrides[gmail_extension_module.get_current_user] = self._fake_user
        try:
            match_bank = client.post(
                "/extension/match-bank",
                json={
                    "organization_id": "default",
                    "extraction": {
                        "vendor": "Acme Corp",
                        "amount": 1250.0,
                        "currency": "USD",
                        "invoice_number": "INV-1001",
                    },
                },
            )
            match_erp = client.post(
                "/extension/match-erp",
                json={
                    "organization_id": "default",
                    "extraction": {
                        "vendor": "Acme Corp",
                        "amount": 1250.0,
                        "currency": "USD",
                        "invoice_number": "INV-1001",
                    },
                },
            )
        finally:
            app.dependency_overrides.pop(gmail_extension_module.get_current_user, None)

        assert match_bank.status_code == 200
        assert "status" in match_bank.json()
        assert "candidate_count" in match_bank.json()
        assert match_erp.status_code == 200
        assert "vendor_match" in match_erp.json()
        assert "duplicate_invoice" in match_erp.json()

    def test_repair_historical_invoices_replays_live_gmail_records_without_runtime_side_effects(self):
        finance_email = SimpleNamespace(
            id="finance-email-1",
            gmail_id="msg-repair-1",
            email_type="invoice",
            confidence=0.92,
            organization_id="default",
            user_id="extension-user-1",
            status="processed",
            metadata={},
            created_at="2026-03-18T10:00:00+00:00",
        )
        updated_finance_email = SimpleNamespace(
            id="finance-email-1",
            gmail_id="msg-repair-1",
            email_type="invoice",
            confidence=0.92,
            organization_id="default",
            user_id="extension-user-1",
            status="review_required",
            metadata={"field_provenance": {"amount": {"source": "attachment"}}},
            created_at="2026-03-18T10:00:00+00:00",
        )

        class _FakeDB:
            def list_finance_emails_for_repair(self, *_args, **_kwargs):
                return [finance_email]

            def get_finance_email_by_gmail_id(self, gmail_id):
                assert gmail_id == "msg-repair-1"
                return updated_finance_email

            def get_ap_item_by_message_id(self, organization_id, message_id):
                assert organization_id == "default"
                assert message_id == "msg-repair-1"
                return {"id": "ap-item-1", "metadata": {}}

            def get_ap_item_by_thread(self, _organization_id, _thread_id):
                return None

        class _FakeGmailClient:
            def __init__(self, user_id):
                assert user_id == "extension-user-1"

            async def ensure_authenticated(self):
                return True

            async def get_message(self, message_id):
                assert message_id == "msg-repair-1"
                return SimpleNamespace(
                    id="msg-repair-1",
                    thread_id="thread-repair-1",
                    subject="Invoice INV-REPAIR-1",
                    sender="billing@vendor.test",
                    snippet="Invoice attached",
                    body_text="Please find attached invoice.",
                    attachments=[],
                    date=datetime.now(timezone.utc),
                )

        replay_mock = AsyncMock(return_value={"status": "refreshed"})

        app.dependency_overrides[gmail_extension_module.get_current_user] = self._fake_user
        try:
            with patch.object(gmail_extension_module, "get_db", return_value=_FakeDB()):
                with patch.object(gmail_extension_module, "_gmail_api_client", _FakeGmailClient):
                    with patch.object(
                        gmail_extension_module,
                        "build_worklist_item",
                        return_value={
                            "id": "ap-item-1",
                            "requires_field_review": True,
                            "blocked_fields": ["amount"],
                            "workflow_paused_reason": "Workflow paused until amount is confirmed because the email and attachment disagree.",
                        },
                    ):
                        with patch.object(gmail_webhooks_module, "process_invoice_email", replay_mock):
                            response = client.post(
                                "/extension/repair-historical-invoices",
                                json={
                                    "organization_id": "default",
                                    "limit": 10,
                                },
                            )
        finally:
            app.dependency_overrides.pop(gmail_extension_module.get_current_user, None)

        assert response.status_code == 200
        payload = response.json()
        assert payload["processed"] == 1
        assert payload["review_required"] == 1
        assert payload["repaired"] == 0
        assert payload["results"][0]["gmail_id"] == "msg-repair-1"
        assert payload["results"][0]["requires_field_review"] is True
        assert payload["results"][0]["blocked_fields"] == ["amount"]
        replay_mock.assert_awaited_once()
        replay_kwargs = replay_mock.await_args.kwargs
        assert replay_kwargs["run_runtime"] is False
        assert replay_kwargs["create_draft"] is False
        assert replay_kwargs["refresh_reason"] == "historical_repair_pass"

    def test_cleanup_gmail_labels_migrates_and_deletes_legacy_mailbox_labels(self):
        class _FakeGmailClient:
            def __init__(self, user_id):
                assert user_id == "extension-user-1"

            async def ensure_authenticated(self):
                return True

        cleanup_mock = AsyncMock(
            return_value={
                "status": "completed",
                "dry_run": False,
                "labels_scanned": 2,
                "labels_deleted": 2,
                "messages_relabelled": 7,
                "results": [
                    {
                        "label_name": "Clearledgr/Invoice",
                        "target_labels": ["Clearledgr/Invoices"],
                        "messages_relabelled": 5,
                        "deleted": True,
                    },
                    {
                        "label_name": "Clearledgr/Payment Request",
                        "target_labels": ["Clearledgr/Payment Requests"],
                        "messages_relabelled": 2,
                        "deleted": True,
                    },
                ],
            }
        )

        app.dependency_overrides[gmail_extension_module.get_current_user] = self._fake_user
        try:
            with patch.object(gmail_extension_module, "_gmail_api_client", _FakeGmailClient):
                with patch("clearledgr.services.gmail_labels.cleanup_legacy_labels", cleanup_mock):
                    response = client.post(
                        "/extension/cleanup-gmail-labels",
                        json={
                            "organization_id": "default",
                            "dry_run": False,
                            "max_messages_per_label": 250,
                        },
                    )
        finally:
            app.dependency_overrides.pop(gmail_extension_module.get_current_user, None)

        assert response.status_code == 200
        payload = response.json()
        assert payload["mailbox_user_email"] == "extension@example.com"
        assert payload["labels_deleted"] == 2
        assert payload["messages_relabelled"] == 7
        cleanup_mock.assert_awaited_once()
        kwargs = cleanup_mock.await_args.kwargs
        assert kwargs["user_email"] == "extension@example.com"
        assert kwargs["dry_run"] is False
        assert kwargs["max_messages_per_label"] == 250

    def test_extension_cors_preflight_returns_single_origin_header(self):
        response = client.options(
            "/extension/worklist?organization_id=default",
            headers={
                "Origin": "https://mail.google.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code in {200, 204}
        allow_origin = str(response.headers.get("access-control-allow-origin") or "")
        assert allow_origin == "https://mail.google.com"
        assert "," not in allow_origin
        assert "*" not in allow_origin

    def test_extension_worklist_nudges_gmail_autopilot_progress(self):
        class _FakeDB:
            def list_ap_items(self, *_args, **_kwargs):
                return []

        app.dependency_overrides[gmail_extension_module.get_current_user] = lambda: TokenData(
            user_id="extension-user-1",
            email="extension@example.com",
            organization_id="default",
            role="user",
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        try:
            with patch.object(gmail_extension_module, "get_db", return_value=_FakeDB()):
                with patch("clearledgr.services.gmail_autopilot.ensure_gmail_autopilot_progress", AsyncMock(return_value={"started": True, "nudged": True})) as ensure_mock:
                    response = client.get("/extension/worklist?organization_id=default")
        finally:
            app.dependency_overrides.pop(gmail_extension_module.get_current_user, None)

        assert response.status_code == 200
        assert response.json()["items"] == []
        ensure_mock.assert_awaited_once()

    def test_cors_policy_drops_wildcard_when_explicit_origins_present(self):
        origins, regex = main_module._resolve_cors_policy(
            "*, https://mail.google.com, https://mail.google.com",
            r"^chrome-extension://ignored$",
        )
        assert origins == ["https://mail.google.com"]
        assert regex is None

    def test_cors_policy_wildcard_only_falls_back_to_safe_defaults(self):
        origins, regex = main_module._resolve_cors_policy(
            "*",
            "",
        )
        assert origins == main_module._default_cors_origins
        assert regex == r"^chrome-extension://[a-z]{32}$"
    
    def test_invoice_pipeline(self):
        """Invoice pipeline requires auth."""
        response = client.get("/extension/invoice-pipeline/default")
        assert response.status_code == 401

    class _FakeAuditService:
        def __init__(self):
            self.events = []

        def record_event(self, **kwargs):
            self.events.append(kwargs)

    class _FakeExtensionDB:
        def __init__(self, *, ap_item=None, slack_thread=None, audit_events=None):
            self.ap_item = ap_item or {
                "id": "ap-item-1",
                "organization_id": "default",
                "thread_id": "gmail-thread-1",
                "state": "needs_approval",
                "vendor_name": "Acme Corp",
                "invoice_number": "INV-1001",
                "amount": 1250.50,
                "currency": "USD",
                "next_action": "approve_or_reject",
                "exception_code": "approval_required",
                "metadata": {
                    "correlation_id": "corr-123",
                    "teams": {"channel": "19:teams-channel", "message_id": "teams-message-1"},
                },
            }
            self.slack_thread = slack_thread or {
                "channel_id": "C123",
                "thread_ts": "171.100",
                "thread_id": "171.100",
            }
            self.audit_events = audit_events or [
                {"event_type": "state_transition"},
                {"event_type": "approval_requested"},
            ]
            self.audit_rows = []

        def get_ap_item(self, email_id):
            candidates = {
                str(self.ap_item.get("id") or ""),
                str(self.ap_item.get("thread_id") or ""),
                str(self.ap_item.get("message_id") or ""),
            }
            return self.ap_item if str(email_id) in candidates else None

        def get_ap_item_by_thread(self, organization_id, thread_id):
            if str(organization_id or "") != str(self.ap_item.get("organization_id") or ""):
                return None
            return self.ap_item if str(thread_id) == str(self.ap_item.get("thread_id") or "") else None

        def get_ap_item_by_message_id(self, organization_id, message_id):
            if str(organization_id or "") != str(self.ap_item.get("organization_id") or ""):
                return None
            return self.ap_item if str(message_id) == str(self.ap_item.get("message_id") or "") else None

        def list_ap_audit_events(self, ap_item_id):
            return list(self.audit_events) if str(ap_item_id) == str(self.ap_item.get("id") or "") else []

        def append_ap_audit_event(self, payload):
            key = str((payload or {}).get("idempotency_key") or "").strip()
            if key:
                existing = self.get_ap_audit_event_by_key(key)
                if existing:
                    return existing
            data = dict(payload or {})
            if "payload_json" not in data:
                data["payload_json"] = dict(data.get("metadata") or {})
            row = {"id": f"audit-{len(self.audit_rows) + 1}", **data}
            self.audit_rows.append(row)
            return row

        def get_ap_audit_event_by_key(self, idempotency_key):
            key = str(idempotency_key or "").strip()
            if not key:
                return None
            for row in self.audit_rows:
                if str(row.get("idempotency_key") or "").strip() == key:
                    return row
            return None

        def update_ap_item(self, ap_item_id, **kwargs):
            if str(ap_item_id) != str(self.ap_item.get("id") or ""):
                return False
            for key, value in (kwargs or {}).items():
                self.ap_item[key] = value
            return True

        def get_slack_thread(self, gmail_id):
            if str(gmail_id) == str(self.ap_item.get("thread_id") or ""):
                return dict(self.slack_thread or {})
            return None

    def test_approval_nudge_endpoint_uses_runtime_with_canonical_ap_item_reference(self, monkeypatch):
        app.dependency_overrides[gmail_extension_module.get_current_user] = self._fake_user
        fake_audit = self._FakeAuditService()
        app.dependency_overrides[gmail_extension_module.get_audit_service] = lambda: fake_audit

        fake_db = self._FakeExtensionDB()
        captured: dict = {}

        async def _runtime_execute(self, intent, payload=None, *, idempotency_key=None):
            captured["intent"] = intent
            captured["payload"] = dict(payload or {})
            captured["idempotency_key"] = idempotency_key
            return {"status": "nudged", "ap_item_id": payload.get("ap_item_id"), "audit_event_id": "audit-runtime-1"}

        try:
            with patch.object(gmail_extension_module, "get_db", return_value=fake_db):
                with patch("clearledgr.services.finance_agent_runtime.FinanceAgentRuntime.execute_intent", _runtime_execute):
                    response = client.post(
                        "/extension/approval-nudge",
                        json={
                            "email_id": "gmail-thread-1",
                            "ap_item_id": "ap-item-1",
                            "message": "Please review today",
                            "organization_id": "default",
                        },
                    )
        finally:
            app.dependency_overrides.pop(gmail_extension_module.get_current_user, None)
            app.dependency_overrides.pop(gmail_extension_module.get_audit_service, None)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "nudged"
        assert captured["intent"] == "nudge_approval"
        assert captured["payload"]["ap_item_id"] == "ap-item-1"
        assert captured["payload"]["email_id"] == "gmail-thread-1"
        assert captured["payload"]["source_message_ref"] == "gmail-thread-1"
        assert data["audit_event_id"]
        assert not fake_audit.events

    def test_budget_decision_approve_override_uses_runtime_with_canonical_ap_item_reference(self):
        app.dependency_overrides[gmail_extension_module.get_current_user] = self._fake_user
        fake_audit = self._FakeAuditService()
        app.dependency_overrides[gmail_extension_module.get_audit_service] = lambda: fake_audit
        fake_db = self._FakeExtensionDB()
        captured: dict = {}

        async def _runtime_execute(self, intent, payload=None, *, idempotency_key=None):
            captured["intent"] = intent
            captured["payload"] = dict(payload or {})
            return {"status": "approved", "ap_item_id": payload.get("ap_item_id")}

        try:
            with patch.object(gmail_extension_module, "get_db", return_value=fake_db):
                with patch("clearledgr.services.finance_agent_runtime.FinanceAgentRuntime.execute_intent", _runtime_execute):
                    response = client.post(
                        "/extension/budget-decision",
                        json={
                            "email_id": "gmail-thread-1",
                            "ap_item_id": "ap-item-1",
                            "decision": "approve_override",
                            "justification": "Policy exception approved",
                            "organization_id": "default",
                        },
                    )
        finally:
            app.dependency_overrides.pop(gmail_extension_module.get_current_user, None)
            app.dependency_overrides.pop(gmail_extension_module.get_audit_service, None)

        assert response.status_code == 200
        assert captured["intent"] == "approve_invoice"
        assert captured["payload"]["ap_item_id"] == "ap-item-1"
        assert captured["payload"]["email_id"] == "gmail-thread-1"
        assert captured["payload"]["approve_override"] is True
        assert captured["payload"]["action_variant"] == "budget_override"
        assert not fake_audit.events

    def test_budget_decision_request_adjustment_uses_runtime_request_info(self):
        app.dependency_overrides[gmail_extension_module.get_current_user] = self._fake_user
        fake_audit = self._FakeAuditService()
        app.dependency_overrides[gmail_extension_module.get_audit_service] = lambda: fake_audit
        fake_db = self._FakeExtensionDB()
        captured: dict = {}

        async def _runtime_execute(self, intent, payload=None, *, idempotency_key=None):
            captured["intent"] = intent
            captured["payload"] = dict(payload or {})
            return {"status": "needs_info", "ap_item_id": payload.get("ap_item_id")}

        try:
            with patch.object(gmail_extension_module, "get_db", return_value=fake_db):
                with patch("clearledgr.services.finance_agent_runtime.FinanceAgentRuntime.execute_intent", _runtime_execute):
                    response = client.post(
                        "/extension/budget-decision",
                        json={
                            "email_id": "gmail-thread-1",
                            "ap_item_id": "ap-item-1",
                            "decision": "request_budget_adjustment",
                            "justification": "Need updated cost centre approval",
                            "organization_id": "default",
                        },
                    )
        finally:
            app.dependency_overrides.pop(gmail_extension_module.get_current_user, None)
            app.dependency_overrides.pop(gmail_extension_module.get_audit_service, None)

        assert response.status_code == 200
        assert captured["intent"] == "request_info"
        assert captured["payload"]["ap_item_id"] == "ap-item-1"
        assert captured["payload"]["email_id"] == "gmail-thread-1"
        assert captured["payload"]["reason"] == "Need updated cost centre approval"
        assert not fake_audit.events

    def test_budget_decision_reject_uses_runtime_reject_invoice(self):
        app.dependency_overrides[gmail_extension_module.get_current_user] = self._fake_user
        fake_audit = self._FakeAuditService()
        app.dependency_overrides[gmail_extension_module.get_audit_service] = lambda: fake_audit
        fake_db = self._FakeExtensionDB()
        captured: dict = {}

        async def _runtime_execute(self, intent, payload=None, *, idempotency_key=None):
            captured["intent"] = intent
            captured["payload"] = dict(payload or {})
            return {"status": "rejected", "ap_item_id": payload.get("ap_item_id")}

        try:
            with patch.object(gmail_extension_module, "get_db", return_value=fake_db):
                with patch("clearledgr.services.finance_agent_runtime.FinanceAgentRuntime.execute_intent", _runtime_execute):
                    response = client.post(
                        "/extension/budget-decision",
                        json={
                            "email_id": "gmail-thread-1",
                            "ap_item_id": "ap-item-1",
                            "decision": "reject",
                            "justification": "Budget not approved",
                            "organization_id": "default",
                        },
                    )
        finally:
            app.dependency_overrides.pop(gmail_extension_module.get_current_user, None)
            app.dependency_overrides.pop(gmail_extension_module.get_audit_service, None)

        assert response.status_code == 200
        assert captured["intent"] == "reject_invoice"
        assert captured["payload"]["ap_item_id"] == "ap-item-1"
        assert captured["payload"]["email_id"] == "gmail-thread-1"
        assert captured["payload"]["reason"] == "Budget not approved"
        assert not fake_audit.events

    def test_finance_summary_share_preview_email_draft_returns_preview_and_audits(self):
        app.dependency_overrides[gmail_extension_module.get_current_user] = self._fake_user
        fake_audit = self._FakeAuditService()
        app.dependency_overrides[gmail_extension_module.get_audit_service] = lambda: fake_audit
        fake_db = self._FakeExtensionDB(
            ap_item={
                "id": "ap-item-2",
                "organization_id": "default",
                "thread_id": "gmail-thread-2",
                "state": "failed_post",
                "vendor_name": "Vendor Ops",
                "invoice_number": "INV-2002",
                "amount": 902.14,
                "currency": "USD",
                "next_action": "retry_posting",
                "exception_code": "erp_post_failed",
                "metadata": {"correlation_id": "corr-456"},
            }
        )
        try:
            with patch.object(gmail_extension_module, "get_db", return_value=fake_db):
                response = client.post(
                    "/extension/finance-summary-share",
                    json={
                        "email_id": "gmail-thread-2",
                        "target": "email_draft",
                        "preview_only": True,
                        "recipient_email": "financelead@example.com",
                        "organization_id": "default",
                    },
                )
        finally:
            app.dependency_overrides.pop(gmail_extension_module.get_current_user, None)
            app.dependency_overrides.pop(gmail_extension_module.get_audit_service, None)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "preview"
        assert data["target"] == "email_draft"
        assert data["preview"]["kind"] == "email_draft"
        assert data["preview"]["draft"]["to"] == "financelead@example.com"
        assert data["audit_event_id"]
        assert fake_db.audit_rows[-1]["event_type"] == "finance_summary_share_previewed"
        assert not fake_audit.events

    def test_finance_summary_share_preview_uses_runtime_contract(self, monkeypatch):
        captured: dict = {}

        async def _share(self, **kwargs):
            captured["kwargs"] = dict(kwargs or {})
            captured["actor_email"] = self.actor_email
            return {
                "status": "preview",
                "target": "email_draft",
                "email_id": "gmail-thread-2",
                "ap_item_id": "ap-item-2",
                "summary": {"title": "Finance lead exception summary", "lines": ["One line"]},
                "preview": {
                    "kind": "email_draft",
                    "draft": {"to": "financelead@example.com", "subject": "Subj", "body": "Body"},
                },
                "audit_event_id": "audit-summary-runtime-1",
            }

        monkeypatch.setattr(
            "clearledgr.services.finance_agent_runtime.FinanceAgentRuntime.share_finance_summary",
            _share,
        )

        fake_db = self._FakeExtensionDB()
        fake_audit = self._FakeAuditService()
        app.dependency_overrides[gmail_extension_module.get_current_user] = self._fake_user
        app.dependency_overrides[gmail_extension_module.get_audit_service] = lambda: fake_audit
        try:
            with patch.object(gmail_extension_module, "get_db", return_value=fake_db):
                response = client.post(
                    "/extension/finance-summary-share",
                    json={
                        "email_id": "gmail-thread-2",
                        "ap_item_id": "ap-item-2",
                        "target": "email_draft",
                        "preview_only": True,
                        "recipient_email": "financelead@example.com",
                        "organization_id": "default",
                    },
                )
        finally:
            app.dependency_overrides.pop(gmail_extension_module.get_current_user, None)
            app.dependency_overrides.pop(gmail_extension_module.get_audit_service, None)

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "preview"
        assert captured["actor_email"] == "extension@example.com"
        assert captured["kwargs"]["reference_id"] == "ap-item-2"
        assert captured["kwargs"]["preview_only"] is True
        assert payload["audit_event_id"] == "audit-summary-runtime-1"
        assert not fake_audit.events

    def test_finance_summary_share_preview_slack_thread_returns_message_preview(self):
        app.dependency_overrides[gmail_extension_module.get_current_user] = self._fake_user
        fake_audit = self._FakeAuditService()
        app.dependency_overrides[gmail_extension_module.get_audit_service] = lambda: fake_audit
        fake_db = self._FakeExtensionDB(
            ap_item={
                "id": "ap-item-3",
                "organization_id": "default",
                "thread_id": "gmail-thread-3",
                "state": "needs_approval",
                "vendor_name": "Blue Supply",
                "invoice_number": "INV-3003",
                "amount": 450.00,
                "currency": "USD",
                "next_action": "approve_or_reject",
                "exception_code": "approval_required",
                "metadata": {"correlation_id": "corr-789"},
            },
            slack_thread={"channel_id": "C999", "thread_ts": "333.10", "thread_id": "333.10"},
        )
        try:
            with patch.object(gmail_extension_module, "get_db", return_value=fake_db):
                response = client.post(
                    "/extension/finance-summary-share",
                    json={
                        "email_id": "gmail-thread-3",
                        "target": "slack_thread",
                        "preview_only": True,
                        "organization_id": "default",
                    },
                )
        finally:
            app.dependency_overrides.pop(gmail_extension_module.get_current_user, None)
            app.dependency_overrides.pop(gmail_extension_module.get_audit_service, None)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "preview"
        assert data["target"] == "slack_thread"
        assert data["preview"]["kind"] == "slack_thread"
        assert data["preview"]["channel_id"] == "C999"
        assert "Finance lead exception summary" in data["preview"]["text"]

    def test_finance_summary_share_preview_teams_reply_returns_activity_preview(self):
        app.dependency_overrides[gmail_extension_module.get_current_user] = self._fake_user
        fake_audit = self._FakeAuditService()
        app.dependency_overrides[gmail_extension_module.get_audit_service] = lambda: fake_audit
        fake_db = self._FakeExtensionDB(
            ap_item={
                "id": "ap-item-4",
                "organization_id": "default",
                "thread_id": "gmail-thread-4",
                "state": "needs_info",
                "vendor_name": "Northwind",
                "invoice_number": "INV-4004",
                "amount": 120.75,
                "currency": "USD",
                "next_action": "request_info",
                "exception_code": "missing_fields",
                "metadata": {
                    "correlation_id": "corr-101",
                    "teams": {"channel": "19:chan", "message_id": "msg-42"},
                },
            }
        )
        try:
            with patch.object(gmail_extension_module, "get_db", return_value=fake_db):
                response = client.post(
                    "/extension/finance-summary-share",
                    json={
                        "email_id": "gmail-thread-4",
                        "target": "teams_reply",
                        "preview_only": True,
                        "organization_id": "default",
                    },
                )
        finally:
            app.dependency_overrides.pop(gmail_extension_module.get_current_user, None)
            app.dependency_overrides.pop(gmail_extension_module.get_audit_service, None)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "preview"
        assert data["target"] == "teams_reply"
        assert data["preview"]["kind"] == "teams_reply"
        assert data["preview"]["channel_id"] == "19:chan"
        activity = data["preview"]["activity"]
        assert isinstance(activity, dict)
        assert activity.get("replyToId") == "msg-42"
        assert "attachments" in activity

    def test_vendor_followup_endpoint_prepares_draft_and_updates_metadata(self):
        app.dependency_overrides[gmail_extension_module.get_current_user] = self._fake_user
        fake_audit = self._FakeAuditService()
        app.dependency_overrides[gmail_extension_module.get_audit_service] = lambda: fake_audit

        runtime_response = {
            "status": "prepared",
            "draft_id": "draft-followup-123",
            "followup_attempt_count": 1,
            "followup_next_action": "await_vendor_response",
            "audit_event_id": "audit-followup-1",
        }
        mock_runtime = MagicMock()
        mock_runtime.execute_intent = AsyncMock(return_value=runtime_response)

        try:
            with patch.object(gmail_extension_module, "_build_finance_runtime", return_value=mock_runtime):
                response = client.post(
                    "/extension/vendor-followup",
                    json={
                        "email_id": "gmail-thread-followup-1",
                        "organization_id": "default",
                    },
                )
        finally:
            app.dependency_overrides.pop(gmail_extension_module.get_current_user, None)
            app.dependency_overrides.pop(gmail_extension_module.get_audit_service, None)

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "prepared"
        assert payload["draft_id"] == "draft-followup-123"
        assert payload["followup_attempt_count"] == 1
        assert payload["followup_next_action"] == "await_vendor_response"
        assert payload["audit_event_id"]
        mock_runtime.execute_intent.assert_awaited_once()

    def test_vendor_followup_endpoint_respects_sla_wait_window(self):
        app.dependency_overrides[gmail_extension_module.get_current_user] = self._fake_user
        fake_audit = self._FakeAuditService()
        app.dependency_overrides[gmail_extension_module.get_audit_service] = lambda: fake_audit

        runtime_response = {
            "status": "waiting_sla",
            "followup_attempt_count": 1,
            "followup_next_action": "await_vendor_response",
        }
        mock_runtime = MagicMock()
        mock_runtime.execute_intent = AsyncMock(return_value=runtime_response)

        try:
            with patch.object(gmail_extension_module, "_build_finance_runtime", return_value=mock_runtime):
                response = client.post(
                    "/extension/vendor-followup",
                    json={
                        "email_id": "gmail-thread-followup-2",
                        "organization_id": "default",
                    },
                )
        finally:
            app.dependency_overrides.pop(gmail_extension_module.get_current_user, None)
            app.dependency_overrides.pop(gmail_extension_module.get_audit_service, None)

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "waiting_sla"
        assert payload["followup_attempt_count"] == 1
        assert payload["followup_next_action"] == "await_vendor_response"

    def test_record_field_correction_uses_runtime_contract(self, monkeypatch):
        captured: dict = {}

        def _record(self, **kwargs):
            captured["kwargs"] = dict(kwargs or {})
            captured["actor_email"] = self.actor_email
            return {
                "status": "recorded",
                "ap_item_id": kwargs.get("ap_item_id"),
                "field": kwargs.get("field"),
                "learning_result": {"recorded": True},
                "audit_event_id": "audit-field-correction-1",
            }

        monkeypatch.setattr(
            "clearledgr.services.finance_agent_runtime.FinanceAgentRuntime.record_field_correction",
            _record,
        )

        app.dependency_overrides[gmail_extension_module.get_current_user] = self._fake_user
        try:
            response = client.post(
                "/extension/record-field-correction",
                json={
                    "ap_item_id": "ap-item-1",
                    "field": "invoice_number",
                    "original_value": "INV-OLD",
                    "corrected_value": "INV-NEW",
                    "feedback": "Corrected from vendor email",
                },
            )
        finally:
            app.dependency_overrides.pop(gmail_extension_module.get_current_user, None)

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "recorded"
        assert captured["actor_email"] == "extension@example.com"
        assert captured["kwargs"]["ap_item_id"] == "ap-item-1"
        assert captured["kwargs"]["field"] == "invoice_number"

    def test_vendor_followup_endpoint_idempotency_replays_previous_response(self):
        app.dependency_overrides[gmail_extension_module.get_current_user] = self._fake_user
        fake_audit = self._FakeAuditService()
        app.dependency_overrides[gmail_extension_module.get_audit_service] = lambda: fake_audit

        call_count = {"n": 0}

        async def _mock_execute(intent, payload, *, idempotency_key=None):
            call_count["n"] += 1
            base = {
                "status": "prepared",
                "draft_id": "draft-followup-idem",
                "followup_attempt_count": 1,
                "followup_next_action": "await_vendor_response",
                "audit_event_id": "audit-followup-idem-1",
            }
            if call_count["n"] > 1:
                base["idempotency_replayed"] = True
            return base

        mock_runtime = MagicMock()
        mock_runtime.execute_intent = _mock_execute

        body = {
            "email_id": "gmail-thread-followup-idem",
            "organization_id": "default",
            "idempotency_key": "idem-followup-1",
        }

        try:
            with patch.object(gmail_extension_module, "_build_finance_runtime", return_value=mock_runtime):
                first = client.post("/extension/vendor-followup", json=body)
                second = client.post("/extension/vendor-followup", json=body)
        finally:
            app.dependency_overrides.pop(gmail_extension_module.get_current_user, None)
            app.dependency_overrides.pop(gmail_extension_module.get_audit_service, None)

        assert first.status_code == 200
        assert second.status_code == 200
        first_payload = first.json()
        second_payload = second.json()
        assert first_payload["status"] == "prepared"
        assert second_payload["status"] == "prepared"
        assert second_payload["idempotency_replayed"] is True

    def test_route_low_risk_approval_endpoint_routes_and_replays_idempotent_request(self):
        app.dependency_overrides[gmail_extension_module.get_current_user] = self._fake_user
        fake_audit = self._FakeAuditService()
        app.dependency_overrides[gmail_extension_module.get_audit_service] = lambda: fake_audit
        fake_db = self._FakeExtensionDB(
            ap_item={
                "id": "ap-item-route-1",
                "organization_id": "default",
                "thread_id": "gmail-thread-route-1",
                "state": "validated",
                "vendor_name": "Route Co",
                "invoice_number": "INV-ROUTE-1",
                "amount": 140.0,
                "currency": "USD",
                "metadata": {"correlation_id": "corr-route-1"},
            }
        )
        fake_workflow = MagicMock()
        fake_workflow.evaluate_batch_route_low_risk_for_approval.return_value = {
            "eligible": True,
            "reason_codes": [],
            "state": "validated",
        }
        fake_workflow.build_invoice_data_from_ap_item.return_value = SimpleNamespace(
            gmail_id="gmail-thread-route-1"
        )
        fake_workflow._send_for_approval = AsyncMock(return_value={"status": "pending_approval", "slack_ts": "111.22"})

        body = {
            "email_id": "gmail-thread-route-1",
            "organization_id": "default",
            "idempotency_key": "idem-route-1",
        }

        try:
            with patch.object(gmail_extension_module, "get_db", return_value=fake_db):
                with patch("clearledgr.services.finance_skills.ap_skill.get_invoice_workflow", return_value=fake_workflow):
                    first = client.post("/extension/route-low-risk-approval", json=body)
                    second = client.post("/extension/route-low-risk-approval", json=body)
        finally:
            app.dependency_overrides.pop(gmail_extension_module.get_current_user, None)
            app.dependency_overrides.pop(gmail_extension_module.get_audit_service, None)

        assert first.status_code == 200
        assert second.status_code == 200
        first_payload = first.json()
        second_payload = second.json()
        assert first_payload["status"] == "pending_approval"
        assert second_payload["status"] == "pending_approval"
        assert second_payload["idempotency_replayed"] is True
        assert any(row.get("event_type") == "route_low_risk_for_approval" for row in fake_db.audit_rows)

    def test_retry_recoverable_failure_endpoint_uses_resume_workflow_and_replays_idempotent_request(self):
        app.dependency_overrides[gmail_extension_module.get_current_user] = self._fake_user
        fake_audit = self._FakeAuditService()
        app.dependency_overrides[gmail_extension_module.get_audit_service] = lambda: fake_audit

        call_count = {"n": 0}

        async def _mock_execute(intent, payload, *, idempotency_key=None):
            call_count["n"] += 1
            base = {
                "status": "posted",
                "erp_reference": "ERP-REC-1",
                "audit_event_id": "audit-retry-1",
            }
            if call_count["n"] > 1:
                base["idempotency_replayed"] = True
            return base

        mock_runtime = MagicMock()
        mock_runtime.execute_intent = _mock_execute

        body = {
            "email_id": "gmail-thread-retry-1",
            "organization_id": "default",
            "idempotency_key": "idem-retry-1",
        }

        try:
            with patch.object(gmail_extension_module, "_build_finance_runtime", return_value=mock_runtime):
                first = client.post("/extension/retry-recoverable-failure", json=body)
                second = client.post("/extension/retry-recoverable-failure", json=body)
        finally:
            app.dependency_overrides.pop(gmail_extension_module.get_current_user, None)
            app.dependency_overrides.pop(gmail_extension_module.get_audit_service, None)

        assert first.status_code == 200
        assert second.status_code == 200
        first_payload = first.json()
        second_payload = second.json()
        assert first_payload["status"] == "posted"
        assert first_payload["erp_reference"] == "ERP-REC-1"
        assert second_payload["status"] == "posted"
        assert second_payload["idempotency_replayed"] is True


class TestOrgConfigEndpoints:
    """Strict AP-v1 profile should not expose legacy /config routes."""

    @staticmethod
    def _fake_user(role: str = "user", org_id: str = "default"):
        return TokenData(
            user_id="config-user-1",
            email="config-user@example.com",
            organization_id=org_id,
            role=role,
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    def test_org_config_surface_disabled_in_strict_profile(self):
        response = client.get("/config/organizations/default")
        assert response.status_code == 404
        body = response.json()
        assert body.get("detail") == "endpoint_disabled_in_ap_v1_profile"

    def test_org_config_thresholds_surface_disabled_in_strict_profile(self):
        response = client.get("/config/organizations/other-org/thresholds")
        assert response.status_code == 404
        body = response.json()
        assert body.get("detail") == "endpoint_disabled_in_ap_v1_profile"

    def test_org_config_same_org_surface_disabled_in_strict_profile(self):
        response = client.get("/config/organizations/default/thresholds")
        assert response.status_code == 404
        body = response.json()
        assert body.get("detail") == "endpoint_disabled_in_ap_v1_profile"


class TestSettingsEndpoints:
    """Test organization settings endpoints."""
    
    def test_get_settings(self):
        """Legacy /settings surface is disabled in strict AP-v1 profile."""
        response = client.get("/settings/default")
        assert response.status_code == 404
        assert response.json().get("detail") == "endpoint_disabled_in_ap_v1_profile"
    
    def test_update_approval_thresholds(self):
        """Legacy /settings mutations are disabled in strict AP-v1 profile."""
        response = client.put("/settings/default/approval-thresholds", json={
            "auto_approve_limit": 500,
            "manager_approval_limit": 5000,
            "executive_approval_limit": 25000,
        })
        assert response.status_code == 404
        assert response.json().get("detail") == "endpoint_disabled_in_ap_v1_profile"


class TestAgentIntentEndpoints:
    @staticmethod
    def _fake_user():
        return TokenData(
            user_id="agent-user-1",
            email="agent@example.com",
            organization_id="default",
            role="user",
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    @staticmethod
    def _fake_admin():
        return TokenData(
            user_id="agent-admin-1",
            email="agent-admin@example.com",
            organization_id="default",
            role="admin",
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    @staticmethod
    def _fake_operator():
        return TokenData(
            user_id="agent-operator-1",
            email="agent-operator@example.com",
            organization_id="default",
            role="operator",
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    def test_preview_intent_endpoint_calls_runtime(self):
        app.dependency_overrides[agent_intents_module.get_current_user] = self._fake_user
        preview_response = {
            "intent": "route_low_risk_for_approval",
            "mode": "preview",
            "status": "eligible",
            "ap_item_id": "ap-item-1",
            "email_id": "gmail-thread-1",
            "policy_precheck": {"eligible": True, "reason_codes": []},
        }
        mock_runtime = MagicMock()
        mock_runtime.preview_intent = MagicMock(return_value=preview_response)
        try:
            with patch.object(agent_intents_module, "_runtime_for_request", return_value=mock_runtime):
                response = client.post(
                    "/api/agent/intents/preview",
                    json={
                        "intent": "route_low_risk_for_approval",
                        "input": {"email_id": "gmail-thread-1"},
                        "organization_id": "default",
                    },
                )
        finally:
            app.dependency_overrides.pop(agent_intents_module.get_current_user, None)

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "eligible"
        assert payload["intent"] == "route_low_risk_for_approval"
        mock_runtime.preview_intent.assert_called_once()

    def test_preview_intent_endpoint_blocks_cross_org_request(self):
        app.dependency_overrides[agent_intents_module.get_current_user] = self._fake_user
        try:
            response = client.post(
                "/api/agent/intents/preview",
                json={
                    "intent": "read_ap_workflow_health",
                    "input": {"limit": 10},
                    "organization_id": "other-org",
                },
            )
        finally:
            app.dependency_overrides.pop(agent_intents_module.get_current_user, None)

        assert response.status_code == 403
        assert response.json().get("detail") == "org_mismatch"

    def test_execute_intent_endpoint_calls_runtime(self):
        app.dependency_overrides[agent_intents_module.get_current_user] = self._fake_operator
        execute_response = {
            "intent": "route_low_risk_for_approval",
            "status": "pending_approval",
            "ap_item_id": "ap-item-1",
            "email_id": "gmail-thread-1",
            "policy_precheck": {"eligible": True, "reason_codes": []},
            "audit_event_id": "audit-1",
        }
        mock_runtime = MagicMock()
        mock_runtime.execute_intent = AsyncMock(return_value=execute_response)
        try:
            with patch.object(agent_intents_module, "_runtime_for_request", return_value=mock_runtime):
                response = client.post(
                    "/api/agent/intents/execute",
                    json={
                        "intent": "route_low_risk_for_approval",
                        "input": {"email_id": "gmail-thread-1"},
                        "idempotency_key": "idem-agent-1",
                        "organization_id": "default",
                    },
                )
        finally:
            app.dependency_overrides.pop(agent_intents_module.get_current_user, None)

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "pending_approval"
        assert payload["audit_event_id"] == "audit-1"
        mock_runtime.execute_intent.assert_awaited_once()

    def test_execute_intent_endpoint_blocks_cross_org_request(self):
        app.dependency_overrides[agent_intents_module.get_current_user] = self._fake_operator
        try:
            response = client.post(
                "/api/agent/intents/execute",
                json={
                    "intent": "route_low_risk_for_approval",
                    "input": {"email_id": "gmail-thread-1"},
                    "idempotency_key": "idem-agent-org-block",
                    "organization_id": "other-org",
                },
            )
        finally:
            app.dependency_overrides.pop(agent_intents_module.get_current_user, None)

        assert response.status_code == 403
        assert response.json().get("detail") == "org_mismatch"

    def test_execute_intent_endpoint_allows_admin_cross_org_request(self):
        app.dependency_overrides[agent_intents_module.get_current_user] = self._fake_admin
        execute_response = {
            "intent": "route_low_risk_for_approval",
            "status": "pending_approval",
            "ap_item_id": "ap-item-admin",
            "email_id": "gmail-thread-admin",
        }
        mock_runtime = MagicMock()
        mock_runtime.execute_intent = AsyncMock(return_value=execute_response)
        try:
            with patch.object(agent_intents_module, "_runtime_for_request", return_value=mock_runtime):
                response = client.post(
                    "/api/agent/intents/execute",
                    json={
                        "intent": "route_low_risk_for_approval",
                        "input": {"email_id": "gmail-thread-admin"},
                        "organization_id": "other-org",
                    },
                )
        finally:
            app.dependency_overrides.pop(agent_intents_module.get_current_user, None)

        assert response.status_code == 200
        assert response.json().get("status") == "pending_approval"
        mock_runtime.execute_intent.assert_awaited_once()

    def test_execute_intent_endpoint_supports_prepare_vendor_followups(self):
        app.dependency_overrides[agent_intents_module.get_current_user] = self._fake_operator
        execute_response = {
            "intent": "prepare_vendor_followups",
            "status": "prepared",
            "ap_item_id": "ap-item-2",
            "email_id": "gmail-thread-2",
            "draft_id": "draft-2",
            "audit_event_id": "audit-2",
        }
        mock_runtime = MagicMock()
        mock_runtime.execute_intent = AsyncMock(return_value=execute_response)
        try:
            with patch.object(agent_intents_module, "_runtime_for_request", return_value=mock_runtime):
                response = client.post(
                    "/api/agent/intents/execute",
                    json={
                        "intent": "prepare_vendor_followups",
                        "input": {"email_id": "gmail-thread-2", "force": False},
                        "idempotency_key": "idem-agent-2",
                        "organization_id": "default",
                    },
                )
        finally:
            app.dependency_overrides.pop(agent_intents_module.get_current_user, None)

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "prepared"
        assert payload["draft_id"] == "draft-2"
        mock_runtime.execute_intent.assert_awaited_once()

    def test_preview_intent_endpoint_supports_read_ap_workflow_health(self):
        app.dependency_overrides[agent_intents_module.get_current_user] = self._fake_user

        class _FakeRuntimeDB:
            def list_ap_items(self, organization_id, state=None, limit=200, prioritized=False):
                _ = state, prioritized
                if str(organization_id or "") != "default":
                    return []
                return [
                    {"id": "ap-1", "organization_id": "default", "state": "needs_info"},
                    {"id": "ap-2", "organization_id": "default", "state": "failed_post"},
                    {"id": "ap-3", "organization_id": "default", "state": "validated"},
                ][: max(1, int(limit or 200))]

        try:
            with patch.object(agent_intents_module, "get_db", return_value=_FakeRuntimeDB()):
                response = client.post(
                    "/api/agent/intents/preview",
                    json={
                        "intent": "read_ap_workflow_health",
                        "input": {"limit": 100},
                        "organization_id": "default",
                    },
                )
        finally:
            app.dependency_overrides.pop(agent_intents_module.get_current_user, None)

        assert response.status_code == 200
        payload = response.json()
        assert payload["intent"] == "read_ap_workflow_health"
        assert payload["status"] == "ready"
        assert payload["summary"]["total_items"] == 3
        assert payload["policy_precheck"]["read_only"] is True

    def test_preview_intent_endpoint_supports_read_vendor_compliance_health(self):
        app.dependency_overrides[agent_intents_module.get_current_user] = self._fake_user

        class _FakeRuntimeDB:
            use_postgres = False

            def _prepare_sql(self, sql):
                return sql

            def connect(self):
                import sqlite3
                from contextlib import contextmanager

                @contextmanager
                def _conn():
                    conn = sqlite3.connect(":memory:")
                    conn.row_factory = sqlite3.Row
                    cur = conn.cursor()
                    cur.execute(
                        """
                        CREATE TABLE vendor_profiles (
                            vendor_name TEXT,
                            organization_id TEXT,
                            requires_po INTEGER,
                            contract_amount REAL,
                            payment_terms TEXT,
                            bank_details_changed_at TEXT,
                            approval_override_rate REAL,
                            anomaly_flags TEXT,
                            invoice_count INTEGER
                        )
                        """
                    )
                    cur.execute(
                        """
                        INSERT INTO vendor_profiles
                        (vendor_name, organization_id, requires_po, contract_amount, payment_terms,
                         bank_details_changed_at, approval_override_rate, anomaly_flags, invoice_count)
                        VALUES
                        ('Acme Supplies', 'default', 1, NULL, 'Net 30', NULL, 0.35, '["po_missing"]', 12)
                        """
                    )
                    conn.commit()
                    try:
                        yield conn
                    finally:
                        conn.close()

                return _conn()

        try:
            with patch.object(agent_intents_module, "get_db", return_value=_FakeRuntimeDB()):
                response = client.post(
                    "/api/agent/intents/preview",
                    json={
                        "intent": "read_vendor_compliance_health",
                        "input": {"limit": 100},
                        "organization_id": "default",
                    },
                )
        finally:
            app.dependency_overrides.pop(agent_intents_module.get_current_user, None)

        assert response.status_code == 200
        payload = response.json()
        assert payload["intent"] == "read_vendor_compliance_health"
        assert payload["status"] == "ready"
        assert payload["summary"]["total_vendors"] == 1
        assert payload["summary"]["high_override_vendors_count"] == 1

    def test_list_skills_endpoint_returns_runtime_skill_registry(self):
        app.dependency_overrides[agent_intents_module.get_current_user] = self._fake_user
        try:
            with patch.object(agent_intents_module, "get_db", return_value=MagicMock()):
                response = client.get("/api/agent/intents/skills")
        finally:
            app.dependency_overrides.pop(agent_intents_module.get_current_user, None)

        assert response.status_code == 200
        payload = response.json()
        assert payload["organization_id"] == "default"
        assert isinstance(payload.get("skills"), list)
        assert "route_low_risk_for_approval" in payload.get("supported_intents", [])
        ap_skill = next((row for row in payload["skills"] if row.get("skill_id") == "ap_v1"), None)
        assert ap_skill is not None
        assert isinstance(ap_skill.get("manifest"), dict)
        assert "readiness" in ap_skill

    def test_skill_readiness_endpoint_returns_runtime_gate_report(self):
        app.dependency_overrides[agent_intents_module.get_current_user] = self._fake_user
        readiness_payload = {
            "organization_id": "default",
            "skill_id": "ap_v1",
            "status": "blocked",
            "gates": [
                {
                    "gate": "legal_transition_correctness",
                    "status": "pass",
                    "target": 0.99,
                    "actual": 1.0,
                }
            ],
            "blocked_reasons": [],
        }
        mock_runtime = MagicMock()
        mock_runtime.skill_readiness = MagicMock(return_value=readiness_payload)
        try:
            with patch.object(agent_intents_module, "_runtime_for_request", return_value=mock_runtime):
                response = client.get(
                    "/api/agent/intents/skills/ap_v1/readiness?window_hours=168&organization_id=default"
                )
        finally:
            app.dependency_overrides.pop(agent_intents_module.get_current_user, None)

        assert response.status_code == 200
        payload = response.json()
        assert payload["skill_id"] == "ap_v1"
        assert isinstance(payload.get("gates"), list)
        mock_runtime.skill_readiness.assert_called_once()

    def test_preview_request_endpoint_uses_canonical_skill_request_contract(self):
        app.dependency_overrides[agent_intents_module.get_current_user] = self._fake_user
        preview_response = {
            "status": "eligible",
            "intent": "route_low_risk_for_approval",
            "skill_id": "ap_v1",
            "recommended_next_action": "execute_intent",
            "legal_actions": ["execute_intent"],
            "blockers": [],
            "confidence": 0.95,
            "evidence_refs": ["gmail-thread-1"],
        }
        mock_runtime = MagicMock()
        mock_runtime.organization_id = "default"
        mock_runtime.preview_skill_request = MagicMock(return_value=preview_response)
        try:
            with patch.object(agent_intents_module, "_runtime_for_request", return_value=mock_runtime):
                response = client.post(
                    "/api/agent/intents/preview-request",
                    json={
                        "organization_id": "default",
                        "request": {
                            "org_id": "default",
                            "skill_id": "ap_v1",
                            "task_type": "route_low_risk_for_approval",
                            "entity_id": "gmail-thread-1",
                            "correlation_id": "corr-1",
                            "payload": {"email_id": "gmail-thread-1"},
                        },
                    },
                )
        finally:
            app.dependency_overrides.pop(agent_intents_module.get_current_user, None)

        assert response.status_code == 200
        payload = response.json()
        assert payload["skill_id"] == "ap_v1"
        assert payload["recommended_next_action"] == "execute_intent"
        mock_runtime.preview_skill_request.assert_called_once()

    def test_execute_request_endpoint_uses_canonical_action_execution_contract(self):
        app.dependency_overrides[agent_intents_module.get_current_user] = self._fake_operator
        execute_response = {
            "status": "pending_approval",
            "intent": "route_low_risk_for_approval",
            "skill_id": "ap_v1",
            "recommended_next_action": "route_low_risk_for_approval",
            "legal_actions": ["route_low_risk_for_approval"],
            "blockers": [],
            "confidence": 0.95,
            "evidence_refs": ["gmail-thread-1"],
            "action_execution": {
                "entity_id": "gmail-thread-1",
                "action": "route_low_risk_for_approval",
                "preview": False,
                "idempotency_key": "idem-contract-1",
            },
        }
        mock_runtime = MagicMock()
        mock_runtime.organization_id = "default"
        mock_runtime.execute_skill_request = AsyncMock(return_value=execute_response)
        try:
            with patch.object(agent_intents_module, "_runtime_for_request", return_value=mock_runtime):
                response = client.post(
                    "/api/agent/intents/execute-request",
                    json={
                        "organization_id": "default",
                        "request": {
                            "org_id": "default",
                            "skill_id": "ap_v1",
                            "task_type": "route_low_risk_for_approval",
                            "entity_id": "gmail-thread-1",
                            "payload": {"email_id": "gmail-thread-1"},
                        },
                        "action": {
                            "entity_id": "gmail-thread-1",
                            "action": "route_low_risk_for_approval",
                            "preview": False,
                            "idempotency_key": "idem-contract-1",
                        },
                    },
                )
        finally:
            app.dependency_overrides.pop(agent_intents_module.get_current_user, None)

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "pending_approval"
        assert payload["action_execution"]["idempotency_key"] == "idem-contract-1"
        mock_runtime.execute_skill_request.assert_awaited_once()


class TestOnboardingEndpoints:
    """Test onboarding flow endpoints."""
    
    def test_onboarding_status(self):
        """Test getting onboarding status."""
        response = client.get("/onboarding/default/status")
        assert response.status_code in [200, 404]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
