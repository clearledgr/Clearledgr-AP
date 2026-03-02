"""
Tests for API Endpoints

Tests the FastAPI endpoints for the Clearledgr API.
"""

from datetime import datetime, timedelta, timezone
import asyncio
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock

# Import the FastAPI app
import main as main_module
from main import app
from clearledgr.api import gmail_extension as gmail_extension_module
from clearledgr.api import agent_intents as agent_intents_module
from clearledgr.api import gmail_webhooks as gmail_webhooks_module
from clearledgr.api import admin_console as admin_console_module
from clearledgr.api import ap_items as ap_items_module
from clearledgr.api import auth as auth_module
from clearledgr.core.auth import TokenData

client = TestClient(app)


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
    
    def test_register_validation(self):
        """Test registration validates password strength."""
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
        response = client.post("/auth/register", json={
            "email": "newuser@example.com",
            "password": "StrongPass123!",
            "name": "New User",
            "organization_id": "test-org",
        })
        # May fail if user exists, but should be 200 or 400
        assert response.status_code in [200, 400]
    
    def test_google_identity_auth(self):
        """Test Google Identity authentication."""
        response = client.post("/auth/google-identity", json={
            "email": "user@company.com",
            "google_id": "google-123456",
        })
        
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "user_id" in data
        assert "organization_id" in data

    def test_google_callback_uses_one_time_auth_code_exchange(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-google-client")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-google-secret")
        monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
        monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-token-key")

        state = auth_module._sign_google_state(
            {
                "organization_id": "default",
                "redirect_path": "/console",
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
        monkeypatch.setattr(auth_module, "_google_auth_code_store", {})

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
            role="user",
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    def test_retry_post_uses_resume_workflow_success(self):
        fake_db = MagicMock()
        fake_db.get_ap_item.return_value = {
            "id": "ap-1",
            "organization_id": "default",
            "state": "failed_post",
        }

        app.dependency_overrides[ap_items_module.get_current_user] = self._fake_user
        try:
            with patch.object(ap_items_module, "get_db", return_value=fake_db):
                with patch(
                    "clearledgr.services.invoice_workflow.InvoiceWorkflowService.resume_workflow",
                    AsyncMock(return_value={"status": "recovered", "erp_reference": "ERP-RET-1"}),
                ) as resume_mock:
                    response = client.post("/api/ap/items/ap-1/retry-post?organization_id=default")
        finally:
            app.dependency_overrides.pop(ap_items_module.get_current_user, None)

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "posted"
        assert payload["erp_reference"] == "ERP-RET-1"
        resume_mock.assert_awaited_once_with("ap-1")

    def test_retry_post_returns_502_when_resume_still_failing(self):
        fake_db = MagicMock()
        fake_db.get_ap_item.return_value = {
            "id": "ap-2",
            "organization_id": "default",
            "state": "failed_post",
        }

        app.dependency_overrides[ap_items_module.get_current_user] = self._fake_user
        try:
            with patch.object(ap_items_module, "get_db", return_value=fake_db):
                with patch(
                    "clearledgr.services.invoice_workflow.InvoiceWorkflowService.resume_workflow",
                    AsyncMock(return_value={"status": "still_failing", "reason": "connector_timeout"}),
                ):
                    response = client.post("/api/ap/items/ap-2/retry-post?organization_id=default")
        finally:
            app.dependency_overrides.pop(ap_items_module.get_current_user, None)

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
        state = admin_console_module._sign_state(
            {
                "organization_id": "default",
                "user_id": "gmail-user-1",
                "redirect_url": "/console?org=default&page=integrations",
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
        assert "/console" in location
        assert "org=default" in location
        assert "page=integrations" in location
        assert "success=true" in location

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
        app.dependency_overrides[admin_console_module.get_current_user] = lambda: self._fake_user("viewer")
        try:
            response = client.post(
                "/api/admin/integrations/gmail/connect/start",
                json={"organization_id": "default", "redirect_path": "/console?page=integrations"},
            )
        finally:
            app.dependency_overrides.pop(admin_console_module.get_current_user, None)
        assert response.status_code == 403

    def test_start_gmail_connect_returns_google_auth_url(self, monkeypatch):
        monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
        captured = {}

        def _fake_auth_url(*, state):
            captured["state"] = state
            return f"https://accounts.google.com/o/oauth2/v2/auth?state={state}"

        app.dependency_overrides[admin_console_module.get_current_user] = lambda: self._fake_user("admin")
        try:
            with patch.object(admin_console_module, "generate_auth_url", side_effect=_fake_auth_url):
                response = client.post(
                    "/api/admin/integrations/gmail/connect/start",
                    json={"organization_id": "default", "redirect_path": "/console?org=default&page=integrations"},
                )
        finally:
            app.dependency_overrides.pop(admin_console_module.get_current_user, None)

        assert response.status_code == 200
        payload = response.json()
        assert payload["organization_id"] == "default"
        assert payload["redirect_path"] == "/console?org=default&page=integrations"
        assert payload["auth_url"].startswith("https://accounts.google.com/o/oauth2/v2/auth")
        signed_state = captured.get("state")
        assert isinstance(signed_state, str) and "." in signed_state
        decoded = gmail_webhooks_module._unsign_oauth_state(signed_state)
        assert decoded.get("user_id") == "admin-user-1"
        assert decoded.get("organization_id") == "default"
        assert decoded.get("redirect_url") == "/console?org=default&page=integrations"


class TestERPEndpoints:
    """Test canonical ERP integration surfaces."""
    
    def test_admin_integrations_includes_erp_status(self):
        app.dependency_overrides[admin_console_module.get_current_user] = lambda: TokenData(
            user_id="erp-user-1",
            email="erp-user@example.com",
            organization_id="default",
            role="owner",
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        try:
            response = client.get("/api/admin/integrations?organization_id=default")
        finally:
            app.dependency_overrides.pop(admin_console_module.get_current_user, None)
        assert response.status_code == 200
        payload = response.json()
        assert any(row.get("name") == "erp" for row in payload.get("integrations", []))

    def test_admin_integrations_blocks_cross_org_for_non_admin(self):
        app.dependency_overrides[admin_console_module.get_current_user] = lambda: TokenData(
            user_id="erp-user-2",
            email="erp-user-2@example.com",
            organization_id="default",
            role="user",
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        try:
            response = client.get("/api/admin/integrations?organization_id=other-org")
        finally:
            app.dependency_overrides.pop(admin_console_module.get_current_user, None)
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
            role="user",
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    
    def test_triage_endpoint(self):
        """Test email triage endpoint."""
        app.dependency_overrides[gmail_extension_module.get_current_user] = self._fake_user
        try:
            with patch.object(gmail_extension_module, "temporal_enabled", return_value=True):
                with patch.object(gmail_extension_module, "TemporalRuntime") as runtime_cls:
                    runtime = MagicMock()
                    runtime.start_workflow = AsyncMock(
                        return_value={
                            "email_id": "test-email-123",
                            "classification": {"type": "INVOICE", "confidence": 0.99},
                            "extraction": {"vendor": "Acme Corp", "amount": 1500.0},
                        }
                    )
                    runtime_cls.return_value = runtime
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

    def test_approve_and_post_uses_canonical_invoice_workflow(self, monkeypatch):
        captured: dict = {}

        class _FakeWorkflow:
            async def approve_invoice(self, **kwargs):
                captured.update(kwargs)
                return {"status": "approved", "invoice_id": kwargs.get("gmail_id")}

        monkeypatch.setattr(
            "clearledgr.services.invoice_workflow.get_invoice_workflow",
            lambda _org_id: _FakeWorkflow(),
        )

        app.dependency_overrides[gmail_extension_module.get_current_user] = self._fake_user
        try:
            response = client.post(
                "/extension/approve-and-post",
                json={
                    "email_id": "thread-123",
                    "extraction": {
                        "vendor": "Acme Supplies",
                        "amount": 842.19,
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
        assert captured["gmail_id"] == "thread-123"
        assert captured["source_channel"] == "gmail_extension"
        assert captured["allow_budget_override"] is True
        assert captured["allow_confidence_override"] is True
        assert captured["allow_po_exception_override"] is True

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

        class _FakeDB:
            def save_gmail_autopilot_state(self, **kwargs):
                state_calls.append(kwargs)

        monkeypatch.setattr(gmail_extension_module.httpx, "AsyncClient", _FakeAsyncClient)
        monkeypatch.setattr(gmail_extension_module.token_store, "store", _store)
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
        assert response.status_code == 403
        assert response.json().get("detail") == "extension_user_not_provisioned"

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

    def test_approval_nudge_endpoint_sends_slack_and_audits(self):
        app.dependency_overrides[gmail_extension_module.get_current_user] = self._fake_user
        fake_audit = self._FakeAuditService()
        app.dependency_overrides[gmail_extension_module.get_audit_service] = lambda: fake_audit

        fake_db = self._FakeExtensionDB()
        fake_slack_client = MagicMock()
        fake_slack_client.send_message = AsyncMock(
            return_value=MagicMock(channel="C123", thread_ts="171.100", ts="171.200")
        )
        fake_workflow = MagicMock()
        fake_workflow.slack_client = fake_slack_client
        fake_workflow.teams_client = None

        try:
            with patch.object(gmail_extension_module, "get_db", return_value=fake_db):
                with patch("clearledgr.services.invoice_workflow.get_invoice_workflow", return_value=fake_workflow):
                    response = client.post(
                        "/extension/approval-nudge",
                        json={
                            "email_id": "gmail-thread-1",
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
        assert data["slack"]["status"] == "sent"
        assert data["audit_event_id"]
        assert fake_db.audit_rows[-1]["event_type"] == "approval_nudge_sent"
        assert fake_audit.events[-1]["action"] == "approval_nudge"

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
        assert fake_audit.events[-1]["action"] == "finance_summary_share_previewed"

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
        fake_db = self._FakeExtensionDB(
            ap_item={
                "id": "ap-item-followup-1",
                "organization_id": "default",
                "thread_id": "gmail-thread-followup-1",
                "state": "needs_info",
                "vendor_name": "Northwind",
                "invoice_number": "INV-FOLLOWUP-1",
                "amount": 120.75,
                "currency": "USD",
                "sender": "billing@northwind.example",
                "subject": "Invoice follow-up",
                "user_id": "finance-user",
                "metadata": {
                    "correlation_id": "corr-followup-1",
                    "needs_info_question": "Please share the PO number.",
                },
            }
        )

        class _FakeGmailClient:
            def __init__(self, user_id):
                self.user_id = user_id

            async def ensure_authenticated(self):
                return True

            async def create_draft(self, **_kwargs):
                return "draft-followup-123"

        try:
            with patch.object(gmail_extension_module, "get_db", return_value=fake_db):
                with patch("clearledgr.services.gmail_api.GmailAPIClient", _FakeGmailClient):
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
        metadata = fake_db.ap_item["metadata"]
        assert metadata["needs_info_draft_id"] == "draft-followup-123"
        assert metadata["followup_attempt_count"] == 1
        assert metadata["followup_next_action"] == "await_vendor_response"
        assert metadata.get("followup_last_sent_at")
        assert fake_db.audit_rows[-1]["event_type"] == "vendor_followup_draft_prepared"
        assert fake_audit.events[-1]["action"] == "vendor_followup_prepared"

    def test_vendor_followup_endpoint_respects_sla_wait_window(self):
        app.dependency_overrides[gmail_extension_module.get_current_user] = self._fake_user
        fake_audit = self._FakeAuditService()
        app.dependency_overrides[gmail_extension_module.get_audit_service] = lambda: fake_audit
        now_iso = datetime.now(timezone.utc).isoformat()
        fake_db = self._FakeExtensionDB(
            ap_item={
                "id": "ap-item-followup-2",
                "organization_id": "default",
                "thread_id": "gmail-thread-followup-2",
                "state": "needs_info",
                "vendor_name": "Northwind",
                "invoice_number": "INV-FOLLOWUP-2",
                "amount": 88.00,
                "currency": "USD",
                "sender": "billing@northwind.example",
                "subject": "Invoice follow-up",
                "user_id": "finance-user",
                "metadata": {
                    "needs_info_question": "Please confirm invoice date.",
                    "followup_attempt_count": 1,
                    "followup_last_sent_at": now_iso,
                    "needs_info_draft_id": "draft-existing-1",
                },
            }
        )

        try:
            with patch.object(gmail_extension_module, "get_db", return_value=fake_db):
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
        assert payload["needs_info_draft_id"] == "draft-existing-1"

    def test_vendor_followup_endpoint_idempotency_replays_previous_response(self):
        app.dependency_overrides[gmail_extension_module.get_current_user] = self._fake_user
        fake_audit = self._FakeAuditService()
        app.dependency_overrides[gmail_extension_module.get_audit_service] = lambda: fake_audit
        fake_db = self._FakeExtensionDB(
            ap_item={
                "id": "ap-item-followup-idem",
                "organization_id": "default",
                "thread_id": "gmail-thread-followup-idem",
                "state": "needs_info",
                "vendor_name": "Northwind",
                "invoice_number": "INV-FOLLOWUP-IDEM",
                "amount": 88.0,
                "currency": "USD",
                "sender": "billing@northwind.example",
                "subject": "Invoice follow-up",
                "user_id": "finance-user",
                "metadata": {"correlation_id": "corr-followup-idem"},
            }
        )

        class _FakeGmailClient:
            def __init__(self, user_id):
                self.user_id = user_id

            async def ensure_authenticated(self):
                return True

            async def create_draft(self, **_kwargs):
                return "draft-followup-idem"

        body = {
            "email_id": "gmail-thread-followup-idem",
            "organization_id": "default",
            "idempotency_key": "idem-followup-1",
        }

        try:
            with patch.object(gmail_extension_module, "get_db", return_value=fake_db):
                with patch("clearledgr.services.gmail_api.GmailAPIClient", _FakeGmailClient):
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
        assert len(fake_db.audit_rows) == 1

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
        fake_db = self._FakeExtensionDB(
            ap_item={
                "id": "ap-item-retry-1",
                "organization_id": "default",
                "thread_id": "gmail-thread-retry-1",
                "state": "failed_post",
                "vendor_name": "Retry Co",
                "invoice_number": "INV-RETRY-1",
                "amount": 141.0,
                "currency": "USD",
                "last_error": "connector timeout",
                "metadata": {"correlation_id": "corr-retry-1"},
            }
        )
        fake_workflow = MagicMock()
        fake_workflow.evaluate_batch_retry_recoverable_failure.return_value = {
            "eligible": True,
            "reason_codes": [],
            "recoverability": {"recoverable": True, "reason": "recoverable_timeout"},
            "state": "failed_post",
        }
        fake_workflow.resume_workflow = AsyncMock(return_value={"status": "recovered", "erp_reference": "ERP-REC-1"})

        body = {
            "email_id": "gmail-thread-retry-1",
            "organization_id": "default",
            "idempotency_key": "idem-retry-1",
        }

        try:
            with patch.object(gmail_extension_module, "get_db", return_value=fake_db):
                with patch("clearledgr.services.finance_skills.ap_skill.get_invoice_workflow", return_value=fake_workflow):
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
        assert any(row.get("event_type") == "retry_recoverable_failure_completed" for row in fake_db.audit_rows)


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
        try:
            with patch.object(agent_intents_module, "get_db", return_value=MagicMock()):
                with patch.object(
                    agent_intents_module.FinanceAgentRuntime,
                    "preview_intent",
                    return_value=preview_response,
                ) as preview_mock:
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
        preview_mock.assert_called_once()

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
        app.dependency_overrides[agent_intents_module.get_current_user] = self._fake_user
        execute_response = {
            "intent": "route_low_risk_for_approval",
            "status": "pending_approval",
            "ap_item_id": "ap-item-1",
            "email_id": "gmail-thread-1",
            "policy_precheck": {"eligible": True, "reason_codes": []},
            "audit_event_id": "audit-1",
        }
        try:
            with patch.object(agent_intents_module, "get_db", return_value=MagicMock()):
                with patch.object(
                    agent_intents_module.FinanceAgentRuntime,
                    "execute_intent",
                    AsyncMock(return_value=execute_response),
                ) as exec_mock:
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
        exec_mock.assert_awaited_once()

    def test_execute_intent_endpoint_blocks_cross_org_request(self):
        app.dependency_overrides[agent_intents_module.get_current_user] = self._fake_user
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
        try:
            with patch.object(agent_intents_module, "get_db", return_value=MagicMock()):
                with patch.object(
                    agent_intents_module.FinanceAgentRuntime,
                    "execute_intent",
                    AsyncMock(return_value=execute_response),
                ) as exec_mock:
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
        exec_mock.assert_awaited_once()

    def test_execute_intent_endpoint_supports_prepare_vendor_followups(self):
        app.dependency_overrides[agent_intents_module.get_current_user] = self._fake_user
        execute_response = {
            "intent": "prepare_vendor_followups",
            "status": "prepared",
            "ap_item_id": "ap-item-2",
            "email_id": "gmail-thread-2",
            "draft_id": "draft-2",
            "audit_event_id": "audit-2",
        }
        try:
            with patch.object(agent_intents_module, "get_db", return_value=MagicMock()):
                with patch.object(
                    agent_intents_module.FinanceAgentRuntime,
                    "execute_intent",
                    AsyncMock(return_value=execute_response),
                ) as exec_mock:
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
        exec_mock.assert_awaited_once()

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
        try:
            with patch.object(agent_intents_module, "get_db", return_value=MagicMock()):
                with patch.object(
                    agent_intents_module.FinanceAgentRuntime,
                    "skill_readiness",
                    return_value=readiness_payload,
                ) as readiness_mock:
                    response = client.get(
                        "/api/agent/intents/skills/ap_v1/readiness?window_hours=168&organization_id=default"
                    )
        finally:
            app.dependency_overrides.pop(agent_intents_module.get_current_user, None)

        assert response.status_code == 200
        payload = response.json()
        assert payload["skill_id"] == "ap_v1"
        assert isinstance(payload.get("gates"), list)
        readiness_mock.assert_called_once()

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
        try:
            with patch.object(agent_intents_module, "get_db", return_value=MagicMock()):
                with patch.object(
                    agent_intents_module.FinanceAgentRuntime,
                    "preview_skill_request",
                    return_value=preview_response,
                ) as preview_mock:
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
        preview_mock.assert_called_once()

    def test_execute_request_endpoint_uses_canonical_action_execution_contract(self):
        app.dependency_overrides[agent_intents_module.get_current_user] = self._fake_user
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
        try:
            with patch.object(agent_intents_module, "get_db", return_value=MagicMock()):
                with patch.object(
                    agent_intents_module.FinanceAgentRuntime,
                    "execute_skill_request",
                    AsyncMock(return_value=execute_response),
                ) as execute_mock:
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
        execute_mock.assert_awaited_once()


class TestOnboardingEndpoints:
    """Test onboarding flow endpoints."""
    
    def test_onboarding_status(self):
        """Test getting onboarding status."""
        response = client.get("/onboarding/default/status")
        assert response.status_code in [200, 404]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
