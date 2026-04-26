"""NetSuite custom-field annotation target.

Writes ``custbody_clearledgr_state`` (configurable via target_config)
on the Vendor Bill record so a NetSuite user looking at the bill
sees the current Clearledgr state inline — no need to open the
Clearledgr panel sub-tab.

Customer-side prerequisite: the SuiteApp's bundle must declare the
custom field. The backend can write to it; the field has to exist.
Default field id ``custbody_clearledgr_state`` is what the
Clearledgr SuiteApp installer creates; customers who renamed it
override via ``target_config.field_id``.

Skips for non-NetSuite bills + bills that don't have an
``ns_internal_id`` in their erp_metadata.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from clearledgr.services.annotation_targets.base import (
    AnnotationContext,
    AnnotationResult,
    register_target,
)

logger = logging.getLogger(__name__)


class NetSuiteCustomFieldTarget:
    target_type = "netsuite_custom_field"

    async def apply(self, context: AnnotationContext) -> AnnotationResult:
        if context.source_type != "netsuite":
            return AnnotationResult(
                status="skipped",
                skip_reason="not_netsuite_source",
            )
        ns_internal_id = self._extract_ns_id(context)
        if not ns_internal_id:
            return AnnotationResult(
                status="skipped",
                skip_reason="missing_ns_internal_id",
            )

        connection = self._resolve_connection(context.organization_id)
        if connection is None:
            return AnnotationResult(
                status="skipped",
                skip_reason="no_netsuite_connection",
            )

        field_id = str(context.target_config.get("field_id") or "custbody_clearledgr_state").strip()
        body = {field_id: context.new_state}

        from clearledgr.core.http_client import get_http_client
        from clearledgr.integrations.erp_netsuite import _oauth_header
        url = (
            f"https://{connection.account_id}.suitetalk.api.netsuite.com"
            f"/services/rest/record/v1/vendorBill/{ns_internal_id}"
        )
        try:
            auth_header = _oauth_header(connection, "PATCH", url)
        except Exception as exc:  # noqa: BLE001
            return AnnotationResult(
                status="failed",
                response_body_preview=f"oauth_header_failed: {exc}",
            )
        client = get_http_client()
        try:
            response = await client.request(
                "PATCH", url,
                headers={
                    "Authorization": auth_header,
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal",
                },
                json=body,
                timeout=30,
            )
        except Exception:
            raise  # outbox retries

        if response.status_code >= 400:
            snippet = ""
            try:
                snippet = response.text[:500]
            except Exception:
                snippet = ""
            # 4xx is configuration error (custom field doesn't exist,
            # role lacks permission) — surface but DON'T raise so the
            # outbox doesn't keep retrying a permanent failure.
            if 400 <= response.status_code < 500:
                return AnnotationResult(
                    status="failed",
                    response_code=response.status_code,
                    response_body_preview=snippet,
                    metadata={"reason": "netsuite_4xx_config"},
                )
            # 5xx is transient — raise so outbox retries.
            raise RuntimeError(
                f"netsuite_custom_field {response.status_code}: {snippet[:200]}"
            )

        return AnnotationResult(
            status="succeeded",
            applied_value=context.new_state,
            external_id=ns_internal_id,
            response_code=response.status_code,
            metadata={"field_id": field_id},
        )

    @staticmethod
    def _extract_ns_id(context: AnnotationContext) -> str:
        # First check explicit erp_metadata, then fall back to
        # looking up the AP item.
        meta = context.metadata or {}
        ns_id = str(meta.get("ns_internal_id") or "").strip()
        if ns_id:
            return ns_id
        from clearledgr.core.database import get_db
        db = get_db()
        if not hasattr(db, "get_ap_item"):
            return ""
        try:
            row = db.get_ap_item(context.box_id) or {}
        except Exception:
            return ""
        # erp_reference holds the NetSuite internal id for ERP-native
        # bills (set at intake by the dispatcher).
        return str(row.get("erp_reference") or "").strip()

    @staticmethod
    def _resolve_connection(organization_id: str):
        from clearledgr.core.database import get_db
        from clearledgr.integrations.erp_router import _erp_connection_from_row
        db = get_db()
        if not hasattr(db, "get_erp_connections"):
            return None
        try:
            for row in db.get_erp_connections(organization_id):
                if str(row.get("erp_type") or "").lower() == "netsuite":
                    return _erp_connection_from_row(row)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "netsuite_custom_field: connection lookup failed — %s", exc,
            )
        return None


register_target(NetSuiteCustomFieldTarget())
