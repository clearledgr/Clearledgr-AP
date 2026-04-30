"""
SAP ERP Integration

All SAP-specific API calls: journal entries, bills, vendors, credits,
settlements, attachments, Service Layer session management, and OData helpers.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from clearledgr.core.http_client import get_http_client

from decimal import ROUND_HALF_UP
from clearledgr.core.money import Q2, money_to_float, to_decimal
from clearledgr.integrations.erp_sanitization import _sanitize_odata_value

logger = logging.getLogger(__name__)

_ERP_TIMEOUT = 30


def _extract_sap_validation_message(payload: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, dict):
            detail = str(message.get("value") or message.get("Message") or "").strip()
            if detail:
                return detail
        detail = str(message or "").strip()
        if detail:
            return detail
        for key in ("code", "reason"):
            detail = str(error.get(key) or "").strip()
            if detail:
                return detail
        inner = error.get("innererror")
        if isinstance(inner, dict):
            for key in ("message", "detail"):
                detail = str(inner.get(key) or "").strip()
                if detail:
                    return detail
    for key in ("Message", "message", "reason", "error"):
        detail = str(payload.get(key) or "").strip()
        if detail:
            return detail
    return None


def _decode_sap_login_credentials(access_token: Optional[str]) -> tuple[str, str]:
    token = str(access_token or "").strip()
    if not token:
        return "", ""
    try:
        import base64
        decoded = base64.b64decode(token).decode("utf-8")
    except Exception:
        return "", ""
    if ":" not in decoded:
        return "", ""
    username, password = decoded.split(":", 1)
    return username, password


def _normalize_sap_doc_entry(reference: Optional[Any]) -> Optional[str]:
    token = str(reference or "").strip()
    if token.isdigit():
        return token
    return None


def _sap_session_headers(
    session_cookie: str,
    *,
    csrf_token: Optional[str] = None,
) -> Dict[str, str]:
    headers = {"Cookie": f"B1SESSION={session_cookie}"}
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token
    return headers


async def _open_sap_service_layer_session(
    connection,
    client: httpx.AsyncClient,
    *,
    fetch_csrf_for: Optional[str] = None,
) -> Dict[str, Any]:
    if not connection.access_token or not connection.base_url:
        return {"status": "error", "erp": "sap", "reason": "SAP not properly configured"}

    try:
        username, password = _decode_sap_login_credentials(connection.access_token)
        session_cookie = ""
        if username:
            login_url = f"{connection.base_url}/Login"
            login_payload = {
                "CompanyDB": connection.company_code or "",
                "UserName": username,
                "Password": password,
            }
            login_resp = await client.post(login_url, json=login_payload, timeout=30)
            if login_resp.status_code == 401:
                return {"status": "error", "erp": "sap", "reason": "authentication_failed", "needs_reauth": True}
            login_resp.raise_for_status()
            session_cookie = str(login_resp.cookies.get("B1SESSION") or "").strip()
        else:
            session_cookie = str(connection.access_token or "").strip()

        if not session_cookie:
            return {"status": "error", "erp": "sap", "reason": "authentication_failed", "needs_reauth": True}

        headers = _sap_session_headers(session_cookie)
        csrf_token = None
        if fetch_csrf_for:
            csrf_resp = await client.get(
                fetch_csrf_for,
                headers={**headers, "X-CSRF-Token": "Fetch"},
                timeout=30,
            )
            if csrf_resp.status_code == 401:
                return {"status": "error", "erp": "sap", "reason": "authentication_failed", "needs_reauth": True}
            csrf_resp.raise_for_status()
            csrf_token = str(csrf_resp.headers.get("x-csrf-token") or "").strip()
            headers = _sap_session_headers(session_cookie, csrf_token=csrf_token)

        return {
            "status": "success",
            "erp": "sap",
            "session_cookie": session_cookie,
            "csrf_token": csrf_token,
            "headers": headers,
        }
    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        logger.error("SAP session setup HTTP error: status=%d", status_code)
        reason = f"http_{status_code}"
        try:
            payload = e.response.json()
        except Exception:
            payload = None
        validation_message = _extract_sap_validation_message(payload)
        if validation_message:
            reason = validation_message
        return {
            "status": "error",
            "erp": "sap",
            "reason": reason,
            "needs_reauth": status_code == 401,
        }
    except Exception as e:
        logger.error("SAP session setup error: %s", type(e).__name__)
        return {"status": "error", "erp": "sap", "reason": "sap_session_setup_failed"}


# ==================== Journal Entry ====================

async def post_to_sap(
    connection,
    entry: Dict[str, Any],
) -> Dict[str, Any]:
    """Post a journal entry to SAP — flavor-aware dispatcher.

    B1 connections (Service Layer base_url) -> _post_to_sap_b1.
    S/4HANA connections -> _post_to_sap_s4hana via API_JOURNALENTRY_SRV.
    """
    if is_sap_s4hana_connection(connection):
        return await _post_to_sap_s4hana(connection, entry)
    return await _post_to_sap_b1(connection, entry)


async def _post_to_sap_b1(
    connection,
    entry: Dict[str, Any],
) -> Dict[str, Any]:
    """SAP Business One Service Layer journal entry post."""
    if not connection.access_token or not connection.base_url:
        return {"status": "error", "erp": "sap", "reason": "SAP not properly configured"}

    # Build SAP journal entry format
    sap_entry = {
        "ReferenceDate": entry.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
        "Memo": entry.get("description", "Auto-generated by Clearledgr"),
        "JournalEntryLines": [],
    }

    line_num = 0
    for line in entry.get("lines", []):
        sap_line = {
            "Line_ID": line_num,
            "AccountCode": line.get("account", ""),
            "Debit": line.get("debit", 0),
            "Credit": line.get("credit", 0),
            "LineMemo": line.get("account_name", ""),
        }
        sap_entry["JournalEntryLines"].append(sap_line)
        line_num += 1

    # Make OData call
    url = f"{connection.base_url}/JournalEntries"

    try:
        client = get_http_client()
        response = await client.post(
            url,
            json=sap_entry,
            headers={
                "Authorization": f"Bearer {connection.access_token}",
                "Content-Type": "application/json",
            },
            timeout=60,  # SAP can be slow
        )

        response.raise_for_status()
        result = response.json()

        entry_num = result.get("JdtNum") or result.get("DocEntry")
        logger.info(f"Posted to SAP: {entry_num}")
        return {
            "status": "success",
            "erp": "sap",
            "entry_id": entry_num,
        }

    except httpx.HTTPStatusError as e:
        logger.error("SAP OData error: %s", e.response.status_code)
        return {"status": "error", "erp": "sap", "reason": f"SAP API {e.response.status_code}"}
    except Exception as e:
        logger.error("SAP error: %s", type(e).__name__)
        return {"status": "error", "erp": "sap", "reason": "posting_failed"}


# ==================== Bill Posting ====================

async def post_bill_to_sap(
    connection,
    bill,
    gl_map: Optional[Dict[str, str]] = None,
    field_mappings: Optional[Dict[str, str]] = None,
    custom_fields: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Post a vendor bill to SAP — flavor-aware dispatcher.

    B1 connections (Service Layer) -> _post_bill_to_sap_b1.
    S/4HANA connections -> _post_bill_to_sap_s4hana via
    API_SUPPLIERINVOICE_PROCESS_SRV with TaxCode (MWSKZ) propagation.
    """
    if is_sap_s4hana_connection(connection):
        return await _post_bill_to_sap_s4hana(
            connection, bill,
            gl_map=gl_map,
            field_mappings=field_mappings,
            custom_fields=custom_fields,
        )
    return await _post_bill_to_sap_b1(
        connection, bill,
        gl_map=gl_map,
        field_mappings=field_mappings,
        custom_fields=custom_fields,
    )


