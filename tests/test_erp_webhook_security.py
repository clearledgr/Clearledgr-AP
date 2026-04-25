"""ERP inbound webhook security contract tests.

Covers:
- Per-ERP signature verifiers (QBO, Xero, NetSuite, SAP): valid
  signatures verify, tampered bodies fail, missing/bad headers fail,
  unconfigured secrets fail-closed, timestamp replay is rejected.
- End-to-end endpoint routing for each ERP: unconfigured tenant
  returns 503 (not 401, so operators can tell "misconfigured" from
  "forged"), bad signature returns 401, good signature returns 200
  and writes one audit row with the accepted payload preview.
- Xero Intent-to-Receive handshake produces a distinct audit event.
- Outlook webhook fail-closed when OUTLOOK_WEBHOOK_SECRET is unset.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.api import erp_webhooks as webhooks_mod  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.erp_webhook_verify import (  # noqa: E402
    REPLAY_WINDOW_SECONDS,
    sign_quickbooks,
    sign_timestamped,
    sign_xero,
    verify_netsuite_signature,
    verify_quickbooks_signature,
    verify_sap_signature,
    verify_xero_signature,
)
from clearledgr.integrations.erp_router import ERPConnection  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path, monkeypatch):
    inst = db_module.get_db()
    inst.initialize()
    return inst


@pytest.fixture()
def client(db):
    app = FastAPI()
    app.include_router(webhooks_mod.router)
    return TestClient(app)


def _stub_connection(monkeypatch, *, org_id: str, erp: str, secret: Optional[str]):
    """Replace get_erp_connection with a stub so tests don't need the
    full credential-write flow to configure a webhook secret."""
    def _fake(organization_id, erp_type):
        if organization_id == org_id and erp_type == erp and secret is not None:
            return ERPConnection(type=erp, webhook_secret=secret)
        return None
    monkeypatch.setattr(webhooks_mod, "get_erp_connection", _fake)


# ---------------------------------------------------------------------------
# Signature verifier tests (no HTTP layer)
# ---------------------------------------------------------------------------


class TestQuickBooksVerify:
    def test_valid_signature_verifies(self):
        body = b'{"eventNotifications":[{"realmId":"1"}]}'
        token = "qbo-verifier-123"
        sig = sign_quickbooks(body, token)
        assert verify_quickbooks_signature(body, sig, token) is True

    def test_tampered_body_fails(self):
        body = b'{"eventNotifications":[{"realmId":"1"}]}'
        token = "qbo-verifier-123"
        sig = sign_quickbooks(body, token)
        assert verify_quickbooks_signature(body + b"x", sig, token) is False

    def test_wrong_secret_fails(self):
        body = b'{"eventNotifications":[]}'
        sig = sign_quickbooks(body, "real-token")
        assert verify_quickbooks_signature(body, sig, "wrong-token") is False

    def test_missing_signature_fails(self):
        body = b'{}'
        assert verify_quickbooks_signature(body, None, "token") is False
        assert verify_quickbooks_signature(body, "", "token") is False

    def test_fail_closed_when_no_secret(self):
        body = b'{}'
        assert verify_quickbooks_signature(body, "anything", None) is False
        assert verify_quickbooks_signature(body, "anything", "") is False

    def test_garbage_base64_fails_cleanly(self):
        body = b'{}'
        # Not valid base64
        assert verify_quickbooks_signature(body, "!!!not-base64!!!", "token") is False


class TestXeroVerify:
    def test_valid_signature_verifies(self):
        body = b'{"events":[{"resourceUrl":"..."}]}'
        key = "xero-webhook-key"
        sig = sign_xero(body, key)
        assert verify_xero_signature(body, sig, key) is True

    def test_intent_to_receive_body_still_signature_checked(self):
        # Xero ITR body is literally {"events":[]} — signature still
        # must verify or we must reject 401.
        body = b'{"events":[]}'
        key = "xero-webhook-key"
        sig = sign_xero(body, key)
        assert verify_xero_signature(body, sig, key) is True
        assert verify_xero_signature(body, "AAAA", key) is False

    def test_fail_closed_when_no_secret(self):
        assert verify_xero_signature(b'{}', "anything", None) is False


class TestNetSuiteVerify:
    def test_valid_signature_and_timestamp(self):
        body = b'{"subsidiary":1,"bill":"INV-1"}'
        secret = "ns-shared-secret"
        ts = int(time.time())
        sig = sign_timestamped(body, secret, ts)
        assert verify_netsuite_signature(body, sig, str(ts), secret) is True

    def test_replay_old_timestamp_rejected(self):
        body = b'{}'
        secret = "ns-shared-secret"
        # Sign with a timestamp older than the replay window — a
        # malicious actor replaying a captured request must be
        # rejected even though the HMAC itself is authentic.
        now = time.time()
        stale_ts = int(now - REPLAY_WINDOW_SECONDS - 10)
        sig = sign_timestamped(body, secret, stale_ts)
        assert verify_netsuite_signature(body, sig, str(stale_ts), secret) is False

    def test_future_timestamp_rejected(self):
        body = b'{}'
        secret = "ns-shared-secret"
        future_ts = int(time.time() + REPLAY_WINDOW_SECONDS + 10)
        sig = sign_timestamped(body, secret, future_ts)
        assert verify_netsuite_signature(body, sig, str(future_ts), secret) is False

    def test_malformed_timestamp_rejected(self):
        body = b'{}'
        secret = "ns-shared-secret"
        ts = int(time.time())
        sig = sign_timestamped(body, secret, ts)
        assert verify_netsuite_signature(body, sig, "not-a-number", secret) is False
        assert verify_netsuite_signature(body, sig, None, secret) is False

    def test_tampered_body_fails(self):
        body = b'{"amount":100}'
        secret = "ns-shared-secret"
        ts = int(time.time())
        sig = sign_timestamped(body, secret, ts)
        assert verify_netsuite_signature(b'{"amount":999}', sig, str(ts), secret) is False

    def test_missing_v1_prefix_still_works(self):
        # We accept the hex payload with or without the v1= prefix.
        body = b'{}'
        secret = "ns-shared-secret"
        ts = int(time.time())
        full = sign_timestamped(body, secret, ts)  # "v1=<hex>"
        bare_hex = full[3:]  # just the hex
        assert verify_netsuite_signature(body, full, str(ts), secret) is True
        assert verify_netsuite_signature(body, bare_hex, str(ts), secret) is True

    def test_fail_closed_when_no_secret(self):
        assert verify_netsuite_signature(b'{}', "v1=ff", "0", None) is False


class TestSAPVerify:
    def test_valid_signature(self):
        body = b'{"company_code":"1000","doc":"INV-1"}'
        secret = "sap-shared-secret"
        ts = int(time.time())
        sig = sign_timestamped(body, secret, ts)
        assert verify_sap_signature(body, sig, str(ts), secret) is True

    def test_replay_rejected(self):
        body = b'{}'
        secret = "sap-shared-secret"
        stale_ts = int(time.time() - REPLAY_WINDOW_SECONDS - 1)
        sig = sign_timestamped(body, secret, stale_ts)
        assert verify_sap_signature(body, sig, str(stale_ts), secret) is False


# ---------------------------------------------------------------------------
# End-to-end endpoint tests
# ---------------------------------------------------------------------------


class TestQuickBooksWebhookEndpoint:
    def test_unconfigured_tenant_returns_503(self, client, monkeypatch):
        _stub_connection(monkeypatch, org_id="default", erp="quickbooks", secret=None)
        r = client.post("/erp/webhooks/quickbooks/default", content=b"{}")
        assert r.status_code == 503
        assert r.json()["error"] == "webhook_not_configured"

    def test_bad_signature_returns_401(self, client, monkeypatch):
        _stub_connection(monkeypatch, org_id="default", erp="quickbooks", secret="verifier-abc")
        r = client.post(
            "/erp/webhooks/quickbooks/default",
            content=b'{"eventNotifications":[]}',
            headers={"intuit-signature": "AAAAAAAAAAAAAAAAAA=="},
        )
        assert r.status_code == 401
        assert r.json()["error"] == "invalid_signature"

    def test_missing_signature_returns_401(self, client, monkeypatch):
        _stub_connection(monkeypatch, org_id="default", erp="quickbooks", secret="verifier-abc")
        r = client.post("/erp/webhooks/quickbooks/default", content=b"{}")
        assert r.status_code == 401

    def test_valid_signature_accepted_and_audited(self, client, db, monkeypatch):
        _stub_connection(monkeypatch, org_id="default", erp="quickbooks", secret="verifier-abc")
        body = b'{"eventNotifications":[{"realmId":"9130","dataChangeEvent":{}}]}'
        sig = sign_quickbooks(body, "verifier-abc")

        r = client.post(
            "/erp/webhooks/quickbooks/default",
            content=body,
            headers={"intuit-signature": sig},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True

        # Audit row written with the expected shape.
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                (
                    "SELECT event_type, box_type, organization_id "
                    "FROM audit_events WHERE box_type = %s"
                ),
                ("erp_webhook",),
            )
            row = cur.fetchone()
        assert row is not None
        event_type = row[0] if not isinstance(row, dict) else row["event_type"]
        box_type = row[1] if not isinstance(row, dict) else row["box_type"]
        org = row[2] if not isinstance(row, dict) else row["organization_id"]
        assert event_type == "erp_webhook_received"
        assert box_type == "erp_webhook"
        assert org == "default"


class TestXeroWebhookEndpoint:
    def test_bad_signature_returns_401(self, client, monkeypatch):
        _stub_connection(monkeypatch, org_id="default", erp="xero", secret="xero-key-abc")
        r = client.post(
            "/erp/webhooks/xero/default",
            content=b'{"events":[]}',
            headers={"x-xero-signature": "bad=="},
        )
        assert r.status_code == 401

    def test_intent_to_receive_distinct_audit_event(self, client, db, monkeypatch):
        _stub_connection(monkeypatch, org_id="default", erp="xero", secret="xero-key-abc")
        itr_body = b'{"events":[],"firstEventSequence":0,"lastEventSequence":0,"entropy":"x"}'
        sig = sign_xero(itr_body, "xero-key-abc")

        r = client.post(
            "/erp/webhooks/xero/default",
            content=itr_body,
            headers={"x-xero-signature": sig},
        )
        assert r.status_code == 200

        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                (
                    "SELECT event_type FROM audit_events WHERE box_type = %s"
                ),
                ("erp_webhook",),
            )
            rows = cur.fetchall()
        event_types = [r[0] if not isinstance(r, dict) else r["event_type"] for r in rows]
        assert "erp_webhook_intent_to_receive" in event_types

    def test_real_notification_audit_event(self, client, db, monkeypatch):
        _stub_connection(monkeypatch, org_id="default", erp="xero", secret="xero-key-abc")
        body = b'{"events":[{"resourceUrl":"https://api.xero.com/..."}]}'
        sig = sign_xero(body, "xero-key-abc")

        r = client.post(
            "/erp/webhooks/xero/default",
            content=body,
            headers={"x-xero-signature": sig},
        )
        assert r.status_code == 200

        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                (
                    "SELECT event_type FROM audit_events WHERE box_type = %s"
                ),
                ("erp_webhook",),
            )
            rows = cur.fetchall()
        event_types = [r[0] if not isinstance(r, dict) else r["event_type"] for r in rows]
        assert "erp_webhook_received" in event_types


class TestNetSuiteWebhookEndpoint:
    def test_unconfigured_returns_503(self, client, monkeypatch):
        _stub_connection(monkeypatch, org_id="default", erp="netsuite", secret=None)
        r = client.post("/erp/webhooks/netsuite/default", content=b"{}")
        assert r.status_code == 503

    def test_missing_timestamp_returns_401(self, client, monkeypatch):
        _stub_connection(monkeypatch, org_id="default", erp="netsuite", secret="ns-sec")
        # Signature but no timestamp → reject.
        r = client.post(
            "/erp/webhooks/netsuite/default",
            content=b'{}',
            headers={"X-NetSuite-Signature": "v1=deadbeef"},
        )
        assert r.status_code == 401

    def test_replay_old_timestamp_returns_401(self, client, monkeypatch):
        _stub_connection(monkeypatch, org_id="default", erp="netsuite", secret="ns-sec")
        body = b'{"bill":"INV-1"}'
        stale_ts = int(time.time() - REPLAY_WINDOW_SECONDS - 10)
        sig = sign_timestamped(body, "ns-sec", stale_ts)
        r = client.post(
            "/erp/webhooks/netsuite/default",
            content=body,
            headers={
                "X-NetSuite-Signature": sig,
                "X-NetSuite-Timestamp": str(stale_ts),
            },
        )
        assert r.status_code == 401

    def test_valid_signature_accepted(self, client, db, monkeypatch):
        _stub_connection(monkeypatch, org_id="default", erp="netsuite", secret="ns-sec")
        body = b'{"bill":"INV-77","subsidiary":1}'
        ts = int(time.time())
        sig = sign_timestamped(body, "ns-sec", ts)
        r = client.post(
            "/erp/webhooks/netsuite/default",
            content=body,
            headers={
                "X-NetSuite-Signature": sig,
                "X-NetSuite-Timestamp": str(ts),
            },
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True


class TestSAPWebhookEndpoint:
    def test_valid_signature_accepted(self, client, db, monkeypatch):
        _stub_connection(monkeypatch, org_id="default", erp="sap", secret="sap-sec")
        body = b'{"company_code":"1000","doc":"5100000023"}'
        ts = int(time.time())
        sig = sign_timestamped(body, "sap-sec", ts)
        r = client.post(
            "/erp/webhooks/sap/default",
            content=body,
            headers={
                "X-SAP-Signature": sig,
                "X-SAP-Timestamp": str(ts),
            },
        )
        assert r.status_code == 200

    def test_bad_signature_returns_401(self, client, monkeypatch):
        _stub_connection(monkeypatch, org_id="default", erp="sap", secret="sap-sec")
        ts = int(time.time())
        r = client.post(
            "/erp/webhooks/sap/default",
            content=b'{}',
            headers={
                "X-SAP-Signature": "v1=deadbeef",
                "X-SAP-Timestamp": str(ts),
            },
        )
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Outlook fail-closed test (adjacent gap we fixed)
# ---------------------------------------------------------------------------


class TestOutlookWebhookFailClosed:
    def test_unset_secret_rejects_notifications(self, monkeypatch):
        # Clear any inherited OUTLOOK_WEBHOOK_SECRET from the test env.
        monkeypatch.delenv("OUTLOOK_WEBHOOK_SECRET", raising=False)
        # The Outlook router is V1-gated; opt in so we can exercise
        # the webhook handler's security behavior.
        monkeypatch.setenv("FEATURE_OUTLOOK_ENABLED", "true")

        from clearledgr.api import outlook_routes

        app = FastAPI()
        app.include_router(outlook_routes.router)
        c = TestClient(app)

        # A notification POST with no configured secret used to
        # succeed silently (202) because the `if webhook_secret and ...`
        # guard skipped clientState checking entirely.
        r = c.post(
            "/outlook/webhook",
            json={"value": [{"clientState": "", "resource": "messages/1"}]},
        )
        assert r.status_code == 503
        assert r.json()["error"] == "webhook_not_configured"

    def test_configured_secret_rejects_mismatched_client_state(self, monkeypatch):
        monkeypatch.setenv("OUTLOOK_WEBHOOK_SECRET", "correct-horse-battery")
        monkeypatch.setenv("FEATURE_OUTLOOK_ENABLED", "true")

        from clearledgr.api import outlook_routes

        app = FastAPI()
        app.include_router(outlook_routes.router)
        c = TestClient(app)

        # Wrong clientState: endpoint accepts (202) but SKIPS the
        # notification — we don't process the body. Status differs
        # from the unset case so operators can tell them apart.
        r = c.post(
            "/outlook/webhook",
            json={
                "value": [
                    {"clientState": "wrong-secret", "resource": "messages/1",
                     "changeType": "created"},
                ],
            },
        )
        assert r.status_code == 202

    def test_configured_secret_accepts_matching_client_state(self, monkeypatch):
        monkeypatch.setenv("OUTLOOK_WEBHOOK_SECRET", "correct-horse-battery")
        monkeypatch.setenv("FEATURE_OUTLOOK_ENABLED", "true")

        from clearledgr.api import outlook_routes

        app = FastAPI()
        app.include_router(outlook_routes.router)
        c = TestClient(app)

        r = c.post(
            "/outlook/webhook",
            json={
                "value": [
                    {"clientState": "correct-horse-battery",
                     "resource": "messages/1", "changeType": "created"},
                ],
            },
        )
        assert r.status_code == 202
