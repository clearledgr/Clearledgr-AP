"""Vendor master data sync — pulls vendor records from ERP into Clearledgr profiles.

Compares ERP vendor directory against existing vendor profiles, upserts
changes, and detects important mutations (new vendors, deactivated vendors,
bank detail changes, payment terms changes).

Designed to run as a daily background job.  Never raises — returns a
summary dict on completion or error.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


async def sync_vendors_from_erp(
    organization_id: str,
    force_refresh: bool = True,
) -> Dict[str, Any]:
    """Sync vendor master data from ERP to Clearledgr vendor profiles.

    Steps:
    1. Fetch all vendors from ERP (via list_all_vendors with force_refresh)
    2. Bulk query existing Clearledgr vendor profiles
    3. For each ERP vendor, upsert profile with ERP-sourced fields
    4. Detect changes: new vendors, deactivated, bank/terms changes
    5. Return sync summary

    Never raises — returns summary with error on failure.
    """
    started_at = datetime.now(timezone.utc)

    try:
        from clearledgr.core.database import get_db
        from clearledgr.integrations.erp_router import (
            get_erp_connection,
            list_all_vendors,
        )

        db = get_db()
        connection = get_erp_connection(organization_id)
        if not connection:
            return _empty_summary(organization_id, started_at, reason="no_erp_connection")

        erp_type = str(connection.type or "").strip().lower()

        # Step 1: Fetch all vendors from ERP
        erp_vendors = await list_all_vendors(
            organization_id,
            force_refresh=force_refresh,
        )
        if not erp_vendors:
            return _empty_summary(organization_id, started_at, reason="no_vendors_from_erp")

        # Step 2: Bulk query existing profiles
        vendor_names = [v["name"] for v in erp_vendors if v.get("name")]
        existing_profiles = db.get_vendor_profiles_bulk(organization_id, vendor_names)

        # Step 3+4: Upsert and detect changes
        synced = 0
        new_vendors: List[str] = []
        deactivated_vendors: List[str] = []
        terms_changed: List[Dict[str, Any]] = []
        reactivated_vendors: List[str] = []

        for erp_vendor in erp_vendors:
            name = erp_vendor.get("name")
            if not name:
                continue

            existing = existing_profiles.get(name)
            is_new = existing is None

            # Build metadata with ERP-sourced fields
            old_metadata = (existing or {}).get("metadata") or {}
            if isinstance(old_metadata, str):
                import json
                try:
                    old_metadata = json.loads(old_metadata)
                except Exception:
                    old_metadata = {}

            new_metadata = {
                **old_metadata,
                "erp_vendor_id": erp_vendor.get("vendor_id") or "",
                "erp_type": erp_type,
                "erp_email": erp_vendor.get("email") or "",
                "erp_phone": erp_vendor.get("phone") or "",
                "erp_address": erp_vendor.get("address") or "",
                "erp_tax_id": erp_vendor.get("tax_id") or "",
                "erp_currency": erp_vendor.get("currency") or "",
                "erp_active": erp_vendor.get("active", True),
                "erp_balance": erp_vendor.get("balance", 0.0),
                "erp_synced_at": started_at.isoformat(),
            }

            # Detect payment terms change
            erp_terms = erp_vendor.get("payment_terms") or ""
            old_terms = (existing or {}).get("payment_terms") or ""
            if not is_new and erp_terms and old_terms and erp_terms != old_terms:
                terms_changed.append({
                    "vendor": name,
                    "old_terms": old_terms,
                    "new_terms": erp_terms,
                })

            # Detect deactivation
            erp_active = erp_vendor.get("active", True)
            was_active = old_metadata.get("erp_active", True) if not is_new else True
            if not is_new and was_active and not erp_active:
                deactivated_vendors.append(name)
            if not is_new and not was_active and erp_active:
                reactivated_vendors.append(name)

            if is_new:
                new_vendors.append(name)

            # Upsert — only set fields that the vendor profile schema supports
            upsert_fields: Dict[str, Any] = {"metadata": new_metadata}
            if erp_terms:
                upsert_fields["payment_terms"] = erp_terms

            try:
                db.upsert_vendor_profile(organization_id, name, **upsert_fields)
                synced += 1
            except Exception as exc:
                logger.warning(
                    "[VendorSync] Failed to upsert vendor %s for org %s: %s",
                    name, organization_id, exc,
                )

        completed_at = datetime.now(timezone.utc)
        duration_s = round((completed_at - started_at).total_seconds(), 1)

        summary = {
            "organization_id": organization_id,
            "erp_type": erp_type,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "duration_s": duration_s,
            "erp_vendor_count": len(erp_vendors),
            "synced_count": synced,
            "new_vendors": new_vendors,
            "new_vendor_count": len(new_vendors),
            "deactivated_vendors": deactivated_vendors,
            "deactivated_count": len(deactivated_vendors),
            "reactivated_vendors": reactivated_vendors,
            "reactivated_count": len(reactivated_vendors),
            "terms_changed": terms_changed,
            "terms_changed_count": len(terms_changed),
        }

        logger.info(
            "[VendorSync] org=%s erp=%s synced=%d new=%d deactivated=%d terms_changed=%d (%.1fs)",
            organization_id, erp_type, synced,
            len(new_vendors), len(deactivated_vendors), len(terms_changed), duration_s,
        )

        return summary

    except Exception as exc:
        logger.error("[VendorSync] sync failed for org %s: %s", organization_id, exc)
        return _empty_summary(organization_id, started_at, reason=str(exc))


def _empty_summary(
    organization_id: str,
    started_at: datetime,
    reason: str = "",
) -> Dict[str, Any]:
    return {
        "organization_id": organization_id,
        "erp_type": None,
        "started_at": started_at.isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "duration_s": 0,
        "erp_vendor_count": 0,
        "synced_count": 0,
        "new_vendors": [],
        "new_vendor_count": 0,
        "deactivated_vendors": [],
        "deactivated_count": 0,
        "reactivated_vendors": [],
        "reactivated_count": 0,
        "terms_changed": [],
        "terms_changed_count": 0,
        "reason": reason,
    }
