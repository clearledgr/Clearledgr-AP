"""Tests for Wave 3 / E1 — sanctions screening + pre-payment gate.

Covers:
  * Store: record_sanctions_check + get_latest + list filters,
    review-status transitions.
  * ComplyAdvantage adapter: missing API key, hit + clear payload
    parsing, type filtering (sanction vs pep vs adverse-media).
  * Service screen_vendor: clear → vendor_profiles.sanctions_status
    becomes 'clear'; hit → 'review' + revalidation fan-out;
    error preserves prior disposition.
  * Pre-payment gate: 'blocked' raises SanctionsBlockedError;
    'review' / 'unscreened' / 'clear' do not.
  * record_payment_confirmation refuses to write a row for a
    blocked vendor.
  * Re-screen scheduler: returns vendors with stale
    last_sanctions_check_at; excludes blocked / archived.
  * API: trigger / list / get / clear / confirm with org isolation.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.api import sanctions as sanctions_routes  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import get_current_user  # noqa: E402
from clearledgr.services.onboarding.complyadvantage_provider import (  # noqa: E402
    ComplyAdvantageProvider,
)
from clearledgr.services.onboarding.kyc_provider import KYCCheckResult  # noqa: E402
from clearledgr.services.payment_tracking import (  # noqa: E402
    record_payment_confirmation,
)
from clearledgr.services.sanctions_screening import (  # noqa: E402
    SanctionsBlockedError,
    gate_payment_against_sanctions,
    screen_vendor,
    vendors_due_for_rescreen,
)


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgA", organization_name="Acme Holdings")
    inst.ensure_organization("orgB", organization_name="Beta Co")
    return inst


def _user(org: str = "orgA") -> SimpleNamespace:
    return SimpleNamespace(
        user_id="user-1", email="ops@orgA.com",
        organization_id=org, role="user",
    )


@pytest.fixture()
def client_orgA(db):
    app = FastAPI()
    app.include_router(sanctions_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgA")
    return TestClient(app)


def _make_awaiting_ap_item(
    db, *, item_id: str, vendor: str = "Acme Vendor",
    org: str = "orgA",
) -> dict:
    item = db.create_ap_item({
        "id": item_id,
        "organization_id": org,
        "vendor_name": vendor,
        "amount": 1500.0,
        "currency": "EUR",
        "state": "received",
    })
    for s in (
        "validated", "needs_approval", "approved",
        "ready_to_post", "posted_to_erp", "awaiting_payment",
    ):
        db.update_ap_item(item["id"], state=s)
    return db.get_ap_item(item["id"])


# ─── Store ──────────────────────────────────────────────────────────


def test_store_record_and_get_latest(db):
    db.record_sanctions_check(
        organization_id="orgA",
        vendor_name="Acme Vendor",
        check_type="sanctions",
        provider="complyadvantage",
        status="clear",
        evidence={"total_hits": 0},
    )
    db.record_sanctions_check(
        organization_id="orgA",
        vendor_name="Acme Vendor",
        check_type="sanctions",
        provider="complyadvantage",
        status="hit",
        matches=[{"name": "Acme Vendor SA", "match_score": 0.92}],
    )
    latest = db.get_latest_sanctions_check("orgA", "Acme Vendor")
    assert latest is not None
    assert latest["status"] == "hit"
    assert isinstance(latest["matches"], list) and len(latest["matches"]) == 1


def test_store_list_filter_by_status(db):
    db.record_sanctions_check(
        organization_id="orgA", vendor_name="V1",
        check_type="sanctions", provider="complyadvantage", status="hit",
    )
    db.record_sanctions_check(
        organization_id="orgA", vendor_name="V2",
        check_type="sanctions", provider="complyadvantage", status="clear",
    )
    hits = db.list_sanctions_checks("orgA", status="hit")
    assert len(hits) == 1
    assert hits[0]["vendor_name"] == "V1"


def test_store_review_status_transitions(db):
    row = db.record_sanctions_check(
        organization_id="orgA", vendor_name="V1",
        check_type="sanctions", provider="complyadvantage", status="hit",
    )
    db.update_sanctions_check_review(
        row["id"], review_status="cleared",
        cleared_by="op@orgA", cleared_reason="false_positive_homonym",
    )
    fresh = db.get_sanctions_check(row["id"])
    assert fresh["review_status"] == "cleared"
    assert fresh["cleared_by"] == "op@orgA"
    assert fresh["cleared_reason"] == "false_positive_homonym"
    assert fresh["cleared_at"] is not None


def test_store_invalid_review_status_raises(db):
    row = db.record_sanctions_check(
        organization_id="orgA", vendor_name="V1",
        check_type="sanctions", provider="complyadvantage", status="hit",
    )
    with pytest.raises(ValueError):
        db.update_sanctions_check_review(row["id"], review_status="bogus")


def test_store_tenant_isolation(db):
    db.record_sanctions_check(
        organization_id="orgA", vendor_name="Shared",
        check_type="sanctions", provider="complyadvantage", status="hit",
    )
    db.record_sanctions_check(
        organization_id="orgB", vendor_name="Shared",
        check_type="sanctions", provider="complyadvantage", status="clear",
    )
    a_latest = db.get_latest_sanctions_check("orgA", "Shared")
    b_latest = db.get_latest_sanctions_check("orgB", "Shared")
    assert a_latest["status"] == "hit"
    assert b_latest["status"] == "clear"


# ─── ComplyAdvantage adapter ───────────────────────────────────────


def test_provider_missing_api_key_returns_error():
    p = ComplyAdvantageProvider(api_key=None)
    result = asyncio.run(p.sanctions_screen(
        legal_name="Acme", country="GB",
    ))
    assert result.status == "error"
    assert result.error == "api_key_missing"


def test_provider_hit_payload_filters_to_sanctions():
    p = ComplyAdvantageProvider(api_key="test-key")
    fake_search = {
        "content": {
            "data": {
                "id": "search-1",
                "search_term": "Acme",
                "hits": [
                    {
                        "id": "hit-1",
                        "match_score": 0.91,
                        "types": ["sanction"],
                        "doc": {"name": "Acme Bad Co", "entity_type": "company"},
                    },
                    {
                        "id": "hit-2",
                        "match_score": 0.55,
                        "types": ["pep"],
                        "doc": {"name": "Acme Politician", "entity_type": "person"},
                    },
                ],
            }
        }
    }
    with patch(
        "clearledgr.services.onboarding.complyadvantage_provider._post_search",
        new=AsyncMock(return_value=fake_search),
    ):
        sanctions = asyncio.run(p.sanctions_screen(
            legal_name="Acme", country="GB",
        ))
        pep = asyncio.run(p.pep_check(
            legal_name="Acme", country="GB",
        ))
    assert sanctions.status == "hit"
    assert len(sanctions.matches) == 1
    assert sanctions.matches[0]["name"] == "Acme Bad Co"
    assert pep.status == "hit"
    assert pep.matches[0]["name"] == "Acme Politician"


def test_provider_clear_payload():
    p = ComplyAdvantageProvider(api_key="test-key")
    fake_search = {"content": {"data": {"hits": []}}}
    with patch(
        "clearledgr.services.onboarding.complyadvantage_provider._post_search",
        new=AsyncMock(return_value=fake_search),
    ):
        result = asyncio.run(p.sanctions_screen(
            legal_name="Clean Vendor", country="GB",
        ))
    assert result.status == "clear"
    assert result.matches == []


def test_provider_company_registry_inconclusive():
    """ComplyAdvantage doesn't run a registry product; the adapter
    must return inconclusive so the planner routes elsewhere."""
    p = ComplyAdvantageProvider(api_key="test-key")
    result = asyncio.run(p.company_registry_lookup(
        legal_name="Acme", country="GB",
    ))
    assert result.status == "inconclusive"
    assert result.error == "company_registry_not_supported_by_provider"


# ─── Service screen_vendor ──────────────────────────────────────────


def _stub_provider_call(returned: KYCCheckResult):
    """Patch sanctions_screen on whatever provider get_kyc_provider
    returns so the service layer behaves end-to-end without real HTTP."""
    from clearledgr.services.onboarding import kyc_provider as _kp
    return patch.object(
        _kp.NotConfiguredKYCProvider,
        "sanctions_screen",
        new=AsyncMock(return_value=returned),
    )


def test_service_clear_rolls_up_to_clear(db):
    db.upsert_vendor_profile(
        "orgA", "Vendor X", primary_contact_email="ap@vendor-x.com",
    )
    with _stub_provider_call(KYCCheckResult(
        status="clear", check_type="sanctions",
        provider="not_configured", checked_at="2026-04-29T10:00:00+00:00",
    )):
        result = screen_vendor(
            db, organization_id="orgA", vendor_name="Vendor X",
            country="GB",
        )
    assert result.status == "clear"
    assert result.sanctions_status == "clear"
    profile = db.get_vendor_profile("orgA", "Vendor X")
    assert profile["sanctions_status"] == "clear"
    assert profile["last_sanctions_check_at"] is not None


def test_service_hit_rolls_up_to_review_and_revalidates(db):
    db.upsert_vendor_profile("orgA", "Bad Vendor")
    item = _make_awaiting_ap_item(
        db, item_id="AP-sanc-1", vendor="Bad Vendor",
    )
    with _stub_provider_call(KYCCheckResult(
        status="hit", check_type="sanctions",
        provider="not_configured",
        matches=[{"name": "Bad Vendor SA", "match_score": 0.95}],
        checked_at="2026-04-29T10:00:00+00:00",
    )):
        result = screen_vendor(
            db, organization_id="orgA", vendor_name="Bad Vendor",
            country="GB", actor="ops@orgA",
        )
    assert result.status == "hit"
    assert result.sanctions_status == "review"
    assert result.matches_count == 1
    assert result.revalidated_ap_items == 1
    # AP item should be flagged with vendor_sanctions_hit exception.
    fresh_item = db.get_ap_item(item["id"])
    assert fresh_item["exception_code"] == "vendor_sanctions_hit"


def test_service_error_preserves_prior_disposition(db):
    db.upsert_vendor_profile(
        "orgA", "Vendor X", sanctions_status="clear",
    )
    with _stub_provider_call(KYCCheckResult(
        status="error", check_type="sanctions",
        provider="not_configured", error="upstream_unavailable",
        checked_at="2026-04-29T10:00:00+00:00",
    )):
        result = screen_vendor(
            db, organization_id="orgA", vendor_name="Vendor X",
            country="GB",
        )
    assert result.status == "error"
    profile = db.get_vendor_profile("orgA", "Vendor X")
    # Disposition NOT downgraded back to 'unscreened' on transient
    # provider failure.
    assert profile["sanctions_status"] == "clear"


# ─── Pre-payment gate ───────────────────────────────────────────────


def test_gate_blocked_raises(db):
    db.upsert_vendor_profile(
        "orgA", "Bad Vendor", sanctions_status="blocked",
    )
    with pytest.raises(SanctionsBlockedError):
        gate_payment_against_sanctions(
            db, organization_id="orgA", vendor_name="Bad Vendor",
        )


def test_gate_review_does_not_raise(db):
    """'review' is the AP-item-level exception's job, not the
    payment gate — payments are still attempted but the AP item is
    flagged in the queue."""
    db.upsert_vendor_profile(
        "orgA", "Vendor X", sanctions_status="review",
    )
    gate_payment_against_sanctions(
        db, organization_id="orgA", vendor_name="Vendor X",
    )  # no raise


def test_gate_unscreened_does_not_raise(db):
    db.upsert_vendor_profile("orgA", "Vendor X")
    gate_payment_against_sanctions(
        db, organization_id="orgA", vendor_name="Vendor X",
    )


def test_record_payment_confirmation_blocked_vendor_raises(db):
    db.upsert_vendor_profile(
        "orgA", "Bad Vendor", sanctions_status="blocked",
    )
    item = _make_awaiting_ap_item(
        db, item_id="AP-sanc-block", vendor="Bad Vendor",
    )
    with pytest.raises(SanctionsBlockedError):
        record_payment_confirmation(
            db,
            organization_id="orgA",
            ap_item_id=item["id"],
            payment_id="P-BLOCKED",
            source="manual",
            status="confirmed",
            amount=1500.0,
        )
    # No payment confirmation row was inserted.
    rows = db.list_payment_confirmations_for_ap_item("orgA", item["id"])
    assert rows == []


def test_record_payment_confirmation_clear_vendor_proceeds(db):
    db.upsert_vendor_profile(
        "orgA", "Good Vendor", sanctions_status="clear",
    )
    item = _make_awaiting_ap_item(
        db, item_id="AP-sanc-good", vendor="Good Vendor",
    )
    result = record_payment_confirmation(
        db, organization_id="orgA", ap_item_id=item["id"],
        payment_id="P-OK", source="manual", status="confirmed",
        amount=1500.0,
    )
    assert result.duplicate is False
    assert result.ap_state_after == "payment_executed"


# ─── Re-screen scheduler ────────────────────────────────────────────


def test_rescreen_picks_stale_vendors(db):
    # V1 last screened a year ago — eligible.
    db.upsert_vendor_profile(
        "orgA", "V1",
        sanctions_status="clear",
        last_sanctions_check_at="2025-04-29T00:00:00+00:00",
    )
    # V2 unscreened — eligible.
    db.upsert_vendor_profile("orgA", "V2")
    # V3 fresh — not eligible.
    from datetime import datetime, timezone
    db.upsert_vendor_profile(
        "orgA", "V3",
        sanctions_status="clear",
        last_sanctions_check_at=datetime.now(timezone.utc).isoformat(),
    )
    # V4 blocked — already at most severe; not eligible.
    db.upsert_vendor_profile(
        "orgA", "V4", sanctions_status="blocked",
    )
    due = vendors_due_for_rescreen(db, organization_id="orgA")
    names = {v["vendor_name"] for v in due}
    assert "V1" in names
    assert "V2" in names
    assert "V3" not in names
    assert "V4" not in names


# ─── API ────────────────────────────────────────────────────────────


def test_api_trigger_screen_clear(db, client_orgA):
    db.upsert_vendor_profile("orgA", "Vendor API")
    with _stub_provider_call(KYCCheckResult(
        status="clear", check_type="sanctions",
        provider="not_configured",
        checked_at="2026-04-29T10:00:00+00:00",
    )):
        resp = client_orgA.post(
            "/api/workspace/vendors/Vendor API/sanctions-screen",
            json={"country": "GB"},
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "clear"
    assert data["sanctions_status"] == "clear"


def test_api_unknown_vendor_404(client_orgA):
    resp = client_orgA.post(
        "/api/workspace/vendors/Unknown/sanctions-screen", json={},
    )
    assert resp.status_code == 404


def test_api_list_filters_by_review_status(db, client_orgA):
    db.upsert_vendor_profile("orgA", "V1")
    db.record_sanctions_check(
        organization_id="orgA", vendor_name="V1",
        check_type="sanctions", provider="complyadvantage", status="hit",
        matches=[{"name": "V1 SA"}],
    )
    resp = client_orgA.get(
        "/api/workspace/sanctions-checks?review_status=open",
    )
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


def test_api_clear_check_rolls_back_disposition(db, client_orgA):
    db.upsert_vendor_profile("orgA", "Vendor C")
    row = db.record_sanctions_check(
        organization_id="orgA", vendor_name="Vendor C",
        check_type="sanctions", provider="complyadvantage", status="hit",
        matches=[{"name": "Vendor C SA"}],
    )
    db.upsert_vendor_profile(
        "orgA", "Vendor C", sanctions_status="review",
    )
    resp = client_orgA.post(
        f"/api/workspace/sanctions-checks/{row['id']}/clear",
        json={"reason": "homonym_false_positive"},
    )
    assert resp.status_code == 200
    assert resp.json()["review_status"] == "cleared"
    profile = db.get_vendor_profile("orgA", "Vendor C")
    assert profile["sanctions_status"] == "clear"


def test_api_confirm_check_flips_to_blocked(db, client_orgA):
    db.upsert_vendor_profile("orgA", "Vendor Bad")
    row = db.record_sanctions_check(
        organization_id="orgA", vendor_name="Vendor Bad",
        check_type="sanctions", provider="complyadvantage", status="hit",
        matches=[{"name": "Vendor Bad"}],
    )
    resp = client_orgA.post(
        f"/api/workspace/sanctions-checks/{row['id']}/confirm",
        json={"reason": "match_confirmed"},
    )
    assert resp.status_code == 200
    assert resp.json()["review_status"] == "confirmed"
    profile = db.get_vendor_profile("orgA", "Vendor Bad")
    assert profile["sanctions_status"] == "blocked"


def test_api_get_check_cross_org_404(db, client_orgA):
    db.upsert_vendor_profile("orgB", "Other Vendor")
    other = db.record_sanctions_check(
        organization_id="orgB", vendor_name="Other Vendor",
        check_type="sanctions", provider="complyadvantage", status="hit",
    )
    resp = client_orgA.get(
        f"/api/workspace/sanctions-checks/{other['id']}",
    )
    assert resp.status_code == 404
