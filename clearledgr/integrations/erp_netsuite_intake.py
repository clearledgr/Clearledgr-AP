"""NetSuite read-direction enrichment for ERP-native bill intake.

When the SuiteScript ``afterSubmit`` fires a webhook for a vendor-bill
event, the payload carries the bill summary but not the full
coordination context the email-arrival pipeline needs (vendor master,
PO + GRN linkage, line items + GL distribution, vendor bank history).
This module pulls all of that from NetSuite via TBA REST so the
ERP-native dispatcher can build an :class:`InvoiceData` and call
``InvoiceWorkflowService.process_new_invoice`` — same pipeline as the
Gmail path.

Public entry point: :func:`fetch_intake_context` returns an
:class:`IntakeContext` TypedDict the dispatcher consumes.

Module is OAuth-1.0 / TBA throughout — same auth path the existing
write-direction module (:mod:`clearledgr.integrations.erp_netsuite`)
already uses. We deliberately don't share a single httpx client object
between modules; ``get_http_client()`` returns the process-wide
shared one.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, TypedDict

from clearledgr.core.http_client import get_http_client
from clearledgr.integrations.erp_netsuite import _oauth_header

logger = logging.getLogger(__name__)


# ─── Public types ──────────────────────────────────────────────────


class IntakeContext(TypedDict, total=False):
    """Enriched data the ERP-native dispatcher needs to construct
    InvoiceData and run it through the full pipeline."""

    bill_header: Dict[str, Any]
    bill_lines: List[Dict[str, Any]]
    expense_lines: List[Dict[str, Any]]
    vendor: Optional[Dict[str, Any]]
    linked_po: Optional[Dict[str, Any]]
    linked_po_lines: List[Dict[str, Any]]
    goods_receipts: List[Dict[str, Any]]
    vendor_bank_history: List[Dict[str, Any]]
    raw_payload: Dict[str, Any]


# ─── Public entry point ────────────────────────────────────────────


async def fetch_intake_context(connection, ns_internal_id: str) -> IntakeContext:
    """Pull every enrichment field the coordination pipeline needs.

    Best-effort: every sub-fetch is wrapped in a try/except so a
    missing optional record (e.g. no PO linked to this bill) returns
    an empty section rather than failing the whole intake. The
    dispatcher proceeds with whatever was available; the validation
    pipeline downstream tolerates partial data (a bill with no
    PO linkage will fail 3-way match and route to needs_approval —
    correct behaviour, distinct from a bill that the dispatcher
    couldn't process).
    """
    context: IntakeContext = {
        "bill_header": {},
        "bill_lines": [],
        "expense_lines": [],
        "vendor": None,
        "linked_po": None,
        "linked_po_lines": [],
        "goods_receipts": [],
        "vendor_bank_history": [],
        "raw_payload": {},
    }

    bill = await _fetch_vendor_bill_expanded(connection, ns_internal_id)
    if not bill:
        return context

    context["raw_payload"] = bill
    context["bill_header"] = _extract_bill_header(bill)
    context["bill_lines"] = _extract_bill_item_lines(bill)
    context["expense_lines"] = _extract_bill_expense_lines(bill)

    # Vendor record — entity.id is on the bill payload
    entity = bill.get("entity") if isinstance(bill.get("entity"), dict) else {}
    vendor_id = str(entity.get("id") or "").strip()
    if vendor_id:
        try:
            context["vendor"] = await _fetch_vendor(connection, vendor_id)
            if context["vendor"]:
                context["vendor_bank_history"] = _extract_vendor_bank(context["vendor"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("ns_intake: vendor fetch failed for %s — %s", vendor_id, exc)

    # PO linkage — read off bill line items. NetSuite bill line
    # `orderDoc.id` is the PO transaction internal id. A single bill
    # can reference multiple POs; the canonical 3-way-match call only
    # consumes one PO at a time, so we fetch the *first* unique PO
    # and treat additional POs as a multi-PO exception (handled by
    # the validation pipeline).
    po_internal_ids = _extract_po_internal_ids(context["bill_lines"])
    if po_internal_ids:
        primary_po_id = po_internal_ids[0]
        try:
            po = await _fetch_purchase_order(connection, primary_po_id)
            if po:
                context["linked_po"] = po
                context["linked_po_lines"] = _extract_po_lines(po)
                # Item Receipts (GRN equivalent) — a separate query
                # against the transaction record because they're not
                # nested in the PO payload.
                try:
                    context["goods_receipts"] = await _fetch_item_receipts_for_po(
                        connection, primary_po_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "ns_intake: item-receipts fetch failed for PO %s — %s",
                        primary_po_id, exc,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("ns_intake: PO fetch failed for %s — %s", primary_po_id, exc)

    return context


# ─── Sub-fetchers (reusable building blocks) ───────────────────────


async def _fetch_vendor_bill_expanded(connection, bill_id: str) -> Optional[Dict[str, Any]]:
    """GET /vendorBill/{id}?expand=item,expense — full payload with line + GL distribution."""
    if not connection.account_id:
        return None
    url = (
        f"https://{connection.account_id}.suitetalk.api.netsuite.com"
        f"/services/rest/record/v1/vendorBill/{bill_id}?expandSubResources=true"
    )
    return await _ns_get(connection, url, label="vendor_bill")


async def _fetch_vendor(connection, vendor_id: str) -> Optional[Dict[str, Any]]:
    """GET /vendor/{id} — vendor master record + bank details."""
    if not connection.account_id:
        return None
    url = (
        f"https://{connection.account_id}.suitetalk.api.netsuite.com"
        f"/services/rest/record/v1/vendor/{vendor_id}"
    )
    return await _ns_get(connection, url, label="vendor")


async def _fetch_purchase_order(connection, po_id: str) -> Optional[Dict[str, Any]]:
    """GET /purchaseOrder/{id}?expandSubResources=true — PO + lines."""
    if not connection.account_id:
        return None
    url = (
        f"https://{connection.account_id}.suitetalk.api.netsuite.com"
        f"/services/rest/record/v1/purchaseOrder/{po_id}?expandSubResources=true"
    )
    return await _ns_get(connection, url, label="purchase_order")


async def _fetch_item_receipts_for_po(connection, po_internal_id: str) -> List[Dict[str, Any]]:
    """SuiteQL: item receipts created from a PO.

    NetSuite's REST suiteql endpoint accepts a SQL-flavoured query
    and returns the resulting transaction rows. We then GET each one
    individually for line-level received quantities.
    """
    if not connection.account_id:
        return []
    suiteql_url = (
        f"https://{connection.account_id}.suitetalk.api.netsuite.com"
        f"/services/rest/query/v1/suiteql"
    )
    query = {
        "q": (
            "SELECT id, tranid, trandate FROM transaction "
            f"WHERE type='ItemRcpt' AND createdfrom = {int(po_internal_id) if str(po_internal_id).isdigit() else po_internal_id} "
            "ORDER BY trandate DESC"
        )
    }
    auth_header = _oauth_header(connection, "POST", suiteql_url)
    client = get_http_client()
    receipts: List[Dict[str, Any]] = []
    try:
        response = await client.post(
            suiteql_url,
            headers={
                "Authorization": auth_header,
                "Content-Type": "application/json",
                "Prefer": "transient",
            },
            json=query,
            timeout=30,
        )
        if response.status_code >= 400:
            logger.warning("ns_intake: suiteql GR query %s — %s", response.status_code, response.text[:300])
            return []
        items = (response.json() or {}).get("items") or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("ns_intake: suiteql GR query raised — %s", exc)
        return []

    for item in items:
        rec_id = str(item.get("id") or "").strip()
        if not rec_id:
            continue
        try:
            full = await _ns_get(
                connection,
                f"https://{connection.account_id}.suitetalk.api.netsuite.com"
                f"/services/rest/record/v1/itemReceipt/{rec_id}?expandSubResources=true",
                label="item_receipt",
            )
            if full:
                receipts.append(full)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ns_intake: itemReceipt fetch failed for %s — %s", rec_id, exc)
    return receipts


async def _ns_get(connection, url: str, *, label: str) -> Optional[Dict[str, Any]]:
    """Shared GET path — TBA OAuth + 401 retry hook + JSON parse."""
    auth_header = _oauth_header(connection, "GET", url)
    client = get_http_client()
    try:
        response = await client.get(
            url,
            headers={"Authorization": auth_header, "Content-Type": "application/json"},
            timeout=60,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("ns_intake: %s GET raised — %s", label, exc)
        return None
    if response.status_code == 401:
        logger.warning("ns_intake: %s 401 (auth issue) — connection may need re-auth", label)
        return None
    if response.status_code == 404:
        return None
    if response.status_code >= 400:
        logger.warning(
            "ns_intake: %s %s — %s",
            label, response.status_code, str(response.text)[:300],
        )
        return None
    try:
        return response.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("ns_intake: %s JSON parse failed — %s", label, exc)
        return None


# ─── Field-extraction helpers ──────────────────────────────────────


def _extract_bill_header(bill: Dict[str, Any]) -> Dict[str, Any]:
    entity = bill.get("entity") if isinstance(bill.get("entity"), dict) else {}
    currency = bill.get("currency") if isinstance(bill.get("currency"), dict) else {}
    subsidiary = bill.get("subsidiary") if isinstance(bill.get("subsidiary"), dict) else {}
    return {
        "ns_internal_id": str(bill.get("id") or ""),
        "tran_id": str(bill.get("tranId") or ""),
        "tran_date": str(bill.get("tranDate") or ""),
        "due_date": str(bill.get("dueDate") or ""),
        "amount": bill.get("total") or bill.get("amount"),
        "tax_amount": bill.get("taxTotal"),
        "subtotal": bill.get("subTotal"),
        "currency_id": str(currency.get("id") or ""),
        "currency_symbol": str(currency.get("refName") or ""),
        "vendor_id": str(entity.get("id") or ""),
        "vendor_name": str(entity.get("refName") or ""),
        "subsidiary_id": str(subsidiary.get("id") or ""),
        "subsidiary_name": str(subsidiary.get("refName") or ""),
        "memo": str(bill.get("memo") or ""),
        "external_id": str(bill.get("externalId") or ""),
        "status": str(bill.get("status", {}).get("id") if isinstance(bill.get("status"), dict) else bill.get("status") or ""),
        "approval_status": str(bill.get("approvalStatus", {}).get("refName") if isinstance(bill.get("approvalStatus"), dict) else ""),
        "payment_hold": bill.get("paymentHold"),
        "po_number": "",  # filled in from bill_lines below
    }


def _extract_bill_item_lines(bill: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Bill line items (the 'item' sublist after expansion)."""
    item_block = bill.get("item") if isinstance(bill.get("item"), dict) else {}
    raw_lines = item_block.get("items") if isinstance(item_block.get("items"), list) else []
    lines: List[Dict[str, Any]] = []
    for raw in raw_lines:
        if not isinstance(raw, dict):
            continue
        order_doc = raw.get("orderDoc") if isinstance(raw.get("orderDoc"), dict) else {}
        item_ref = raw.get("item") if isinstance(raw.get("item"), dict) else {}
        account = raw.get("account") if isinstance(raw.get("account"), dict) else {}
        lines.append({
            "line_number": raw.get("line"),
            "description": str(raw.get("description") or item_ref.get("refName") or ""),
            "item_id": str(item_ref.get("id") or ""),
            "item_name": str(item_ref.get("refName") or ""),
            "quantity": raw.get("quantity"),
            "unit_price": raw.get("rate"),
            "amount": raw.get("amount"),
            "gl_code": str(account.get("refName") or account.get("id") or ""),
            "tax_amount": raw.get("taxAmount"),
            "po_internal_id": str(order_doc.get("id") or ""),
            "po_number": str(order_doc.get("refName") or ""),
        })
    return lines


def _extract_bill_expense_lines(bill: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Bill expense (GL) lines — separate sublist from item lines."""
    expense_block = bill.get("expense") if isinstance(bill.get("expense"), dict) else {}
    raw_lines = expense_block.get("items") if isinstance(expense_block.get("items"), list) else []
    lines: List[Dict[str, Any]] = []
    for raw in raw_lines:
        if not isinstance(raw, dict):
            continue
        account = raw.get("account") if isinstance(raw.get("account"), dict) else {}
        lines.append({
            "line_number": raw.get("line"),
            "description": str(raw.get("memo") or ""),
            "amount": raw.get("amount"),
            "gl_code": str(account.get("refName") or account.get("id") or ""),
        })
    return lines


def _extract_po_internal_ids(bill_lines: List[Dict[str, Any]]) -> List[str]:
    seen: List[str] = []
    for line in bill_lines:
        po_id = str(line.get("po_internal_id") or "").strip()
        if po_id and po_id not in seen:
            seen.append(po_id)
    return seen


def _extract_po_lines(po: Dict[str, Any]) -> List[Dict[str, Any]]:
    item_block = po.get("item") if isinstance(po.get("item"), dict) else {}
    raw_lines = item_block.get("items") if isinstance(item_block.get("items"), list) else []
    lines: List[Dict[str, Any]] = []
    for raw in raw_lines:
        if not isinstance(raw, dict):
            continue
        item_ref = raw.get("item") if isinstance(raw.get("item"), dict) else {}
        lines.append({
            "line_id": raw.get("lineuniquekey") or raw.get("line"),
            "description": str(raw.get("description") or item_ref.get("refName") or ""),
            "item_id": str(item_ref.get("id") or ""),
            "quantity": raw.get("quantity"),
            "quantity_received": raw.get("quantityReceived"),
            "quantity_billed": raw.get("quantityBilled"),
            "unit_price": raw.get("rate"),
            "amount": raw.get("amount"),
        })
    return lines


def _extract_vendor_bank(vendor: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Vendor bank details — NetSuite stores these under
    `predefinedBankData` (legacy field) or via the EFT Bank Details
    sublist `bankDetails`. We probe both."""
    history: List[Dict[str, Any]] = []
    bank_blob = vendor.get("predefinedBankData")
    if isinstance(bank_blob, dict):
        history.append({
            "iban": bank_blob.get("iban"),
            "account_number": bank_blob.get("bankNumber"),
            "swift": bank_blob.get("bicNumber"),
            "bank_name": bank_blob.get("bankName"),
            "source": "predefinedBankData",
        })
    bank_sublist = vendor.get("bankDetails")
    if isinstance(bank_sublist, dict):
        for raw in (bank_sublist.get("items") or []):
            if not isinstance(raw, dict):
                continue
            history.append({
                "iban": raw.get("iban") or raw.get("ibanNumber"),
                "account_number": raw.get("bankNumber") or raw.get("accountNumber"),
                "swift": raw.get("bicNumber") or raw.get("swiftCode"),
                "bank_name": raw.get("bankName"),
                "is_default": raw.get("default") or raw.get("isDefault"),
                "source": "bankDetails",
            })
    return history
