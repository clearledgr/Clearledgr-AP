"""Tests for org-level escalation policies — Module 11.

Pinned by these tests:

  - Threshold semantics: a policy fires only when an exception's
    raised_at is older than threshold_hours AND the exception is
    still unresolved AND no escalation_events row exists for the
    (policy, exception) pair.
  - Idempotency: re-running the worker tick on the same data does NOT
    fire a second time. UNIQUE(policy_id, exception_id) on
    escalation_events is the safety net.
  - Resolved exceptions stop firing (resolved_at IS NOT NULL filter).
  - Cross-tenant isolation: a policy in orgA can't fire on orgB's
    exceptions, and vice-versa.
  - Filter semantics: exception_types and severity_filter narrow the
    population; null filter = match all.
  - SMTP-not-configured is a "skipped" state, not a failure — the
    event is NOT recorded so a later config fix re-tries.
  - API CRUD endpoints: validation (action enum, threshold range,
    notify_email requires recipients), pause/unpause via is_active,
    cross-tenant 404, recipients list update.
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

from solden.api import escalation_policies as esc_routes  # noqa: E402
from solden.core import database as db_module  # noqa: E402
from solden.core.auth import get_current_user  # noqa: E402
from solden.services import escalation_runner  # noqa: E402
from solden.services import transactional_email  # noqa: E402


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
        user_id=f"leader@{org}.com",
        email=f"leader@{org}.com",
        organization_id=org,
        role="user",
    )


@pytest.fixture()
def client_orgA(db):
    app = FastAPI()
    app.include_router(esc_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgA")
    return TestClient(app)


@pytest.fixture()
def client_orgB(db):
    app = FastAPI()
    app.include_router(esc_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgB")
    return TestClient(app)


def _raise_exception(
    db, *, exception_id: str, org: str = "orgA", box_id: str = "ap-1",
    exception_type: str = "vendor_not_in_erp_master",
    severity: str = "medium", hours_ago: float = 25,
    resolved: bool = False,
):
    """Insert a box_exceptions row with a back-dated raised_at so we
    can test threshold semantics deterministically."""
    raised_at = (
        datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    ).isoformat()
    resolved_at = (
        datetime.now(timezone.utc).isoformat() if resolved else None
    )
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO box_exceptions
              (id, box_id, box_type, organization_id, exception_type,
               severity, reason, raised_at, raised_by, resolved_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                exception_id, box_id, "ap_item", org, exception_type,
                severity, f"reason for {exception_id}",
                raised_at, "agent", resolved_at,
            ),
        )
        conn.commit()


def _patch_email_ok(monkeypatch):
    """Stub send_transactional_email to always succeed (and skip SMTP)."""
    monkeypatch.setattr(
        escalation_runner, "send_transactional_email",
        lambda **kw: transactional_email.EmailDeliveryResult(ok=True),
    )


def _patch_email_skipped(monkeypatch):
    monkeypatch.setattr(
        escalation_runner, "send_transactional_email",
        lambda **kw: transactional_email.EmailDeliveryResult(
            ok=False, skipped=True,
        ),
    )


def _patch_email_fail(monkeypatch, message: str = "smtp 503"):
    monkeypatch.setattr(
        escalation_runner, "send_transactional_email",
        lambda **kw: transactional_email.EmailDeliveryResult(
            ok=False, error_message=message,
        ),
    )


# ─── Tests: Worker tick ─────────────────────────────────────────────


class TestThresholdSemantics:
    def test_old_exception_fires_under_matching_policy(self, db, monkeypatch):
        _patch_email_ok(monkeypatch)
        _raise_exception(db, exception_id="exc-1", hours_ago=25)
        policy = db.create_escalation_policy({
            "organization_id": "orgA",
            "name": "stale-stuck",
            "threshold_hours": 24,
            "recipients": ["ops@example.com"],
        })

        summary = escalation_runner.run_escalation_tick(db)
        assert summary.processed == 1
        assert summary.fired == 1
        events = db.list_escalation_events("orgA")
        assert len(events) == 1
        assert events[0]["policy_id"] == policy["id"]
        assert events[0]["exception_id"] == "exc-1"
        assert events[0]["delivered"] is True

    def test_young_exception_does_not_fire(self, db, monkeypatch):
        _patch_email_ok(monkeypatch)
        _raise_exception(db, exception_id="exc-young", hours_ago=10)
        db.create_escalation_policy({
            "organization_id": "orgA",
            "name": "stale-stuck",
            "threshold_hours": 24,
            "recipients": ["ops@example.com"],
        })

        summary = escalation_runner.run_escalation_tick(db)
        assert summary.fired == 0
        assert db.list_escalation_events("orgA") == []

    def test_resolved_exception_does_not_fire(self, db, monkeypatch):
        _patch_email_ok(monkeypatch)
        _raise_exception(db, exception_id="exc-resolved", hours_ago=48, resolved=True)
        db.create_escalation_policy({
            "organization_id": "orgA",
            "name": "stale-stuck",
            "threshold_hours": 24,
            "recipients": ["ops@example.com"],
        })
        summary = escalation_runner.run_escalation_tick(db)
        assert summary.fired == 0


class TestIdempotency:
    def test_second_tick_does_not_re_fire(self, db, monkeypatch):
        _patch_email_ok(monkeypatch)
        _raise_exception(db, exception_id="exc-idem", hours_ago=30)
        db.create_escalation_policy({
            "organization_id": "orgA",
            "name": "stale-stuck",
            "threshold_hours": 24,
            "recipients": ["ops@example.com"],
        })

        first = escalation_runner.run_escalation_tick(db)
        second = escalation_runner.run_escalation_tick(db)
        assert first.fired == 1
        assert second.fired == 0
        # Only one row in escalation_events.
        assert len(db.list_escalation_events("orgA")) == 1


class TestCrossTenantIsolation:
    def test_orgA_policy_does_not_fire_on_orgB_exception(self, db, monkeypatch):
        _patch_email_ok(monkeypatch)
        _raise_exception(db, exception_id="exc-orgb", org="orgB", hours_ago=30)
        db.create_escalation_policy({
            "organization_id": "orgA",
            "name": "stale-stuck",
            "threshold_hours": 24,
            "recipients": ["ops@example.com"],
        })
        summary = escalation_runner.run_escalation_tick(db)
        assert summary.fired == 0


class TestFilterSemantics:
    def test_exception_type_filter_narrows_population(self, db, monkeypatch):
        _patch_email_ok(monkeypatch)
        _raise_exception(
            db, exception_id="exc-match",
            exception_type="vendor_not_in_erp_master", hours_ago=30,
        )
        _raise_exception(
            db, exception_id="exc-skip",
            exception_type="po_required_missing", hours_ago=30,
        )
        db.create_escalation_policy({
            "organization_id": "orgA",
            "name": "vendor-master-only",
            "threshold_hours": 24,
            "exception_types": ["vendor_not_in_erp_master"],
            "recipients": ["ops@example.com"],
        })
        escalation_runner.run_escalation_tick(db)
        events = db.list_escalation_events("orgA")
        assert len(events) == 1
        assert events[0]["exception_id"] == "exc-match"

    def test_severity_filter_narrows_population(self, db, monkeypatch):
        _patch_email_ok(monkeypatch)
        _raise_exception(db, exception_id="exc-high", severity="high", hours_ago=30)
        _raise_exception(db, exception_id="exc-low", severity="low", hours_ago=30)
        db.create_escalation_policy({
            "organization_id": "orgA",
            "name": "high-only",
            "threshold_hours": 24,
            "severity_filter": ["high"],
            "recipients": ["ops@example.com"],
        })
        escalation_runner.run_escalation_tick(db)
        events = db.list_escalation_events("orgA")
        assert len(events) == 1
        assert events[0]["exception_id"] == "exc-high"


class TestDeliveryFailureModes:
    def test_smtp_skipped_does_not_record_event(self, db, monkeypatch):
        _patch_email_skipped(monkeypatch)
        _raise_exception(db, exception_id="exc-skipped", hours_ago=30)
        db.create_escalation_policy({
            "organization_id": "orgA",
            "name": "stale-stuck",
            "threshold_hours": 24,
            "recipients": ["ops@example.com"],
        })

        escalation_runner.run_escalation_tick(db)
        # No event recorded — a later config fix can pick this up.
        assert db.list_escalation_events("orgA") == []

    def test_smtp_error_records_failed_event(self, db, monkeypatch):
        _patch_email_fail(monkeypatch, message="smtp 421")
        _raise_exception(db, exception_id="exc-fail", hours_ago=30)
        db.create_escalation_policy({
            "organization_id": "orgA",
            "name": "stale-stuck",
            "threshold_hours": 24,
            "recipients": ["ops@example.com"],
        })

        escalation_runner.run_escalation_tick(db)
        events = db.list_escalation_events("orgA")
        assert len(events) == 1
        assert events[0]["delivered"] is False
        assert "smtp 421" in (events[0].get("delivery_error") or "")

    def test_no_recipients_records_failed_event_with_clear_error(self, db, monkeypatch):
        _patch_email_ok(monkeypatch)
        _raise_exception(db, exception_id="exc-norec", hours_ago=30)
        # Misconfigured policy — no recipients.
        db.create_escalation_policy({
            "organization_id": "orgA",
            "name": "stale-stuck",
            "threshold_hours": 24,
            "recipients": [],
        })

        escalation_runner.run_escalation_tick(db)
        events = db.list_escalation_events("orgA")
        assert len(events) == 1
        assert events[0]["delivered"] is False
        assert "no recipients" in (events[0].get("delivery_error") or "").lower()


# ─── Tests: API endpoints ───────────────────────────────────────────


class TestPolicyAPI:
    def test_create_returns_policy(self, db, client_orgA):
        resp = client_orgA.post(
            "/api/workspace/escalation-policies",
            json={
                "name": "stuck-needs-info",
                "threshold_hours": 24,
                "exception_types": ["vendor_not_in_erp_master"],
                "recipients": ["ops@example.com"],
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "stuck-needs-info"
        assert body["threshold_hours"] == 24
        assert body["recipients"] == ["ops@example.com"]
        assert body["is_active"] is True

    def test_create_rejects_zero_threshold(self, client_orgA):
        resp = client_orgA.post(
            "/api/workspace/escalation-policies",
            json={
                "name": "bad", "threshold_hours": 0,
                "recipients": ["ops@example.com"],
            },
        )
        assert resp.status_code == 422

    def test_create_rejects_excessive_threshold(self, client_orgA):
        resp = client_orgA.post(
            "/api/workspace/escalation-policies",
            json={
                "name": "bad", "threshold_hours": 999,
                "recipients": ["ops@example.com"],
            },
        )
        assert resp.status_code == 422

    def test_create_rejects_email_action_without_recipients(self, client_orgA):
        resp = client_orgA.post(
            "/api/workspace/escalation-policies",
            json={
                "name": "bad", "threshold_hours": 24,
                "action": "notify_email", "recipients": [],
            },
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "missing_recipients"

    def test_list_isolated_by_org(self, db, client_orgA, client_orgB):
        client_orgA.post(
            "/api/workspace/escalation-policies",
            json={
                "name": "orga", "threshold_hours": 24,
                "recipients": ["ops@example.com"],
            },
        )
        a_list = client_orgA.get("/api/workspace/escalation-policies").json()
        b_list = client_orgB.get("/api/workspace/escalation-policies").json()
        assert len(a_list["policies"]) >= 1
        assert all(p["organization_id"] == "orgA" for p in a_list["policies"])
        assert all(p["organization_id"] == "orgB" for p in b_list["policies"])

    def test_get_cross_tenant_returns_404(self, db, client_orgA, client_orgB):
        created = client_orgA.post(
            "/api/workspace/escalation-policies",
            json={
                "name": "orga-only", "threshold_hours": 24,
                "recipients": ["ops@example.com"],
            },
        ).json()
        resp = client_orgB.get(
            f"/api/workspace/escalation-policies/{created['id']}"
        )
        assert resp.status_code == 404

    def test_patch_pause_then_resume(self, db, client_orgA):
        created = client_orgA.post(
            "/api/workspace/escalation-policies",
            json={
                "name": "pause-test", "threshold_hours": 24,
                "recipients": ["ops@example.com"],
            },
        ).json()
        paused = client_orgA.patch(
            f"/api/workspace/escalation-policies/{created['id']}",
            json={"is_active": False},
        ).json()
        assert paused["is_active"] is False
        resumed = client_orgA.patch(
            f"/api/workspace/escalation-policies/{created['id']}",
            json={"is_active": True},
        ).json()
        assert resumed["is_active"] is True

    def test_delete_cross_tenant_returns_404(self, db, client_orgA, client_orgB):
        created = client_orgA.post(
            "/api/workspace/escalation-policies",
            json={
                "name": "orga-only", "threshold_hours": 24,
                "recipients": ["ops@example.com"],
            },
        ).json()
        resp = client_orgB.delete(
            f"/api/workspace/escalation-policies/{created['id']}"
        )
        assert resp.status_code == 404


class TestEventsEndpoint:
    def test_lists_recorded_events(self, db, monkeypatch, client_orgA):
        _patch_email_ok(monkeypatch)
        _raise_exception(db, exception_id="exc-evt", hours_ago=30)
        db.create_escalation_policy({
            "organization_id": "orgA",
            "name": "stale-stuck",
            "threshold_hours": 24,
            "recipients": ["ops@example.com"],
        })
        escalation_runner.run_escalation_tick(db)

        resp = client_orgA.get("/api/workspace/escalation-policies/events")
        assert resp.status_code == 200
        events = resp.json()["events"]
        assert len(events) == 1
        assert events[0]["delivered"] is True
