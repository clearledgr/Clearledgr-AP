"""Tests for Module 4 Pass D — reverse vendor sync (Clearledgr → ERP).

Coverage:
  * push_vendor_to_erp returns ``failed:vendor_not_found`` when the
    profile doesn't exist.
  * Returns ``failed:no_erp_connection`` when the org has no ERP.
  * Returns ``no_erp_id`` when the profile lacks erp_vendor_id in
    metadata (operator must run forward sync first).
  * Returns ``not_supported`` for ERP types without an adapter
    (NetSuite + SAP today).
  * Returns ``no_change`` when no pushable fields differ.
  * QuickBooks adapter:
    - reads SyncToken via GET, posts sparse update via POST,
      returns True on 2xx.
    - returns False on the GET 4xx.
  * Xero adapter:
    - Posts to /Contacts with ContactID, returns True on 2xx.
  * Audit event ``vendor_erp_pushed`` recorded on every outcome
    (ok, no_change, failed) with status + fields_pushed.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.services import vendor_erp_push  # noqa: E402
from clearledgr.services.vendor_erp_push import (  # noqa: E402
    PushResult,
    _build_safe_payload,
    _resolve_erp_vendor_id,
    push_vendor_to_erp,
)


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("default", organization_name="default")
    return inst


def _stub_connection(erp_type: str, **extra):
    """Build a connection-like SimpleNamespace stub for adapter tests."""
    base = {
        "type": erp_type,
        "access_token": "tok",
        "realm_id": "realm-1",
        "tenant_id": "tenant-1",
        "refresh_token": "rt",
    }
    base.update(extra)
    return SimpleNamespace(**base)


# ─── Pure helpers ───────────────────────────────────────────────────


def test_resolve_erp_vendor_id_direct():
    profile = {"metadata": {"erp_vendor_id": "100"}}
    assert _resolve_erp_vendor_id(profile, "quickbooks") == "100"


def test_resolve_erp_vendor_id_per_erp_namespace():
    profile = {
        "metadata": {"erp_vendor_ids": {"quickbooks": "qb-1", "xero": "xr-2"}}
    }
    assert _resolve_erp_vendor_id(profile, "quickbooks") == "qb-1"
    assert _resolve_erp_vendor_id(profile, "xero") == "xr-2"
    assert _resolve_erp_vendor_id(profile, "netsuite") is None


def test_resolve_erp_vendor_id_no_metadata():
    assert _resolve_erp_vendor_id({}, "quickbooks") is None


def test_build_safe_payload_drops_empty_fields():
    profile = {
        "primary_contact_email": "a@b.test",
        "registered_address": None,
        "payment_terms": "Net 30",
        "status": "blocked",
        # Field outside the safe set → dropped
        "bank_account_number": "1234",
    }
    payload = _build_safe_payload(profile)
    assert payload == {
        "primary_contact_email": "a@b.test",
        "payment_terms": "Net 30",
        "status": "blocked",
    }


def test_build_safe_payload_drops_default_active_status():
    """status='active' is the table default and a no-op for the
    ERP — _build_safe_payload skips it so the push doesn't emit
    a fake update for vendors that haven't been touched."""
    profile = {
        "primary_contact_email": "a@b.test",
        "status": "active",
    }
    payload = _build_safe_payload(profile)
    assert "status" not in payload
    assert payload["primary_contact_email"] == "a@b.test"


# ─── push_vendor_to_erp dispatch ────────────────────────────────────


@pytest.mark.asyncio
async def test_push_vendor_returns_failed_when_profile_missing(db):
    out = await push_vendor_to_erp(
        organization_id="default",
        vendor_name="Phantom",
    )
    assert out.status == "failed"
    assert out.error == "vendor_not_found"


@pytest.mark.asyncio
async def test_push_vendor_returns_failed_when_no_erp_connection(db):
    db.upsert_vendor_profile(
        organization_id="default", vendor_name="Acme",
        primary_contact_email="ap@acme.test",
        metadata={"erp_vendor_id": "100"},
    )
    with patch(
        "clearledgr.integrations.erp_router.get_erp_connection",
        return_value=None,
    ):
        out = await push_vendor_to_erp(
            organization_id="default", vendor_name="Acme",
        )
    assert out.status == "failed"
    assert out.error == "no_erp_connection"


@pytest.mark.asyncio
async def test_push_vendor_no_erp_id_when_metadata_missing(db):
    db.upsert_vendor_profile(
        organization_id="default", vendor_name="Acme",
        primary_contact_email="ap@acme.test",
    )
    with patch(
        "clearledgr.integrations.erp_router.get_erp_connection",
        return_value=_stub_connection("quickbooks"),
    ):
        out = await push_vendor_to_erp(
            organization_id="default", vendor_name="Acme",
        )
    assert out.status == "no_erp_id"