async def _post_bill_to_sap_b1(
    connection,
    bill,
    gl_map: Optional[Dict[str, str]] = None,
    field_mappings: Optional[Dict[str, str]] = None,
    custom_fields: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Post vendor bill to SAP B1 (A/P Invoice via Service Layer).

    SAP B1: https://help.sap.com/docs/SAP_BUSINESS_ONE
    Validates required fields before posting. company_code must be set in
    the ERP connection credentials (stored as settings_json["gl_account_map"]).

    ``field_mappings`` (Module 5) lets the customer rename dimension
    fields (CostCenter, ProfitCenter, WBSElement). ``custom_fields``
    is the resolved {erp_field_id: value} dict for workflow Z-fields
    (state/box_id/approver) — stamped at the document level so SAP
    cockpit views can filter on Clearledgr-managed work.
    """
    from clearledgr.integrations.erp_router import get_account_code, _dimension_field_name

    if not connection.access_token or not connection.base_url:
        return {"status": "error", "erp": "sap", "reason": "SAP not properly configured"}

    # Pre-flight validation — block before hitting the SAP API
    missing_fields = []
    if not bill.vendor_id:
        missing_fields.append("vendor_id")
    if not bill.amount or bill.amount <= 0:
        missing_fields.append("amount")
    if not connection.company_code:
        missing_fields.append("company_code")
    if missing_fields:
        logger.error("SAP pre-flight validation failed: missing %s", missing_fields)
        return {
            "status": "error",
            "erp": "sap",
            "reason": "sap_validation_failed",
            "missing_fields": missing_fields,
        }

    expense_account = get_account_code("sap", "expenses", gl_map)

    sap_bill = {
        "CardCode": bill.vendor_id,  # Vendor code
        "CompanyCode": connection.company_code,
        "DocDate": bill.invoice_date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "DocDueDate": bill.due_date,
        "NumAtCard": bill.invoice_number,  # Vendor's reference
        "Comments": bill.description or f"Invoice from {bill.vendor_name}",
        "DocumentLines": [],
    }

    # Currency — SAP B1 uses DocCurrency (3-letter code). Tenants with
    # multi-currency disabled will get a clear error from the Service
    # Layer; tenants with it enabled record the bill in its native
    # currency.
    bill_currency = str(getattr(bill, "currency", "") or "").strip().upper()
    if bill_currency and len(bill_currency) == 3:
        sap_bill["DocCurrency"] = bill_currency

    # Tax-code propagation: Clearledgr vat_code -> SAP B1 TaxCode.
    # Bill-level vat_code applies to every line unless the line carries
    # its own override. Operators can remap via gl_map["tax_code_<code>"]
    # (e.g. "tax_code_T1" -> "V7" for an org whose CoA defines V7).
    bill_vat_code = str(getattr(bill, "vat_code", "") or "").upper()

    def _resolve_b1_tax_code(code: str) -> Optional[str]:
        if not code:
            return None
        if gl_map and gl_map.get(f"tax_code_{code}"):
            return str(gl_map[f"tax_code_{code}"])
        return _DEFAULT_B1_TAXCODE_MAP.get(code)

    # SAP B1 wants numeric LineTotals; we serialise quantized floats so
    # the payload matches what we hold in Decimal on our side.
    if bill.line_items:
        for i, item in enumerate(bill.line_items):
            line = {
                "LineNum": i,
                "ItemDescription": item.get("description", ""),
                "AccountCode": item.get("gl_code") or item.get("account_code") or expense_account,
                "LineTotal": money_to_float(item.get("amount", 0)),
            }
            line_vat = str(
                item.get("vat_code") or bill_vat_code or "",
            ).upper()
            tax_code = _resolve_b1_tax_code(line_vat)
            if tax_code:
                line["TaxCode"] = tax_code
            # Module 5 Pass C — carry per-line SAP B1 dimensions
            # (CostCenter, ProfitCenter, WBSElement) from upstream
            # line_items. Renamed below when the customer has
            # configured a Z-field instead of the standard name.
            for dim_key in ("CostCenter", "ProfitCenter", "WBSElement"):
                dim_val = item.get(dim_key)
                if dim_val:
                    line[dim_key] = str(dim_val)
            sap_bill["DocumentLines"].append(line)
    else:
        single_line = {
            "LineNum": 0,
            "ItemDescription": bill.description or f"Invoice {bill.invoice_number}",
            "AccountCode": expense_account,
            "LineTotal": money_to_float(bill.amount),
        }
        tax_code = _resolve_b1_tax_code(bill_vat_code)
        if tax_code:
            single_line["TaxCode"] = tax_code
        sap_bill["DocumentLines"].append(single_line)

    # Tax handling for SAP. Divide tax evenly across lines; Decimal
    # division with ROUND_HALF_UP keeps each line's tax value penny-
    # exact and the totals reconcile.
    if getattr(bill, "tax_amount", None) and bill.tax_amount > 0:
        tax_total = to_decimal(bill.tax_amount)
        n_lines = max(len(sap_bill["DocumentLines"]), 1)
        per_line = (tax_total / n_lines).quantize(Q2, rounding=ROUND_HALF_UP)
        for dl in sap_bill["DocumentLines"]:
            dl["TaxTotal"] = float(per_line)

    # Discount as negative line
    if getattr(bill, "discount_amount", None) and bill.discount_amount > 0:
        sap_bill["DocumentLines"].append({
            "LineNum": len(sap_bill["DocumentLines"]),
            "ItemDescription": f"Discount ({getattr(bill, 'discount_terms', '') or 'early payment'})",
            "AccountCode": expense_account,
            "LineTotal": money_to_float(-bill.discount_amount),
        })

    # Module 5 Pass C — stamp customer-configured Z-fields (workflow).
    # SAP B1 accepts user-defined fields as flat top-level keys on the
    # PurchaseInvoice document. The catalog regex restricts ids at
    # the API boundary so a misconfigured field id can't inject
    # arbitrary payload structure here.
    if custom_fields:
        for erp_field_id, value in custom_fields.items():
            if erp_field_id and value is not None:
                sap_bill[str(erp_field_id)] = value

    # Module 5 Pass C — dimension field renames per line. SAP B1's
    # standard dimensions live on each DocumentLine: CostCenter,
    # ProfitCenter, WBSElement. Tenants that use Z-fields rename here.
    dimension_renames = {}
    for catalog_key, default in (
        ("cost_center_field", "CostCenter"),
        ("profit_center_field", "ProfitCenter"),
        ("wbs_field", "WBSElement"),
    ):
        custom = _dimension_field_name(field_mappings or {}, catalog_key, default)
        if custom != default:
            dimension_renames[default] = custom

    if dimension_renames:
        for line in sap_bill["DocumentLines"]:
            for default_key, custom_key in dimension_renames.items():
                if default_key in line and custom_key != default_key:
                    line[custom_key] = line.pop(default_key)

    url = f"{connection.base_url}/PurchaseInvoices"

    try:
        client = get_http_client()
        session = await _open_sap_service_layer_session(connection, client, fetch_csrf_for=url)
        if session.get("status") != "success":
            return session

        response = await client.post(
            url,
            json=sap_bill,
            headers={**session["headers"], "Content-Type": "application/json"},
            timeout=60,
        )

        if response.status_code == 401:
            return {"status": "error", "erp": "sap", "reason": "authentication_failed", "needs_reauth": True}

        response.raise_for_status()
        result = response.json()

        doc_entry = result.get("DocEntry")
        # Wave 1 / A2 — journal entry traceability. SAP B1 separates
        # the AP invoice DocEntry (this is ``bill_id``) from the
        # auto-created journal entry header (OJDT.DocEntry). The
        # PurchaseInvoice POST response carries the JE DocEntry as
        # ``JournalMemo`` / ``JournalEntry`` depending on B1 version
        # — both forms checked. Auditor traceability requires the
        # JE id, not just the bill id.
        je_id = (
            result.get("JournalEntry")
            or result.get("JournalMemo")
            # Some B1 versions surface it as a nested object
            or (
                result.get("JournalEntryReplica") or {}
            ).get("DocEntry")
        )
        logger.info(
            "Posted A/P Invoice to SAP: bill=%s je=%s",
            doc_entry, je_id,
        )
        return {
            "status": "success",
            "erp": "sap",
            "bill_id": doc_entry,
            "doc_num": result.get("DocNum"),
            "erp_journal_entry_id": (str(je_id) if je_id is not None else None),
        }

    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        # Parse SAP B1 error response for actionable details
        erp_error_detail = ""
        erp_error_code = ""
        try:
            payload = e.response.json()
            # SAP B1: {"error": {"code": -5002, "message": {"lang": "en-us", "value": "..."}}}
            sap_error = payload.get("error") or {}
            erp_error_code = str(sap_error.get("code") or "")
            message_obj = sap_error.get("message")
            if isinstance(message_obj, dict):
                erp_error_detail = message_obj.get("value") or ""
            elif isinstance(message_obj, str):
                erp_error_detail = message_obj
            # Fallback to the general extraction helper
            if not erp_error_detail:
                erp_error_detail = _extract_sap_validation_message(payload) or ""
        except Exception:
            erp_error_detail = e.response.text[:200] if hasattr(e.response, "text") else ""

        detail_lower = erp_error_detail.lower()
        reason = f"http_{status_code}"
        if status_code == 404:
            reason = "erp_configuration_stale"
            logger.error(
                "SAP 404 — likely base_url or company_code mismatch (base_url=%s, company_code=%s). "
                "Verify the SAP Service Layer endpoint and company are accessible.",
                connection.base_url, connection.company_code,
            )
        elif "duplicate" in detail_lower or "already exists" in detail_lower:
            reason = "erp_duplicate_bill"
        elif "account" in detail_lower and ("invalid" in detail_lower or "not found" in detail_lower or "no matching" in detail_lower):
            reason = "erp_gl_account_invalid"
        elif "vendor" in detail_lower and ("not found" in detail_lower or "invalid" in detail_lower or "no matching" in detail_lower):
            reason = "erp_vendor_not_found"
        elif "business partner" in detail_lower and ("not found" in detail_lower or "no matching" in detail_lower):
            reason = "erp_vendor_not_found"

        logger.error(
            "SAP A/P Invoice API error: status=%d reason=%s code=%s detail=%s",
            status_code, reason, erp_error_code, erp_error_detail[:200],
        )
        return {
            "status": "error",
            "erp": "sap",
            "reason": reason,
            "erp_error_detail": erp_error_detail,
            "erp_error_code": erp_error_code,
            "needs_reauth": status_code == 401,
        }
    except Exception as e:
        logger.error("SAP A/P Invoice error: %s: %s", type(e).__name__, e)
        return {"status": "error", "erp": "sap", "reason": "bill_posting_failed", "erp_error_detail": str(e)}


# ==================== Bill Reversal ====================


async def reverse_bill_from_sap(
    connection,
    erp_reference: str,
    *,
    reason: str,
) -> Dict[str, Any]:
    """Reverse a posted SAP A/P Invoice — flavor-aware dispatcher.

    B1 connections (Service Layer) -> _reverse_bill_from_sap_b1.
    S/4HANA connections -> _reverse_bill_from_sap_s4hana via the
    CancelSupplierInvoice OData action.
    """
    if is_sap_s4hana_connection(connection):
        return await _reverse_bill_from_sap_s4hana(connection, erp_reference)
    return await _reverse_bill_from_sap_b1(
        connection, erp_reference, reason=reason,
    )


async def _reverse_bill_from_sap_b1(
    connection,
    erp_reference: str,
    *,
    reason: str,
) -> Dict[str, Any]:
    """Reverse a posted SAP B1 A/P Invoice via the Cancel action.

    SAP B1 Service Layer exposes a ``Cancel`` action on PurchaseInvoices
    that creates a reversing document automatically (linked to the
    original via DocEntry / DocNum). This is the correct SAP-native
    reversal path — direct DELETE of a posted A/P Invoice is not
    supported because posted documents cannot be removed without
    affecting GL continuity.

    Endpoint: ``POST {base_url}/PurchaseInvoices({DocEntry})/Cancel``

    The cancellation creates a new A/P Credit Memo-equivalent document
    that fully offsets the original. SAP returns the new document's
    DocEntry; we expose it as ``reversal_ref`` so the caller can link
    the cancellation document in the audit trail.

    On success returns ``reversal_method="cancel_document"``.
    """
    if not connection.access_token or not connection.base_url:
        return {
            "status": "error",
            "erp": "sap",
            "reason": "SAP not properly configured",
        }

    bill_ref = _normalize_sap_doc_entry(erp_reference)
    if not bill_ref:
        return {
            "status": "error",
            "erp": "sap",
            "reason": "invalid_bill_reference",
        }

    url = f"{connection.base_url}/PurchaseInvoices({bill_ref})/Cancel"

    try:
        client = get_http_client()
        session = await _open_sap_service_layer_session(
            connection, client, fetch_csrf_for=url
        )
        if session.get("status") != "success":
            return session

        response = await client.post(
            url,
            headers={**session["headers"], "Content-Type": "application/json"},
            timeout=60,
        )

        if response.status_code == 401:
            return {
                "status": "error",
                "erp": "sap",
                "reference_id": erp_reference,
                "reversal_method": "cancel_document",
                "reason": "authentication_failed",
                "needs_reauth": True,
            }

        if response.status_code == 404:
            return {
                "status": "already_reversed",
                "erp": "sap",
                "reference_id": erp_reference,
                "reversal_method": "cancel_document",
                "reversal_ref": None,
                "reason": "bill_not_found_in_erp",
            }

        # SAP Cancel typically returns 204 No Content on success. Some
        # versions return 200 with a body containing the new DocEntry
        # of the cancellation document.
        if response.status_code in (200, 204):
            cancellation_doc_entry: Optional[str] = None
            try:
                if response.status_code == 200 and response.content:
                    body = response.json() or {}
                    if isinstance(body, dict):
                        cancellation_doc_entry = (
                            body.get("DocEntry")
                            or body.get("CancellationDocEntry")
                        )
                        if cancellation_doc_entry is not None:
                            cancellation_doc_entry = str(cancellation_doc_entry)
            except Exception:
                pass

            logger.info(
                "Cancelled SAP A/P Invoice %s (reason=%s, cancel_doc=%s)",
                erp_reference, reason, cancellation_doc_entry,
            )
            return {
                "status": "success",
                "erp": "sap",
                "reference_id": erp_reference,
                "reversal_method": "cancel_document",
                "reversal_ref": cancellation_doc_entry,
                "erp_status": "Cancelled",
            }

        response.raise_for_status()

    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        erp_error_detail = ""
        erp_error_code = ""
        try:
            payload = e.response.json()
            sap_error = payload.get("error") or {}
            erp_error_code = str(sap_error.get("code") or "")
            message_obj = sap_error.get("message")
            if isinstance(message_obj, dict):
                erp_error_detail = message_obj.get("value") or ""
            elif isinstance(message_obj, str):
                erp_error_detail = message_obj
            if not erp_error_detail:
                erp_error_detail = _extract_sap_validation_message(payload) or ""
        except Exception:
            erp_error_detail = (
                e.response.text[:200] if hasattr(e.response, "text") else ""
            )

        detail_lower = erp_error_detail.lower()
        reason_code = f"http_{status_code}"

        if status_code == 404 or "does not exist" in detail_lower:
            return {
                "status": "already_reversed",
                "erp": "sap",
                "reference_id": erp_reference,
                "reversal_method": "cancel_document",
                "reversal_ref": None,
                "reason": "bill_not_found_in_erp",
            }
        elif (
            "already" in detail_lower
            and ("cancel" in detail_lower or "reversed" in detail_lower)
        ):
            return {
                "status": "already_reversed",
                "erp": "sap",
                "reference_id": erp_reference,
                "reversal_method": "cancel_document",
                "reversal_ref": None,
                "reason": "already_cancelled_in_erp",
            }
        elif "paid" in detail_lower or (
            "payment" in detail_lower and "applied" in detail_lower
        ):
            reason_code = "payment_already_applied"
        elif "closed" in detail_lower and "period" in detail_lower:
            reason_code = "accounting_period_closed"
        elif "draft" in detail_lower:
            reason_code = "bill_is_draft_not_posted"

        logger.error(
            "SAP A/P Invoice reverse HTTP error: status=%d reason=%s code=%s detail=%s",
            status_code, reason_code, erp_error_code, erp_error_detail[:200],
        )
        return {
            "status": "error",
            "erp": "sap",
            "reference_id": erp_reference,
            "reversal_method": "cancel_document",
            "reason": reason_code,
            "erp_error_detail": erp_error_detail,
            "erp_error_code": erp_error_code,
            "needs_reauth": status_code == 401,
        }
    except Exception as exc:
        logger.error(
            "SAP A/P Invoice reverse error: %s: %s",
            type(exc).__name__, exc,
        )
        return {
            "status": "error",
            "erp": "sap",
            "reference_id": erp_reference,
            "reversal_method": "cancel_document",
            "reason": "bill_reversal_failed",
            "erp_error_detail": str(exc),
        }

    # Defensive — should never reach here because all branches return above.
    return {
        "status": "error",
        "erp": "sap",
        "reference_id": erp_reference,
        "reversal_method": "cancel_document",
        "reason": "unexpected_reversal_path",
    }


# ==================== Bill & Credit Note Lookup ====================

async def get_purchase_invoice_sap(
    connection,
    bill_id: str,
) -> Dict[str, Any]:
    """Fetch a SAP purchase invoice with enough context for credit/payment follow-ons."""
    if not connection.access_token or not connection.base_url:
        return {"status": "error", "erp": "sap", "reason": "SAP not properly configured"}

    bill_ref = _normalize_sap_doc_entry(bill_id)
    if not bill_ref:
        return {"status": "error", "erp": "sap", "reason": "invalid_bill_reference"}

    url = f"{connection.base_url}/PurchaseInvoices({bill_ref})"
    try:
        client = get_http_client()
        session = await _open_sap_service_layer_session(connection, client)
        if session.get("status") != "success":
            return session
        response = await client.get(
            url,
            headers=session["headers"],
            timeout=60,
        )
        if response.status_code == 401:
            return {"status": "error", "erp": "sap", "reason": "authentication_failed", "needs_reauth": True}
        response.raise_for_status()
        payload = response.json()
        document_lines = payload.get("DocumentLines")
        return {
            "status": "success",
            "erp": "sap",
            "bill_id": str(payload.get("DocEntry") or bill_ref),
            "vendor_id": str(payload.get("CardCode") or "").strip() or None,
            "doc_num": payload.get("DocNum"),
            "doc_currency": payload.get("DocCurrency"),
            "doc_total": payload.get("DocTotal"),
            "document_lines": document_lines if isinstance(document_lines, list) else [],
        }
    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        logger.error("SAP purchase invoice GET HTTP error: status=%d", status_code)
        reason = f"http_{status_code}"
        try:
            payload = e.response.json()
        except Exception:
            payload = None
        validation_message = _extract_sap_validation_message(payload)
        if validation_message:
            reason = validation_message
        return {
            "status": "error",
            "erp": "sap",
            "reason": reason,
            "needs_reauth": status_code == 401,
        }
    except Exception as e:
        logger.error("SAP purchase invoice GET error: %s", type(e).__name__)
        return {"status": "error", "erp": "sap", "reason": "bill_lookup_failed"}


async def find_credit_note_sap(
    connection,
    credit_note_number: str,
) -> Optional[Dict[str, Any]]:
    """Find a SAP A/P credit memo by vendor reference number."""
    if not connection.access_token or not connection.base_url:
        return None
    safe_number = _sanitize_odata_value(credit_note_number)
    if not safe_number:
        return None

    url = f"{connection.base_url}/PurchaseCreditNotes"
    params = {
        "$filter": f"NumAtCard eq '{safe_number}'",
        "$top": "1",
        "$select": "DocEntry,DocNum,NumAtCard,DocTotal",
    }
    try:
        client = get_http_client()
        session = await _open_sap_service_layer_session(connection, client)
        if session.get("status") != "success":
            return None
        response = await client.get(
            url,
            params=params,
            headers=session["headers"],
            timeout=60,
        )
        response.raise_for_status()
        items = response.json().get("value", [])
        if items:
            row = items[0]
            return {
                "credit_note_id": str(row.get("DocEntry") or ""),
                "credit_note_number": row.get("NumAtCard"),
                "doc_num": row.get("DocNum"),
                "amount": row.get("DocTotal"),
                "erp": "sap",
            }
    except Exception as e:
        logger.error("SAP credit note lookup error: %s", e)
    return None


def _build_sap_credit_note_lines(
    bill: Dict[str, Any],
    amount: float,
) -> Dict[str, Any]:
    bill_ref = _normalize_sap_doc_entry(bill.get("bill_id"))
    if not bill_ref:
        return {"lines": [], "available_amount": 0.0}

    target_amount = round(float(amount or 0.0), 2)
    line_entries: List[tuple[Dict[str, Any], float]] = []
    for line in bill.get("document_lines") or []:
        if not isinstance(line, dict):
            continue
        try:
            line_total = round(abs(float(line.get("LineTotal") or 0.0)), 2)
        except (TypeError, ValueError):
            continue
        if line_total <= 0:
            continue
        line_entries.append((line, line_total))

    available_amount = round(sum(entry[1] for entry in line_entries), 2)
    remaining = target_amount
    lines: List[Dict[str, Any]] = []
    for line, line_total in line_entries:
        applied = round(min(line_total, remaining), 2)
        if applied <= 0:
            continue
        line_payload: Dict[str, Any] = {
            "BaseType": 18,
            "BaseEntry": int(bill_ref),
            "BaseLine": int(line.get("LineNum") or 0),
            "LineTotal": applied,
        }
        if line.get("AccountCode"):
            line_payload["AccountCode"] = line.get("AccountCode")
        if line.get("TaxCode"):
            line_payload["TaxCode"] = line.get("TaxCode")
        lines.append(line_payload)
        remaining = round(remaining - applied, 2)
        if remaining <= 0:
            break

    return {
        "lines": lines if remaining <= 0 else [],
        "available_amount": available_amount,
    }


# ==================== Credit Application ====================

async def apply_credit_note_to_sap(
    connection,
    application,
    *,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a SAP A/P credit memo against a posted purchase invoice."""
    if not connection.access_token or not connection.base_url:
        return {"status": "error", "erp": "sap", "reason": "SAP not properly configured"}

    missing_fields = []
    target_ref = _normalize_sap_doc_entry(application.target_erp_reference)
    if not target_ref:
        missing_fields.append("target_erp_reference")
    if not str(application.credit_note_number or "").strip():
        missing_fields.append("credit_note_number")
    if float(application.amount or 0.0) <= 0:
        missing_fields.append("amount")
    if missing_fields:
        return {
            "status": "error",
            "erp": "sap",
            "reason": "sap_credit_application_validation_failed",
            "missing_fields": missing_fields,
        }

    # Late-bound through erp_router so test patches on erp_router.* propagate.
    from clearledgr.integrations import erp_router as _router
    existing_credit = await _router.find_credit_note_sap(connection, str(application.credit_note_number or ""))
    if existing_credit and existing_credit.get("credit_note_id"):
        existing_ref = str(existing_credit.get("credit_note_id") or "").strip()
        return {
            "status": "already_applied",
            "erp": "sap",
            "erp_reference": existing_ref,
            "credit_note_reference": existing_ref,
            "credit_note_number": existing_credit.get("credit_note_number") or application.credit_note_number,
            "target_erp_reference": target_ref,
            "amount": round(float(application.amount or 0.0), 2),
            "idempotency_key": idempotency_key,
        }

    bill = await _router.get_purchase_invoice_sap(connection, target_ref or "")
    if bill.get("status") != "success":
        return bill
    if not bill.get("vendor_id"):
        return {
            "status": "error",
            "erp": "sap",
            "reason": "bill_vendor_not_resolved",
            "target_erp_reference": application.target_erp_reference,
        }

    line_plan = _build_sap_credit_note_lines(bill, float(application.amount or 0.0))
    available_amount = float(line_plan.get("available_amount") or 0.0)
    if available_amount and round(float(application.amount or 0.0), 2) > available_amount:
        return {
            "status": "error",
            "erp": "sap",
            "reason": "credit_amount_exceeds_bill_total",
            "available_amount": available_amount,
            "target_erp_reference": target_ref,
        }
    if not line_plan.get("lines"):
        return {
            "status": "error",
            "erp": "sap",
            "reason": "sap_credit_lines_not_resolved",
            "target_erp_reference": target_ref,
        }

    url = f"{connection.base_url}/PurchaseCreditNotes"
    payload = {
        "CardCode": bill["vendor_id"],
        "DocDate": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "NumAtCard": str(application.credit_note_number or "").strip()[:100],
        "Comments": str(
            application.note
            or f"Credit note {application.credit_note_number} for invoice {bill.get('doc_num') or target_ref}"
        )[:254],
        "DocumentLines": line_plan["lines"],
    }

    try:
        client = get_http_client()
        session = await _open_sap_service_layer_session(connection, client, fetch_csrf_for=url)
        if session.get("status") != "success":
            return session
        response = await client.post(
            url,
            json=payload,
            headers={**session["headers"], "Content-Type": "application/json"},
            timeout=60,
        )
        if response.status_code == 401:
            return {"status": "error", "erp": "sap", "reason": "authentication_failed", "needs_reauth": True}
        response.raise_for_status()
        try:
            result = response.json()
        except Exception:
            result = {}
        credit_id = result.get("DocEntry") or result.get("DocNum") or application.credit_note_number
        return {
            "status": "success",
            "erp": "sap",
            "erp_reference": str(credit_id),
            "credit_note_reference": str(result.get("DocEntry") or credit_id),
            "credit_note_number": application.credit_note_number,
            "target_erp_reference": target_ref,
            "amount": round(float(application.amount or 0.0), 2),
            "idempotency_key": idempotency_key,
        }
    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        logger.error("SAP credit memo HTTP error: status=%d", status_code)
        reason = f"http_{status_code}"
        try:
            payload = e.response.json()
        except Exception:
            payload = None
        validation_message = _extract_sap_validation_message(payload)
        if validation_message:
            reason = validation_message
        return {
            "status": "error",
            "erp": "sap",
            "reason": reason,
            "needs_reauth": status_code == 401,
        }
    except Exception as e:
        logger.error("SAP credit memo error: %s", type(e).__name__)
        return {"status": "error", "erp": "sap", "reason": "credit_application_failed"}


# ==================== Settlement ====================

async def apply_settlement_to_sap(
    connection,
    application,
    *,
    gl_map: Optional[Dict[str, str]] = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a SAP vendor payment against a posted purchase invoice."""
    from clearledgr.integrations.erp_router import get_account_code

    if not connection.access_token or not connection.base_url:
        return {"status": "error", "erp": "sap", "reason": "SAP not properly configured"}

    source_document_type = str(application.source_document_type or "").strip().lower()
    if source_document_type == "refund":
        return {
            "status": "error",
            "erp": "sap",
            "reason": "refund_settlement_api_not_available_for_connector",
        }

    missing_fields = []
    target_ref = _normalize_sap_doc_entry(application.target_erp_reference)
    if not target_ref:
        missing_fields.append("target_erp_reference")
    if float(application.amount or 0.0) <= 0:
        missing_fields.append("amount")
    if missing_fields:
        return {
            "status": "error",
            "erp": "sap",
            "reason": "sap_settlement_validation_failed",
            "missing_fields": missing_fields,
        }

    # Late-bound through erp_router so test patches on erp_router.* propagate.
    from clearledgr.integrations import erp_router as _router
    bill = await _router.get_purchase_invoice_sap(connection, target_ref or "")
    if bill.get("status") != "success":
        return bill
    if not bill.get("vendor_id"):
        return {
            "status": "error",
            "erp": "sap",
            "reason": "bill_vendor_not_resolved",
            "target_erp_reference": application.target_erp_reference,
        }

    url = f"{connection.base_url}/VendorPayments"
    payload = {
        "CardCode": bill["vendor_id"],
        "DocType": "rSupplier",
        "DocDate": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "Remarks": str(
            application.note
            or application.source_reference
            or f"Settlement for invoice {bill.get('doc_num') or target_ref}"
        )[:254],
        "TransferAccount": get_account_code("sap", "cash", gl_map),
        "TransferDate": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "TransferSum": round(float(application.amount or 0.0), 2),
        "Invoices": [
            {
                "DocEntry": int(target_ref),
                "InvoiceType": "it_PurchaseInvoice",
                "SumApplied": round(float(application.amount or 0.0), 2),
            }
        ],
    }

    try:
        client = get_http_client()
        session = await _open_sap_service_layer_session(connection, client, fetch_csrf_for=url)
        if session.get("status") != "success":
            return session
        response = await client.post(
            url,
            json=payload,
            headers={**session["headers"], "Content-Type": "application/json"},
            timeout=60,
        )
        if response.status_code == 401:
            return {"status": "error", "erp": "sap", "reason": "authentication_failed", "needs_reauth": True}
        response.raise_for_status()
        try:
            result = response.json()
        except Exception:
            result = {}
        payment_id = result.get("DocEntry") or result.get("DocNum") or application.source_reference or target_ref
        return {
            "status": "success",
            "erp": "sap",
            "erp_reference": str(payment_id),
            "payment_id": str(payment_id),
            "target_erp_reference": target_ref,
            "amount": round(float(application.amount or 0.0), 2),
            "source_reference": application.source_reference,
            "idempotency_key": idempotency_key,
        }
    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        logger.error("SAP vendor payment HTTP error: status=%d", status_code)
        reason = f"http_{status_code}"
        try:
            payload = e.response.json()
        except Exception:
            payload = None
        validation_message = _extract_sap_validation_message(payload)
        if validation_message:
            reason = validation_message
        return {
            "status": "error",
            "erp": "sap",
            "reason": reason,
            "needs_reauth": status_code == 401,
        }
    except Exception as e:
        logger.error("SAP vendor payment error: %s", type(e).__name__)
        return {"status": "error", "erp": "sap", "reason": "settlement_application_failed"}


# ==================== Vendor Management ====================

async def create_vendor_sap(
    connection,
    vendor,
) -> Dict[str, Any]:
    """Create vendor (Business Partner) in SAP."""
    if not connection.access_token or not connection.base_url:
        return {"status": "error", "erp": "sap", "reason": "SAP not configured"}

    sap_bp = {
        "CardName": vendor.name,
        "CardType": "cSupplier",
        "EmailAddress": vendor.email,
        "Phone1": vendor.phone,
    }

    url = f"{connection.base_url}/BusinessPartners"

    try:
        client = get_http_client()
        response = await client.post(
            url,
            json=sap_bp,
            headers={
                "Authorization": f"Bearer {connection.access_token}",
                "Content-Type": "application/json",
            },
            timeout=60,
        )
        response.raise_for_status()
        result = response.json()

        return {
            "status": "success",
            "vendor_id": result.get("CardCode"),
            "name": result.get("CardName"),
        }
    except Exception as e:
        logger.error("SAP vendor creation error: %s", type(e).__name__)
        return {"status": "error", "erp": "sap", "reason": "vendor_creation_failed"}


async def find_vendor_sap(
    connection,
    name: Optional[str] = None,
    email: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Find vendor in SAP."""
    if not connection.access_token or not connection.base_url:
        return None

    filters = ["CardType eq 'cSupplier'"]
    if name:
        safe_name = _sanitize_odata_value(name)
        filters.append(f"contains(CardName, '{safe_name}')")
    if email:
        safe_email = _sanitize_odata_value(email)
        filters.append(f"EmailAddress eq '{safe_email}'")

    url = f"{connection.base_url}/BusinessPartners"
    params = {"$filter": " and ".join(filters), "$top": 1}

    try:
        client = get_http_client()
        response = await client.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {connection.access_token}"},
            timeout=60,
        )
        response.raise_for_status()
        result = response.json()

        items = result.get("value", [])
        if items:
            v = items[0]
            return {
                "vendor_id": v.get("CardCode"),
                "name": v.get("CardName"),
                "email": v.get("EmailAddress"),
            }
    except Exception as e:
        logger.error(f"SAP vendor search error: {e}")

    return None


# ==================== Bill Finder ====================

async def find_bill_sap(
    connection,
    invoice_number: str,
) -> Optional[Dict[str, Any]]:
    """Check if a purchase invoice already exists in SAP."""
    if not connection.access_token or not connection.base_url:
        return None
    safe_number = _sanitize_odata_value(invoice_number)
    if not safe_number:
        return None
    url = f"{connection.base_url}/PurchaseInvoices"
    params = {
        "$filter": f"NumAtCard eq '{safe_number}'",
        "$top": "1",
        "$select": "DocEntry,NumAtCard,DocTotal",
    }
    try:
        client = get_http_client()
        response = await client.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {connection.access_token}"},
            timeout=60,
        )
        response.raise_for_status()
        items = response.json().get("value", [])
        if items:
            row = items[0]
            return {
                "bill_id": str(row.get("DocEntry")),
                "doc_number": row.get("NumAtCard"),
                "amount": row.get("DocTotal"),
                "erp": "sap",
            }
    except Exception as e:
        logger.error("SAP bill lookup error: %s", e)
    return None


