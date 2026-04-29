"""Tests for Module 4 Pass A — vendor risk score wired into list +
detail payloads.

Coverage:
  * compute_risk_from_profile is callable without a DB and returns
    the same shape as the service's compute().
  * The vendor list payload (``_build_vendor_summary_rows``) carries
    a ``risk_score`` integer per row, matching what the risk service
    computes from the same profile.
  * The vendor detail payload (``_build_vendor_detail_payload``)
    carries a ``risk`` dict with score + per-component breakdown.
  * Sanity: a vendor with zero data scores 0 (no profile = no score),
    and a vendor with fresh KYC + recent activity scores at most
    a small number from missing-field components.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.services import ap_vendor_analysis  # noqa: E402
from clearledgr.services.vendor_risk import (  # noqa: E402
    VendorRiskScoreService,
    compute_risk_from_profile,
)


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("default", organization_name="default")
    return inst


# ─── compute_risk_from_profile ──────────────────────────────────────


def test_compute_risk_from_profile_empty_returns_zero():
    out = compute_risk_from_profile(None)
    assert out.score == 0
    assert out.vendor_found is False
    assert out.components == []


def test_compute_risk_from_profile_new_vendor():
    profile = {
        "vendor_name": "X",
        "invoice_count": 0,
        "kyc_completion_date": None,
        "registration_number": "",
        "vat_number": "",
        "director_names": [],
    }
    out = compute_risk_from_profile(profile)
    # New vendor (30) + KYC missing (15) + missing reg (5) + missing
    # vat (5) + missing directors (5) = 60
    assert out.score == 60
    codes = {c.code for c in out.components}
    assert "new_vendor" in codes
    assert "kyc_missing" in codes


def test_compute_risk_from_profile_iban_freeze_dominates():
    profile = {
        "invoice_count": 100,  # established vendor
        "iban_change_pending": True,
        "registration_number": "REG1",
        "vat_number": "VAT1",
        "director_names": ["Jane Doe"],
        "kyc_completion_date": datetime.now(timezone.utc).date().isoformat(),
    }
    out = compute_risk_from_profile(profile)
    # IBAN freeze alone is +50; no other components fire.
    assert out.score == 50
    assert {c.code for c in out.components} == {"iban_change_freeze_active"}


def test_compute_risk_clamps_at_100():
    profile = {
        "invoice_count": 0,
        "iban_change_pending": True,
        "bank_details_changed_at": (
            datetime.now(timezone.utc) - timedelta(days=5)
        ).isoformat(),
        "approval_override_rate": 0.6,
        "kyc_completion_date": None,
        "registration_number": "",
        "vat_number": "",
        "director_names": [],
    }
    out = compute_risk_from_profile(profile)
    assert out.score == 100
    # All eight component types fire — but the score is clamped.
    assert len(out.components) >= 7


def test_service_compute_matches_pure_function(db):
    """The class-based service is a thin wrapper — its result must
    match calling the pure function directly with the same profile."""
    db.upsert_vendor_profile(
        organization_id="default",
        vendor_name="Acme",
        invoice_count=0,
    )
    profile = db.get_vendor_profile("default", "Acme")
    direct = compute_risk_from_profile(profile)
    via_service = VendorRiskScoreService("default", db=db).compute("Acme")
    assert direct.score == via_service.score
    assert {c.code for c in direct.components} == {
        c.code for c in via_service.components
    }


# ─── List payload ───────────────────────────────────────────────────


def test_summary_rows_include_risk_score(db):
    """Every row in the vendor summary list carries an integer
    ``risk_score`` field. Even vendors with no profile collapse to 0
    rather than missing the key — the SPA gates the chip on ``> 0``."""
    # Two vendors with very different profiles.
    db.upsert_vendor_profile(
        organization_id="default",
        vendor_name="Acme",
        invoice_count=0,  # new vendor → score includes +30
    )
    db.upsert_vendor_profile(
        organization_id="default",
        vendor_name="Globex",
        invoice_count=50,
        registration_number="REG-100",
        vat_number="VAT-100",
        director_names=["Hank Scorpio"],
        kyc_completion_date=datetime.now(timezone.utc).date().isoformat(),
    )
    # Seed at least one AP item per vendor so they show up in the
    # summary builder (which iterates ap_items).
    db.create_ap_item({
        "ap_item_id": "ap-acme-1",
        "organization_id": "default",
        "vendor_name": "Acme",
        "amount": 500.0,
        "state": "received",
    })
    db.create_ap_item({
        "ap_item_id": "ap-globex-1",
        "organization_id": "default",
        "vendor_name": "Globex",
        "amount": 1000.0,
        "state": "received",
    })

    rows = ap_vendor_analysis._build_vendor_summary_rows(db, "default", limit=50)
    by_name = {r["vendor_name"]: r for r in rows}
    assert "Acme" in by_name and "Globex" in by_name
    for row in rows:
        assert "risk_score" in row, f"missing risk_score on {row['vendor_name']}"
        assert isinstance(row["risk_score"], int)
        assert 0 <= row["risk_score"] <= 100
    # New-vendor Acme should outscore the established Globex.
    assert by_name["Acme"]["risk_score"] > by_name["Globex"]["risk_score"]


# ─── Detail payload ─────────────────────────────────────────────────


def test_detail_payload_includes_risk_breakdown(db):
    db.upsert_vendor_profile(
        organization_id="default",
        vendor_name="Initech",
        invoice_count=0,
        iban_change_pending=True,
    )
    db.create_ap_item({
        "ap_item_id": "ap-initech-1",
        "organization_id": "default",
        "vendor_name": "Initech",
        "amount": 250.0,
        "state": "received",
    })
    payload = ap_vendor_analysis._build_vendor_detail_payload(
        db, "default", "Initech",
    )
    assert "risk" in payload
    risk = payload["risk"]
    assert risk["score"] >= 50  # IBAN freeze alone
    assert risk["vendor_found"] is True
    codes = {c["code"] for c in risk.get("components") or []}
    assert "iban_change_freeze_active" in codes
    assert "new_vendor" in codes
