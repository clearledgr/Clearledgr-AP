"""
Tests for API Endpoints

Tests the FastAPI endpoints for the Clearledgr API.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

# Import the FastAPI app
from main import app

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
    
    def test_triage_endpoint(self):
        """Test email triage endpoint."""
        response = client.post("/extension/triage", json={
            "email_id": "test-email-123",
            "subject": "Invoice #12345 from Acme Corp",
            "sender": "billing@acme.com",
            "body": "Please find attached invoice for $1,500.00",
            "organization_id": "default",
        })
        assert response.status_code == 200
        data = response.json()
        assert "classification" in data or "category" in data
    
    def test_invoice_pipeline(self):
        """Test invoice pipeline endpoint."""
        response = client.get("/extension/invoice-pipeline/default")
        assert response.status_code == 200


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
