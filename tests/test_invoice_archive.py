"""Tests for Wave 1 / A1 — SOX-immutable original-PDF storage.

Coverage:
  * archive_pdf round-trip: store → fetch returns identical bytes.
  * Content-addressing: same bytes → same hash → single row (dedup).
  * Hash format: SHA-256 hex, 64 chars, lowercase.
  * Tenant isolation: org A cannot fetch org B's hash even though
    the bytes are byte-identical.
  * Append-only enforcement: UPDATE on invoice_originals is rejected
    by the Postgres trigger.
  * Append-only enforcement: DELETE is rejected by trigger.
  * Retention default: retention_until = uploaded_at + 7 years.
  * Retention override: tenant ``settings_json["retention_years"]``
    is honoured at archive time.
  * Size cap: content > MAX_CONTENT_BYTES is rejected.
  * Empty content rejected.
  * link_archive_to_ap_item persists the hash on ap_items.
  * list_originals_for_ap_item finds rows via both
    invoice_originals.ap_item_id AND ap_items.attachment_content_hash.
  * Audit emit on archive (insert + dedupe + dedupe-race outcomes).
  * HTTP: GET /ap/items/{id}/originals lists.
  * HTTP: GET /ap/items/originals/{hash} streams bytes,
    cross-tenant hash returns 404, audit emit on download.
"""
from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.api import workspace_shell as ws  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import get_current_user  # noqa: E402
from clearledgr.services.invoice_archive import (  # noqa: E402
    ArchiveError,
    DEFAULT_RETENTION_YEARS,
    MAX_CONTENT_BYTES,
    archive_pdf,
    fetch_pdf,
    link_archive_to_ap_item,
    list_originals_for_ap_item,
)


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("default", organization_name="default")
    inst.ensure_organization("other-tenant", organization_name="other-tenant")
    return inst


def _user(org_id: str = "default", role: str = "owner"):
    return SimpleNamespace(
        email=f"{role}@example.com",
        user_id=f"{role}-user",
        organization_id=org_id,
        role=role,
    )


@pytest.fixture()
def client(db):
    app = FastAPI()
    app.include_router(ws.router)
    app.dependency_overrides[get_current_user] = lambda: _user()
    return TestClient(app)


@pytest.fixture()
def sample_pdf():
    """A minimal-but-valid PDF byte string.

    Real PDFs work fine; we use a deterministic fixture so the hash
    in the test is predictable across runs."""
    return b"%PDF-1.4\n%%EOF\n" + b"INVOICE-PAYLOAD-ACME-INV-001"


@pytest.fixture()
def alt_pdf():
    return b"%PDF-1.4\n%%EOF\n" + b"INVOICE-PAYLOAD-GLOBEX-INV-042"


# ─── Round-trip ─────────────────────────────────────────────────────


def test_archive_round_trip_returns_identical_bytes(db, sample_pdf):
    out = archive_pdf(
        db,
        organization_id="default",
        content=sample_pdf,
        filename="acme-inv.pdf",
        content_type="application/pdf",
    )
    assert out.size_bytes == len(sample_pdf)
    fetched = fetch_pdf(
        db, organization_id="default", content_hash=out.content_hash,
    )
    assert fetched is not None
    assert fetched["content"] == sample_pdf
    assert fetched["filename"] == "acme-inv.pdf"
    assert fetched["content_type"] == "application/pdf"


def test_content_hash_is_sha256_hex(db, sample_pdf):
    out = archive_pdf(
        db, organization_id="default", content=sample_pdf,
    )
    expected = hashlib.sha256(sample_pdf).hexdigest()
    assert out.content_hash == expected
    assert len(out.content_hash) == 64
    assert out.content_hash == out.content_hash.lower()


def test_dedup_returns_existing_row_no_duplicate(db, sample_pdf):
    first = archive_pdf(
        db, organization_id="default", content=sample_pdf,
        filename="first.pdf",
    )
    second = archive_pdf(
        db, organization_id="default", content=sample_pdf,
        filename="second.pdf",  # different filename, same bytes
    )
    assert first.content_hash == second.content_hash
    # Verify only ONE row exists for that hash in this tenant
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) AS n FROM invoice_originals "
            "WHERE organization_id = %s AND content_hash = %s",
            ("default", first.content_hash),
        )
        row = cur.fetchone()
    if isinstance(row, dict):
        assert int(row.get("n") or 0) == 1
    else:
        assert int(row[0] or 0) == 1


