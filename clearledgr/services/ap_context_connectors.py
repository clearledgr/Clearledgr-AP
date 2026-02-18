"""Multi-system AP context aggregation helpers.

This module enriches AP item context with non-email systems:
- bank feeds / bank matches
- credit-card statement transactions
- procurement (purchase orders + goods receipts)
- payroll accrual context
- spreadsheet references
- document management system (DMS) references

All connectors are best-effort and designed to fail open so the sidebar can
still render partial context.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from clearledgr.services.accruals import AccrualType, get_accruals_service
from clearledgr.services.purchase_orders import get_purchase_order_service


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_text(value: Any) -> str:
    text = str(value or "").strip()
    return text


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sheet_id_from_ref(ref: str) -> Optional[str]:
    value = _normalize_text(ref)
    if not value:
        return None
    if "/" not in value and len(value) >= 20:
        return value
    try:
        parsed = urlparse(value)
    except Exception:
        return None
    host = (parsed.netloc or "").lower()
    path = parsed.path or ""
    if "docs.google.com" in host and "/spreadsheets/d/" in path:
        return path.split("/spreadsheets/d/", 1)[1].split("/", 1)[0].strip() or None
    query_id = parse_qs(parsed.query).get("id")
    if query_id and query_id[0]:
        return query_id[0]
    return None


def _bank_source_ref(match: Dict[str, Any], idx: int = 0) -> str:
    provider = _normalize_text(match.get("provider") or "bank")
    txn_id = _normalize_text(
        match.get("transaction_id")
        or match.get("id")
        or match.get("reference")
        or match.get("bank_transaction_id")
    )
    if txn_id:
        return f"{provider}:{txn_id}"
    amount = _safe_float(match.get("amount"))
    tx_date = _normalize_text(match.get("date") or match.get("transaction_date"))
    return f"{provider}:{tx_date}:{amount:.2f}:{idx}"


def _card_source_ref(match: Dict[str, Any], idx: int = 0) -> str:
    provider = _normalize_text(match.get("provider") or match.get("issuer") or "card")
    txn_id = _normalize_text(
        match.get("transaction_id")
        or match.get("id")
        or match.get("reference")
        or match.get("statement_line_id")
    )
    if txn_id:
        return f"{provider}:{txn_id}"
    card_last4 = _normalize_text(match.get("card_last4") or match.get("last4") or "xxxx")
    amount = _safe_float(match.get("amount"))
    tx_date = _normalize_text(match.get("date") or match.get("transaction_date") or match.get("posted_at"))
    return f"{provider}:{card_last4}:{tx_date}:{amount:.2f}:{idx}"


def _source_payload(
    ap_item_id: str,
    source_type: str,
    source_ref: str,
    subject: str,
    sender: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "ap_item_id": ap_item_id,
        "source_type": source_type,
        "source_ref": source_ref,
        "subject": subject,
        "sender": sender,
        "detected_at": _now_iso(),
        "metadata": metadata or {},
    }


@dataclass
class ConnectorResult:
    payload: Dict[str, Any]
    discovered_sources: List[Dict[str, Any]]
    has_data: bool
    errors: List[str]


def _collect_bank_context(
    item: Dict[str, Any],
    metadata: Dict[str, Any],
    audit_events: List[Dict[str, Any]],
) -> ConnectorResult:
    errors: List[str] = []
    matches: List[Dict[str, Any]] = []
    discovered: List[Dict[str, Any]] = []

    raw_bank = metadata.get("bank_match")
    if isinstance(raw_bank, dict) and raw_bank:
        matches.append(raw_bank)

    raw_bank_matches = metadata.get("bank_matches") or metadata.get("matched_bank_transactions")
    if isinstance(raw_bank_matches, list):
        matches.extend([entry for entry in raw_bank_matches if isinstance(entry, dict)])

    for event in audit_events:
        event_type = _normalize_text(event.get("event_type")).lower()
        if "bank" not in event_type:
            continue
        payload = _as_dict(event.get("payload_json"))
        if not payload:
            continue
        event_match = payload.get("bank_match")
        if isinstance(event_match, dict):
            matches.append(event_match)
        event_matches = payload.get("bank_matches")
        if isinstance(event_matches, list):
            matches.extend([entry for entry in event_matches if isinstance(entry, dict)])

    normalized: List[Dict[str, Any]] = []
    seen_refs: set[str] = set()
    for idx, match in enumerate(matches):
        ref = _bank_source_ref(match, idx=idx)
        if ref in seen_refs:
            continue
        seen_refs.add(ref)
        amount = _safe_float(match.get("amount"))
        normalized.append(
            {
                "source_ref": ref,
                "provider": _normalize_text(match.get("provider") or "bank"),
                "transaction_id": _normalize_text(
                    match.get("transaction_id") or match.get("id") or match.get("bank_transaction_id")
                ),
                "reference": _normalize_text(match.get("reference")),
                "amount": amount,
                "currency": _normalize_text(match.get("currency") or item.get("currency") or "USD"),
                "transaction_date": _normalize_text(match.get("date") or match.get("transaction_date")),
                "description": _normalize_text(
                    match.get("description") or match.get("merchant_name") or match.get("counterparty_name")
                ),
                "confidence": _safe_float(match.get("confidence"), _safe_float(match.get("score"), 0.0)),
                "status": _normalize_text(match.get("status") or "matched"),
            }
        )
        discovered.append(
            _source_payload(
                ap_item_id=str(item.get("id") or ""),
                source_type="bank",
                source_ref=ref,
                subject=f"Bank transaction {ref}",
                sender=_normalize_text(match.get("provider") or "bank_feed"),
                metadata={"match": match},
            )
        )

    provider_status: List[Dict[str, Any]] = []
    try:
        from clearledgr.services.bank_feeds import get_bank_service

        for provider_name in ("okra", "truelayer", "nordigen"):
            try:
                service = get_bank_service(provider=provider_name)
                provider_status.append(
                    {
                        "provider": provider_name,
                        "configured": bool(getattr(service, "is_configured", lambda: False)()),
                    }
                )
            except Exception as exc:  # pragma: no cover - defensive
                provider_status.append({"provider": provider_name, "configured": False})
                errors.append(f"{provider_name}:{exc}")
    except Exception:
        # Bank feed package may be unavailable in some runtime slices.
        provider_status = []

    payload = {
        "matched_transactions": normalized,
        "count": len(normalized),
        "providers": provider_status,
        "connector_available": any(p.get("configured") for p in provider_status) or bool(normalized),
    }
    return ConnectorResult(
        payload=payload,
        discovered_sources=discovered,
        has_data=bool(normalized),
        errors=errors,
    )


def _collect_card_statement_context(
    item: Dict[str, Any],
    metadata: Dict[str, Any],
    audit_events: List[Dict[str, Any]],
) -> ConnectorResult:
    errors: List[str] = []
    matches: List[Dict[str, Any]] = []
    discovered: List[Dict[str, Any]] = []

    for key in (
        "credit_card_match",
        "card_match",
        "card_transaction",
        "credit_card_transaction",
    ):
        raw = metadata.get(key)
        if isinstance(raw, dict) and raw:
            matches.append(raw)

    for key in (
        "credit_card_matches",
        "card_matches",
        "card_transactions",
        "credit_card_transactions",
        "matched_card_transactions",
        "statement_lines",
    ):
        raw = metadata.get(key)
        if isinstance(raw, list):
            matches.extend([entry for entry in raw if isinstance(entry, dict)])

    for event in audit_events:
        event_type = _normalize_text(event.get("event_type")).lower()
        if "card" not in event_type and "statement" not in event_type:
            continue
        payload = _as_dict(event.get("payload_json"))
        if not payload:
            continue
        event_match = payload.get("credit_card_match") or payload.get("card_match")
        if isinstance(event_match, dict):
            matches.append(event_match)
        event_matches = payload.get("credit_card_matches") or payload.get("card_matches")
        if isinstance(event_matches, list):
            matches.extend([entry for entry in event_matches if isinstance(entry, dict)])

    normalized: List[Dict[str, Any]] = []
    seen_refs: set[str] = set()
    for idx, match in enumerate(matches):
        ref = _card_source_ref(match, idx=idx)
        if ref in seen_refs:
            continue
        seen_refs.add(ref)
        provider = _normalize_text(match.get("provider") or match.get("issuer") or "card")
        amount = _safe_float(match.get("amount"))
        normalized.append(
            {
                "source_ref": ref,
                "provider": provider,
                "transaction_id": _normalize_text(
                    match.get("transaction_id")
                    or match.get("id")
                    or match.get("statement_line_id")
                ),
                "card_last4": _normalize_text(match.get("card_last4") or match.get("last4")),
                "amount": amount,
                "currency": _normalize_text(match.get("currency") or item.get("currency") or "USD"),
                "transaction_date": _normalize_text(
                    match.get("date") or match.get("transaction_date") or match.get("posted_at")
                ),
                "description": _normalize_text(
                    match.get("description") or match.get("merchant_name") or match.get("reference")
                ),
                "status": _normalize_text(match.get("status") or "matched"),
                "confidence": _safe_float(match.get("confidence"), _safe_float(match.get("score"), 0.0)),
            }
        )
        discovered.append(
            _source_payload(
                ap_item_id=str(item.get("id") or ""),
                source_type="card_statement",
                source_ref=ref,
                subject=f"Card transaction {ref}",
                sender=provider,
                metadata={"match": match},
            )
        )

    payload = {
        "matched_transactions": normalized,
        "count": len(normalized),
        "connector_available": bool(normalized),
    }
    return ConnectorResult(
        payload=payload,
        discovered_sources=discovered,
        has_data=bool(normalized),
        errors=errors,
    )


def _collect_procurement_context(
    item: Dict[str, Any],
    metadata: Dict[str, Any],
) -> ConnectorResult:
    errors: List[str] = []
    discovered: List[Dict[str, Any]] = []
    payload: Dict[str, Any] = {
        "po": None,
        "goods_receipts": [],
        "match": {},
        "connector_available": False,
    }

    organization_id = _normalize_text(item.get("organization_id") or "default")
    po_number = _normalize_text(
        metadata.get("po_number")
        or metadata.get("purchase_order_number")
        or metadata.get("po_id")
        or item.get("po_number")
    )
    vendor_name = _normalize_text(item.get("vendor_name") or metadata.get("vendor_name"))
    amount = _safe_float(item.get("amount"))
    invoice_id = _normalize_text(item.get("id"))

    try:
        service = get_purchase_order_service(organization_id)
    except Exception as exc:  # pragma: no cover - defensive
        return ConnectorResult(payload=payload, discovered_sources=[], has_data=False, errors=[str(exc)])

    try:
        po = service.get_po_by_number(po_number) if po_number else None
        if not po and vendor_name:
            candidates = service.search_pos(vendor_name=vendor_name)
            if candidates:
                po = candidates[0]

        if po:
            po_dict = po.to_dict()
            payload["po"] = po_dict
            payload["connector_available"] = True
            payload["goods_receipts"] = [
                receipt.to_dict() for receipt in service.get_goods_receipts_for_po(po.po_id)
            ]
            match = service.match_invoice_to_po(
                invoice_id=invoice_id or f"invoice:{po.po_number}",
                invoice_amount=amount,
                invoice_vendor=vendor_name,
                invoice_po_number=po.po_number,
                invoice_lines=_as_list(metadata.get("line_items")),
            )
            payload["match"] = match.to_dict()
            discovered.append(
                _source_payload(
                    ap_item_id=invoice_id,
                    source_type="procurement",
                    source_ref=po.po_number or po.po_id,
                    subject=f"PO {po.po_number or po.po_id}",
                    sender="procurement_system",
                    metadata={"po_id": po.po_id},
                )
            )
        elif po_number:
            payload["match"] = {
                "status": "not_found",
                "exceptions": [
                    {
                        "type": "no_po",
                        "message": f"No purchase order found for reference {po_number}",
                        "severity": "medium",
                    }
                ],
            }
    except Exception as exc:
        errors.append(str(exc))

    has_data = bool(payload.get("po") or payload.get("goods_receipts") or payload.get("match"))
    return ConnectorResult(payload=payload, discovered_sources=discovered, has_data=has_data, errors=errors)


def _collect_payroll_context(
    item: Dict[str, Any],
    metadata: Dict[str, Any],
) -> ConnectorResult:
    errors: List[str] = []
    discovered: List[Dict[str, Any]] = []
    payload: Dict[str, Any] = {
        "entries": [],
        "count": 0,
        "total_amount": 0.0,
        "connector_available": False,
    }

    organization_id = _normalize_text(item.get("organization_id") or "default")
    vendor_name = _normalize_text(item.get("vendor_name") or metadata.get("vendor_name") or "")
    invoice_id = _normalize_text(item.get("id"))

    try:
        service = get_accruals_service(organization_id)
        entries = service.list_accruals(
            accrual_type=AccrualType.PAYROLL,
            vendor_name=vendor_name or None,
            limit=20,
        )
        normalized = [entry.to_dict() for entry in entries]
        payload["entries"] = normalized
        payload["count"] = len(normalized)
        payload["total_amount"] = round(sum(_safe_float(entry.get("amount")) for entry in normalized), 2)
        payload["connector_available"] = True

        for entry in normalized[:10]:
            accrual_id = _normalize_text(entry.get("accrual_id"))
            if not accrual_id:
                continue
            discovered.append(
                _source_payload(
                    ap_item_id=invoice_id,
                    source_type="payroll",
                    source_ref=accrual_id,
                    subject=_normalize_text(entry.get("description") or "Payroll accrual"),
                    sender="payroll_system",
                    metadata={"accrual": entry},
                )
            )
    except Exception as exc:
        errors.append(str(exc))

    has_data = bool(payload.get("count"))
    return ConnectorResult(payload=payload, discovered_sources=discovered, has_data=has_data, errors=errors)


def _collect_spreadsheet_context(
    item: Dict[str, Any],
    metadata: Dict[str, Any],
    sources: List[Dict[str, Any]],
) -> ConnectorResult:
    errors: List[str] = []
    discovered: List[Dict[str, Any]] = []
    refs: List[str] = []
    invoice_id = _normalize_text(item.get("id"))

    # Metadata-driven references.
    for key in (
        "spreadsheet_url",
        "spreadsheet_id",
        "sheet_url",
        "sheet_id",
        "sheets_url",
        "sheet_link",
    ):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            refs.append(value.strip())

    if isinstance(metadata.get("spreadsheets"), list):
        for entry in metadata["spreadsheets"]:
            if isinstance(entry, str):
                refs.append(entry)
            elif isinstance(entry, dict):
                candidate = entry.get("url") or entry.get("spreadsheet_url") or entry.get("spreadsheet_id")
                if candidate:
                    refs.append(str(candidate))

    # Existing linked sources.
    for source in sources:
        source_type = _normalize_text(source.get("source_type")).lower()
        if source_type in {"spreadsheet", "sheets"}:
            source_ref = _normalize_text(source.get("source_ref"))
            if source_ref:
                refs.append(source_ref)

    unique_refs: List[str] = []
    seen: set[str] = set()
    for ref in refs:
        normalized_ref = _normalize_text(ref)
        if not normalized_ref or normalized_ref in seen:
            continue
        seen.add(normalized_ref)
        unique_refs.append(normalized_ref)

    references: List[Dict[str, Any]] = []
    for ref in unique_refs:
        sheet_id = _sheet_id_from_ref(ref)
        references.append(
            {
                "reference": ref,
                "spreadsheet_id": sheet_id,
                "host": _normalize_text(urlparse(ref).netloc if "://" in ref else "google_sheets"),
            }
        )
        source_ref = sheet_id or ref
        discovered.append(
            _source_payload(
                ap_item_id=invoice_id,
                source_type="spreadsheet",
                source_ref=source_ref,
                subject=f"Spreadsheet {sheet_id or ref}",
                sender="sheets",
                metadata={"reference": ref},
            )
        )

    payload = {
        "references": references,
        "count": len(references),
        "connector_available": bool(references),
    }
    return ConnectorResult(
        payload=payload,
        discovered_sources=discovered,
        has_data=bool(references),
        errors=errors,
    )


def _collect_dms_context(
    item: Dict[str, Any],
    metadata: Dict[str, Any],
    sources: List[Dict[str, Any]],
) -> ConnectorResult:
    errors: List[str] = []
    discovered: List[Dict[str, Any]] = []
    refs: List[str] = []
    invoice_id = _normalize_text(item.get("id"))

    for key in (
        "dms_url",
        "dms_document_url",
        "dms_id",
        "dms_document_id",
        "document_url",
    ):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            refs.append(value.strip())

    if isinstance(metadata.get("dms_documents"), list):
        for entry in metadata["dms_documents"]:
            if isinstance(entry, str):
                refs.append(entry.strip())
            elif isinstance(entry, dict):
                candidate = entry.get("url") or entry.get("id") or entry.get("document_id")
                if candidate:
                    refs.append(str(candidate).strip())

    for source in sources:
        source_type = _normalize_text(source.get("source_type")).lower()
        if source_type == "dms":
            source_ref = _normalize_text(source.get("source_ref"))
            if source_ref:
                refs.append(source_ref)

    unique_refs: List[str] = []
    seen: set[str] = set()
    for ref in refs:
        normalized_ref = _normalize_text(ref)
        if not normalized_ref or normalized_ref in seen:
            continue
        seen.add(normalized_ref)
        unique_refs.append(normalized_ref)

    documents: List[Dict[str, Any]] = []
    for ref in unique_refs:
        parsed = urlparse(ref) if "://" in ref else None
        documents.append(
            {
                "reference": ref,
                "document_id": _normalize_text(ref.split("/")[-1]) if parsed else ref,
                "host": _normalize_text(parsed.netloc if parsed else "dms"),
            }
        )
        discovered.append(
            _source_payload(
                ap_item_id=invoice_id,
                source_type="dms",
                source_ref=ref,
                subject=f"DMS document {ref}",
                sender="dms",
                metadata={"reference": ref},
            )
        )

    payload = {
        "documents": documents,
        "count": len(documents),
        "connector_available": bool(documents),
    }
    return ConnectorResult(
        payload=payload,
        discovered_sources=discovered,
        has_data=bool(documents),
        errors=errors,
    )


def build_multi_system_context(
    item: Dict[str, Any],
    metadata: Dict[str, Any],
    sources: List[Dict[str, Any]],
    audit_events: List[Dict[str, Any]],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Build normalized multi-system context and discovered source links."""
    bank = _collect_bank_context(item=item, metadata=metadata, audit_events=audit_events)
    cards = _collect_card_statement_context(item=item, metadata=metadata, audit_events=audit_events)
    procurement = _collect_procurement_context(item=item, metadata=metadata)
    payroll = _collect_payroll_context(item=item, metadata=metadata)
    spreadsheets = _collect_spreadsheet_context(item=item, metadata=metadata, sources=sources)
    dms_documents = _collect_dms_context(item=item, metadata=metadata, sources=sources)

    discovered = (
        bank.discovered_sources
        + cards.discovered_sources
        + procurement.discovered_sources
        + payroll.discovered_sources
        + spreadsheets.discovered_sources
        + dms_documents.discovered_sources
    )

    summary = {
        "has_bank": bank.has_data,
        "has_card_statements": cards.has_data,
        "has_procurement": procurement.has_data,
        "has_payroll": payroll.has_data,
        "has_spreadsheets": spreadsheets.has_data,
        "has_dms_documents": dms_documents.has_data,
        "connected_systems": [
            name
            for name, present in (
                ("bank", bank.has_data),
                ("card_statements", cards.has_data),
                ("procurement", procurement.has_data),
                ("payroll", payroll.has_data),
                ("spreadsheets", spreadsheets.has_data),
                ("dms_documents", dms_documents.has_data),
            )
            if present
        ],
        "errors": (
            bank.errors
            + cards.errors
            + procurement.errors
            + payroll.errors
            + spreadsheets.errors
            + dms_documents.errors
        ),
    }

    return (
        {
            "bank": bank.payload,
            "card_statements": cards.payload,
            "procurement": procurement.payload,
            "payroll": payroll.payload,
            "spreadsheets": spreadsheets.payload,
            "dms_documents": dms_documents.payload,
            "summary": summary,
        },
        discovered,
    )
