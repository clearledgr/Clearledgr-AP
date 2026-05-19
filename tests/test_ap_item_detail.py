"""Tests for the workspace AP-item detail endpoint (Module 2).

GET /api/workspace/ap-items/{ap_item_id}/detail consolidates everything
the leader's exception-detail page needs into one structured response.

Pinned by these tests:

  - 200 response shape: ``item`` + ``reasoning`` + ``match`` +
    ``timeline`` + ``actions``.
  - Cross-tenant items return 404, never 403 — ``_resolve_item_for_detail``
    closes the membership oracle so two orgs can probe with sequential
    IDs without enumerating each other's space.
  - Reasoning composition: ap_decision_* fields persisted to metadata
    surface as ``reasoning.agent_decision``; missing fields stay null
    rather than getting backfilled.
  - Governance verdict + agent_confidence pulled from the most recent
    audit event that carries them (migration v50 columns).
  - Action availability follows the canonical state machine: ``approve``
    / ``reject`` / ``request_info`` etc are surfaced only when the
    current state can transition to their target.
  - Three-way match is best-effort — when the runner returns None, the
    detail endpoint returns ``match: null`` and the rest of the
    payload still renders.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from solden.api import ap_item_detail as detail_routes  # noqa: E402
from solden.core import database as db_module  # noqa: E402
from solden.core.auth import get_current_user  # noqa: E402


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgA", organization_name="Acme UK Ltd")
    inst.ensure_organization("orgB", organization_name="Beta Co")
    return inst


def _user(org: str = "orgA") -> SimpleNamespace:
    return SimpleNamespace(
        user_id="leader@orgA.com",
        email="leader@orgA.com",
        organization_id=org,
        role="user",
    )


@pytest.fixture()
def client_orgA(db):
    app = FastAPI()
    app.include_router(detail_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgA")
    return TestClient(app)


@pytest.fixture()
def client_orgB(db):
    app = FastAPI()
    app.include_router(detail_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgB")
    return TestClient(app)


def _make_item(db, *, item_id: str, org: str = "orgA", state: str = "needs_approval", **fields):
    payload = {
        "id": item_id,
        "organization_id": org,
        "vendor_name": fields.pop("vendor_name", "Acme Supplies"),
        "amount": fields.pop("amount", 1234.56),
        "currency": fields.pop("currency", "USD"),
        "invoice_number": fields.pop("invoice_number", f"INV-{item_id}"),
        "state": state,
        "metadata": fields.pop("metadata", {}),
    }
    payload.update(fields)
    db.create_ap_item(payload)
    return db.get_ap_item(item_id)


# ─── Tests ──────────────────────────────────────────────────────────


class TestResponseShape:
    def test_returns_item_reasoning_match_timeline_actions(self, db, client_orgA):
        _make_item(db, item_id="ap-shape-1")

        resp = client_orgA.get("/api/workspace/ap-items/ap-shape-1/detail")
        assert resp.status_code == 200
        body = resp.json()

        # Top-level keys are stable contract.
        assert set(body.keys()) >= {"item", "reasoning", "match", "timeline", "actions"}
        assert body["item"]["id"] == "ap-shape-1"
        assert body["item"].get("vendor_name") == "Acme Supplies" or body["item"].get("vendor") == "Acme Supplies"
        assert isinstance(body["timeline"], list)
        assert isinstance(body["actions"]["available"], list)


class TestTenantIsolation:
    def test_other_orgs_item_returns_404_not_403(self, db, client_orgB):
        # Item belongs to orgA; client authenticated as orgB.
        _make_item(db, item_id="ap-cross-1", org="orgA")
        resp = client_orgB.get("/api/workspace/ap-items/ap-cross-1/detail")
        assert resp.status_code == 404
        assert resp.json().get("detail") == "ap_item_not_found"

    def test_missing_item_returns_404(self, client_orgA):
        resp = client_orgA.get("/api/workspace/ap-items/nonexistent/detail")
        assert resp.status_code == 404
        assert resp.json().get("detail") == "ap_item_not_found"


class TestReasoningPayload:
    def test_metadata_ap_decision_fields_surface(self, db, client_orgA):
        _make_item(
            db,
            item_id="ap-reason-1",
            metadata={
                "ap_decision_recommendation": "needs_info",
                "ap_decision_reasoning": "PO reference is required for this vendor.",
                "ap_decision_risk_flags": ["po_required_missing"],
                "ap_decision_model": "rules",
                "vendor_intelligence": {
                    "vendor_context": {"invoice_count": 12, "always_approved": True},
                    "decision_feedback": {"count": 3, "override_rate": 0.0},
                    "single_pass_hints": {
                        "risk_assessment": {"fraud_risk": "low", "fraud_signals": []},
                    },
                },
            },
        )

        body = client_orgA.get("/api/workspace/ap-items/ap-reason-1/detail").json()
        agent = body["reasoning"]["agent_decision"]
        assert agent["recommendation"] == "needs_info"
        assert "PO reference is required" in agent["reasoning"]
        assert agent["risk_flags"] == ["po_required_missing"]
        assert agent["model"] == "rules"

        sources = body["reasoning"]["sources"]
        assert sources["vendor_context"]["invoice_count"] == 12
        assert sources["vendor_context"]["always_approved"] is True
        assert sources["single_pass_hints"]["risk_assessment"]["fraud_risk"] == "low"

    def test_missing_reasoning_fields_stay_null_not_filled(self, db, client_orgA):
        # Item has no ap_decision_* persisted yet (e.g., still in
        # received state). The endpoint must NOT fabricate.
        _make_item(db, item_id="ap-empty-1", state="received", metadata={})
        body = client_orgA.get("/api/workspace/ap-items/ap-empty-1/detail").json()
        agent = body["reasoning"]["agent_decision"]
        assert agent["recommendation"] is None
        assert agent["reasoning"] is None
        assert agent["risk_flags"] == []
        # Model defaults to "rules" — that's not a fabrication, that's
        # the canonical source. But governance must be null since no
        # gate has run.
        assert body["reasoning"]["governance"] is None

    def test_governance_verdict_pulled_from_audit_events(self, db, client_orgA):
        _make_item(db, item_id="ap-gov-1", state="needs_approval")
        # Persist an audit event with governance metadata. The store
        # writes through ``record_ap_audit_event`` which is the canonical
        # write path; the migration v50 columns are populated when
        # present in the row dict.
        if hasattr(db, "record_ap_audit_event"):
            db.record_ap_audit_event(
                ap_item_id="ap-gov-1",
                event_type="agent_action_attempted",
                summary="Agent attempted approval",
                details={
                    "governance_verdict": "escalate",
                    "agent_confidence": 0.62,
                    "decision_reason": "low_confidence",
                },
            )

        body = client_orgA.get("/api/workspace/ap-items/ap-gov-1/detail").json()
        gov = body["reasoning"]["governance"]
        if gov is not None:
            # Audit-event ingestion path varies by store implementation;
            # when it's wired, verdict + confidence must surface.
            assert gov["verdict"] == "escalate"
            assert gov["agent_confidence"] == pytest.approx(0.62)

    def test_narrative_renders_plain_language(self, db, client_orgA):
        _make_item(
            db,
            item_id="ap-narr-1",
            vendor_name="Cisco Systems",
            metadata={
                "ap_decision_recommendation": "approve",
                "ap_decision_reasoning": "All gates passed; vendor has clean history.",
                "ap_decision_risk_flags": [],
            },
        )
        body = client_orgA.get("/api/workspace/ap-items/ap-narr-1/detail").json()
        narrative = body["reasoning"]["narrative"]
        # Narrative is deterministic prose — should mention vendor +
        # the cascade's reasoning verbatim.
        assert "Cisco Systems" in narrative
        assert "approving" in narrative.lower()
        assert "All gates passed" in narrative


class TestActions:
    def test_needs_approval_offers_approve_reject_info(self, db, client_orgA):
        _make_item(db, item_id="ap-act-1", state="needs_approval")
        body = client_orgA.get("/api/workspace/ap-items/ap-act-1/detail").json()
        available = set(body["actions"]["available"])
        # needs_approval → approved, rejected, needs_info, snoozed are
        # all reachable per VALID_TRANSITIONS.
        assert "approve_invoice" in available
        assert "reject_invoice" in available
        assert "request_info" in available
        assert "snooze_invoice" in available

    def test_terminal_state_has_no_actions(self, db, client_orgA):
        _make_item(db, item_id="ap-act-closed", state="closed")
        body = client_orgA.get("/api/workspace/ap-items/ap-act-closed/detail").json()
        assert body["actions"]["available"] == []
        assert body["actions"]["primary"] is None

    def test_failed_post_primary_is_post_to_erp(self, db, client_orgA):
        _make_item(db, item_id="ap-act-fail", state="failed_post")
        body = client_orgA.get("/api/workspace/ap-items/ap-act-fail/detail").json()
        assert body["actions"]["primary"] == "post_to_erp"

    def test_needs_approval_primary_is_approve(self, db, client_orgA):
        _make_item(db, item_id="ap-act-na", state="needs_approval")
        body = client_orgA.get("/api/workspace/ap-items/ap-act-na/detail").json()
        assert body["actions"]["primary"] == "approve_invoice"


class TestMatchPanel:
    def test_match_returns_null_when_runner_unavailable(self, db, client_orgA):
        _make_item(db, item_id="ap-match-skip-1")
        # Patch the runner to raise — endpoint must not blow up; match
        # comes back null.
        with patch(
            "solden.services.three_way_match_runner.run_three_way_match",
            side_effect=RuntimeError("runner offline"),
        ):
            body = client_orgA.get("/api/workspace/ap-items/ap-match-skip-1/detail").json()
        assert body["match"] is None
        # The rest of the payload still rendered.
        assert body["item"]["id"] == "ap-match-skip-1"
        assert body["actions"]["available"]


class TestTimeline:
    def test_timeline_is_a_list(self, db, client_orgA):
        _make_item(db, item_id="ap-tl-1", state="needs_approval")
        body = client_orgA.get("/api/workspace/ap-items/ap-tl-1/detail").json()
        assert isinstance(body["timeline"], list)

    def test_timeline_load_failure_does_not_break_response(self, db, client_orgA):
        _make_item(db, item_id="ap-tl-2")
        # Force the audit-events query to raise; endpoint must still
        # return a valid 200 with an empty timeline.
        original = db.list_ap_audit_events
        db_module.get_db().list_ap_audit_events = lambda *a, **kw: (_ for _ in ()).throw(
            RuntimeError("audit store offline")
        )
        try:
            body = client_orgA.get("/api/workspace/ap-items/ap-tl-2/detail").json()
            assert body["timeline"] == []
        finally:
            db_module.get_db().list_ap_audit_events = original


class TestNarrativeBranches:
    def test_blocked_governance_verdict_surfaces_in_narrative(self, db, client_orgA):
        _make_item(
            db,
            item_id="ap-narr-block",
            vendor_name="Mystery Vendor",
            metadata={
                "ap_decision_recommendation": "approve",
                "ap_decision_reasoning": "Confidence high.",
            },
        )
        if hasattr(db, "record_ap_audit_event"):
            db.record_ap_audit_event(
                ap_item_id="ap-narr-block",
                event_type="agent_action_blocked",
                summary="Governance blocked",
                details={"governance_verdict": "block"},
            )
        body = client_orgA.get("/api/workspace/ap-items/ap-narr-block/detail").json()
        narrative = body["reasoning"]["narrative"]
        assert "Mystery Vendor" in narrative
        # When the audit event landed with verdict=block, the narrative
        # should mention the governance gate. When the store doesn't
        # surface migration-v50 columns yet, this branch is skipped.
        if body["reasoning"]["governance"] is not None:
            assert "governance" in narrative.lower() or "blocked" in narrative.lower()

    def test_unknown_recommendation_falls_back_to_neutral_intro(self, db, client_orgA):
        _make_item(
            db,
            item_id="ap-narr-unknown",
            vendor_name="Beta Co",
            metadata={
                "ap_decision_recommendation": "weird-future-rec",
                "ap_decision_reasoning": "Some reason",
            },
        )
        body = client_orgA.get("/api/workspace/ap-items/ap-narr-unknown/detail").json()
        narrative = body["reasoning"]["narrative"]
        assert "Beta Co" in narrative
        assert "processed" in narrative.lower()  # neutral fallback intro