@pytest.mark.asyncio
async def test_push_vendor_not_supported_for_netsuite(db):
    db.upsert_vendor_profile(
        organization_id="default", vendor_name="Acme",
        primary_contact_email="ap@acme.test",
        metadata={"erp_vendor_id": "100"},
    )
    with patch(
        "clearledgr.integrations.erp_router.get_erp_connection",
        return_value=_stub_connection("netsuite"),
    ):
        out = await push_vendor_to_erp(
            organization_id="default", vendor_name="Acme",
        )
    assert out.status == "not_supported"
    assert "netsuite" in (out.error or "").lower()


@pytest.mark.asyncio
async def test_push_vendor_no_change_when_payload_empty(db):
    """Profile with no pushable fields → no_change. Use a fresh
    vendor name so prior tests in this module don't leak fields
    via upsert."""
    db.upsert_vendor_profile(
        organization_id="default", vendor_name="EmptyPushFixture",
        metadata={"erp_vendor_id": "100"},
    )
    with patch(
        "clearledgr.integrations.erp_router.get_erp_connection",
        return_value=_stub_connection("quickbooks"),
    ):
        out = await push_vendor_to_erp(
            organization_id="default", vendor_name="EmptyPushFixture",
        )
    assert out.status == "no_change", f"got {out.status}: {out.error}"


# ─── QuickBooks adapter ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_quickbooks_adapter_reads_sync_token_then_posts(db):
    """The QBO adapter MUST read SyncToken before pushing — required
    by QBO's optimistic-concurrency contract."""
    db.upsert_vendor_profile(
        organization_id="default", vendor_name="QbAcme",
        primary_contact_email="ap@qb-acme.test",
        payment_terms="Net 30",
        metadata={"erp_vendor_id": "100"},
    )

    # Capture the requests
    captured = {}

    class _Resp:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self._payload = payload or {}
        def json(self):
            return self._payload
        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"http {self.status_code}")

    async def _fake_get(url, headers=None, timeout=None, **kwargs):
        captured["get_url"] = url
        return _Resp(200, {"Vendor": {"Id": "100", "SyncToken": "5"}})

    async def _fake_post(url, json=None, headers=None, timeout=None, **kwargs):
        captured["post_url"] = url
        captured["post_body"] = json
        return _Resp(200, {"Vendor": {"Id": "100", "SyncToken": "6"}})

    fake_client = MagicMock()
    fake_client.get = AsyncMock(side_effect=_fake_get)
    fake_client.post = AsyncMock(side_effect=_fake_post)

    with patch(
        "clearledgr.integrations.erp_router.get_erp_connection",
        return_value=_stub_connection("quickbooks"),
    ), patch(
        "clearledgr.core.http_client.get_http_client",
        return_value=fake_client,
    ):
        out = await push_vendor_to_erp(
            organization_id="default", vendor_name="QbAcme",
        )

    assert out.status == "ok", out.error
    # SyncToken from GET threaded into POST body
    assert captured["post_body"]["SyncToken"] == "5"
    assert captured["post_body"]["sparse"] is True
    # Email + payment terms landed
    assert captured["post_body"]["PrimaryEmailAddr"]["Address"] == "ap@qb-acme.test"
    assert captured["post_body"]["PrintOnCheckName"] == "Net 30"
    assert sorted(out.fields_pushed) == ["payment_terms", "primary_contact_email"]


@pytest.mark.asyncio
async def test_quickbooks_adapter_fails_on_get_4xx(db):
    """If the SyncToken read fails the push must fail closed."""
    db.upsert_vendor_profile(
        organization_id="default", vendor_name="Acme",
        primary_contact_email="ap@acme.test",
        metadata={"erp_vendor_id": "100"},
    )

    class _Resp:
        status_code = 404
        def json(self): return {}
        def raise_for_status(self): pass

    async def _fake_get(url, **kwargs):
        return _Resp()

    fake_client = MagicMock()
    fake_client.get = AsyncMock(side_effect=_fake_get)
    fake_client.post = AsyncMock()

    with patch(
        "clearledgr.integrations.erp_router.get_erp_connection",
        return_value=_stub_connection("quickbooks"),
    ), patch(
        "clearledgr.core.http_client.get_http_client",
        return_value=fake_client,
    ):
        out = await push_vendor_to_erp(
            organization_id="default", vendor_name="Acme",
        )

    assert out.status == "failed"
    # POST must NOT have been issued — GET returned 404 first.
    fake_client.post.assert_not_called()


