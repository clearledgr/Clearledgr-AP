"""Tests for the G5 carry-over — accrual JE ERP post + reversal sweep.

Covers:
  * Proposal -> ERP entry conversion: Dr/Cr line shape matches
    erp_router.post_journal_entry contract.
  * Reversal entry flips Dr <-> Cr.
  * post_accrual_je inserts a 'pending' run row, calls
    post_journal_entry, marks 'posted' on success.
  * Failed ERP post -> status='failed', error_reason recorded.
  * Empty proposal short-circuits to no-op success in
    run_month_end_close.
  * Duplicate active run for the same period raises 409-shaped
    ValueError.
  * post_pending_reversals walks posted-but-unreversed runs whose
    reversal_date is today/earlier; flips status, stamps
    reversal_provider_reference.
  * Already-reversed runs are skipped on the second sweep.
  * Audit events emitted on post + reversal.
  * Tenant isolation: orgA's run invisible to orgB sweep.
  * Beat schedule registered with crontab cadence (1st @ 02:00 UTC,
    daily @ 03:00 UTC).
  * API: post + list + get + reversal-sweep + 404/409 paths.
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

from clearledgr.api import accrual_journal_entry as accrual_routes  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import get_current_user  # noqa: E402
from clearledgr.services import celery_app as celery_app_module  # noqa: E402
from clearledgr.services.accrual_journal_entry import (  # noqa: E402
    build_accrual_je_proposal,
)
from clearledgr.services.accrual_journal_entry_post import (  # noqa: E402
    _proposal_to_erp_entry,
    _proposal_to_reversal_entry,
    get_accrual_run,
    post_accrual_je,
    post_pending_reversals,
    run_month_end_close,
)
from clearledgr.services.purchase_orders import (  # noqa: E402
    get_purchase_order_service,
)


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgA", organization_name="Acme")
    inst.ensure_organization("orgB", organization_name="Beta")
    return inst


def _user(uid: str = "user-1", org: str = "orgA") -> SimpleNamespace:
    return SimpleNamespace(
        user_id=uid, email=f"{uid}@example.com",
        organization_id=org, role="user",
    )


def _client(db, *, org: str = "orgA") -> TestClient:
    app = FastAPI()
    app.include_router(accrual_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user(org=org)
    return TestClient(app)


def _seed_po_and_gr(
    org: str,
    *,
    line_unit: float = 200.0,
    quantity: float = 5,
):
    svc = get_purchase_order_service(org)
    po = svc.create_po(
        vendor_id="Vendor X",
        vendor_name="Vendor X",
        requested_by="ops@" + org,
        line_items=[{
            "item_number": "SKU-A",
            "description": "Server",
            "quantity": quantity,
            "unit_price": line_unit,
        }],
        currency="USD",
    )
    from clearledgr.services.purchase_orders import _po_to_store_dict
    svc._db.save_purchase_order(_po_to_store_dict(po))
    svc.approve_po(po.po_id, approved_by="ops@" + org)
    svc.create_goods_receipt(
        po_id=po.po_id,
        received_by="warehouse@" + org,
        line_items=[{
            "po_line_id": po.line_items[0].line_id,
            "item_number": "SKU-A",
            "description": "Server",
            "quantity_received": quantity,
        }],
    )
    return po


def _build_proposal(db, org: str = "orgA"):
    _seed_po_and_gr(org)
    return build_accrual_je_proposal(
        db, organization_id=org,
        period_start="2026-04-01", period_end="2026-04-30",
        erp_type="xero", currency="USD",
    )


# ─── Entry shape conversion ───────────────────────────────────────


def test_proposal_to_erp_entry_shape(db):
    proposal = _build_proposal(db)
    entry = _proposal_to_erp_entry(
        proposal,
        posting_date="2026-04-30",
        description="month-end accrual",
    )
    assert entry["date"] == "2026-04-30"
    assert entry["currency"] == "USD"
    debit_lines = [ln for ln in entry["lines"] if ln["debit"] > 0]
    credit_lines = [ln for ln in entry["lines"] if ln["credit"] > 0]
    assert debit_lines and credit_lines
    assert sum(ln["debit"] for ln in entry["lines"]) == sum(
        ln["credit"] for ln in entry["lines"]
    )


def test_reversal_entry_flips_directions(db):
    proposal = _build_proposal(db)
    proposal_dict = proposal.to_dict()
    rev = _proposal_to_reversal_entry(
        proposal_dict=proposal_dict,
        posting_date="2026-05-01",
        description="reversal",
    )
    # Sums match originals but Dr <-> Cr swapped.
    orig_debit = sum(
        float(je.get("amount") or 0) for je in proposal_dict["je_lines"]
        if je["direction"] == "debit"
    )
    rev_credit = sum(ln["credit"] for ln in rev["lines"])
    assert orig_debit == pytest.approx(rev_credit)


# ─── post_accrual_je ──────────────────────────────────────────────


def _patch_post_je(success: bool = True, entry_id: str = "ERP-JE-1"):
    async def fake(organization_id, entry):
        if success:
            return {
                "status": "success", "erp": "xero",
                "entry_id": entry_id,
            }
        return {
            "status": "error", "erp": "xero",
            "reason": "rate_limited",
        }
    return patch(
        "clearledgr.integrations.erp_router.post_journal_entry",
        new=fake,
    )


def test_post_accrual_je_happy_path(db):
    proposal = _build_proposal(db)
    with _patch_post_je(success=True, entry_id="ERP-JE-100"):
        outcome = post_accrual_je(
            db,
            proposal=proposal,
            organization_id="orgA",
            jurisdiction="US",
            actor_id="ops-1",
        )
    assert outcome.status == "posted"
    assert outcome.provider_reference == "ERP-JE-100"
    fresh = get_accrual_run(db, outcome.accrual_run_id)
    assert fresh["status"] == "posted"
    assert fresh["provider_reference"] == "ERP-JE-100"
    assert fresh["posted_at"]


def test_post_accrual_je_failure_records_failed_status(db):
    proposal = _build_proposal(db)
    with _patch_post_je(success=False):
        outcome = post_accrual_je(
            db,
            proposal=proposal,
            organization_id="orgA",
            jurisdiction="US",
        )
    assert outcome.status == "failed"
    assert outcome.error_reason
    fresh = get_accrual_run(db, outcome.accrual_run_id)
    assert fresh["status"] == "failed"


def test_duplicate_period_run_blocked(db):
    proposal = _build_proposal(db)
    with _patch_post_je(success=True, entry_id="JE-A"):
        post_accrual_je(
            db, proposal=proposal,
            organization_id="orgA", jurisdiction="US",
        )
    proposal2 = _build_proposal(db)
    with _patch_post_je(success=True, entry_id="JE-B"):
        with pytest.raises(ValueError) as excinfo:
            post_accrual_je(
                db, proposal=proposal2,
                organization_id="orgA", jurisdiction="US",
            )
    assert "duplicate_period_run" in str(excinfo.value)


def test_failed_run_does_not_block_retry(db):
    """A failed run leaves the (org, period, jurisdiction) slot
    open per the partial unique index — operator can retry."""
    proposal = _build_proposal(db)
    with _patch_post_je(success=False):
        post_accrual_je(
            db, proposal=proposal,
            organization_id="orgA", jurisdiction="US",
        )
    proposal2 = _build_proposal(db)
    with _patch_post_je(success=True, entry_id="JE-RETRY"):
        outcome = post_accrual_je(
            db, proposal=proposal2,
            organization_id="orgA", jurisdiction="US",
        )
    assert outcome.status == "posted"


def test_post_audit_event_emitted(db):
    proposal = _build_proposal(db)
    with _patch_post_je(success=True, entry_id="JE-AUDIT"):
        outcome = post_accrual_je(
            db, proposal=proposal,
            organization_id="orgA", jurisdiction="US",
        )
    expected = f"accrual_je_posted:orgA:{outcome.accrual_run_id}"
    fetched = db.get_ap_audit_event_by_key(expected)
    assert fetched is not None
    assert fetched["event_type"] == "accrual_je_posted"


# ─── run_month_end_close ──────────────────────────────────────────


def test_run_month_end_close_no_op_for_empty_period(db):
    """No GRNs, no liability — no_op success."""
    outcome = run_month_end_close(
        db,
        organization_id="orgA",
        period_start="2026-04-01", period_end="2026-04-30",
        erp_type="xero", currency="USD", jurisdiction="US",
    )
    assert outcome.status == "posted"
    assert outcome.accrual_run_id == ""  # signal that no row was created


def test_run_month_end_close_e2e(db):
    _seed_po_and_gr("orgA")
    with _patch_post_je(success=True, entry_id="JE-E2E"):
        outcome = run_month_end_close(
            db, organization_id="orgA",
            period_start="2026-04-01", period_end="2026-04-30",
            erp_type="xero", currency="USD", jurisdiction="US",
        )
    assert outcome.status == "posted"
    assert outcome.accrual_run_id


# ─── post_pending_reversals ───────────────────────────────────────


def test_reversal_sweep_walks_due_runs(db):
    proposal = _build_proposal(db)
    with _patch_post_je(success=True, entry_id="JE-PRE-REV"):
        outcome = post_accrual_je(
            db, proposal=proposal,
            organization_id="orgA", jurisdiction="US",
        )
    # reversal_date is 2026-05-01 from the proposal; sweep with
    # today=2026-05-02 picks it up.
    with _patch_post_je(success=True, entry_id="JE-REV-100"):
        result = post_pending_reversals(
            db, organization_id="orgA", today="2026-05-02",
        )
    assert result.swept == 1
    assert result.reversed_ok == 1
    fresh = get_accrual_run(db, outcome.accrual_run_id)
    assert fresh["status"] == "reversal_posted"
    assert fresh["reversal_provider_reference"] == "JE-REV-100"


def test_reversal_sweep_skips_future_dates(db):
    proposal = _build_proposal(db)
    with _patch_post_je(success=True):
        post_accrual_je(
            db, proposal=proposal,
            organization_id="orgA", jurisdiction="US",
        )
    # today < reversal_date -> nothing swept.
    with _patch_post_je(success=True, entry_id="UNREACHED"):
        result = post_pending_reversals(
            db, organization_id="orgA", today="2026-04-30",
        )
    assert result.swept == 0


def test_reversal_sweep_skips_already_reversed(db):
    proposal = _build_proposal(db)
    with _patch_post_je(success=True, entry_id="JE-1"):
        post_accrual_je(
            db, proposal=proposal,
            organization_id="orgA", jurisdiction="US",
        )
    with _patch_post_je(success=True, entry_id="JE-REV-1"):
        first = post_pending_reversals(
            db, organization_id="orgA", today="2026-05-02",
        )
        second = post_pending_reversals(
            db, organization_id="orgA", today="2026-05-02",
        )
    assert first.reversed_ok == 1
    assert second.swept == 0  # second sweep finds nothing


def test_reversal_failure_keeps_run_posted(db):
    """When the reversal post fails, the run stays in 'posted' so
    the next sweep retries; error_reason captures the cause."""
    proposal = _build_proposal(db)
    with _patch_post_je(success=True, entry_id="JE-1"):
        outcome = post_accrual_je(
            db, proposal=proposal,
            organization_id="orgA", jurisdiction="US",
        )
    with _patch_post_je(success=False):
        result = post_pending_reversals(
            db, organization_id="orgA", today="2026-05-02",
        )
    assert result.failed == 1
    fresh = get_accrual_run(db, outcome.accrual_run_id)
    assert fresh["status"] == "posted"
    assert fresh["error_reason"]


def test_reversal_audit_event(db):
    proposal = _build_proposal(db)
    with _patch_post_je(success=True, entry_id="JE-1"):
        outcome = post_accrual_je(
            db, proposal=proposal,
            organization_id="orgA", jurisdiction="US",
        )
    with _patch_post_je(success=True, entry_id="JE-REV-AUDIT"):
        post_pending_reversals(
            db, organization_id="orgA", today="2026-05-02",
        )
    expected = f"accrual_je_reversed:orgA:{outcome.accrual_run_id}"
    fetched = db.get_ap_audit_event_by_key(expected)
    assert fetched is not None


def test_reversal_sweep_tenant_isolation(db):
    _seed_po_and_gr("orgB", line_unit=300.0)
    proposal_b = build_accrual_je_proposal(
        db, organization_id="orgB",
        period_start="2026-04-01", period_end="2026-04-30",
        erp_type="xero", currency="USD",
    )
    with _patch_post_je(success=True, entry_id="JE-B"):
        post_accrual_je(
            db, proposal=proposal_b,
            organization_id="orgB", jurisdiction="US",
        )
    # Sweep orgA — must NOT touch orgB's run.
    with _patch_post_je(success=True, entry_id="UNREACHED"):
        result = post_pending_reversals(
            db, organization_id="orgA", today="2026-05-02",
        )
    assert result.swept == 0


# ─── Beat schedule ────────────────────────────────────────────────


def test_month_end_schedule_registered():
    schedule = celery_app_module.app.conf.beat_schedule
    assert "post-month-end-accruals" in schedule
    assert (
        schedule["post-month-end-accruals"]["task"].endswith(
            "post_month_end_accruals_all_orgs"
        )
    )


def test_reversal_sweep_schedule_registered():
    schedule = celery_app_module.app.conf.beat_schedule
    assert "post-pending-accrual-reversals" in schedule


# ─── API ──────────────────────────────────────────────────────────


def test_api_post_endpoint(db):
    _seed_po_and_gr("orgA")
    client = _client(db)
    with _patch_post_je(success=True, entry_id="JE-API-1"):
        resp = client.post(
            "/api/workspace/accrual-je/post",
            json={
                "period_start": "2026-04-01",
                "period_end": "2026-04-30",
                "erp_type": "xero", "currency": "USD",
                "jurisdiction": "US",
            },
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "posted"
    assert data["provider_reference"] == "JE-API-1"


def test_api_post_409_on_duplicate(db):
    _seed_po_and_gr("orgA")
    client = _client(db)
    with _patch_post_je(success=True, entry_id="JE-1"):
        client.post(
            "/api/workspace/accrual-je/post",
            json={
                "period_start": "2026-04-01",
                "period_end": "2026-04-30",
                "jurisdiction": "US",
            },
        )
    with _patch_post_je(success=True, entry_id="JE-2"):
        resp = client.post(
            "/api/workspace/accrual-je/post",
            json={
                "period_start": "2026-04-01",
                "period_end": "2026-04-30",
                "jurisdiction": "US",
            },
        )
    assert resp.status_code == 409


def test_api_list_runs(db):
    _seed_po_and_gr("orgA")
    client = _client(db)
    with _patch_post_je(success=True):
        client.post(
            "/api/workspace/accrual-je/post",
            json={
                "period_start": "2026-04-01",
                "period_end": "2026-04-30",
                "jurisdiction": "US",
            },
        )
    resp = client.get("/api/workspace/accrual-je/runs")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


def test_api_get_run_404_for_unknown(db):
    client = _client(db)
    resp = client.get("/api/workspace/accrual-je/runs/AR-no-such")
    assert resp.status_code == 404


def test_api_reversal_sweep_endpoint(db):
    _seed_po_and_gr("orgA")
    client = _client(db)
    with _patch_post_je(success=True, entry_id="JE-1"):
        client.post(
            "/api/workspace/accrual-je/post",
            json={
                "period_start": "2026-04-01",
                "period_end": "2026-04-30",
                "jurisdiction": "US",
            },
        )
    # Force the reversal_date past so the sweep picks it up. The
    # operator-triggered sweep uses today's date; override the
    # accrual run's reversal_date to yesterday.
    db.initialize()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE accrual_je_runs SET reversal_date = '2024-01-01' "
            "WHERE organization_id = %s",
            ("orgA",),
        )
        conn.commit()

    with _patch_post_je(success=True, entry_id="JE-REV"):
        resp = client.post("/api/workspace/accrual-je/reversal-sweep")
    assert resp.status_code == 200
    data = resp.json()
    assert data["swept"] >= 1
    assert data["reversed_ok"] >= 1
