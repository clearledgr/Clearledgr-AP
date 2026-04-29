"""Tests for Module 4 Pass E — bulk vendor import via CSV.

Coverage:
  * Header normalisation: aliases (vendor → vendor_name, email →
    primary_contact_email) work; unknown columns silently dropped.
  * Required column missing → fatal_error.
  * Per-row validation: required vendor_name, valid email, status
    in {active, blocked, archived}, length caps.
  * commit_rows skips invalid rows, upserts valid ones via the
    store, emits a single ``vendor_bulk_imported`` audit event.
  * MAX_ROWS / MAX_CSV_BYTES enforcement.
  * HTTP: /preview admin-gated, /commit admin-gated, /commit
    rejects fatal-error CSVs with 422.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.api.vendor_status import router as vendor_status_router  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import (  # noqa: E402
    ROLE_AP_CLERK,
    get_current_user,
)
from clearledgr.services.vendor_csv_import import (  # noqa: E402
    MAX_CSV_BYTES,
    commit_rows,
    parse_and_validate,
)


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("default", organization_name="default")
    return inst


def _user(role: str = "owner", uid: str = "owner-user"):
    return SimpleNamespace(
        email=f"{role}@example.com",
        user_id=uid,
        organization_id="default",
        role=role,
    )


@pytest.fixture()
def client_factory():
    def _build(user_factory=lambda: _user()):
        app = FastAPI()
        app.include_router(vendor_status_router)
        app.dependency_overrides[get_current_user] = user_factory
        return TestClient(app)
    return _build


# ─── parse_and_validate ─────────────────────────────────────────────


def test_parse_canonicalises_header_aliases():
    csv_text = (
        "Vendor,Email,Address,Terms\n"
        "Acme,ap@acme.test,123 Acme St,Net 30\n"
    )
    out = parse_and_validate(csv_text)
    assert out.fatal_error is None
    assert out.total_rows == 1
    assert out.valid_rows == 1
    row = out.rows[0]
    assert row.parsed["vendor_name"] == "Acme"
    assert row.parsed["primary_contact_email"] == "ap@acme.test"
    assert row.parsed["registered_address"] == "123 Acme St"
    assert row.parsed["payment_terms"] == "Net 30"


def test_parse_drops_unknown_columns():
    csv_text = (
        "vendor_name,internal_id,xyz_col\n"
        "Globex,42,whatever\n"
    )
    out = parse_and_validate(csv_text)
    assert out.valid_rows == 1
    assert out.rows[0].parsed == {"vendor_name": "Globex"}


def test_parse_missing_vendor_name_column_fatal():
    csv_text = "email,address\nfoo@bar,addr\n"
    out = parse_and_validate(csv_text)
    assert out.fatal_error == "missing_required_column:vendor_name"


def test_parse_invalid_email_marks_row_error():
    csv_text = (
        "vendor_name,email\n"
        "Acme,not-an-email\n"
    )
    out = parse_and_validate(csv_text)
    assert out.error_rows == 1
    assert out.rows[0].valid is False
    assert any("invalid_email" in e for e in out.rows[0].errors)


def test_parse_invalid_status_marks_row_error():
    csv_text = (
        "vendor_name,status\n"
        "Acme,dancing\n"
    )
    out = parse_and_validate(csv_text)
    assert out.error_rows == 1
    assert any("invalid_status" in e for e in out.rows[0].errors)


def test_parse_valid_status_passes():
    csv_text = (
        "vendor_name,status\n"
        "Acme,blocked\n"
        "Globex,active\n"
        "Initech,archived\n"
    )
    out = parse_and_validate(csv_text)
    assert out.error_rows == 0
    assert out.valid_rows == 3
    assert out.rows[0].parsed["status"] == "blocked"


def test_parse_skips_blank_lines():
    csv_text = (
        "vendor_name\n"
        "Acme\n"
        "\n"
        "Globex\n"
        ",\n"
    )
    out = parse_and_validate(csv_text)
    assert out.total_rows == 2  # blanks dropped


def test_parse_oversized_csv_fatal():
    big = "vendor_name\n" + ("X,A\n" * (MAX_CSV_BYTES // 4 + 100))
    out = parse_and_validate(big)
    assert out.fatal_error and out.fatal_error.startswith("csv_too_large")


def test_parse_too_many_rows_fatal():
    rows = ["vendor_name"] + [f"V{i}" for i in range(5_001)]
    csv_text = "\n".join(rows)
    out = parse_and_validate(csv_text)
    assert out.fatal_error and out.fatal_error.startswith("too_many_rows")


def test_parse_handles_tsv():
    """Operators paste tab-separated selections from spreadsheets;
    the sniffer should detect that without a config flag."""
    csv_text = "vendor_name\temail\nAcme\tap@acme.test\n"
    out = parse_and_validate(csv_text)
    assert out.fatal_error is None
    assert out.valid_rows == 1
    assert out.rows[0].parsed["primary_contact_email"] == "ap@acme.test"


# ─── commit_rows ────────────────────────────────────────────────────


def test_commit_upserts_valid_rows(db):
    csv_text = (
        "vendor_name,email,terms\n"
        "Acme,ap@acme.test,Net 30\n"
        "Globex,ops@globex.test,Net 60\n"
    )
    preview = parse_and_validate(csv_text)
    summary = commit_rows(
        db, "default", preview.rows, actor="owner@example.test",
    )
    assert summary["applied_count"] == 2
    assert summary["skipped_count"] == 0

    acme = db.get_vendor_profile("default", "Acme")
    assert acme is not None
    assert acme.get("primary_contact_email") == "ap@acme.test"
    assert acme.get("payment_terms") == "Net 30"


def test_commit_skips_invalid_rows(db):
    csv_text = (
        "vendor_name,email\n"
        "Acme,ap@acme.test\n"
        "BadVendor,not-an-email\n"
    )
    preview = parse_and_validate(csv_text)
    summary = commit_rows(
        db, "default", preview.rows, actor="owner@example.test",
    )
    assert summary["applied_count"] == 1
    assert summary["skipped_count"] == 1
    # Acme landed; BadVendor did not
    assert db.get_vendor_profile("default", "Acme") is not None
    assert db.get_vendor_profile("default", "BadVendor") is None


def test_commit_emits_audit_event(db):
    csv_text = "vendor_name\nAcme\nGlobex\n"
    preview = parse_and_validate(csv_text)
    commit_rows(
        db, "default", preview.rows, actor="owner@example.test",
    )
    events = db.search_audit_events(
        organization_id="default",
        event_types=["vendor_bulk_imported"],
    )
    assert events.get("events"), "expected vendor_bulk_imported event"
    payload = events["events"][0].get("payload_json") or {}
    if isinstance(payload, str):
        payload = json.loads(payload)
    assert payload["applied_count"] == 2
    assert "Acme" in payload["applied_vendor_names"]


# ─── HTTP layer ─────────────────────────────────────────────────────


def test_preview_endpoint_admin_gated(client_factory):
    client = client_factory(lambda: _user(role=ROLE_AP_CLERK, uid="clerk"))
    resp = client.post(
        "/api/vendors/import/preview?organization_id=default",
        json={"csv_text": "vendor_name\nAcme\n"},
    )
    assert resp.status_code == 403


def test_preview_endpoint_returns_validation_payload(client_factory):
    client = client_factory()
    resp = client.post(
        "/api/vendors/import/preview?organization_id=default",
        json={"csv_text": "vendor_name,email\nAcme,bad-email\n"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["error_rows"] == 1
    assert body["valid_rows"] == 0
    assert any("invalid_email" in e for e in body["rows"][0]["errors"])


def test_commit_endpoint_writes_profiles(db, client_factory):
    client = client_factory()
    resp = client.post(
        "/api/vendors/import/commit?organization_id=default",
        json={"csv_text": "vendor_name,email\nFreshlyImported,ap@imp.test\n"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["applied_count"] == 1
    profile = db.get_vendor_profile("default", "FreshlyImported")
    assert profile is not None
    assert profile.get("primary_contact_email") == "ap@imp.test"


def test_commit_endpoint_rejects_fatal_csv(client_factory):
    client = client_factory()
    resp = client.post(
        "/api/vendors/import/commit?organization_id=default",
        json={"csv_text": "email,address\nfoo,bar\n"},  # missing vendor_name
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["reason"] == "csv_invalid"
    assert resp.json()["detail"]["fatal_error"] == "missing_required_column:vendor_name"