# ─── Xero adapter ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_xero_adapter_posts_contact_with_id(db):
    db.upsert_vendor_profile(
        organization_id="default", vendor_name="Globex",
        primary_contact_email="ap@globex.test",
        registered_address="100 High St, London",
        vat_number="GB12345",
        metadata={"erp_vendor_ids": {"xero": "contact-99"}},
    )

    captured = {}

    class _Resp:
        status_code = 200
        def json(self): return {"Contacts": [{"ContactID": "contact-99"}]}
        def raise_for_status(self): pass

    async def _fake_post(url, json=None, headers=None, timeout=None, **kwargs):
        captured["post_url"] = url
        captured["post_body"] = json
        return _Resp()

    fake_client = MagicMock()
    fake_client.post = AsyncMock(side_effect=_fake_post)

    with patch(
        "clearledgr.integrations.erp_router.get_erp_connection",
        return_value=_stub_connection("xero"),
    ), patch(
        "clearledgr.core.http_client.get_http_client",
        return_value=fake_client,
    ):
        out = await push_vendor_to_erp(
            organization_id="default", vendor_name="Globex",
        )

    assert out.status == "ok", out.error
    assert "Contacts" in captured["post_body"]
    contact = captured["post_body"]["Contacts"][0]
    assert contact["ContactID"] == "contact-99"
    assert contact["EmailAddress"] == "ap@globex.test"
    assert contact["TaxNumber"] == "GB12345"


# ─── Audit emission ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_push_emits_audit_event_on_success(db):
    db.upsert_vendor_profile(
        organization_id="default", vendor_name="Acme",
        primary_contact_email="ap@acme.test",
        metadata={"erp_vendor_id": "100"},
    )

    class _Resp:
        def __init__(self, code, body=None):
            self.status_code = code
            self._body = body or {}
        def json(self): return self._body
        def raise_for_status(self): pass

    fake_client = MagicMock()
    fake_client.get = AsyncMock(return_value=_Resp(200, {"Vendor": {"SyncToken": "1"}}))
    fake_client.post = AsyncMock(return_value=_Resp(200, {}))

    with patch(
        "clearledgr.integrations.erp_router.get_erp_connection",
        return_value=_stub_connection("quickbooks"),
    ), patch(
        "clearledgr.core.http_client.get_http_client",
        return_value=fake_client,
    ):
        out = await push_vendor_to_erp(
            organization_id="default", vendor_name="Acme",
        )

    assert out.status == "ok"
    events = db.search_audit_events(
        organization_id="default",
        event_types=["vendor_erp_pushed"],
    )
    matching = [e for e in events.get("events", []) if e.get("box_id") == "Acme"]
    assert matching, "expected vendor_erp_pushed audit event"
    payload = matching[0].get("payload_json") or {}
    if isinstance(payload, str):
        import json
        payload = json.loads(payload)
    assert payload["status"] == "ok"
    assert payload["erp_type"] == "quickbooks"
    assert payload["erp_vendor_id"] == "100"
    assert "primary_contact_email" in payload["fields_pushed"]


@pytest.mark.asyncio
async def test_push_emits_audit_event_on_failure(db):
    db.upsert_vendor_profile(
        organization_id="default", vendor_name="Acme",
        primary_contact_email="ap@acme.test",
        metadata={"erp_vendor_id": "100"},
    )

    fake_client = MagicMock()

    class _Resp:
        status_code = 500
        def json(self): return {}
        def raise_for_status(self): raise RuntimeError("upstream broke")

    fake_client.get = AsyncMock(return_value=_Resp())
    fake_client.post = AsyncMock(return_value=_Resp())

    with patch(
        "clearledgr.integrations.erp_router.get_erp_connection",
        return_value=_stub_connection("quickbooks"),
    ), patch(
        "clearledgr.core.http_client.get_http_client",
        return_value=fake_client,
    ):
        out = await push_vendor_to_erp(
            organization_id="default", vendor_name="Acme",
        )

    assert out.status == "failed"
    events = db.search_audit_events(
        organization_id="default",
        event_types=["vendor_erp_pushed"],
    )
    matching = [e for e in events.get("events", []) if e.get("box_id") == "Acme"]
    assert matching, "expected vendor_erp_pushed audit event for failure"
    payload = matching[0].get("payload_json") or {}
    if isinstance(payload, str):
        import json
        payload = json.loads(payload)
    assert payload["status"] == "failed"