# ==================== Attachment ====================

async def _attach_to_sap(
    connection, bill_id: str, file_bytes: bytes, filename: str,
) -> Optional[Dict[str, Any]]:
    """Upload attachment to a SAP Business One PurchaseInvoice.

    SAP B1 Service Layer requires multipart/form-data for /Attachments2
    so the file body actually travels — the JSON-only path needs the
    file to already exist on the SAP server filesystem, which it
    won't for our cloud-uploaded PDFs. Honest stub here until the
    multipart upload (boundary, Content-Disposition, two-step create
    + link to bill) is implemented.
    """
    creds = connection.credentials or {}
    base_url = str(creds.get("base_url") or "").rstrip("/")
    session_id = creds.get("session_id", "")
    if not base_url or not session_id:
        return None
    logger.warning(
        "[SAP] attachment upload not yet implemented — bill_id=%s filename=%s bytes=%d",
        bill_id, filename, len(file_bytes or b""),
    )
    return {"attached": False, "erp": "sap", "reason": "multipart_upload_not_implemented"}


# ==================== Payment Status Lookup ====================

async def get_payment_status_sap(
    connection,
    bill_id: str,
) -> Dict[str, Any]:
    """Read payment status for a SAP purchase invoice. GET only — never executes payments.

    Fetches PurchaseInvoices({id}) and compares PaidToDate vs DocTotal.
    """
    if not connection.access_token or not connection.base_url:
        return {"paid": False, "error": "SAP not properly configured"}

    bill_ref = _normalize_sap_doc_entry(bill_id)
    if not bill_ref:
        return {"paid": False, "error": "invalid_bill_reference"}

    url = f"{connection.base_url}/PurchaseInvoices({bill_ref})"
    try:
        client = get_http_client()
        session = await _open_sap_service_layer_session(connection, client)
        if session.get("status") != "success":
            return {"paid": False, "error": session.get("reason", "session_failed")}

        response = await client.get(
            url,
            headers=session["headers"],
            timeout=60,
        )
        if response.status_code == 401:
            return {"paid": False, "error": "authentication_failed", "needs_reauth": True}

        response.raise_for_status()
        payload = response.json()

        doc_total = float(payload.get("DocTotal") or 0)
        paid_to_date = float(payload.get("PaidToDate") or 0)
        remaining = round(doc_total - paid_to_date, 2)

        # Detect cancelled invoices
        cancelled = str(payload.get("Cancelled") or "").lower()
        if cancelled in ("tyes", "y", "true", "yes"):
            return {
                "paid": False,
                "payment_failed": True,
                "reason": "invoice_cancelled",
            }

        if paid_to_date >= doc_total and doc_total > 0:
            # Detect closure method: credit memo vs payment
            closure_method = "payment"
            # SAP: if paid but no outgoing payment reference, check for
            # credit memo closure
            doc_type = str(payload.get("DocObjectCode") or "").lower()
            if doc_type in ("ocreditnote", "creditnote"):
                closure_method = "credit_applied"
            elif not str(payload.get("PaymentReference") or "").strip():
                # No explicit payment reference — may be credit
                closure_method = "unknown_non_payment"

            result = {
                "paid": True,
                "payment_amount": round(paid_to_date, 2),
                "payment_date": str(payload.get("UpdateDate") or ""),
                "payment_method": "",
                "payment_reference": str(payload.get("DocEntry") or bill_ref),
                "partial": False,
                "remaining_balance": 0.0,
            }
            if closure_method != "payment":
                result["closure_method"] = closure_method
            return result
        elif paid_to_date > 0 and remaining > 0:
            return {
                "paid": False,
                "payment_amount": round(paid_to_date, 2),
                "payment_date": str(payload.get("UpdateDate") or ""),
                "payment_method": "",
                "payment_reference": str(payload.get("DocEntry") or bill_ref),
                "partial": True,
                "remaining_balance": remaining,
            }
        else:
            return {"paid": False, "reason": "unpaid"}
    except httpx.HTTPStatusError as e:
        logger.error("SAP payment status HTTP error: status=%d", e.response.status_code)
        return {"paid": False, "error": f"http_{e.response.status_code}"}
    except Exception as e:
        logger.error("SAP payment status error: %s", type(e).__name__)
        return {"paid": False, "error": "payment_status_lookup_failed"}


