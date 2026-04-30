import asyncio
import re

from clearledgr.integrations import erp_router
from clearledgr.integrations.erp_router import (
    ERPConnection,
    find_vendor_credit_quickbooks,
    find_credit_note_sap,
    find_credit_note_xero,
    find_credit_note_netsuite,
    find_vendor_netsuite,
    find_vendor_quickbooks,
    find_vendor_xero,
)


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


def test_quickbooks_vendor_credit_query_is_sanitized(monkeypatch):
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
            return _FakeResponse({"QueryResponse": {"VendorCredit": []}})

    monkeypatch.setattr(erp_router.httpx, "AsyncClient", _FakeAsyncClient)

    conn = ERPConnection(type="quickbooks", access_token="token-1", realm_id="realm-1")
    asyncio.run(find_vendor_credit_quickbooks(conn, "VC-1' OR 1=1 --"))

    query = captured["query"]
    assert query.startswith("SELECT Id, DocNumber, TotalAmt, Balance, VendorRef FROM VendorCredit WHERE DocNumber = '")
    assert query.count("'") == 2
    operand = re.search(r"DocNumber = '(.*)'", query)
    assert operand is not None
    assert "'" not in operand.group(1)
    assert "%" not in operand.group(1)
    assert "_" not in operand.group(1)


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


def test_netsuite_credit_note_query_is_sanitized(monkeypatch):
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
    asyncio.run(find_credit_note_netsuite(conn, "VC-1' OR 1=1 --"))

    query = captured["query"]
    assert "type = 'VendCred'" in query
    assert "FETCH FIRST 1 ROWS ONLY" in query
    operand = re.search(r"tranid = '(.*)' AND type = 'VendCred'", query)
    assert operand is not None
    assert "'" not in operand.group(1)
    assert "%" not in operand.group(1)
    assert "_" not in operand.group(1)


def test_sap_credit_note_filter_is_sanitized(monkeypatch):
    captured = {}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params=None, headers=None, timeout=None):
            captured["filter"] = str((params or {}).get("$filter") or "")
            return _FakeResponse({"value": []})

    monkeypatch.setattr(erp_router.httpx, "AsyncClient", _FakeAsyncClient)

    conn = ERPConnection(type="sap", access_token="session-token", base_url="https://sap.example.com/b1s/v2")
    asyncio.run(find_credit_note_sap(conn, "CN-SAP-1' or NumAtCard eq 'CN-SAP-2"))

    where_clause = captured["filter"]
    assert where_clause.startswith("NumAtCard eq '")
    assert "' or " not in where_clause
    assert "''" not in where_clause


def test_xero_vendor_name_where_clause_is_sanitized(monkeypatch):
    captured = {}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params=None, headers=None, timeout=None):
            captured["where"] = str((params or {}).get("where") or "")
            return _FakeResponse({"Contacts": []})

    monkeypatch.setattr(erp_router.httpx, "AsyncClient", _FakeAsyncClient)

    conn = ERPConnection(type="xero", access_token="token-1", tenant_id="tenant-1")
    asyncio.run(find_vendor_xero(conn, name='Acme" OR Name.Contains("x")', email=None))

    where_clause = captured["where"]
    assert where_clause.startswith("IsSupplier==true")
    assert "Name.Contains" in where_clause
    # Ensure unsanitized quote/control fragments are not present.
    assert '" OR ' not in where_clause
    assert "''" not in where_clause


def test_xero_credit_note_where_clause_is_sanitized(monkeypatch):
    captured = {}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params=None, headers=None, timeout=None):
            captured["where"] = str((params or {}).get("where") or "")
            return _FakeResponse({"CreditNotes": []})

    monkeypatch.setattr(erp_router.httpx, "AsyncClient", _FakeAsyncClient)

    conn = ERPConnection(type="xero", access_token="token-1", tenant_id="tenant-1")
    asyncio.run(find_credit_note_xero(conn, 'CN-1" OR Type=="ACCRECCREDIT'))

    where_clause = captured["where"]
    assert where_clause.startswith('Type=="ACCPAYCREDIT" AND CreditNoteNumber=="')
    assert '" OR ' not in where_clause
    assert "''" not in where_clause
