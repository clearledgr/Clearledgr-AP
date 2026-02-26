"""
Tests for API Endpoints

Tests the FastAPI endpoints for the Clearledgr API.
"""

from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock

# Import the FastAPI app
from main import app
from clearledgr.api import gmail_extension as gmail_extension_module
from clearledgr.core.auth import TokenData

client = TestClient(app)


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


class TestAnalyticsEndpoints:
    """Test analytics dashboard endpoints."""
    
    def test_dashboard_metrics(self):
        """Test fetching dashboard metrics."""
        response = client.get("/analytics/dashboard/default")
        assert response.status_code == 200
        data = response.json()
        
        # Should have expected fields
        assert "pending_review" in data or "needs_review" in data
    
    def test_spend_by_vendor(self):
        """Test spend by vendor report."""
        response = client.get("/analytics/spend-by-vendor/default")
        assert response.status_code == 200
    
    def test_processing_metrics(self):
        """Test processing metrics endpoint."""
        response = client.get("/analytics/processing-metrics/default")
        assert response.status_code == 200


class TestAPWorkflowEndpoints:
    """Test AP workflow endpoints."""
    
    def test_get_pending_payments(self):
        """Test getting pending payments."""
        response = client.get("/ap/payments/pending", params={"organization_id": "default"})
        assert response.status_code == 200
        assert isinstance(response.json(), list)
    
    def test_get_payment_summary(self):
        """Test payment summary."""
        response = client.get("/ap/payments/summary", params={"organization_id": "default"})
        assert response.status_code == 200
        data = response.json()
        assert "pending" in data or "scheduled" in data
    
    def test_create_payment(self):
        """Test creating a payment."""
        response = client.post("/ap/payments/create", json={
            "invoice_id": "TEST-INV-001",
            "vendor_id": "TEST-V001",
            "vendor_name": "Test Vendor",
            "amount": 100.00,
            "method": "ach",
            "organization_id": "default",
        })
        assert response.status_code == 200
        data = response.json()
        assert "payment_id" in data
    
    def test_get_gl_accounts(self):
        """Test getting GL accounts."""
        response = client.get("/ap/gl/accounts", params={"organization_id": "default"})
        assert response.status_code == 200
        assert isinstance(response.json(), list)
    
    def test_gl_suggestion(self):
        """Test GL code suggestion."""
        response = client.get("/ap/gl/suggest", params={
            "vendor": "AWS",
            "organization_id": "default",
        })
        assert response.status_code == 200
    
    def test_create_gl_correction(self):
        """Test recording a GL correction."""
        response = client.post("/ap/gl/correct", json={
            "invoice_id": "TEST-GL-001",
            "vendor": "Test Vendor",
            "original_gl": "5000",
            "corrected_gl": "5200",
            "reason": "Software subscription",
            "organization_id": "default",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["corrected_gl"] == "5200"
    
    def test_get_recurring_rules(self):
        """Test getting recurring rules."""
        response = client.get("/ap/recurring/rules", params={"organization_id": "default"})
        assert response.status_code == 200
        assert isinstance(response.json(), list)
    
    def test_create_recurring_rule(self):
        """Test creating a recurring rule."""
        response = client.post("/ap/recurring/rules", json={
            "vendor": "Test SaaS",
            "expected_frequency": "monthly",
            "expected_amount": 99.00,
            "amount_tolerance_pct": 5.0,
            "action": "auto_approve",
            "organization_id": "default",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["vendor"] == "Test SaaS"
    
    def test_get_upcoming_invoices(self):
        """Test getting upcoming expected invoices."""
        response = client.get("/ap/recurring/upcoming", params={
            "days": 30,
            "organization_id": "default",
        })
        assert response.status_code == 200


class TestGmailWebhooks:
    """Test Gmail Pub/Sub webhook endpoints."""
    
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
    
    def test_gmail_status_not_connected(self):
        """Test Gmail status for non-connected user."""
        response = client.get("/gmail/status/nonexistent-user")
        assert response.status_code == 200
        data = response.json()
        assert data["connected"] is False


class TestERPEndpoints:
    """Test ERP integration endpoints."""
    
    def test_erp_status(self):
        """Test ERP connection status."""
        response = client.get("/erp/status/default")
        assert response.status_code == 200
    
    def test_oauth_status(self):
        """Test OAuth connection status."""
        response = client.get("/oauth/status")
        assert response.status_code == 200


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
    
    def test_invoice_pipeline(self):
        """Test invoice pipeline endpoint."""
        response = client.get("/extension/invoice-pipeline/default")
        assert response.status_code == 200

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
            row = {"id": f"audit-{len(self.audit_rows) + 1}", **dict(payload or {})}
            self.audit_rows.append(row)
            return row

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


class TestSettingsEndpoints:
    """Test organization settings endpoints."""
    
    def test_get_settings(self):
        """Test getting organization settings."""
        response = client.get("/settings/default")
        assert response.status_code == 200
    
    def test_update_approval_thresholds(self):
        """Test updating approval thresholds."""
        response = client.put("/settings/default/approval-thresholds", json={
            "auto_approve_limit": 500,
            "manager_approval_limit": 5000,
            "executive_approval_limit": 25000,
        })
        assert response.status_code in [200, 404]  # 404 if org doesn't exist


class TestLearningEndpoints:
    """Test learning/feedback loop endpoints."""
    
    def test_get_statistics(self):
        """Test getting learning statistics."""
        response = client.get("/learning/statistics/default")
        assert response.status_code == 200
    
    def test_record_feedback(self):
        """Test recording user feedback."""
        response = client.post("/learning/record", json={
            "entity_type": "invoice",
            "entity_id": "test-inv-001",
            "feedback_type": "gl_correction",
            "original_value": "5000",
            "corrected_value": "5200",
            "organization_id": "default",
        })
        assert response.status_code == 200


class TestOnboardingEndpoints:
    """Test onboarding flow endpoints."""
    
    def test_onboarding_status(self):
        """Test getting onboarding status."""
        response = client.get("/onboarding/default/status")
        assert response.status_code in [200, 404]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
