import asyncio

import pytest
from fastapi import HTTPException

from clearledgr.api.onboarding import ERPConnectionRequest, connect_sap


class _FakeSAPResponse:
    def __init__(self):
        self.cookies = {"B1SESSION": "sap-session-1"}

    def raise_for_status(self):
        return None


def test_connect_sap_uses_tls_verification_by_default(monkeypatch):
    captured = {}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            captured["verify"] = kwargs.get("verify")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None):
            captured["url"] = url
            return _FakeSAPResponse()

    monkeypatch.setenv("SAP_TLS_CA_BUNDLE_PATH", "")
    monkeypatch.setattr("httpx.AsyncClient", _FakeAsyncClient)

    request = ERPConnectionRequest(
        erp_type="sap",
        base_url="https://sap.example.com",
        username="sap-user",
        password="sap-secret",
    )

    connection = asyncio.run(connect_sap(request))
    assert connection is not None
    assert connection.type == "sap"
    assert captured.get("verify") is True


def test_connect_sap_uses_configured_ca_bundle(monkeypatch, tmp_path):
    captured = {}
    ca_bundle = tmp_path / "sap-ca.pem"
    ca_bundle.write_text("-----BEGIN CERTIFICATE-----\nTEST\n-----END CERTIFICATE-----\n", encoding="utf-8")

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            captured["verify"] = kwargs.get("verify")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None):
            return _FakeSAPResponse()

    monkeypatch.setenv("SAP_TLS_CA_BUNDLE_PATH", str(ca_bundle))
    monkeypatch.setattr("httpx.AsyncClient", _FakeAsyncClient)

    request = ERPConnectionRequest(
        erp_type="sap",
        base_url="https://sap.example.com",
        username="sap-user",
        password="sap-secret",
    )

    connection = asyncio.run(connect_sap(request))
    assert connection is not None
    assert captured.get("verify") == str(ca_bundle)


def test_connect_sap_rejects_missing_ca_bundle(monkeypatch):
    monkeypatch.setenv("SAP_TLS_CA_BUNDLE_PATH", "/tmp/does-not-exist-sap-ca.pem")
    request = ERPConnectionRequest(
        erp_type="sap",
        base_url="https://sap.example.com",
        username="sap-user",
        password="sap-secret",
    )

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(connect_sap(request))

    assert excinfo.value.status_code == 400
    assert "CA bundle path does not exist" in str(excinfo.value.detail)
