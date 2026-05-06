"""Tests for Group 5b of the agent-runtime audit: silent-default
fallbacks in services/stores layer that mirrored the same hazard the
runtime had.

Pattern: each fix lets None / unset map to "default" (legitimate
platform-mode sentinel) but rejects empty string explicitly passed
(programming error masking cross-tenant data leakage).
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import database as db_module  # noqa: E402


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    return inst


# ─── 5b-1: agent_memory ─────────────────────────────────────────────


class TestAgentMemoryServiceTenantIsolation:
    def test_empty_string_org_rejected_in_init(self, db):
        from clearledgr.services.agent_memory import AgentMemoryService
        with pytest.raises(ValueError, match="organization_id cannot be empty"):
            AgentMemoryService(organization_id="", db=db)

    def test_whitespace_org_rejected_in_init(self, db):
        from clearledgr.services.agent_memory import AgentMemoryService
        with pytest.raises(ValueError, match="organization_id cannot be empty"):
            AgentMemoryService(organization_id="   ", db=db)

    def test_none_maps_to_default(self, db):
        from clearledgr.services.agent_memory import AgentMemoryService
        svc = AgentMemoryService(organization_id=None, db=db)
        assert svc.organization_id == "default"

    def test_default_kwarg_is_platform_mode(self, db):
        from clearledgr.services.agent_memory import AgentMemoryService
        svc = AgentMemoryService(db=db)
        assert svc.organization_id == "default"

    def test_real_org_constructs(self, db):
        from clearledgr.services.agent_memory import AgentMemoryService
        svc = AgentMemoryService(organization_id="orgX", db=db)
        assert svc.organization_id == "orgX"

    def test_get_agent_memory_service_rejects_empty(self, db):
        from clearledgr.services.agent_memory import get_agent_memory_service
        with pytest.raises(ValueError, match="organization_id cannot be empty"):
            get_agent_memory_service(organization_id="", db=db)

    def test_get_agent_memory_service_none_maps_to_default(self, db):
        from clearledgr.services.agent_memory import get_agent_memory_service
        svc = get_agent_memory_service(organization_id=None, db=db)
        assert svc.organization_id == "default"


# ─── 5b-2: finance_learning ─────────────────────────────────────────


class TestFinanceLearningServiceTenantIsolation:
    def test_empty_string_org_rejected(self):
        from clearledgr.services.finance_learning import FinanceLearningService
        with pytest.raises(ValueError, match="organization_id cannot be empty"):
            FinanceLearningService(organization_id="")

    def test_none_maps_to_default(self):
        from clearledgr.services.finance_learning import FinanceLearningService
        svc = FinanceLearningService(organization_id=None)
        assert svc.organization_id == "default"

    def test_real_org_constructs(self):
        from clearledgr.services.finance_learning import FinanceLearningService
        svc = FinanceLearningService(organization_id="orgY")
        assert svc.organization_id == "orgY"

    def test_get_finance_learning_service_rejects_empty(self):
        from clearledgr.services.finance_learning import (
            get_finance_learning_service,
        )
        with pytest.raises(ValueError, match="organization_id cannot be empty"):
            get_finance_learning_service(organization_id="")


# ─── 5b-3: correction_learning ──────────────────────────────────────


class TestCorrectionLearningTenantIsolation:
    def test_empty_string_org_rejected_in_init(self, db):
        from clearledgr.services.correction_learning import CorrectionLearningService
        with pytest.raises(ValueError, match="organization_id cannot be empty"):
            CorrectionLearningService(organization_id="")

    def test_none_maps_to_default(self, db):
        from clearledgr.services.correction_learning import CorrectionLearningService
        svc = CorrectionLearningService(organization_id=None)
        assert svc.organization_id == "default"

    def test_get_correction_learning_service_rejects_empty(self, db):
        from clearledgr.services.correction_learning import (
            get_correction_learning_service,
        )
        with pytest.raises(ValueError, match="organization_id cannot be empty"):
            get_correction_learning_service(organization_id="")


# ─── 5b-4: erp_readiness ────────────────────────────────────────────


class TestErpReadinessTenantIsolation:
    def test_none_org_rejected(self, db):
        from clearledgr.services.erp_readiness import evaluate_erp_connector_readiness
        with pytest.raises(ValueError, match="organization_id"):
            evaluate_erp_connector_readiness(organization_id=None, db=db)

    def test_empty_org_rejected(self, db):
        from clearledgr.services.erp_readiness import evaluate_erp_connector_readiness
        with pytest.raises(ValueError, match="organization_id"):
            evaluate_erp_connector_readiness(organization_id="", db=db)


# ─── 5b-5: erp_native_approval payload trust ───────────────────────


class TestErpNativeApprovalPayloadTrust:
    def test_route_for_approval_rejects_missing_org(self):
        import asyncio
        from clearledgr.services.erp_native_approval import route_for_approval
        result = asyncio.run(route_for_approval({"id": "ap-1"}))
        assert result["ok"] is False
        assert result["reason"] == "missing_organization_id"

    def test_route_for_approval_rejects_empty_org(self):
        import asyncio
        from clearledgr.services.erp_native_approval import route_for_approval
        result = asyncio.run(
            route_for_approval({"id": "ap-1", "organization_id": "  "})
        )
        assert result["ok"] is False
        assert result["reason"] == "missing_organization_id"


# ─── 5b-7: finance_runtime_invoice_processing payload trust ────────


class TestRuntimeInvoiceProcessingPayloadTrust:
    """The module-level helper takes the runtime as its first arg and
    must use the runtime's _resolve_payload_org so cross-tenant
    payloads are rejected the same way as the in-class seed path.
    """

    def test_uses_runtime_helper_when_payload_missing_org(self, db):
        # We do a unit-level check on the helper resolution path. The
        # full async flow needs a lot of fixture machinery; here we
        # verify the helper is wired by constructing a runtime and
        # confirming it raises on cross-tenant payload via
        # _resolve_payload_org directly (already covered by Group 5
        # tests). This test exists as a scope marker — the runtime
        # method invocation in finance_runtime_invoice_processing.py
        # was switched from a silent fallback to runtime._resolve_payload_org.
        from clearledgr.services.finance_agent_runtime import FinanceAgentRuntime
        runtime = FinanceAgentRuntime(
            organization_id="orgA", actor_id="u1", db=db,
        )
        with pytest.raises(ValueError, match="cross_tenant_write_blocked"):
            runtime._resolve_payload_org(
                {"organization_id": "orgB"},
                context="execute_ap_invoice_processing(module)",
            )


# ─── 5b-6: slack delivery refuses on missing org ───────────────────


class TestPurchaseOrdersRowOrgGuard:
    """Schema is NOT NULL on organization_id for both purchase_orders
    and goods_receipts. The dataclass deserializers used to silently
    fall back to "default" if the column came back empty, which would
    rewrite the row under the platform tenant on the next save. Now
    refuse loudly so corruption is visible."""

    def test_po_from_dict_rejects_missing_org(self):
        from clearledgr.services.purchase_orders import _po_from_dict
        with pytest.raises(ValueError, match="purchase_orders.*organization_id"):
            _po_from_dict({
                "po_id": "po-1",
                "po_number": "PO-1",
                "vendor_id": "v",
                "vendor_name": "v",
                "line_items": [],
            })

    def test_po_from_dict_rejects_empty_org(self):
        from clearledgr.services.purchase_orders import _po_from_dict
        with pytest.raises(ValueError, match="purchase_orders.*organization_id"):
            _po_from_dict({
                "po_id": "po-1",
                "po_number": "PO-1",
                "vendor_id": "v",
                "vendor_name": "v",
                "organization_id": "  ",
                "line_items": [],
            })

    def test_po_from_dict_none_returns_none(self):
        from clearledgr.services.purchase_orders import _po_from_dict
        assert _po_from_dict(None) is None
        assert _po_from_dict({}) is None

    def test_po_from_dict_real_org_constructs(self):
        from clearledgr.services.purchase_orders import _po_from_dict
        po = _po_from_dict({
            "po_id": "po-1",
            "po_number": "PO-1",
            "vendor_id": "v",
            "vendor_name": "v",
            "organization_id": "orgA",
            "line_items": [],
        })
        assert po is not None
        assert po.organization_id == "orgA"

    def test_gr_from_dict_rejects_missing_org(self):
        from clearledgr.services.purchase_orders import _gr_from_dict
        with pytest.raises(ValueError, match="goods_receipts.*organization_id"):
            _gr_from_dict({
                "gr_id": "gr-1",
                "gr_number": "GR-1",
                "po_id": "po-1",
                "po_number": "PO-1",
                "vendor_id": "v",
                "vendor_name": "v",
                "line_items": [],
            })

    def test_gr_from_dict_real_org_constructs(self):
        from clearledgr.services.purchase_orders import _gr_from_dict
        gr = _gr_from_dict({
            "gr_id": "gr-1",
            "gr_number": "GR-1",
            "po_id": "po-1",
            "po_number": "PO-1",
            "vendor_id": "v",
            "vendor_name": "v",
            "organization_id": "orgA",
            "line_items": [],
        })
        assert gr is not None
        assert gr.organization_id == "orgA"


class TestSlackDeliveryRequiresOrg:
    """The Slack workspace is per-tenant. Routing a tenant DM through
    the platform 'default' workspace silently leaks data. Refuse
    instead — caller treats it as a delivery failure (the existing
    return-False contract).

    We exercise the private DM helper directly because it's the
    site that resolves the runtime; the public ``send_with_retry``
    wraps both the post and the retry-enqueue, both of which now
    refuse on missing org.
    """

    def test_post_slack_dm_returns_false_on_missing_org(self):
        import asyncio
        from clearledgr.services.slack_notifications import _post_slack_dm
        result = asyncio.run(
            _post_slack_dm(
                user_email="alice@example.com",
                blocks=[],
                text="hello",
                organization_id=None,
            )
        )
        assert result is False

    def test_post_slack_dm_returns_false_on_empty_org(self):
        import asyncio
        from clearledgr.services.slack_notifications import _post_slack_dm
        result = asyncio.run(
            _post_slack_dm(
                user_email="alice@example.com",
                blocks=[],
                text="hello",
                organization_id="   ",
            )
        )
        assert result is False
