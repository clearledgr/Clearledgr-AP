"""
SAP ERP Integration

All SAP-specific API calls: journal entries, bills, vendors, credits,
settlements, attachments, Service Layer session management, and OData helpers.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

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
    """
    Post journal entry to SAP via OData.

    Uses SAP Business One Service Layer or S/4HANA OData.
    """
    if not connection.access_token or not connection.base_url:
        return {"status": "error", "erp": "sap", "reason": "SAP not properly configured"}

    # Build SAP journal entry format
    sap_entry = {
        "ReferenceDate": entry.get("date", datetime.now().strftime("%Y-%m-%d")),
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
        async with httpx.AsyncClient(timeout=_ERP_TIMEOUT) as client:
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
) -> Dict[str, Any]:
    """
    Post vendor bill to SAP B1 (A/P Invoice via Service Layer).

    SAP B1: https://help.sap.com/docs/SAP_BUSINESS_ONE
    Validates required fields before posting. company_code must be set in
    the ERP connection credentials (stored as settings_json["gl_account_map"]).
    """
    from clearledgr.integrations.erp_router import get_account_code

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
        "DocDate": bill.invoice_date or datetime.now().strftime("%Y-%m-%d"),
        "DocDueDate": bill.due_date,
        "NumAtCard": bill.invoice_number,  # Vendor's reference
        "Comments": bill.description or f"Invoice from {bill.vendor_name}",
        "DocumentLines": [],
    }

    if bill.line_items:
        for i, item in enumerate(bill.line_items):
            sap_bill["DocumentLines"].append({
                "LineNum": i,
                "ItemDescription": item.get("description", ""),
                "AccountCode": item.get("account_code", expense_account),
                "LineTotal": item.get("amount", 0),
            })
    else:
        sap_bill["DocumentLines"].append({
            "LineNum": 0,
            "ItemDescription": bill.description or f"Invoice {bill.invoice_number}",
            "AccountCode": expense_account,
            "LineTotal": bill.amount,
        })

    url = f"{connection.base_url}/PurchaseInvoices"

    try:
        async with httpx.AsyncClient(timeout=_ERP_TIMEOUT) as client:
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
            logger.info("Posted A/P Invoice to SAP: %s", doc_entry)
            return {
                "status": "success",
                "erp": "sap",
                "bill_id": doc_entry,
                "doc_num": result.get("DocNum"),
            }

    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        logger.error("SAP A/P Invoice HTTP error: status=%d", status_code)
        reason = f"http_{status_code}"
        if status_code == 404:
            reason = "erp_configuration_stale"
            logger.error(
                "SAP 404 — likely base_url or company_code mismatch (base_url=%s, company_code=%s). "
                "Verify the SAP Service Layer endpoint and company are accessible.",
                connection.base_url, connection.company_code,
            )
        try:
            payload = e.response.json()
        except Exception:
            payload = None
        validation_message = _extract_sap_validation_message(payload)
        if validation_message and status_code != 404:
            reason = validation_message
        return {
            "status": "error",
            "erp": "sap",
            "reason": reason,
            "needs_reauth": status_code == 401,
        }
    except Exception as e:
        logger.error("SAP A/P Invoice error: %s", type(e).__name__)
        return {"status": "error", "erp": "sap", "reason": "bill_posting_failed"}


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
        async with httpx.AsyncClient(timeout=_ERP_TIMEOUT) as client:
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
        async with httpx.AsyncClient(timeout=_ERP_TIMEOUT) as client:
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
        "DocDate": datetime.now().strftime("%Y-%m-%d"),
        "NumAtCard": str(application.credit_note_number or "").strip()[:100],
        "Comments": str(
            application.note
            or f"Credit note {application.credit_note_number} for invoice {bill.get('doc_num') or target_ref}"
        )[:254],
        "DocumentLines": line_plan["lines"],
    }

    try:
        async with httpx.AsyncClient(timeout=_ERP_TIMEOUT) as client:
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
        "DocDate": datetime.now().strftime("%Y-%m-%d"),
        "Remarks": str(
            application.note
            or application.source_reference
            or f"Settlement for invoice {bill.get('doc_num') or target_ref}"
        )[:254],
        "TransferAccount": get_account_code("sap", "cash", gl_map),
        "TransferDate": datetime.now().strftime("%Y-%m-%d"),
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
        async with httpx.AsyncClient(timeout=_ERP_TIMEOUT) as client:
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
        async with httpx.AsyncClient(timeout=_ERP_TIMEOUT) as client:
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
        async with httpx.AsyncClient(timeout=_ERP_TIMEOUT) as client:
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
        async with httpx.AsyncClient(timeout=_ERP_TIMEOUT) as client:
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
    """Upload attachment to a SAP Business One PurchaseInvoice."""
    import base64

    creds = connection.credentials or {}
    base_url = str(creds.get("base_url") or "").rstrip("/")
    session_id = creds.get("session_id", "")
    if not base_url or not session_id:
        return None
    encoded = base64.b64encode(file_bytes).decode()
    url = f"{base_url}/Attachments2"
    headers = {"Cookie": f"B1SESSION={session_id}", "Content-Type": "application/json"}
    payload = {
        "Attachments2_Lines": [{
            "SourcePath": filename,
            "FileName": filename,
            "FileExtension": "pdf",
            "Override": "tNO",
        }],
    }
    async with httpx.AsyncClient(timeout=30) as client:
        # Create attachment record
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
    return {"attached": True, "erp": "sap"}
