"""
ERP Sanitization Helpers

Shared input sanitization and query-building utilities used across all ERP
connectors. Keeps injection-prevention logic in a single place.
"""

import re
from typing import Dict, List, Optional


_QB_QUERY_VALUE_ALLOWED_CHARS = re.compile(r"[^A-Za-z0-9@._\-\s]")
_NS_LIKE_VALUE_ALLOWED_CHARS = re.compile(r"[^A-Za-z0-9@._\-\s]")
_NS_EMAIL_VALUE_ALLOWED_CHARS = re.compile(r"[^A-Za-z0-9@._\-\+]")
_XERO_WHERE_VALUE_ALLOWED_CHARS = re.compile(r"[^A-Za-z0-9@._\-\s]")
_SAP_ODATA_VALUE_ALLOWED_CHARS = re.compile(r"[^A-Za-z0-9@._\-\s]")


def _sanitize_quickbooks_like_operand(value: Optional[str]) -> Optional[str]:
    """Return a safe LIKE operand for QuickBooks query strings.

    QuickBooks query API does not support parameter binding. To prevent query
    manipulation, we apply strict allowlist sanitization and remove wildcard
    operators from user-provided values.
    """
    text = str(value or "").strip()
    if not text:
        return None
    sanitized = _QB_QUERY_VALUE_ALLOWED_CHARS.sub(" ", text)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    if not sanitized:
        return None
    return sanitized[:120]


def _sanitize_netsuite_like_operand(value: Optional[str]) -> Optional[str]:
    """Return a safe SuiteQL LIKE operand for NetSuite vendor search."""
    text = str(value or "").strip()
    if not text:
        return None
    sanitized = _NS_LIKE_VALUE_ALLOWED_CHARS.sub(" ", text)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    if not sanitized:
        return None
    return sanitized[:120]


def _sanitize_netsuite_email_operand(value: Optional[str]) -> Optional[str]:
    """Return a safe SuiteQL equality operand for NetSuite email search."""
    text = str(value or "").strip()
    if not text:
        return None
    sanitized = _NS_EMAIL_VALUE_ALLOWED_CHARS.sub("", text)
    sanitized = sanitized.strip()
    if not sanitized:
        return None
    return sanitized[:160]


def _sanitize_xero_where_operand(value: Optional[str]) -> Optional[str]:
    """Return a safe operand for Xero where-clause Name.Contains filter."""
    text = str(value or "").strip()
    if not text:
        return None
    sanitized = _XERO_WHERE_VALUE_ALLOWED_CHARS.sub(" ", text)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    if not sanitized:
        return None
    return sanitized[:120]


def _sanitize_odata_value(value: Optional[str]) -> str:
    """Return a safe OData filter operand for SAP Business Partner search.

    Prevents OData filter injection by stripping non-alphanumeric characters
    and escaping single quotes.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    sanitized = _SAP_ODATA_VALUE_ALLOWED_CHARS.sub(" ", text)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    # OData single-quote escape: ' -> ''
    sanitized = sanitized.replace("'", "''")
    return sanitized[:120]


def _escape_query_literal(value: str) -> str:
    """Escape single quotes for query syntaxes that require inline literals."""
    return str(value).replace("'", "''")


def _build_quickbooks_vendor_lookup_query(
    *,
    name_operand: Optional[str],
    email_operand: Optional[str],
) -> Optional[str]:
    if name_operand:
        literal = _escape_query_literal(name_operand)
        return f"SELECT * FROM Vendor WHERE DisplayName LIKE '%{literal}%'"
    if email_operand:
        literal = _escape_query_literal(email_operand)
        return f"SELECT * FROM Vendor WHERE PrimaryEmailAddr LIKE '%{literal}%'"
    return None


def _build_quickbooks_vendor_credit_lookup_query(*, credit_note_operand: Optional[str]) -> Optional[str]:
    if not credit_note_operand:
        return None
    literal = _escape_query_literal(credit_note_operand)
    return (
        "SELECT Id, DocNumber, TotalAmt, Balance, VendorRef FROM VendorCredit "
        f"WHERE DocNumber = '{literal}'"
    )


def _build_netsuite_vendor_lookup_query(
    *,
    name_operand: Optional[str],
    email_operand: Optional[str],
) -> Optional[str]:
    conditions: List[str] = []
    if name_operand:
        literal = _escape_query_literal(name_operand)
        conditions.append(f"companyName LIKE '%{literal}%'")
    if email_operand:
        literal = _escape_query_literal(email_operand)
        conditions.append(f"email = '{literal}'")
    if not conditions:
        return None
    return f"SELECT id, companyName, email FROM vendor WHERE {' OR '.join(conditions)} FETCH FIRST 1 ROWS ONLY"


def _build_xero_vendor_lookup_where(*, name_operand: Optional[str]) -> str:
    where = "IsSupplier==true"
    if name_operand:
        literal = _escape_query_literal(name_operand)
        where += f' AND Name.Contains("{literal}")'
    return where
