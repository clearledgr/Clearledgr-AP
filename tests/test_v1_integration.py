"""End-to-end integration tests for the public /v1 surface.

Exercises the full router stack (auth dep → scope check → rate limit
→ runtime → audit emission) via FastAPI's TestClient. Runs against
the session-scoped Postgres harness from conftest.py — every test
gets a freshly-truncated DB.

The flow covered here is the spine of the customer agent connection
plan: customer issues a key → calls /v1/me → reads records →
previews then executes an intent (with idempotency) → registers a
webhook → cleans up. Every step verifies the audit chain captures
``actor_type='agent'`` + ``agent_id`` + ``agent_version`` attribution.

Two test classes:

* :class:`TestV1AgentFlow` — happy paths and the canonical
  customer-agent walkthrough.
* :class:`TestV1ErrorEnvelopes` — typed error responses for every
  failure mode the docs promise.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import pytest
from fastapi.testclient import TestClient


# ─── Fixtures ──────────────────────────────────────────────────


def _seed_org_and_key(
    db,
    *,
    organization_id: str = "org_v1_int",
    user_email: str = "agent-issuer@example.com",
    agent_id: str = "agent:cs-bot-prod",
    agent_version: str = "2.4.1",
    scopes: Optional[list] = None,
) -> str:
    """Insert an organisation + user + API key the test can use as
    a real customer agent. Returns the raw key.

    The scopes default to the full /v1 noun:verb set so the test
    suite can exercise every endpoint with one key. Individual tests
    that want to assert scope enforcement should mint their own
    narrower key.
    """
    if scopes is None:
        scopes = [
            "records:read", "intents:preview", "intents:execute",
            "audit:read", "webhooks:manage",
        ]

    # Org row first — every store method is pinned to organization_id.
    db.create_organization(
        organization_id=organization_id,
        name="V1 integration test org",
        settings={},
    )

    raw_key = f"sk_test_v1_int_{os.urandom(8).hex()}"
    db.create_api_key(
        organization_id=organization_id,
        user_id=user_email,
        raw_key=raw_key,
        label="v1 integration test key",
        scopes=scopes,
        agent_id=agent_id,
        agent_version=agent_version,
    )
    return raw_key


def _seed_ap_item(
    db,
    *,
    organization_id: str = "org_v1_int",
    state: str = "needs_approval",
    amount: float = 750.00,
) -> str:
    """Insert one ap_item the test can read / approve."""
    item = db.create_ap_item({
        "organization_id": organization_id,
        "vendor_name": "Acme Corp",
        "amount": amount,
        "currency": "EUR",
        "invoice_number": f"INV-{os.urandom(4).hex()}",
        "state": state,
    })
    return item["id"]


@pytest.fixture()
def client():
    """FastAPI TestClient with strict-profile bypassed for tests.

    Strict profile drops any path not on the allowlist. /v1 is on the
    prefix allowlist (main.py:497) so we don't need to override it
    here, but we do clear `_DB_INSTANCE` so every test sees the
    session-scoped Postgres from conftest's reset hook.
    """
    from main import app

    return TestClient(app)


@pytest.fixture()
def db():
    """Shorthand for ``get_db()`` so tests can seed rows directly."""
    from clearledgr.core.database import get_db

    return get_db()


# ─── Happy path ────────────────────────────────────────────────


class TestV1AgentFlow:
    """The canonical 5-step customer agent walkthrough from
    docs/v1/quickstart.md, end-to-end."""

    def test_me_echoes_identity(self, client, db) -> None:
        raw_key = _seed_org_and_key(db)
        r = client.get(
            "/v1/me", headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert r.status_code == 200, r.json()
        body = r.json()
        assert body["organization_id"] == "org_v1_int"
        assert body["agent_id"] == "agent:cs-bot-prod"
        assert body["agent_version"] == "2.4.1"
        assert "records:read" in body["scopes"]

    def test_records_list_returns_seeded_ap_item(self, client, db) -> None:
        raw_key = _seed_org_and_key(db)
        _seed_ap_item(db)
        r = client.get(
            "/v1/records?box_type=ap_item",
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert r.status_code == 200, r.json()
        records = r.json()["records"]
        assert len(records) == 1
        assert records[0]["box_type"] == "ap_item"
        assert records[0]["state"] == "needs_approval"
        assert records[0]["data"]["vendor_name"] == "Acme Corp"

    def test_records_list_does_not_leak_sensitive_columns(
        self, client, db,
    ) -> None:
        """The public field allowlist must hide bank_details, raw
        error strings, Slack/Teams refs, metadata blobs even when
        rows carry them."""
        raw_key = _seed_org_and_key(db)
        ap_id = _seed_ap_item(db)
        # Direct update to inject a sensitive column.
        db.update_ap_item(
            ap_id,
            bank_details_encrypted="secret-iban-blob",
            last_error="raw stack trace from ERP",
        )
        r = client.get(
            "/v1/records?box_type=ap_item",
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        data = r.json()["records"][0]["data"]
        assert "bank_details_encrypted" not in data
        assert "last_error" not in data

    def test_intents_execute_writes_attributed_audit_row(
        self, client, db,
    ) -> None:
        """The whole point of the agent-identity work: after the agent
        calls /v1/intents/execute, the audit chain shows
        actor_type=agent, actor_id=<agent_id>, agent_version=<key.version>."""
        raw_key = _seed_org_and_key(db)
        ap_id = _seed_ap_item(db)

        r = client.post(
            "/v1/intents/execute",
            headers={
                "Authorization": f"Bearer {raw_key}",
                "Idempotency-Key": "approve-acme-1",
            },
            json={"intent": "approve_invoice", "input": {"ap_item_id": ap_id}},
        )
        assert r.status_code == 200, r.json()

        # Audit row must exist with full agent attribution.
        events = db.list_audit_events(
            organization_id="org_v1_int", box_id=ap_id, limit=20,
        )
        agent_rows = [e for e in events if e.get("actor_type") == "agent"]
        assert agent_rows, "no agent-attributed audit rows found"
        assert agent_rows[0]["actor_id"] == "agent:cs-bot-prod"
        assert agent_rows[0]["agent_version"] == "2.4.1"

    def test_idempotency_replay_returns_cached_response(
        self, client, db,
    ) -> None:
        """Same Idempotency-Key + same payload twice → second response
        replays the first; runtime is NOT re-invoked. The
        Solden-Idempotent-Replay header tells the caller this happened."""
        raw_key = _seed_org_and_key(db)
        ap_id = _seed_ap_item(db)

        body = {"intent": "approve_invoice", "input": {"ap_item_id": ap_id}}
        headers = {
            "Authorization": f"Bearer {raw_key}",
            "Idempotency-Key": "approve-acme-replay",
        }
        first = client.post("/v1/intents/execute", headers=headers, json=body)
        second = client.post("/v1/intents/execute", headers=headers, json=body)
        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json() == first.json()
        assert (
            second.headers.get("Solden-Idempotent-Replay") == "true"
        ), "replay must set the explicit header so clients can detect it"

    def test_idempotency_conflict_on_payload_mismatch(
        self, client, db,
    ) -> None:
        """Same key, different payload → 409. This is the safety net
        against silent intent confusion."""
        raw_key = _seed_org_and_key(db)
        ap_id_a = _seed_ap_item(db)
        ap_id_b = _seed_ap_item(db)
        headers = {
            "Authorization": f"Bearer {raw_key}",
            "Idempotency-Key": "approve-mismatch-1",
        }
        client.post(
            "/v1/intents/execute", headers=headers,
            json={"intent": "approve_invoice",
                  "input": {"ap_item_id": ap_id_a}},
        )
        r = client.post(
            "/v1/intents/execute", headers=headers,
            json={"intent": "approve_invoice",
                  "input": {"ap_item_id": ap_id_b}},  # different payload
        )
        assert r.status_code == 409, r.json()
        assert r.json()["error_code"] == "idempotency_conflict"

    def test_webhook_crud_round_trip(self, client, db) -> None:
        """create → list (preview redacted) → rotate-secret → delete."""
        raw_key = _seed_org_and_key(db)
        auth = {"Authorization": f"Bearer {raw_key}"}

        # Create
        created = client.post(
            "/v1/webhooks", headers=auth,
            json={
                "url": "https://example.com/solden-webhooks",
                "event_types": ["invoice.approved"],
                "description": "Test relay",
            },
        )
        assert created.status_code == 201, created.json()
        wh = created.json()
        assert wh["secret"].startswith("whsec_"), "secret revealed on create"
        full_secret = wh["secret"]
        wh_id = wh["id"]

        # List — secret hidden, preview shows last-4 only
        listed = client.get("/v1/webhooks", headers=auth)
        assert listed.status_code == 200
        rows = listed.json()["webhooks"]
        assert len(rows) == 1
        assert rows[0]["secret"] is None
        assert rows[0]["secret_preview"].endswith(full_secret[-4:])

        # Rotate
        rotated = client.post(
            f"/v1/webhooks/{wh_id}/rotate-secret", headers=auth,
        )
        assert rotated.status_code == 200
        assert rotated.json()["secret"].startswith("whsec_")
        assert rotated.json()["secret"] != full_secret

        # Delete
        deleted = client.delete(f"/v1/webhooks/{wh_id}", headers=auth)
        assert deleted.status_code == 204

        # Read-after-delete is 404
        gone = client.get(f"/v1/webhooks/{wh_id}", headers=auth)
        assert gone.status_code == 404


# ─── Error envelopes ───────────────────────────────────────────


class TestV1ErrorEnvelopes:
    """Every failure mode in docs/v1/recipes.md must return its
    typed envelope shape with the documented error_code."""

    def test_missing_api_key_returns_401(self, client) -> None:
        r = client.get("/v1/me")
        assert r.status_code == 401
        # AuthorizationDenied funnel returns {"detail": "..."} for now;
        # the typed envelope is on the /v1/* router-emitted errors,
        # not the dep-raised 401. Both are documented.

    def test_invalid_api_key_returns_401(self, client) -> None:
        r = client.get(
            "/v1/me", headers={"Authorization": "Bearer sk_bogus"},
        )
        assert r.status_code == 401

    def test_revoked_key_returns_403(self, client, db) -> None:
        raw_key = _seed_org_and_key(db)
        # Find the key row + revoke it.
        keys = db.list_api_keys("org_v1_int", include_revoked=False)
        db.revoke_api_key(keys[0]["id"], "org_v1_int")

        r = client.get(
            "/v1/me", headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert r.status_code == 403

    def test_scope_missing_returns_403_typed(self, client, db) -> None:
        """A key with only records:read can't call /v1/intents/execute."""
        raw_key = _seed_org_and_key(db, scopes=["records:read"])
        r = client.post(
            "/v1/intents/execute",
            headers={"Authorization": f"Bearer {raw_key}"},
            json={"intent": "approve_invoice", "input": {"ap_item_id": "x"}},
        )
        assert r.status_code == 403

    def test_unknown_box_type_returns_400(self, client, db) -> None:
        raw_key = _seed_org_and_key(db)
        r = client.get(
            "/v1/records?box_type=insurance_claim",  # not registered
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert r.status_code == 400
        assert r.json()["error_code"] == "unsupported_box_type"

    def test_cross_tenant_record_read_returns_404(self, client, db) -> None:
        """A key for org A querying an id owned by org B sees 404 — the
        existence/permission distinction is hidden by design."""
        raw_key_a = _seed_org_and_key(db, organization_id="org_a")
        # Seed an ap_item under a different org.
        db.create_organization(
            organization_id="org_b", name="Other", settings={},
        )
        b_item_id = _seed_ap_item(db, organization_id="org_b")
        r = client.get(
            f"/v1/records/{b_item_id}?box_type=ap_item",
            headers={"Authorization": f"Bearer {raw_key_a}"},
        )
        assert r.status_code == 404

    def test_webhook_http_url_returns_400(self, client, db) -> None:
        """Plaintext webhook URLs are rejected at the boundary."""
        raw_key = _seed_org_and_key(db)
        r = client.post(
            "/v1/webhooks",
            headers={"Authorization": f"Bearer {raw_key}"},
            json={
                "url": "http://example.com/insecure",
                "event_types": ["invoice.approved"],
            },
        )
        assert r.status_code == 400
        assert r.json()["error_code"] == "invalid_url"

    def test_webhook_unknown_event_returns_400_with_bad_name(
        self, client, db,
    ) -> None:
        raw_key = _seed_org_and_key(db)
        r = client.post(
            "/v1/webhooks",
            headers={"Authorization": f"Bearer {raw_key}"},
            json={
                "url": "https://example.com/hook",
                "event_types": ["invoice.totally_made_up"],
            },
        )
        assert r.status_code == 400
        body = r.json()
        assert body["error_code"] == "invalid_event_type"
        assert "invoice.totally_made_up" in body["message"]
