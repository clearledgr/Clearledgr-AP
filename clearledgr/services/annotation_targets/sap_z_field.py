"""SAP S/4HANA Z-field annotation target.

Writes ``Z_CLEARLEDGR_STATE`` (configurable) on the supplier-invoice
record via OData PATCH so SAP users see the Clearledgr state inline.

Customer-side prerequisite: the Z-field must be added to the
``A_SupplierInvoice`` view via SAP's customizing transaction (CMOD /
APPEND structure). Backend writes; field has to exist on the
customer's SAP. Skipped if the OData endpoint returns 4xx — same
pattern as NetSuite.
"""
from __future__ import annotations

import logging

from clearledgr.services.annotation_targets.base import (
    AnnotationContext,
    AnnotationResult,
    register_target,
)

logger = logging.getLogger(__name__)


class SapZFieldTarget:
    target_type = "sap_z_field"

    async def apply(self, context: AnnotationContext) -> AnnotationResult:
        if context.source_type != "sap_s4hana":
            return AnnotationResult(
                status="skipped", skip_reason="not_sap_source",
            )
        cc, doc, fy = self._extract_composite_key(context)
        if not (cc and doc and fy):
            return AnnotationResult(
                status="skipped",
                skip_reason="missing_composite_key",
                metadata={"have": {"cc": cc, "doc": doc, "fy": fy}},
            )

        field_id = str(context.target_config.get("field_id") or "YY1_CLEARLEDGR_STATE").strip()
        body = {field_id: context.new_state}

        from clearledgr.integrations.erp_sap_s4hana import (
            _resolve_connection, _build_auth_headers,
            _fetch_csrf_token, _escape_odata,
        )
        from clearledgr.core.http_client import get_http_client

        connection, base_url, service_path, error = _resolve_connection(context.organization_id)
        if error:
            return AnnotationResult(
                status="skipped",
                skip_reason=error.get("reason") or "no_sap_connection",
            )

        url = (
            f"{base_url}{service_path}/A_SupplierInvoice("
            f"CompanyCode='{_escape_odata(cc)}',"
            f"SupplierInvoice='{_escape_odata(doc)}',"
            f"FiscalYear='{_escape_odata(fy)}'"
            f")"
        )
        headers = await _build_auth_headers(connection)
        if "error" in headers:
            return AnnotationResult(
                status="skipped", skip_reason=headers["error"],
            )
        csrf = await _fetch_csrf_token(base_url, service_path, headers)
        if csrf:
            headers["x-csrf-token"] = csrf
        headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

        client = get_http_client()
        try:
            response = await client.request(
                "PATCH", url, headers=headers, json=body, timeout=30,
            )
        except Exception:
            raise  # outbox retries on connection-level errors

        if response.status_code >= 400:
            snippet = ""
            try:
                snippet = response.text[:500]
            except Exception:
                snippet = ""
            # 4xx config error: surface but don't raise so the
            # outbox doesn't loop on a permanent misconfig
            # (e.g. Z-field doesn't exist on the customer's SAP).
            if 400 <= response.status_code < 500:
                return AnnotationResult(
                    status="failed",
                    response_code=response.status_code,
                    response_body_preview=snippet,
                    metadata={"reason": "sap_4xx_config"},
                )
            raise RuntimeError(
                f"sap_z_field {response.status_code}: {snippet[:200]}"
            )

        return AnnotationResult(
            status="succeeded",
            applied_value=context.new_state,
            external_id=f"{cc}/{doc}/{fy}",
            response_code=response.status_code,
            metadata={"field_id": field_id},
        )

    @staticmethod
    def _extract_composite_key(context: AnnotationContext) -> tuple[str, str, str]:
        meta = context.metadata or {}
        cc = str(meta.get("company_code") or meta.get("sap_company_code") or "").strip()
        doc = str(meta.get("supplier_invoice") or meta.get("sap_supplier_invoice") or "").strip()
        fy = str(meta.get("fiscal_year") or meta.get("sap_fiscal_year") or "").strip()
        if cc and doc and fy:
            return cc, doc, fy
        # Fall back to the AP item's erp_reference
        from clearledgr.core.database import get_db
        db = get_db()
        if not hasattr(db, "get_ap_item"):
            return "", "", ""
        try:
            row = db.get_ap_item(context.box_id) or {}
        except Exception:
            return "", "", ""
        ref = str(row.get("erp_reference") or "").strip()
        if not ref or "/" not in ref:
            return "", "", ""
        parts = ref.split("/")
        if len(parts) != 3:
            return "", "", ""
        return parts[0], parts[1], parts[2]


register_target(SapZFieldTarget())
