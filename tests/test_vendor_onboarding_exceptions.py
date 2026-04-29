"""Tests for Module 4 Pass C — vendor onboarding sessions surfaced
as first-class exceptions in Module 2's queue.

Coverage:
  * Synthesizer returns nothing when the org has no pending sessions.
  * BLOCKED sessions surface as high-severity ``vendor_onboarding_blocked``.
  * Non-terminal sessions older than the stall window surface as
    medium-severity ``vendor_onboarding_stalled_<state>``.
  * Sessions whose ``last_activity_at`` is fresher than the window
    are NOT surfaced (no false positives).
  * Synthetic rows have id prefixed ``vos:`` and ``synthetic=True``.
  * /api/admin/box/exceptions merges synthetic rows with canonical;
    box_type='ap_item' filter excludes onboarding rows.
  * Resolving a synthetic ``vos:*`` id returns 400 with a clear
    message (resolution must happen at the session, not the queue).
  * /api/admin/box/exceptions/stats reflects synthetic rows in
    by_severity and by_box_type counts.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.api import box_exceptions_admin  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import get_current_user  # noqa: E402
from clearledgr.core.vendor_onboarding_states import VendorOnboardingState  # noqa: E402
from clearledgr.services.vendor_onboarding_exceptions import (  # noqa: E402
    synthesize_onboarding_exceptions,
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
        app.include_router(box_exceptions_admin.router)
        app.dependency_overrides[get_current_user] = user_factory
        return TestClient(app)
    return _build


def _create_session(
    db,
    *,
    vendor_name: str,
    state: str,
    last_activity_at: str,
    organization_id: str = "default",
) -> str:
    """Create a vendor_onboarding_sessions row directly via SQL.

    The store has no public 'create with custom state + last_activity_at'
    method (production callers go through the state machine), so we
    write directly. The synthesizer reads the same columns.
    """
    import uuid
    sid = f"vos-{uuid.uuid4().hex[:12]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO vendor_onboarding_sessions "
            "(id, organization_id, vendor_name, state, "
            " is_active, invited_at, last_activity_at, "
            " invited_by, "
            " created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, 1, %s, %s, %s, %s, %s)",
            (
                sid, organization_id, vendor_name, state,
                last_activity_at, last_activity_at,
                "test-user",
                now_iso, now_iso,
            ),
        )
        conn.commit()
    return sid


# ─── Synthesizer ────────────────────────────────────────────────────


def test_synthesizer_returns_empty_for_quiet_org(db):
    out = synthesize_onboarding_exceptions(db, "default")
    assert out == []


def test_synthesizer_surfaces_blocked_session(db):
    sid = _create_session(
        db, vendor_name="Acme",
        state=VendorOnboardingState.BLOCKED.value,
        last_activity_at=datetime.now(timezone.utc).isoformat(),
    )
    out = synthesize_onboarding_exceptions(db, "default")
    assert len(out) == 1
    row = out[0]
    assert row["id"] == f"vos:{sid}"
    assert row["box_type"] == "vendor_onboarding_session"
    assert row["box_id"] == sid
    assert row["exception_type"] == "vendor_onboarding_blocked"
    assert row["severity"] == "high"
    assert row["synthetic"] is True
    assert "Acme" in row["reason"]
    assert "BLOCKED" in row["reason"]


def test_synthesizer_surfaces_stalled_invited(db):
    """Session in INVITED state with last_activity 72h ago → medium."""
    stale_ts = (
        datetime.now(timezone.utc) - timedelta(hours=72)
    ).isoformat()
    sid = _create_session(
        db, vendor_name="Globex",
        state=VendorOnboardingState.INVITED.value,
        last_activity_at=stale_ts,
    )
    out = synthesize_onboarding_exceptions(db, "default", stall_hours=48)
    assert len(out) == 1
    row = out[0]
    assert row["id"] == f"vos:{sid}"
    assert row["exception_type"] == "vendor_onboarding_stalled_invited"
    assert row["severity"] == "medium"
    assert "Globex" in row["reason"]
    # Hours-stuck phrasing is in the reason (~72 hours)
    assert "hour" in row["reason"]


def test_synthesizer_skips_fresh_sessions(db):
    """A session whose last_activity is recent must NOT surface as
    a stall — only stuck sessions show up."""
    fresh_ts = (
        datetime.now(timezone.utc) - timedelta(hours=1)
    ).isoformat()
    _create_session(
        db, vendor_name="FreshCo",
        state=VendorOnboardingState.KYC.value,
        last_activity_at=fresh_ts,
    )
    out = synthesize_onboarding_exceptions(db, "default", stall_hours=48)
    assert out == []


def test_synthesizer_filters_by_organization(db):
    """A blocked session in another tenant must not leak."""
    db.ensure_organization("other-tenant", organization_name="other-tenant")
    _create_session(
        db, vendor_name="Other",
        state=VendorOnboardingState.BLOCKED.value,
        last_activity_at=datetime.now(timezone.utc).isoformat(),
        organization_id="other-tenant",
    )
    out = synthesize_onboarding_exceptions(db, "default")
    assert out == []


def test_synthesizer_dedupes_overlapping_sessions(db):
    """Pulling PRE_ACTIVE + BLOCKED separately must not double-count
    a session that ends up in both lists (defensive against future
    state-set changes)."""
    sid = _create_session(
        db, vendor_name="Dup",
        state=VendorOnboardingState.BLOCKED.value,
        last_activity_at=datetime.now(timezone.utc).isoformat(),
    )
    out = synthesize_onboarding_exceptions(db, "default")
    matching = [r for r in out if r["box_id"] == sid]
    assert len(matching) == 1


# ─── HTTP integration ───────────────────────────────────────────────


def test_list_exceptions_merges_synthetic_rows(db, client_factory):
    sid = _create_session(
        db, vendor_name="StuckCo",
        state=VendorOnboardingState.BLOCKED.value,
        last_activity_at=datetime.now(timezone.utc).isoformat(),
    )
    client = client_factory()
    resp = client.get("/api/admin/box/exceptions")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = [item["id"] for item in body["items"]]
    assert f"vos:{sid}" in ids
    # Severity sort puts the high-severity blocked session near the top
    assert body["items"][0]["severity"] in ("critical", "high")


def test_list_exceptions_box_type_filter_includes_onboarding(db, client_factory):
    """Filtering by ``vendor_onboarding_session`` should still include
    synthetic rows — they are vendor_onboarding_session rows by
    definition."""
    sid = _create_session(
        db, vendor_name="FilteredCo",
        state=VendorOnboardingState.BLOCKED.value,
        last_activity_at=datetime.now(timezone.utc).isoformat(),
    )
    client = client_factory()
    resp = client.get(
        "/api/admin/box/exceptions?box_type=vendor_onboarding_session"
    )
    assert resp.status_code == 200
    ids = [item["id"] for item in resp.json()["items"]]
    assert f"vos:{sid}" in ids


def test_list_exceptions_box_type_filter_excludes_onboarding_for_ap_item(
    db, client_factory,
):
    """Filtering by ``ap_item`` must NOT pull in onboarding signals —
    the synthesizer's box_type doesn't match."""
    _create_session(
        db, vendor_name="ApOnly",
        state=VendorOnboardingState.BLOCKED.value,
        last_activity_at=datetime.now(timezone.utc).isoformat(),
    )
    client = client_factory()
    resp = client.get(
        "/api/admin/box/exceptions?box_type=ap_item"
    )
    assert resp.status_code == 200
    ids = [item["id"] for item in resp.json()["items"]]
    assert not any(i.startswith("vos:") for i in ids)


def test_resolve_synthetic_exception_returns_400(db, client_factory):
    sid = _create_session(
        db, vendor_name="ResolveBlocked",
        state=VendorOnboardingState.BLOCKED.value,
        last_activity_at=datetime.now(timezone.utc).isoformat(),
    )
    client = client_factory()
    resp = client.post(
        f"/api/admin/box/exceptions/vos:{sid}/resolve",
        json={"resolution_note": "doesn't matter"},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["reason"] == "synthetic_exception"
    assert detail["vendor_session_id"] == sid


def test_stats_reflect_synthetic_rows(db, client_factory):
    _create_session(
        db, vendor_name="StatsBlocked",
        state=VendorOnboardingState.BLOCKED.value,
        last_activity_at=datetime.now(timezone.utc).isoformat(),
    )
    client = client_factory()
    resp = client.get("/api/admin/box/exceptions/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_unresolved"] >= 1
    assert body["by_severity"]["high"] >= 1
    assert body["by_box_type"].get("vendor_onboarding_session", 0) >= 1
    assert body["by_type"].get("vendor_onboarding_blocked", 0) >= 1
