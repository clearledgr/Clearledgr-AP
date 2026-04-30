"""§15 onboarding gates + structured ERP errors.

Covers:
  - required_actions includes configure_ap_policy when the policy
    row is missing or any of the three thesis-required keys is
    unset
  - required_actions omits configure_ap_policy when all three keys
    are populated
  - _classify_erp_connect_error produces structured payloads per
    thesis: code, missing_permission, remediation_link, message,
    detail — across the exception categories the ERP SDKs raise
"""
from __future__ import annotations


from clearledgr.api.erp_connections import _classify_erp_connect_error
from clearledgr.api.workspace_shell import _is_ap_policy_configured


class TestAPPolicyGate:
    def test_missing_policy_is_not_configured(self, tmp_path, monkeypatch):
        import clearledgr.core.database as db_module
        db = db_module.get_db()
        db.initialize()

        # Org exists but no AP policy row yet.
        assert _is_ap_policy_configured("test-org") is False

    def test_partial_policy_is_not_configured(self, tmp_path, monkeypatch):
        import clearledgr.core.database as db_module
        db = db_module.get_db()
        db.initialize()

        # Policy row present but missing approval_routing — should
        # still count as unconfigured.
        db.upsert_ap_policy_version(
            organization_id="test-org",
            policy_name="ap_business_v1",
            config={
                "auto_approve_threshold": 1000,
                "match_tolerance": 0.02,
                # approval_routing missing
            },
            updated_by="admin",
        )
        assert _is_ap_policy_configured("test-org") is False

    def test_complete_policy_is_configured(self, tmp_path, monkeypatch):
        import clearledgr.core.database as db_module
        db = db_module.get_db()
        db.initialize()

        db.upsert_ap_policy_version(
            organization_id="test-org",
            policy_name="ap_business_v1",
            config={
                "auto_approve_threshold": 1000,
                "match_tolerance": 0.02,
                "approval_routing": {"default": "sarah@acme.com"},
            },
            updated_by="admin",
        )
        assert _is_ap_policy_configured("test-org") is True

    def test_zero_threshold_is_a_valid_value(self, tmp_path, monkeypatch):
        # auto_approve_threshold=0 means "never auto-approve" — a
        # legitimate setting, not unconfigured.
        import clearledgr.core.database as db_module
        db = db_module.get_db()
        db.initialize()

        db.upsert_ap_policy_version(
            organization_id="test-org",
            policy_name="ap_business_v1",
            config={
                "auto_approve_threshold": 0,
                "match_tolerance": 0,
                "approval_routing": {"default": "cfo@acme.com"},
            },
            updated_by="admin",
        )
        assert _is_ap_policy_configured("test-org") is True


class TestERPErrorClassification:
    def test_unauthorized_401_maps_to_erp_unauthorized(self):
        result = _classify_erp_connect_error("NetSuite", Exception("401 Unauthorized"))
        assert result["code"] == "erp_unauthorized"
        assert "AP read + write" in result["missing_permission"]
        assert result["remediation_link"] is not None
        assert "NetSuite" in result["message"]

    def test_forbidden_403_maps_to_missing_permission(self):
        result = _classify_erp_connect_error("QuickBooks", Exception("403 Forbidden"))
        assert result["code"] == "erp_missing_permission"
        assert "vendor master" in result["missing_permission"].lower() or "vendor" in result["missing_permission"].lower()
        assert result["remediation_link"] == "https://app.qbo.intuit.com/app/apps/myapps"

    def test_missing_scope_detected(self):
        result = _classify_erp_connect_error("Xero", Exception("invalid_scope: accounting.transactions"))
        assert result["code"] == "erp_missing_scope"
        assert "scope" in result["missing_permission"].lower()

    def test_account_not_found_404(self):
        result = _classify_erp_connect_error("NetSuite", Exception("Account not found (404)"))
        assert result["code"] == "erp_account_not_found"
        assert "account" in result["message"].lower()

    def test_transient_timeout(self):
        result = _classify_erp_connect_error("Xero", Exception("Request timed out after 30s"))
        assert result["code"] == "erp_transient"
        assert result["missing_permission"] is None
        assert result["remediation_link"] is None

    def test_unknown_error_gets_fallback_code_not_raw_exception(self):
        result = _classify_erp_connect_error("SAP", Exception("Some unexpected SDK error"))
        assert result["code"] == "erp_connection_failed"
        # The fallback still carries a structured message, not just
        # the raw exception. Thesis rule: "Generic 'connection
        # failed' errors are not acceptable" — the payload must
        # name the remediation path even when the category is
        # unknown.
        assert "admin" in result["message"].lower()
        # Raw detail preserved for logs, but not surfaced as the
        # entire user-facing message.
        assert result["detail"] == "Some unexpected SDK error"
        assert result["remediation_link"] is not None  # SAP link

    def test_all_erp_types_have_remediation_links(self):
        # Every supported ERP must have a remediation link mapped so
        # the error copy can always show "where to fix it".
        for erp in ("quickbooks", "xero", "netsuite", "sap"):
            result = _classify_erp_connect_error(erp, Exception("401"))
            assert result["remediation_link"], f"{erp} missing remediation_link"
