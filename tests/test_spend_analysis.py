"""Tests for SpendAnalysisService, the workspace API endpoint, and the agent tool.

Covers:
- Service methods: analyze, top vendors, GL category, monthly trends, anomalies, summary
- API endpoint: GET /api/workspace/spend-analysis
- Agent tool registration: analyze_spending appears in APSkill.get_tools()
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from main import app
from clearledgr.api import workspace_shell as workspace_shell_module
from clearledgr.core import database as db_module
from clearledgr.core.auth import TokenData


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "spend-analysis.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_module._DB_INSTANCE = None
    db = db_module.get_db()
    db.initialize()
    return db


@pytest.fixture()
def client(db):
    def _fake_user():
        return TokenData(
            user_id="analyst-1",
            email="analyst@example.com",
            organization_id="default",
            role="owner",
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    app.dependency_overrides[workspace_shell_module.get_current_user] = _fake_user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(workspace_shell_module.get_current_user, None)


def _create_posted_item(db, item_id, vendor, amount, days_ago=5, gl_code=None):
    """Create an ap_item in posted_to_erp state, backdated by days_ago."""
    now = datetime.now(timezone.utc)
    created = (now - timedelta(days=days_ago)).isoformat()
    posted = (now - timedelta(days=max(0, days_ago - 1))).isoformat()
    item = db.create_ap_item({
        "id": item_id,
        "invoice_key": f"inv-{item_id}",
        "thread_id": f"thread-{item_id}",
        "message_id": f"msg-{item_id}",
        "subject": f"Invoice from {vendor}",
        "sender": "billing@example.com",
        "vendor_name": vendor,
        "amount": amount,
        "currency": "USD",
        "invoice_number": f"INV-{item_id}",
        "state": "posted_to_erp",
        "organization_id": "default",
    })
    # Backdate via raw SQL so it appears in the right period
    sql = db._prepare_sql(
        "UPDATE ap_items SET created_at = ?, erp_posted_at = ? WHERE id = ?"
    )
    with db.connect() as conn:
        conn.execute(sql, (created, posted, item_id))
        conn.commit()

    # Optionally set vendor GL code
    if gl_code:
        db.upsert_vendor_profile(
            "default", vendor, typical_gl_code=gl_code,
        )
    return item


# ---------------------------------------------------------------------------
# Service unit tests
# ---------------------------------------------------------------------------

class TestSpendAnalysisService:

    def test_analyze_returns_all_keys(self, db):
        from clearledgr.services.spend_analysis import SpendAnalysisService
        svc = SpendAnalysisService("default")
        result = svc.analyze(period_days=30)
        assert result["organization_id"] == "default"
        assert result["period_days"] == 30
        assert "summary" in result
        assert "top_vendors" in result
        assert "spend_by_gl_category" in result
        assert "monthly_trends" in result
        assert "budget_utilization" in result
        assert "anomalies" in result

    def test_analyze_empty_org(self, db):
        """No AP items -> empty lists and zero totals."""
        from clearledgr.services.spend_analysis import SpendAnalysisService
        svc = SpendAnalysisService("default")
        result = svc.analyze(30)
        assert result["summary"]["total_spend"] == 0.0
        assert result["summary"]["invoice_count"] == 0
        assert result["top_vendors"] == []

    def test_top_vendors_by_spend(self, db):
        _create_posted_item(db, "SA-1", "Acme Corp", 5000.0, days_ago=5)
        _create_posted_item(db, "SA-2", "Acme Corp", 3000.0, days_ago=3)
        _create_posted_item(db, "SA-3", "Globex Inc", 2000.0, days_ago=4)

        from clearledgr.services.spend_analysis import SpendAnalysisService
        svc = SpendAnalysisService("default")
        top = svc._top_vendors_by_spend(30)
        assert len(top) == 2
        assert top[0]["vendor_name"] == "Acme Corp"
        assert top[0]["total_spend"] == 8000.0
        assert top[0]["invoice_count"] == 2
        assert top[1]["vendor_name"] == "Globex Inc"

    def test_top_vendors_excludes_non_posted(self, db):
        """Items not in posted_to_erp or closed are excluded."""
        _create_posted_item(db, "SA-EXCL-1", "Posted Vendor", 1000.0)
        # Create a needs_approval item
        db.create_ap_item({
            "id": "SA-EXCL-2",
            "invoice_key": "inv-SA-EXCL-2",
            "thread_id": "thread-SA-EXCL-2",
            "message_id": "msg-SA-EXCL-2",
            "subject": "Invoice",
            "sender": "x@example.com",
            "vendor_name": "Pending Vendor",
            "amount": 9999.0,
            "state": "needs_approval",
            "organization_id": "default",
        })
        from clearledgr.services.spend_analysis import SpendAnalysisService
        svc = SpendAnalysisService("default")
        top = svc._top_vendors_by_spend(30)
        vendor_names = [v["vendor_name"] for v in top]
        assert "Posted Vendor" in vendor_names
        assert "Pending Vendor" not in vendor_names

    def test_spend_by_gl_category(self, db):
        _create_posted_item(db, "SA-GL-1", "Vendor A", 1000.0, gl_code="5000")
        _create_posted_item(db, "SA-GL-2", "Vendor B", 2000.0, gl_code="5000")
        _create_posted_item(db, "SA-GL-3", "Vendor C", 500.0, gl_code="6100")
        _create_posted_item(db, "SA-GL-4", "Vendor D", 300.0)  # no GL

        from clearledgr.services.spend_analysis import SpendAnalysisService
        svc = SpendAnalysisService("default")
        gl = svc._spend_by_gl_category(30)
        gl_map = {item["gl_code"]: item["total_spend"] for item in gl}
        assert gl_map["5000"] == 3000.0
        assert gl_map["6100"] == 500.0
        assert gl_map["unclassified"] == 300.0

    def test_monthly_trends_returns_six_months(self, db):
        from clearledgr.services.spend_analysis import SpendAnalysisService
        svc = SpendAnalysisService("default")
        trends = svc._monthly_trends(months=6)
        assert len(trends) == 6
        # Each entry has the right keys
        for t in trends:
            assert "month" in t
            assert "total_spend" in t
            assert "invoice_count" in t
            assert "mom_change_pct" in t

    def test_build_summary(self, db):
        _create_posted_item(db, "SA-SUM-1", "Vendor X", 1000.0, days_ago=5)
        _create_posted_item(db, "SA-SUM-2", "Vendor Y", 2500.0, days_ago=3)

        from clearledgr.services.spend_analysis import SpendAnalysisService
        svc = SpendAnalysisService("default")
        summary = svc._build_summary(30)
        assert summary["total_spend"] == 3500.0
        assert summary["invoice_count"] == 2
        assert summary["period_days"] == 30

    def test_detect_anomalies_new_vendor(self, db):
        """A vendor with spend only in the current period is flagged as new."""
        _create_posted_item(db, "SA-ANOM-1", "Brand New Corp", 5000.0, days_ago=3)

        from clearledgr.services.spend_analysis import SpendAnalysisService
        svc = SpendAnalysisService("default")
        anomalies = svc._detect_portfolio_anomalies(15)
        new_vendor_anomalies = [a for a in anomalies if a["type"] == "new_vendor"]
        vendors = [a["vendor"] for a in new_vendor_anomalies]
        assert "Brand New Corp" in vendors

    def test_analyze_never_raises(self, db):
        """Even with a broken DB, analyze returns a dict, not an exception."""
        from clearledgr.services.spend_analysis import SpendAnalysisService
        svc = SpendAnalysisService("default")
        # Force the whole _build_summary to raise (simulates catastrophic failure)
        with patch.object(svc, "_build_summary", side_effect=RuntimeError("DB gone")):
            result = svc.analyze(30)
        assert "error" in result
        assert result["organization_id"] == "default"

    def test_budget_utilization_delegates(self, db):
        """_budget_utilization delegates to BudgetAwarenessService.get_report()."""
        mock_report = MagicMock()
        mock_report.to_dict.return_value = {"total_budgeted": 10000, "total_spent": 3000}

        mock_service = MagicMock()
        mock_service.get_report.return_value = mock_report

        from clearledgr.services.spend_analysis import SpendAnalysisService
        svc = SpendAnalysisService("default")
        with patch(
            "clearledgr.services.budget_awareness.get_budget_awareness",
            return_value=mock_service,
        ):
            util = svc._budget_utilization()
        assert util["total_budgeted"] == 10000
        assert util["total_spent"] == 3000


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

class TestSpendAnalysisAPI:

    def test_endpoint_returns_200(self, client, db):
        resp = client.get("/api/workspace/spend-analysis")
        assert resp.status_code == 200
        data = resp.json()
        assert data["organization_id"] == "default"
        assert "summary" in data
        assert "top_vendors" in data

    def test_endpoint_period_days_param(self, client, db):
        resp = client.get("/api/workspace/spend-analysis?period_days=90")
        assert resp.status_code == 200
        assert resp.json()["period_days"] == 90

    def test_endpoint_invalid_period_rejected(self, client, db):
        resp = client.get("/api/workspace/spend-analysis?period_days=0")
        assert resp.status_code == 422

    def test_endpoint_requires_auth(self, db):
        app.dependency_overrides.pop(workspace_shell_module.get_current_user, None)
        c = TestClient(app)
        resp = c.get("/api/workspace/spend-analysis")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Agent tool registration tests
# ---------------------------------------------------------------------------

class TestSpendAnalysisTool:

    def test_analyze_spending_in_tool_list(self):
        from clearledgr.core.skills.ap_skill import APSkill
        skill = APSkill("default")
        tool_names = [t.name for t in skill.get_tools()]
        assert "analyze_spending" in tool_names

    def test_tool_count_is_nine(self):
        from clearledgr.core.skills.ap_skill import APSkill
        skill = APSkill("default")
        assert len(skill.get_tools()) == 9

    def test_handler_returns_ok(self, db):
        from clearledgr.core.skills.ap_skill import _handle_analyze_spending
        result = asyncio.run(
            _handle_analyze_spending(period_days=30, organization_id="default")
        )
        assert result["ok"] is True
        assert "summary" in result
        assert "top_vendors" in result

    def test_handler_never_raises(self, db):
        from clearledgr.core.skills.ap_skill import _handle_analyze_spending
        with patch(
            "clearledgr.services.spend_analysis.get_spend_analysis_service",
            side_effect=RuntimeError("boom"),
        ):
            result = asyncio.run(
                _handle_analyze_spending(period_days=30, organization_id="default")
            )
        assert result["ok"] is False
        assert "error" in result

    def test_system_prompt_mentions_analyze_spending(self):
        from clearledgr.core.skills.ap_skill import APSkill
        from clearledgr.core.skills.base import AgentTask
        skill = APSkill("default")
        task = AgentTask(
            task_type="ap_invoice_processing",
            organization_id="default",
            payload={"invoice": {"vendor_name": "Test", "amount": 100, "currency": "USD", "confidence": 0.9}},
        )
        prompt = skill.build_system_prompt(task)
        assert "analyze_spending" in prompt
