"""Tests for CompoundSkill that merges AP + Vendor Compliance (+ optional Recon) tools.

Validates that:
- skill_name is "compound_ap_compliance"
- Tool catalogue merges AP tools + vendor_compliance_snapshot
- include_recon=True adds reconciliation tools
- No tool name conflicts
- vendor_compliance_snapshot handler delegates to VendorComplianceSkill
- System prompt includes compliance guidance
- Handler failure is graceful
"""
import asyncio
from unittest.mock import MagicMock, patch

import pytest

from clearledgr.core.skills.base import AgentTask
from clearledgr.core.skills.compound_skill import CompoundSkill, _handle_vendor_compliance_snapshot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_AP_TOOL_NAMES = {
    "enrich_with_context",
    "run_validation_gate",
    "get_ap_decision",
    "execute_routing",
    "request_vendor_info",
}


def _task():
    return AgentTask(
        task_type="compound_ap_compliance",
        organization_id="org-1",
        payload={
            "invoice": {
                "vendor_name": "Acme Corp",
                "amount": 750.00,
                "currency": "USD",
                "confidence": 0.88,
            }
        },
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_compound_skill_name():
    """CompoundSkill.skill_name must be 'compound_ap_compliance'."""
    skill = CompoundSkill("org-1")
    assert skill.skill_name == "compound_ap_compliance"


def test_merges_ap_and_compliance_tools():
    """get_tools() returns all AP tools plus vendor_compliance_snapshot."""
    skill = CompoundSkill("org-1")
    tools = skill.get_tools()
    names = {t.name for t in tools}

    assert _AP_TOOL_NAMES.issubset(names), f"Missing AP tools: {_AP_TOOL_NAMES - names}"
    assert "vendor_compliance_snapshot" in names


def test_with_recon():
    """include_recon=True adds reconciliation tools to the catalogue."""
    skill = CompoundSkill("org-1", include_recon=True)
    tools = skill.get_tools()
    names = {t.name for t in tools}

    # Should have AP tools + compliance + recon tools
    assert "vendor_compliance_snapshot" in names
    # ReconciliationSkill provides at least import_transactions
    assert "import_transactions" in names


def test_no_tool_name_conflicts():
    """All tool names in the merged catalogue must be unique."""
    skill = CompoundSkill("org-1", include_recon=True)
    tools = skill.get_tools()
    names = [t.name for t in tools]

    assert len(names) == len(set(names)), f"Duplicate tool names: {[n for n in names if names.count(n) > 1]}"


def test_vendor_compliance_snapshot_handler():
    """_handle_vendor_compliance_snapshot delegates to VendorComplianceSkill._build_health_summary."""
    mock_summary = {
        "total_vendors": 12,
        "high_override_vendors_count": 2,
        "top_high_override_vendors": [],
    }

    mock_skill_instance = MagicMock()
    mock_skill_instance._build_health_summary.return_value = mock_summary

    mock_db = MagicMock()

    with (
        patch(
            "clearledgr.services.finance_skills.vendor_compliance_skill.VendorComplianceSkill",
            return_value=mock_skill_instance,
        ),
        patch(
            "clearledgr.core.database.get_db",
            return_value=mock_db,
        ),
    ):
        result = asyncio.run(
            _handle_vendor_compliance_snapshot(
                organization_id="org-1",
                limit=50,
                override_threshold=0.25,
            )
        )

    assert result["ok"] is True
    assert result["total_vendors"] == 12
    assert result["high_override_vendors_count"] == 2
    mock_skill_instance._build_health_summary.assert_called_once()


def test_system_prompt_includes_compliance():
    """build_system_prompt mentions vendor_compliance_snapshot tool."""
    skill = CompoundSkill("org-1")

    # Patch out cross-invoice analyzer to avoid DB calls in APSkill.build_system_prompt
    with patch(
        "clearledgr.services.cross_invoice_analysis.get_cross_invoice_analyzer",
        side_effect=ImportError("not needed"),
    ):
        prompt = skill.build_system_prompt(_task())

    assert "vendor_compliance_snapshot" in prompt
    assert "compliance" in prompt.lower()


def test_compliance_handler_failure_graceful():
    """If VendorComplianceSkill raises, handler returns ok=False with error."""
    with patch(
        "clearledgr.services.finance_skills.vendor_compliance_skill.VendorComplianceSkill",
        side_effect=RuntimeError("vendor module broken"),
    ):
        result = asyncio.run(
            _handle_vendor_compliance_snapshot(
                organization_id="org-1",
            )
        )

    assert result["ok"] is False
    assert "error" in result
    assert "vendor module broken" in result["error"]