# ==================== SAP S/4HANA Write Surface ====================


async def _post_to_sap_s4hana(
    connection,
    entry: Dict[str, Any],
) -> Dict[str, Any]:
    """Post a journal entry to SAP S/4HANA via API_JOURNALENTRY_SRV.

    Entity: A_JournalEntry. Lines deep-create through
    to_JournalEntryItem. DebitCreditCode is 'S' (debit) / 'H' (credit)
    per SAP convention. The composite key
    (CompanyCode, FiscalYear, AccountingDocument) is the canonical JE
    id; we return AccountingDocument as ``entry_id``.
    """
    if not connection.access_token or not connection.base_url:
        return {"status": "error", "erp": "sap", "reason": "SAP not properly configured"}
    if not connection.company_code:
        return {
            "status": "error", "erp": "sap",
            "reason": "missing_company_code_for_s4hana",
        }

    posting_date = entry.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    currency = (entry.get("currency") or "USD").upper()
    description = entry.get("description") or "Auto-generated by Clearledgr"

    items: List[Dict[str, Any]] = []
    for line in entry.get("lines") or []:
        debit = float(line.get("debit") or 0)
        credit = float(line.get("credit") or 0)
        if debit > 0:
            direction = "S"
            amount = debit
        elif credit > 0:
            direction = "H"
            amount = credit
        else:
            continue
        item: Dict[str, Any] = {
            "GLAccount": str(line.get("account") or ""),
            "DebitCreditCode": direction,
            "AmountInTransactionCurrency": round(amount, 2),
            "TransactionCurrency": currency,
            "DocumentItemText": (
                str(line.get("description") or line.get("account_name") or "")[:50]
            ),
        }
        tax_code = line.get("tax_code") or line.get("TaxCode")
        if tax_code:
            item["TaxCode"] = str(tax_code)
        items.append(item)

    if not items:
        return {
            "status": "error", "erp": "sap",
            "reason": "no_lines_to_post",
        }

    payload = {
        "CompanyCode": connection.company_code,
        "AccountingDocumentType": "SA",  # SA = G/L document
        "DocumentDate": posting_date,
        "PostingDate": posting_date,
        "DocumentReferenceID": (
            str(entry.get("reference_id") or "Clearledgr")[:16]
        ),
        "DocumentHeaderText": description[:25],
        "to_JournalEntryItem": items,
    }

    base = str(connection.base_url).rstrip("/")
    url = f"{base}/sap/opu/odata/sap/API_JOURNALENTRY_SRV/A_JournalEntry"

    try:
        client = get_http_client()
        response = await client.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {connection.access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=60,
        )
        if response.status_code == 401:
            return {
                "status": "error", "erp": "sap",
                "reason": "authentication_failed",
                "needs_reauth": True,
            }
        response.raise_for_status()
        body = response.json() or {}
        record = body.get("d") if isinstance(body.get("d"), dict) else body
        accounting_document = (
            record.get("AccountingDocument")
            or record.get("DocumentNumber")
        )
        fiscal_year = record.get("FiscalYear") or record.get("fiscalYear")
        company_code = record.get("CompanyCode") or connection.company_code
        # Composite key as the canonical reference
        je_id = (
            f"{company_code}/{accounting_document}/{fiscal_year}"
            if accounting_document and fiscal_year
            else accounting_document
        )
        logger.info("Posted JE to SAP S/4HANA: %s", je_id)
        return {
            "status": "success",
            "erp": "sap",
            "entry_id": je_id,
            "accounting_document": accounting_document,
            "fiscal_year": fiscal_year,
            "company_code": company_code,
        }
    except httpx.HTTPStatusError as exc:
        sc = exc.response.status_code
        try:
            err_payload = exc.response.json()
        except Exception:
            err_payload = None
        detail = _extract_sap_validation_message(err_payload) or ""
        logger.error(
            "SAP S/4HANA JE post error: status=%d detail=%s", sc, detail[:200],
        )
        return {
            "status": "error", "erp": "sap",
            "reason": f"http_{sc}",
            "erp_error_detail": detail,
            "needs_reauth": sc == 401,
        }
    except Exception as exc:
        logger.error("SAP S/4HANA JE post error: %s", type(exc).__name__)
        return {
            "status": "error", "erp": "sap",
            "reason": "je_post_failed",
            "erp_error_detail": str(exc)[:300],
        }


