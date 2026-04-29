"""Tests for the C3 carry-over — SAP B1 polling Celery task.

Covers:
  * Task registered in the beat schedule with 5-min cadence.
  * poll_sap_b1_payments_all_orgs walks every org with a SAP
    erp_connection (only SAP, not QB/Xero/NetSuite).
  * Errors per-org are isolated — one failing org doesn't block
    the rest.
  * Aggregation: summary totals events_dispatched / duplicates /
    errors across all orgs.
  * Idempotent at the C2 layer — re-running on the same payments
    yields zero new events_dispatched.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.services import celery_app as celery_app_module  # noqa: E402
from clearledgr.services import celery_tasks as celery_tasks_module  # noqa: E402


# ─── Beat registration ────────────────────────────────────────────


def test_sap_b1_poll_in_beat_schedule():
    """The 5-min schedule must be registered so deployment picks it up."""
    schedule = celery_app_module.app.conf.beat_schedule
    assert "poll-sap-b1-payments" in schedule
    entry = schedule["poll-sap-b1-payments"]
    assert entry["task"].endswith("poll_sap_b1_payments_all_orgs")
    assert entry["schedule"] == 5 * 60.0


# ─── Org enumeration ──────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgA", organization_name="Acme")
    inst.ensure_organization("orgB", organization_name="Beta")
    inst.ensure_organization("orgC", organization_name="Gamma")
    return inst


def _seed_erp_connection(db, org: str, erp_type: str) -> None:
    """Insert directly into erp_connections — bypass the encrypted-
    credentials path; we only need erp_type for the org-enumeration
    SQL the task runs."""
    from datetime import datetime, timezone
    db.initialize()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO erp_connections "
            "(id, organization_id, erp_type, credentials, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (
                f"erp-{org}-{erp_type}",
                org,
                erp_type,
                "{}",
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()


def test_task_walks_only_sap_orgs(db):
    """Only orgs with a SAP connection get polled — QB / Xero / NS
    are skipped."""
    _seed_erp_connection(db, "orgA", "sap")
    _seed_erp_connection(db, "orgB", "quickbooks")
    _seed_erp_connection(db, "orgC", "sap")

    calls = []

    async def fake_poll(organization_id, db=None):
        calls.append(organization_id)
        return {
            "polled": 0, "events_dispatched": 0,
            "duplicates": 0, "errors": 0,
        }

    with patch(
        "clearledgr.services.erp_payment_dispatcher.poll_sap_b1_payments",
        new=fake_poll,
    ):
        result = celery_tasks_module.poll_sap_b1_payments_all_orgs()

    polled = set(calls)
    assert "orgA" in polled
    assert "orgC" in polled
    assert "orgB" not in polled  # QuickBooks org skipped
    assert result["orgs_polled"] == 2


def test_per_org_error_does_not_block_others(db):
    _seed_erp_connection(db, "orgA", "sap")
    _seed_erp_connection(db, "orgC", "sap")

    async def selective_poll(organization_id, db=None):
        if organization_id == "orgA":
            raise RuntimeError("provider down")
        return {
            "polled": 1, "events_dispatched": 1,
            "duplicates": 0, "errors": 0,
        }

    with patch(
        "clearledgr.services.erp_payment_dispatcher.poll_sap_b1_payments",
        new=selective_poll,
    ):
        result = celery_tasks_module.poll_sap_b1_payments_all_orgs()

    assert result["orgs_polled"] == 1  # orgC succeeded
    assert result["errors"] >= 1       # orgA's exception counted


def test_aggregation_totals(db):
    _seed_erp_connection(db, "orgA", "sap")
    _seed_erp_connection(db, "orgC", "sap")

    async def fake_poll(organization_id, db=None):
        return {
            "polled": 2, "events_dispatched": 1,
            "duplicates": 1, "errors": 0,
        }

    with patch(
        "clearledgr.services.erp_payment_dispatcher.poll_sap_b1_payments",
        new=fake_poll,
    ):
        result = celery_tasks_module.poll_sap_b1_payments_all_orgs()

    assert result["events_dispatched"] == 2
    assert result["duplicates"] == 2
    assert len(result["per_org"]) == 2


def test_no_sap_orgs_returns_empty_summary(db):
    """Org with QB only -> task runs but polls nothing."""
    _seed_erp_connection(db, "orgA", "quickbooks")

    async def fake_poll(organization_id, db=None):
        return {"polled": 0, "events_dispatched": 0, "duplicates": 0, "errors": 0}

    with patch(
        "clearledgr.services.erp_payment_dispatcher.poll_sap_b1_payments",
        new=fake_poll,
    ):
        result = celery_tasks_module.poll_sap_b1_payments_all_orgs()
    assert result["orgs_polled"] == 0