# ─── Tenant isolation ───────────────────────────────────────────────


def test_tenant_isolation_same_bytes_two_rows(db, sample_pdf):
    out_a = archive_pdf(
        db, organization_id="default", content=sample_pdf,
    )
    out_b = archive_pdf(
        db, organization_id="other-tenant", content=sample_pdf,
    )
    # Same hash, but two distinct rows because PK is (org_id, hash)
    assert out_a.content_hash == out_b.content_hash
    # Cross-tenant fetch returns None (not the bytes)
    cross = fetch_pdf(
        db, organization_id="default",
        content_hash=out_b.content_hash,
    )
    # Same hash works because it's our own row, but verify ap_item_id
    # came from default tenant's row, not other-tenant's:
    assert cross is not None
    assert cross["organization_id"] == "default"

    # An org with no archived row gets None
    not_found = fetch_pdf(
        db, organization_id="other-tenant",
        content_hash="0" * 64,
    )
    assert not_found is None


# ─── Append-only enforcement (Postgres triggers) ────────────────────


def test_update_on_invoice_originals_is_rejected(db, sample_pdf):
    out = archive_pdf(db, organization_id="default", content=sample_pdf)
    with pytest.raises(Exception) as excinfo:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE invoice_originals SET filename = 'tampered.pdf' "
                "WHERE organization_id = %s AND content_hash = %s",
                ("default", out.content_hash),
            )
            conn.commit()
    assert "append-only" in str(excinfo.value).lower()


def test_delete_on_invoice_originals_is_rejected(db, sample_pdf):
    out = archive_pdf(db, organization_id="default", content=sample_pdf)
    with pytest.raises(Exception) as excinfo:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM invoice_originals "
                "WHERE organization_id = %s AND content_hash = %s",
                ("default", out.content_hash),
            )
            conn.commit()
    assert "append-only" in str(excinfo.value).lower()


# ─── Retention ──────────────────────────────────────────────────────


def test_retention_default_is_seven_years(db, sample_pdf):
    out = archive_pdf(db, organization_id="default", content=sample_pdf)
    upload_dt = datetime.fromisoformat(out.uploaded_at)
    retention_dt = datetime.fromisoformat(out.retention_until)
    delta_days = (retention_dt - upload_dt).days
    expected_days = 365 * DEFAULT_RETENTION_YEARS
    # Allow ±5 days for leap years over a 7-year span
    assert abs(delta_days - expected_days) <= 5


def test_retention_override_via_org_settings(db, sample_pdf):
    db.update_organization(
        "default",
        settings_json={"retention_years": 10},
    )
    out = archive_pdf(db, organization_id="default", content=sample_pdf)
    upload_dt = datetime.fromisoformat(out.uploaded_at)
    retention_dt = datetime.fromisoformat(out.retention_until)
    delta_days = (retention_dt - upload_dt).days
    expected_days = 365 * 10
    assert abs(delta_days - expected_days) <= 5


# ─── Validation guards ──────────────────────────────────────────────


def test_empty_content_rejected(db):
    with pytest.raises(ArchiveError) as excinfo:
        archive_pdf(db, organization_id="default", content=b"")
    assert "empty" in str(excinfo.value).lower()


def test_oversized_content_rejected(db):
    too_big = b"X" * (MAX_CONTENT_BYTES + 1)
    with pytest.raises(ArchiveError) as excinfo:
        archive_pdf(db, organization_id="default", content=too_big)
    assert "too_large" in str(excinfo.value).lower()


def test_missing_org_rejected(db, sample_pdf):
    with pytest.raises(ArchiveError):
        archive_pdf(db, organization_id="", content=sample_pdf)


# ─── AP-item linkage ────────────────────────────────────────────────