async def _post_bill_to_sap_s4hana(
    connection,
    bill,
    gl_map: Optional[Dict[str, str]] = None,
    field_mappings: Optional[Dict[str, str]] = None,
    custom_fields: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Post a vendor bill to SAP S/4HANA via API_SUPPLIERINVOICE_PROCESS_SRV.

    Entity: A_SupplierInvoice. Lines via to_SupplierInvoiceItemAcctAssgmt
    (GL-account-keyed accruals). The bill's tax code (vat_code) is
    propagated onto each line via TaxCode (MWSKZ).
    """
    from clearledgr.integrations.erp_router import get_account_code

    if not connection.access_token or not connection.base_url:
        return {"status": "error", "erp": "sap", "reason": "SAP not properly configured"}

    missing = []
    if not bill.vendor_id:
        missing.append("vendor_id")
    if not bill.amount or bill.amount <= 0:
        missing.append("amount")
    if not connection.company_code:
        missing.append("company_code")
    if missing:
        return {
            "status": "error", "erp": "sap",
            "reason": "sap_validation_failed",
            "missing_fields": missing,
        }

    expense_account = get_account_code("sap", "expenses", gl_map)
    currency = (str(getattr(bill, "currency", "") or "USD") or "USD").upper()

    # Map Clearledgr vat_code to S/4HANA MWSKZ. Per-tenant overrides
    # via gl_map["tax_code_*"]; fall back to canonical defaults.
    bill_vat_code = str(getattr(bill, "vat_code", "") or "").upper()

    def _resolve_mwskz(code: str) -> Optional[str]:
        if not code:
            return None
        # Operator override key: gl_map["tax_code_T1"] -> "V7" etc.
        if gl_map and gl_map.get(f"tax_code_{code}"):
            return str(gl_map[f"tax_code_{code}"])
        return _DEFAULT_S4HANA_MWSKZ_MAP.get(code)

    items: List[Dict[str, Any]] = []
    line_idx = 1
    if bill.line_items:
        for item in bill.line_items:
            account = (
                item.get("gl_code") or item.get("account_code") or expense_account
            )
            line: Dict[str, Any] = {
                "SupplierInvoiceItem": str(line_idx),
                "CompanyCode": connection.company_code,
                "GLAccount": str(account),
                "DocumentCurrency": currency,
                "SupplierInvoiceItemAmount": money_to_float(item.get("amount", 0)),
                "DebitCreditCode": "S",
                "SupplierInvoiceItemText": str(item.get("description") or "")[:50],
            }
            line_vat = str(item.get("vat_code") or bill_vat_code or "").upper()
            mwskz = _resolve_mwskz(line_vat)
            if mwskz:
                line["TaxCode"] = mwskz
            cost_center = item.get("CostCenter") or item.get("cost_center")
            if cost_center:
                line["CostCenter"] = str(cost_center)
            profit_center = item.get("ProfitCenter") or item.get("profit_center")
            if profit_center:
                line["ProfitCenter"] = str(profit_center)
            items.append(line)
            line_idx += 1
    else:
        line = {
            "SupplierInvoiceItem": "1",
            "CompanyCode": connection.company_code,
            "GLAccount": str(expense_account),
            "DocumentCurrency": currency,
            "SupplierInvoiceItemAmount": money_to_float(bill.amount),
            "DebitCreditCode": "S",
            "SupplierInvoiceItemText": (
                bill.description or f"Invoice {bill.invoice_number}"
            )[:50],
        }
        mwskz = _resolve_mwskz(bill_vat_code)
        if mwskz:
            line["TaxCode"] = mwskz
        items.append(line)

    payload = {
        "CompanyCode": connection.company_code,
        "DocumentDate": (
            bill.invoice_date
            or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        ),
        "PostingDate": (
            bill.invoice_date
            or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        ),
        "InvoicingParty": str(bill.vendor_id),
        "DocumentCurrency": currency,
        "InvoiceGrossAmount": money_to_float(bill.amount),
        "SupplierInvoiceIDByInvcgParty": str(bill.invoice_number or "")[:16],
        "DocumentHeaderText": (
            bill.description or f"Invoice from {bill.vendor_name}"
        )[:25],
        "to_SupplierInvoiceItemGLAcct": items,
    }

    if getattr(bill, "tax_amount", None) and bill.tax_amount > 0:
        payload["TaxAmount"] = money_to_float(bill.tax_amount)

    base = str(connection.base_url).rstrip("/")
    url = (
        f"{base}/sap/opu/odata/sap/API_SUPPLIERINVOICE_PROCESS_SRV/"
        f"A_SupplierInvoice"
    )

    try:
        client = get_http_client()
        response = await client.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {connection.access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=60,
        )
        if response.status_code == 401:
            return {
                "status": "error", "erp": "sap",
                "reason": "authentication_failed",
                "needs_reauth": True,
            }
        response.raise_for_status()
        body = response.json() or {}
        record = body.get("d") if isinstance(body.get("d"), dict) else body
        supplier_invoice = (
            record.get("SupplierInvoice")
            or record.get("DocumentNumber")
        )
        fiscal_year = record.get("FiscalYear")
        company_code = record.get("CompanyCode") or connection.company_code

        bill_id = (
            f"{company_code}/{supplier_invoice}/{fiscal_year}"
            if supplier_invoice and fiscal_year
            else supplier_invoice
        )
        je_id = bill_id  # In S/4HANA, supplier invoice IS the JE source.
        logger.info(
            "Posted A/P Invoice to SAP S/4HANA: bill=%s je=%s",
            bill_id, je_id,
        )
        return {
            "status": "success",
            "erp": "sap",
            "bill_id": bill_id,
            "doc_num": supplier_invoice,
            "fiscal_year": fiscal_year,
            "company_code": company_code,
            "erp_journal_entry_id": je_id,
        }
    except httpx.HTTPStatusError as exc:
        sc = exc.response.status_code
        try:
            err_payload = exc.response.json()
        except Exception:
            err_payload = None
        detail = _extract_sap_validation_message(err_payload) or ""
        reason = f"http_{sc}"
        if "duplicate" in detail.lower():
            reason = "erp_duplicate_bill"
        elif "supplier" in detail.lower() and "not found" in detail.lower():
            reason = "erp_vendor_not_found"
        elif "g/l" in detail.lower() and "not found" in detail.lower():
            reason = "erp_gl_account_invalid"
        logger.error(
            "SAP S/4HANA bill post error: status=%d reason=%s detail=%s",
            sc, reason, detail[:200],
        )
        return {
            "status": "error", "erp": "sap",
            "reason": reason,
            "erp_error_detail": detail,
            "needs_reauth": sc == 401,
        }
    except Exception as exc:
        logger.error(
            "SAP S/4HANA bill post error: %s: %s",
            type(exc).__name__, exc,
        )
        return {
            "status": "error", "erp": "sap",
            "reason": "bill_posting_failed",
            "erp_error_detail": str(exc)[:300],
        }


async def _reverse_bill_from_sap_s4hana(
    connection,
    erp_reference: str,
) -> Dict[str, Any]:
    """Cancel a posted supplier invoice in SAP S/4HANA.

    erp_reference is the composite key ``CompanyCode/SupplierInvoice/FiscalYear``.
    The cancellation OData action is CancelSupplierInvoice (bound to
    A_SupplierInvoice). After a successful POST, S/4HANA creates a
    cancellation document and clears the original. The cancellation
    document's id is returned as ``cancellation_id``."""
    if not connection.access_token or not connection.base_url:
        return {"status": "error", "erp": "sap", "reason": "SAP not properly configured"}

    parts = (erp_reference or "").split("/")
    if len(parts) != 3:
        return {
            "status": "error", "erp": "sap",
            "reason": "invalid_invoice_key_expected_CC_DOC_FY",
        }
    cc, doc, fy = parts
    base = str(connection.base_url).rstrip("/")
    url = (
        f"{base}/sap/opu/odata/sap/API_SUPPLIERINVOICE_PROCESS_SRV/"
        f"A_SupplierInvoice("
        f"CompanyCode='{cc}',SupplierInvoice='{doc}',FiscalYear='{fy}')"
        f"/CancelSupplierInvoice"
    )
    try:
        client = get_http_client()
        response = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {connection.access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={
                "ReversalReason": "01",   # Reversal in current period
                "PostingDate": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            },
            timeout=60,
        )
        if response.status_code == 401:
            return {
                "status": "error", "erp": "sap",
                "reason": "authentication_failed",
                "needs_reauth": True,
            }
        response.raise_for_status()
        body = response.json() or {}
        record = body.get("d") if isinstance(body.get("d"), dict) else body
        cancellation = (
            record.get("CancellationDocument")
            or record.get("ReverseDocument")
            or record.get("AccountingDocument")
        )
        return {
            "status": "success",
            "erp": "sap",
            "reversed_bill_id": erp_reference,
            "cancellation_id": cancellation,
        }
    except httpx.HTTPStatusError as exc:
        sc = exc.response.status_code
        try:
            err = exc.response.json()
        except Exception:
            err = None
        detail = _extract_sap_validation_message(err) or ""
        return {
            "status": "error", "erp": "sap",
            "reason": f"http_{sc}",
            "erp_error_detail": detail,
            "needs_reauth": sc == 401,
        }
    except Exception as exc:
        return {
            "status": "error", "erp": "sap",
            "reason": "reversal_failed",
            "erp_error_detail": str(exc)[:300],
        }


# Default Clearledgr vat_code -> S/4HANA MWSKZ tax code map.
# Tenants override via gl_account_map["tax_code_<code>"] (e.g.
# settings_json["gl_account_map"]["tax_code_T1"] = "V7" for a tenant
# whose CoA defines V7 as 7% input VAT).
_DEFAULT_S4HANA_MWSKZ_MAP: Dict[str, str] = {
    "T1": "V1",  # Standard rated input VAT
    "T0": "V0",  # Zero-rated input
    "T2": "VE",  # Exempt input
    "RC": "VR",  # Reverse charge input
    "OO": "V0",  # Out of scope -> no VAT line
}


# B1 ships a different default tax-code namespace per CoA template;
# the canonical SAP-supplied template uses 1S (standard input),
# 0S (zero), EX (exempt), R7 (reverse charge). Operators with
# customised CoAs override via gl_map["tax_code_<code>"].
_DEFAULT_B1_TAXCODE_MAP: Dict[str, str] = {
    "T1": "1S",
    "T0": "0S",
    "T2": "EX",
    "RC": "R7",
    "OO": "0S",
}


# ==================== S/4HANA Payment Status (Carry-over) ====================


def is_sap_s4hana_connection(connection) -> bool:
    """Heuristic: SAP B1 Service Layer URLs include ``/b1s/`` (e.g.
    https://<host>:50000/b1s/v1). S/4HANA endpoints typically include
    ``/sap/opu/odata/sap/`` or run against the SAP API hub. Anything
    that doesn't look like B1 we treat as S/4HANA so the polling
    + CloudEvents path is the default for modern SAP shops."""
    base = str(getattr(connection, "base_url", None) or "").lower()
    return "/b1s/" not in base


async def get_payment_status_sap_s4hana(
    connection,
    supplier_invoice_key: str,
) -> Dict[str, Any]:
    """Read payment status for one SAP S/4HANA supplier invoice.

    ``supplier_invoice_key`` is the composite ``CompanyCode/SupplierInvoice/FiscalYear``
    string the intake adapter constructs (the canonical S/4HANA
    primary key). The OData endpoint:

        {base_url}/sap/opu/odata/sap/API_SUPPLIERINVOICE_PROCESS_SRV/
            A_SupplierInvoice(CompanyCode='1000',SupplierInvoice='5105600000',FiscalYear='2026')

    Compares ``PaymentBlockingReason`` + ``IsCleared`` flags. The
    cleared flag is the canonical "the bill has been paid out by
    treasury" signal in S/4HANA.
    """
    if not connection.access_token or not connection.base_url:
        return {"paid": False, "error": "SAP S/4HANA not properly configured"}

    parts = (supplier_invoice_key or "").split("/")
    if len(parts) != 3:
        return {"paid": False, "error": "invalid_invoice_key_expected_CC_DOC_FY"}
    company_code, supplier_invoice, fiscal_year = parts
    base = str(connection.base_url or "").rstrip("/")
    url = (
        f"{base}/sap/opu/odata/sap/API_SUPPLIERINVOICE_PROCESS_SRV/"
        f"A_SupplierInvoice("
        f"CompanyCode='{company_code}',"
        f"SupplierInvoice='{supplier_invoice}',"
        f"FiscalYear='{fiscal_year}')"
    )
    try:
        client = get_http_client()
        response = await client.get(
            url,
            headers={
                "Authorization": f"Bearer {connection.access_token}",
                "Accept": "application/json",
            },
            timeout=_ERP_TIMEOUT,
        )
        if response.status_code == 401:
            return {
                "paid": False,
                "error": "authentication_failed",
                "needs_reauth": True,
            }
        if response.status_code == 404:
            return {"paid": False, "reason": "not_found"}
        response.raise_for_status()
        data = response.json() or {}
        # OData envelope can be either {"d": {...}} (v2) or root-level (v4).
        record = data.get("d") if isinstance(data.get("d"), dict) else data

        is_cleared = str(record.get("IsCleared") or "").lower() in (
            "true", "x", "1", "yes",
        )
        is_cancelled = str(record.get("IsCancelled") or "").lower() in (
            "true", "x", "1", "yes",
        )
        amount = float(record.get("InvoiceGrossAmount") or 0)
        currency = str(record.get("DocumentCurrency") or "")
        payment_block = str(record.get("PaymentBlockingReason") or "").strip()
        clearing_doc = str(record.get("ClearingDocument") or "").strip()
        clearing_date = str(record.get("ClearingDate") or "")

        if is_cancelled:
            return {
                "paid": False,
                "payment_failed": True,
                "reason": "invoice_cancelled",
            }

        if is_cleared and amount > 0:
            return {
                "paid": True,
                "payment_amount": round(amount, 2),
                "payment_date": clearing_date,
                "payment_method": "",
                "payment_reference": clearing_doc or supplier_invoice,
                "partial": False,
                "remaining_balance": 0.0,
                "currency": currency,
            }

        if payment_block and payment_block != "0":
            return {
                "paid": False,
                "reason": f"payment_blocked:{payment_block}",
            }

        return {"paid": False, "reason": "unpaid"}
    except httpx.HTTPStatusError as exc:
        logger.error(
            "SAP S/4HANA payment status HTTP error: status=%d",
            exc.response.status_code,
        )
        return {"paid": False, "error": f"http_{exc.response.status_code}"}
    except Exception as exc:
        logger.warning(
            "SAP S/4HANA payment status failed: %s", type(exc).__name__,
        )
        return {"paid": False, "error": "payment_status_lookup_failed"}


# ==================== Chart of Accounts ====================

_SAP_GROUP_CODE_MAP = {
    # SAP Business One GroupCode mapping to normalized types.
    # GroupCode values vary by CoA template; these cover standard B1.
    "1": "asset",
    "2": "liability",
    "3": "equity",
    "4": "revenue",
    "5": "expense",
    "6": "expense",
    "7": "expense",
    "10": "asset",
    "12": "liability",
    "13": "equity",
    "14": "revenue",
    "15": "expense",
}


async def get_chart_of_accounts_sap(connection) -> List[Dict[str, Any]]:
    """Fetch all accounts from SAP Business One.

    Returns a normalized list of account dicts.  Returns ``[]`` on any error
    so the caller is never blocked.
    """
    if not connection.access_token or not connection.base_url:
        return []

    try:
        client = get_http_client()
        session = await _open_sap_service_layer_session(connection, client)
        if session.get("status") != "success":
            logger.warning("SAP session setup failed for chart-of-accounts fetch")
            return []

        headers = session.get("headers", {})

        url = (
            f"{connection.base_url}/b1s/v1/ChartOfAccounts"
            "?$select=Code,Name,AcctCurrency,ActiveAccount,GroupCode"
            "&$top=5000"
        )
        response = await client.get(url, headers=headers, timeout=60)

        if response.status_code == 401:
            logger.warning("SAP token expired during chart-of-accounts fetch")
            return []

        response.raise_for_status()
        result = response.json()

        accounts: List[Dict[str, Any]] = []
        for acc in result.get("value", []):
            group_code = str(acc.get("GroupCode") or "")
            active_flag = acc.get("ActiveAccount")
            active = True
            if isinstance(active_flag, str):
                active = active_flag.strip().lower() in {"y", "yes", "true", "tyes"}
            elif isinstance(active_flag, bool):
                active = active_flag
            elif active_flag == "tNO":
                active = False
            elif active_flag == "tYES":
                active = True

            accounts.append({
                "id": str(acc.get("Code") or ""),
                "code": str(acc.get("Code") or ""),
                "name": str(acc.get("Name") or ""),
                "type": _SAP_GROUP_CODE_MAP.get(group_code, "other"),
                "sub_type": f"group_{group_code}" if group_code else "",
                "active": active,
                "currency": str(acc.get("AcctCurrency") or ""),
            })
        return accounts

    except Exception as e:
        logger.error("Failed to fetch SAP chart of accounts: %s", type(e).__name__)
        return []


# ==================== Vendor List ====================


async def list_all_vendors_sap(connection) -> List[Dict[str, Any]]:
    """Fetch all supplier business partners from SAP Business One with pagination.

    SAP OData uses ``$skip`` + ``$top`` for pagination.
    Filters to ``CardType eq 'cSupplier'`` for vendors only.
    Returns a normalized list of vendor dicts.  Returns ``[]`` on any error.
    """
    if not connection.access_token or not connection.base_url:
        return []

    page_size = 500
    skip = 0
    all_vendors: List[Dict[str, Any]] = []

    try:
        client = get_http_client()
        session = await _open_sap_service_layer_session(connection, client)
        if session.get("status") != "success":
            logger.warning("SAP session setup failed for vendor list fetch")
            return []

        headers = session.get("headers", {})

        while True:
            url = (
                f"{connection.base_url}/b1s/v1/BusinessPartners"
                f"?$filter=CardType eq 'cSupplier'"
                f"&$select=CardCode,CardName,EmailAddress,Phone1,"
                f"Address,FederalTaxID,Currency,PayTermsGrpCode,CurrentAccountBalance,Valid"
                f"&$top={page_size}&$skip={skip}"
            )
            response = await client.get(url, headers=headers, timeout=60)

            if response.status_code == 401:
                logger.warning("SAP token expired during vendor list fetch")
                break

            response.raise_for_status()
            result = response.json()

            items = result.get("value", [])
            if not items:
                break

            for v in items:
                valid_flag = v.get("Valid")
                active = True
                if isinstance(valid_flag, str):
                    active = valid_flag.strip().lower() in {"y", "yes", "true", "tyes"}
                elif isinstance(valid_flag, bool):
                    active = valid_flag
                elif valid_flag == "tNO":
                    active = False
                elif valid_flag == "tYES":
                    active = True

                all_vendors.append({
                    "vendor_id": str(v.get("CardCode") or ""),
                    "name": str(v.get("CardName") or ""),
                    "email": str(v.get("EmailAddress") or ""),
                    "phone": str(v.get("Phone1") or ""),
                    "tax_id": str(v.get("FederalTaxID") or ""),
                    "currency": str(v.get("Currency") or ""),
                    "active": active,
                    "address": str(v.get("Address") or ""),
                    "payment_terms": str(v.get("PayTermsGrpCode") or ""),
                    "balance": float(v.get("CurrentAccountBalance") or 0),
                })

            if len(items) < page_size:
                break
            skip += page_size

        return all_vendors

    except Exception as e:
        logger.error("Failed to fetch SAP vendor list: %s", type(e).__name__)
        return all_vendors or []
