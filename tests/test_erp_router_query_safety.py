import asyncio
import re

from clearledgr.integrations import erp_router
from clearledgr.integrations.erp_router import ERPConnection, find_vendor_netsuite, find_vendor_quickbooks


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _extract_like_operand(query: str) -> str:
    match = re.search(r"LIKE\s+'%(.*)%'", query)
    return match.group(1) if match else ""


def _extract_netsuite_like_operand(query: str) -> str:
    match = re.search(r"companyName LIKE '%(.*)%'", query)
    return match.group(1) if match else ""


def _extract_netsuite_email_operand(query: str) -> str:
    match = re.search(r"email = '(.*)'", query)
    return match.group(1) if match else ""


def test_quickbooks_vendor_name_query_is_sanitized(monkeypatch):
    captured = {}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params=None, headers=None, timeout=None):
            captured["url"] = url
            captured["query"] = str((params or {}).get("query") or "")
            return _FakeResponse({"QueryResponse": {"Vendor": []}})

    monkeypatch.setattr(erp_router.httpx, "AsyncClient", _FakeAsyncClient)

    conn = ERPConnection(type="quickbooks", access_token="token-1", realm_id="realm-1")
    asyncio.run(find_vendor_quickbooks(conn, name="Acme' OR DisplayName LIKE '%", email=None))

    query = captured["query"]
    assert query.startswith("SELECT * FROM Vendor WHERE DisplayName LIKE '%")
    # Sanitized operands must not introduce extra quote delimiters.
    assert query.count("'") == 2
    operand = _extract_like_operand(query)
    assert "'" not in operand
    assert "%" not in operand
    assert "_" not in operand


def test_quickbooks_vendor_email_query_is_sanitized(monkeypatch):
    captured = {}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params=None, headers=None, timeout=None):
            captured["query"] = str((params or {}).get("query") or "")
            return _FakeResponse({"QueryResponse": {"Vendor": []}})

    monkeypatch.setattr(erp_router.httpx, "AsyncClient", _FakeAsyncClient)

    conn = ERPConnection(type="quickbooks", access_token="token-1", realm_id="realm-1")
    asyncio.run(find_vendor_quickbooks(conn, name=None, email="a%' OR 1=1 --@example.com"))

    query = captured["query"]
    assert query.startswith("SELECT * FROM Vendor WHERE PrimaryEmailAddr LIKE '%")
    assert query.count("'") == 2
    # Only wrapper wildcards are allowed.
    assert query.count("%") == 2
    operand = _extract_like_operand(query)
    assert "'" not in operand
    assert "%" not in operand
    assert "_" not in operand


def test_netsuite_vendor_name_query_is_sanitized(monkeypatch):
    captured = {}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None, timeout=None):
            captured["query"] = str((json or {}).get("q") or "")
            return _FakeResponse({"items": []})

    monkeypatch.setattr(erp_router.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(erp_router, "build_netsuite_oauth_header", lambda *args, **kwargs: "oauth")

    conn = ERPConnection(type="netsuite", account_id="12345")
    asyncio.run(find_vendor_netsuite(conn, name="Acme' OR 1=1 --", email=None))

    query = captured["query"]
    assert "SELECT id, companyName, email FROM vendor WHERE " in query
    assert "FETCH FIRST 1 ROWS ONLY" in query
    operand = _extract_netsuite_like_operand(query)
    assert operand
    assert "'" not in operand
    assert "%" not in operand
    assert "_" not in operand


def test_netsuite_vendor_email_query_is_sanitized(monkeypatch):
    captured = {}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None, timeout=None):
            captured["query"] = str((json or {}).get("q") or "")
            return _FakeResponse({"items": []})

    monkeypatch.setattr(erp_router.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(erp_router, "build_netsuite_oauth_header", lambda *args, **kwargs: "oauth")

    conn = ERPConnection(type="netsuite", account_id="12345")
    asyncio.run(find_vendor_netsuite(conn, name=None, email="a@example.com' OR 1=1 --"))

    query = captured["query"]
    operand = _extract_netsuite_email_operand(query)
    assert operand
    assert "'" not in operand
    assert "%" not in operand
    assert "_" not in operand