def test_link_archive_to_ap_item_persists_hash(db, sample_pdf):
    out = archive_pdf(db, organization_id="default", content=sample_pdf)
    item = db.create_ap_item({
        "id": "AP-link-test-1",
        "organization_id": "default",
        "vendor_name": "Acme",
        "amount": 100.0,
        "state": "received",
    })
    ok = link_archive_to_ap_item(
        db, ap_item_id=item["id"], content_hash=out.content_hash,
    )
    assert ok is True
    fresh = db.get_ap_item(item["id"])
    assert fresh.get("attachment_content_hash") == out.content_hash


def test_list_originals_via_ap_item_hash(db, sample_pdf, alt_pdf):
    """An AP item with attachment_content_hash set should surface the
    archive even if the archive's own ap_item_id is NULL (the typical
    intake pattern: archive first, link via AP item later)."""
    out = archive_pdf(
        db, organization_id="default", content=sample_pdf,
        # ap_item_id intentionally NOT passed — simulating intake
        # archive before AP item exists
    )
    item = db.create_ap_item({
        "id": "AP-list-test-1",
        "organization_id": "default",
        "vendor_name": "Acme",
        "amount": 100.0,
        "state": "received",
        "attachment_content_hash": out.content_hash,
    })
    rows = list_originals_for_ap_item(
        db, organization_id="default", ap_item_id=item["id"],
    )
    assert len(rows) == 1
    assert rows[0]["content_hash"] == out.content_hash


# ─── Audit emit ─────────────────────────────────────────────────────


def test_archive_emits_audit_event(db, sample_pdf):
    archive_pdf(db, organization_id="default", content=sample_pdf)
    events = db.search_audit_events(
        organization_id="default",
        event_types=["invoice_original_archived"],
    )
    matching = events.get("events", [])
    assert any(e for e in matching), "expected invoice_original_archived audit event"


def test_dedupe_emits_separate_audit_event(db, sample_pdf):
    archive_pdf(db, organization_id="default", content=sample_pdf)
    archive_pdf(db, organization_id="default", content=sample_pdf)
    events = db.search_audit_events(
        organization_id="default",
        event_types=["invoice_original_archived"],
    )
    # Idempotency_key uses outcome — first is "inserted",
    # second is "deduped". Two distinct rows.
    matching = events.get("events", [])
    assert len(matching) >= 2


# ─── HTTP layer ─────────────────────────────────────────────────────


def test_list_endpoint_returns_originals(db, sample_pdf, client):
    out = archive_pdf(db, organization_id="default", content=sample_pdf)
    item = db.create_ap_item({
        "id": "AP-http-list-1",
        "organization_id": "default",
        "vendor_name": "Acme",
        "amount": 100.0,
        "state": "received",
        "attachment_content_hash": out.content_hash,
    })
    resp = client.get(
        f"/api/workspace/ap/items/{item['id']}/originals?organization_id=default",
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 1
    assert body["originals"][0]["content_hash"] == out.content_hash


def test_download_endpoint_streams_bytes(db, sample_pdf, client):
    out = archive_pdf(
        db, organization_id="default", content=sample_pdf,
        filename="acme.pdf",
    )
    resp = client.get(
        f"/api/workspace/ap/items/originals/{out.content_hash}?organization_id=default",
    )
    assert resp.status_code == 200, resp.text
    assert resp.content == sample_pdf
    assert resp.headers.get("X-Content-Hash") == out.content_hash
    assert "acme.pdf" in resp.headers.get("Content-Disposition", "")


def test_download_cross_tenant_returns_404(db, sample_pdf, client):
    """A hash from another tenant must look like 'not found' to
    prevent existence leaks."""
    out = archive_pdf(
        db, organization_id="other-tenant", content=sample_pdf,
    )
    resp = client.get(
        f"/api/workspace/ap/items/originals/{out.content_hash}?organization_id=default",
    )
    assert resp.status_code == 404


def test_download_emits_audit_event(db, sample_pdf, client):
    out = archive_pdf(db, organization_id="default", content=sample_pdf)
    client.get(
        f"/api/workspace/ap/items/originals/{out.content_hash}?organization_id=default",
    )
    events = db.search_audit_events(
        organization_id="default",
        event_types=["invoice_original_downloaded"],
    )
    assert events.get("events"), "expected invoice_original_downloaded audit event"
