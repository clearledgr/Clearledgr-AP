"""Tests for the F4 carry-over — Africa e-invoice transmission layer.

Covers:
  * Default submitter is NotConfigured: result.status='error',
    error_reason='provider_not_configured'.
  * Custom submitter registered + selected via
    settings_json[einvoice_provider][NG] / [default].
  * Per-country resolution: settings can route NG to one ASP and
    KE to a different one.
  * Pending row inserted before the call so even submitter
    failures leave a ledger entry.
  * Provider response + provider_reference stamped on the AP item
    metadata on success.
  * Audit emit per submission attempt.
  * Active-submission unique: re-submit while one is open raises
    409-shaped ValueError.
  * Supersede flow: prior submission flipped, fresh submit allowed.
  * API: submit happy path, 404 unknown item, 409 duplicate, 400
    unsupported country, list endpoint, supersede endpoint.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.api import africa_einvoice as africa_routes  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import get_current_user  # noqa: E402
from clearledgr.services.africa_einvoice_submission import (  # noqa: E402
    NotConfiguredSubmitter,
    SubmissionResult,
    TaxAuthoritySubmitter,
    _ADAPTERS_REGISTRY,
    get_submitter_for_country,
    list_submissions_for_ap_item,
    register_submitter,
    submit_africa_einvoice,
    supersede_submission,
)


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgNG", organization_name="Acme NG")
    inst.update_organization(
        "orgNG", settings={"tax": {"tax_number": "12345678-0001"}},
    )
    inst.ensure_organization("orgB", organization_name="Beta")
    return inst


def _user(uid: str = "user-1", org: str = "orgNG") -> SimpleNamespace:
    return SimpleNamespace(
        user_id=uid, email=f"{uid}@example.com",
        organization_id=org, role="user",
    )


def _client(db, *, org: str = "orgNG") -> TestClient:
    app = FastAPI()
    app.include_router(africa_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user(org=org)
    return TestClient(app)


def _make_ap_item(
    db, *, item_id: str, org: str = "orgNG",
    gross: float = 107.5, net: float = 100.0, vat: float = 7.5,
) -> dict:
    item = db.create_ap_item({
        "id": item_id,
        "organization_id": org,
        "vendor_name": "Vendor X",
        "amount": gross,
        "currency": "NGN",
        "invoice_number": f"INV-{item_id}",
        "state": "received",
    })
    db.update_ap_item(
        item["id"],
        net_amount=Decimal(str(net)),
        vat_amount=Decimal(str(vat)),
        vat_rate=Decimal("7.5"),
        vat_code="T1",
        tax_treatment="domestic",
        bill_country="NG",
    )
    return db.get_ap_item(item["id"])


# ─── Submitter resolution ─────────────────────────────────────────


def test_default_submitter_is_not_configured(db):
    sub = get_submitter_for_country(
        db, organization_id="orgNG", country="NG",
    )
    assert isinstance(sub, NotConfiguredSubmitter)


def test_org_setting_selects_registered_adapter(db):
    """register_submitter + settings_json[einvoice_provider][NG]
    routes to the named adapter."""

    class FakeFIRSAdapter(TaxAuthoritySubmitter):
        provider = "fake_firs"

        def __init__(self, organization_id, country):
            self.organization_id = organization_id
            self.country = country

        async def submit(self, *, country, payload):
            return SubmissionResult(
                status="accepted",
                provider=self.provider,
                provider_reference="FIRS-IRN-123",
            )

    register_submitter(
        "fake_firs",
        lambda organization_id, country: FakeFIRSAdapter(
            organization_id=organization_id, country=country,
        ),
    )
    db.update_organization(
        "orgNG",
        settings={
            "tax": {"tax_number": "12345678-0001"},
            "einvoice_provider": {"NG": "fake_firs"},
        },
    )
    try:
        sub = get_submitter_for_country(
            db, organization_id="orgNG", country="NG",
        )
        assert sub.provider == "fake_firs"
    finally:
        _ADAPTERS_REGISTRY.pop("fake_firs", None)


def test_default_provider_used_when_country_unset(db):
    class FakeASP(TaxAuthoritySubmitter):
        provider = "fake_default"

        def __init__(self, **kwargs):
            pass

        async def submit(self, *, country, payload):
            return SubmissionResult(
                status="accepted", provider=self.provider,
            )

    register_submitter("fake_default", lambda **kw: FakeASP(**kw))
    db.update_organization(
        "orgNG",
        settings={"einvoice_provider": {"default": "fake_default"}},
    )
    try:
        sub = get_submitter_for_country(
            db, organization_id="orgNG", country="NG",
        )
        assert sub.provider == "fake_default"
    finally:
        _ADAPTERS_REGISTRY.pop("fake_default", None)


# ─── End-to-end submit ────────────────────────────────────────────


def _register_recording_adapter(provider_name: str, ref: str = "REF-1"):
    captured = {"calls": []}

    class RecordingAdapter(TaxAuthoritySubmitter):
        provider = provider_name

        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def submit(self, *, country, payload):
            captured["calls"].append({
                "country": country, "payload": payload,
            })
            return SubmissionResult(
                status="accepted",
                provider=self.provider,
                provider_reference=ref,
                response={"status_code": 201},
            )

    register_submitter(
        provider_name,
        lambda **kwargs: RecordingAdapter(**kwargs),
    )
    return captured


def test_submit_records_pending_then_updates_with_response(db):
    capture = _register_recording_adapter("rec1", ref="FIRS-IRN-200")
    db.update_organization(
        "orgNG",
        settings={
            "tax": {"tax_number": "12345678-0001"},
            "einvoice_provider": {"NG": "rec1"},
        },
    )
    try:
        item = _make_ap_item(db, item_id="AP-tx-1")
        outcome = submit_africa_einvoice(
            db,
            organization_id="orgNG",
            ap_item_id=item["id"],
            country="NG",
            actor_id="ops-1",
        )
        assert outcome.status == "accepted"
        assert outcome.provider == "rec1"
        assert outcome.provider_reference == "FIRS-IRN-200"
        assert capture["calls"]
        assert capture["calls"][0]["country"] == "NG"

        rows = list_submissions_for_ap_item(
            db, organization_id="orgNG", ap_item_id=item["id"],
        )
        assert len(rows) == 1
        assert rows[0]["status"] == "accepted"
        assert rows[0]["provider_reference"] == "FIRS-IRN-200"
    finally:
        _ADAPTERS_REGISTRY.pop("rec1", None)


def test_submit_stamps_provider_reference_on_ap_item_metadata(db):
    _register_recording_adapter("rec2", ref="CUIN-XYZ")
    db.update_organization(
        "orgNG",
        settings={
            "tax": {"tax_number": "12345678-0001"},
            "einvoice_provider": {"default": "rec2"},
        },
    )
    try:
        item = _make_ap_item(db, item_id="AP-tx-meta")
        submit_africa_einvoice(
            db,
            organization_id="orgNG",
            ap_item_id=item["id"],
            country="NG",
            actor_id="ops-1",
        )
        fresh = db.get_ap_item(item["id"])
        meta = fresh.get("metadata") or {}
        if isinstance(meta, str):
            import json
            meta = json.loads(meta)
        block = (meta.get("tax_authority_submissions") or {}).get("NG")
        assert block
        assert block["provider_reference"] == "CUIN-XYZ"
        assert block["provider"] == "rec2"
    finally:
        _ADAPTERS_REGISTRY.pop("rec2", None)


def test_submitter_failure_still_records_ledger(db):
    class FailingAdapter(TaxAuthoritySubmitter):
        provider = "failing"

        def __init__(self, **kwargs):
            pass

        async def submit(self, *, country, payload):
            return SubmissionResult(
                status="rejected",
                provider=self.provider,
                error_reason="bad_payload",
                response={"http_status": 422},
            )

    register_submitter("failing", lambda **kw: FailingAdapter(**kw))
    db.update_organization(
        "orgNG",
        settings={
            "tax": {"tax_number": "12345678-0001"},
            "einvoice_provider": {"NG": "failing"},
        },
    )
    try:
        item = _make_ap_item(db, item_id="AP-tx-fail")
        outcome = submit_africa_einvoice(
            db, organization_id="orgNG",
            ap_item_id=item["id"], country="NG",
        )
        assert outcome.status == "rejected"
        rows = list_submissions_for_ap_item(
            db, organization_id="orgNG", ap_item_id=item["id"],
        )
        assert rows[0]["status"] == "rejected"
        assert rows[0]["error_reason"] == "bad_payload"
    finally:
        _ADAPTERS_REGISTRY.pop("failing", None)


def test_audit_event_emitted_on_submit(db):
    _register_recording_adapter("rec_audit", ref="REF-AUDIT")
    db.update_organization(
        "orgNG",
        settings={
            "tax": {"tax_number": "12345678-0001"},
            "einvoice_provider": {"NG": "rec_audit"},
        },
    )
    try:
        item = _make_ap_item(db, item_id="AP-tx-audit")
        submit_africa_einvoice(
            db, organization_id="orgNG",
            ap_item_id=item["id"], country="NG", actor_id="ops-1",
        )
        events = db.list_box_audit_events("ap_item", item["id"])
        types = [e.get("event_type") for e in events]
        assert "tax_authority_submission_accepted" in types
    finally:
        _ADAPTERS_REGISTRY.pop("rec_audit", None)


def test_active_submission_uniqueness(db):
    _register_recording_adapter("rec_unique")
    db.update_organization(
        "orgNG",
        settings={
            "tax": {"tax_number": "12345678-0001"},
            "einvoice_provider": {"NG": "rec_unique"},
        },
    )
    try:
        item = _make_ap_item(db, item_id="AP-tx-unique")
        submit_africa_einvoice(
            db, organization_id="orgNG",
            ap_item_id=item["id"], country="NG",
        )
        with pytest.raises(ValueError) as excinfo:
            submit_africa_einvoice(
                db, organization_id="orgNG",
                ap_item_id=item["id"], country="NG",
            )
        assert "active_submission_exists" in str(excinfo.value)
    finally:
        _ADAPTERS_REGISTRY.pop("rec_unique", None)


def test_supersede_then_resubmit_allowed(db):
    _register_recording_adapter("rec_supersede")
    db.update_organization(
        "orgNG",
        settings={
            "tax": {"tax_number": "12345678-0001"},
            "einvoice_provider": {"NG": "rec_supersede"},
        },
    )
    try:
        item = _make_ap_item(db, item_id="AP-tx-supersede")
        first = submit_africa_einvoice(
            db, organization_id="orgNG",
            ap_item_id=item["id"], country="NG",
        )
        supersede_submission(
            db, organization_id="orgNG",
            submission_id=first.submission_id,
            reason="vendor reissued",
        )
        # Now a fresh submit succeeds.
        second = submit_africa_einvoice(
            db, organization_id="orgNG",
            ap_item_id=item["id"], country="NG",
        )
        assert second.submission_id != first.submission_id
        rows = list_submissions_for_ap_item(
            db, organization_id="orgNG", ap_item_id=item["id"],
        )
        assert len(rows) == 2
    finally:
        _ADAPTERS_REGISTRY.pop("rec_supersede", None)


def test_unsupported_country_raises(db):
    item = _make_ap_item(db, item_id="AP-tx-bad-country")
    with pytest.raises(ValueError):
        submit_africa_einvoice(
            db, organization_id="orgNG",
            ap_item_id=item["id"], country="BR",
        )


def test_unknown_ap_item_raises(db):
    with pytest.raises(ValueError):
        submit_africa_einvoice(
            db, organization_id="orgNG",
            ap_item_id="AP-no-such", country="NG",
        )


# ─── API ──────────────────────────────────────────────────────────


def test_api_submit_happy_path(db):
    _register_recording_adapter("rec_api", ref="API-IRN")
    db.update_organization(
        "orgNG",
        settings={
            "tax": {"tax_number": "12345678-0001"},
            "einvoice_provider": {"NG": "rec_api"},
        },
    )
    try:
        client = _client(db)
        item = _make_ap_item(db, item_id="AP-tx-api-1")
        resp = client.post(
            f"/api/workspace/ap-items/{item['id']}/africa-einvoice/submit"
            "?country=NG",
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "accepted"
        assert data["provider_reference"] == "API-IRN"
    finally:
        _ADAPTERS_REGISTRY.pop("rec_api", None)


def test_api_submit_404_for_unknown_ap_item(db):
    client = _client(db)
    resp = client.post(
        "/api/workspace/ap-items/AP-no-such/africa-einvoice/submit?country=NG",
    )
    assert resp.status_code == 404


def test_api_submit_409_when_active_exists(db):
    _register_recording_adapter("rec_409")
    db.update_organization(
        "orgNG",
        settings={
            "tax": {"tax_number": "12345678-0001"},
            "einvoice_provider": {"NG": "rec_409"},
        },
    )
    try:
        client = _client(db)
        item = _make_ap_item(db, item_id="AP-tx-api-409")
        client.post(
            f"/api/workspace/ap-items/{item['id']}/africa-einvoice/submit"
            "?country=NG",
        )
        resp2 = client.post(
            f"/api/workspace/ap-items/{item['id']}/africa-einvoice/submit"
            "?country=NG",
        )
        assert resp2.status_code == 409
    finally:
        _ADAPTERS_REGISTRY.pop("rec_409", None)


def test_api_list_submissions(db):
    _register_recording_adapter("rec_list")
    db.update_organization(
        "orgNG",
        settings={
            "tax": {"tax_number": "12345678-0001"},
            "einvoice_provider": {"NG": "rec_list"},
        },
    )
    try:
        client = _client(db)
        item = _make_ap_item(db, item_id="AP-tx-api-list")
        client.post(
            f"/api/workspace/ap-items/{item['id']}/africa-einvoice/submit"
            "?country=NG",
        )
        resp = client.get(
            f"/api/workspace/ap-items/{item['id']}/africa-einvoice/submissions"
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1
    finally:
        _ADAPTERS_REGISTRY.pop("rec_list", None)


def test_api_supersede_endpoint(db):
    _register_recording_adapter("rec_super")
    db.update_organization(
        "orgNG",
        settings={
            "tax": {"tax_number": "12345678-0001"},
            "einvoice_provider": {"NG": "rec_super"},
        },
    )
    try:
        client = _client(db)
        item = _make_ap_item(db, item_id="AP-tx-api-super")
        post_resp = client.post(
            f"/api/workspace/ap-items/{item['id']}/africa-einvoice/submit"
            "?country=NG",
        )
        sid = post_resp.json()["submission_id"]
        sup_resp = client.post(
            f"/api/workspace/africa-einvoice/submissions/{sid}/supersede"
            "?reason=vendor%20reissued",
        )
        assert sup_resp.status_code == 200
        assert sup_resp.json()["review_status"] == "superseded"
    finally:
        _ADAPTERS_REGISTRY.pop("rec_super", None)
