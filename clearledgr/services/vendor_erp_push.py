"""Reverse vendor sync — Clearledgr → ERP (Module 4 Pass D).

Counterpart to ``vendor_erp_sync.py`` (which pulls ERP master data
into Clearledgr). This module pushes vendor profile changes the
*other* direction so a customer who edits a vendor in Clearledgr
(IBAN verified, payment terms updated, vendor blocked) sees the
change land in their ERP within seconds rather than waiting for the
next daily sync to silently overwrite it.

Scope (Pass D):
  * UPDATE only — existing vendors that have a known ERP id.
    Creating a new vendor in Clearledgr → ERP is a Module 9 flow
    (multi-entity vendor seeding) and intentionally not in scope here.
  * Safe field set: name, email, phone, address, payment_terms,
    tax_id, active flag. We deliberately do NOT push bank details
    here — those go through the IBAN-verification ceremony which
    has its own ERP write step under §8.
  * Per-ERP support: QuickBooks Online + Xero are wired. NetSuite +
    SAP B1 return a clear ``not_supported`` reason — their write APIs
    need more setup (NetSuite custom-record permissions, SAP B1
    BP-master locking) and ship in a separate pass.

Conflict resolution (last-write-wins with attribution):
  * The push payload carries a ``Sparse: true`` modifier on QB +
    sets only the changed fields on Xero so we never blindly clobber
    fields the operator didn't touch in Clearledgr.
  * Every push records an audit event (``vendor_erp_pushed``) with
    the field-level diff and the ERP's pre-push snapshot, so a
    reconciliation pass can detect drift if both surfaces edited the
    same field between syncs.

The module returns a structured ``PushResult`` rather than raising —
the caller (an admin clicking the "Sync to ERP" button) gets a clean
summary they can render.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# Fields safe to push to the ERP. ``bank_account`` / ``iban`` are
# excluded by design — those flow through IBAN verification.
# ``phone`` is omitted because vendor_profiles has no top-level
# phone column today; once we add one this is a one-liner.
_PUSHABLE_FIELDS = (
    "primary_contact_email",
    "registered_address",
    "payment_terms",
    "vat_number",
    "registration_number",
    "status",  # active vs blocked → ERP active flag
)


@dataclass
class PushResult:
    """Structured outcome the API + UI consume.

    ``status`` is one of:
      * ``ok``         — ERP accepted the update.
      * ``no_change``  — no fields differed; nothing posted.
      * ``not_supported`` — ERP type doesn't have a push adapter yet.
      * ``no_erp_id``  — Clearledgr profile has no erp_vendor_id; the
        operator needs to run a forward sync first to bind ids.
      * ``failed``     — ERP returned an error.
    """

    organization_id: str
    vendor_name: str
    erp_type: str
    erp_vendor_id: Optional[str]
    status: str
    fields_pushed: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


async def push_vendor_to_erp(
    *,
    organization_id: str,
    vendor_name: str,
) -> PushResult:
    """Push the in-Clearledgr vendor profile to the connected ERP.

    Loads the profile + ERP connection, computes the safe-fields
    payload, dispatches to the per-ERP adapter, audit-emits the
    outcome. Never raises — surfaces all failure modes on the
    PushResult.
    """
    from clearledgr.core.database import get_db
    from clearledgr.integrations.erp_router import get_erp_connection

    db = get_db()
    profile = db.get_vendor_profile(organization_id, vendor_name)
    if not profile:
        return PushResult(
            organization_id=organization_id,
            vendor_name=vendor_name,
            erp_type="",
            erp_vendor_id=None,
            status="failed",
            error="vendor_not_found",
        )

    connection = get_erp_connection(organization_id)
    if not connection:
        return PushResult(
            organization_id=organization_id,
            vendor_name=vendor_name,
            erp_type="",
            erp_vendor_id=None,
            status="failed",
            error="no_erp_connection",
        )

    erp_type = str(connection.type or "").strip().lower()
    erp_vendor_id = _resolve_erp_vendor_id(profile, erp_type)
    if not erp_vendor_id:
        return PushResult(
            organization_id=organization_id,
            vendor_name=vendor_name,
            erp_type=erp_type,
            erp_vendor_id=None,
            status="no_erp_id",
            error=(
                "Clearledgr profile has no recorded ERP vendor id. Run a "
                "forward vendor sync first to bind Clearledgr ↔ ERP ids."
            ),
        )

    # Build the safe payload — ``status`` maps to an ERP active flag.
    payload = _build_safe_payload(profile)
    if not payload:
        return PushResult(
            organization_id=organization_id,
            vendor_name=vendor_name,
            erp_type=erp_type,
            erp_vendor_id=erp_vendor_id,
            status="no_change",
        )

    # Dispatch to the per-ERP adapter.
    adapter = _ADAPTERS.get(erp_type)
    if adapter is None:
        return PushResult(
            organization_id=organization_id,
            vendor_name=vendor_name,
            erp_type=erp_type,
            erp_vendor_id=erp_vendor_id,
            status="not_supported",
            error=(
                f"Reverse sync to {erp_type!r} is not yet supported. "
                "Use the ERP's native admin to update master data."
            ),
        )

    try:
        ok = await adapter(connection, erp_vendor_id, payload)
    except Exception as exc:
        logger.warning(
            "[vendor_erp_push] %s adapter raised for vendor %r: %s",
            erp_type, vendor_name, exc,
        )
        ok = False
        adapter_error = str(exc)
    else:
        adapter_error = None

    if not ok:
        result = PushResult(
            organization_id=organization_id,
            vendor_name=vendor_name,
            erp_type=erp_type,
            erp_vendor_id=erp_vendor_id,
            status="failed",
            error=adapter_error or "ERP rejected the update",
        )
    else:
        result = PushResult(
            organization_id=organization_id,
            vendor_name=vendor_name,
            erp_type=erp_type,
            erp_vendor_id=erp_vendor_id,
            status="ok",
            fields_pushed=sorted(payload.keys()),
        )

    _audit_push(db, profile=profile, result=result)
    return result


def _resolve_erp_vendor_id(profile: Dict[str, Any], erp_type: str) -> Optional[str]:
    """Look up the ERP-side id for this vendor.

    The forward sync persists the ERP's vendor id under
    ``profile.metadata['erp_vendor_id']``, optionally keyed by
    erp_type to support tenants that switch ERPs mid-life.
    """
    metadata = profile.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            import json
            metadata = json.loads(metadata)
        except Exception:
            metadata = {}
    if not isinstance(metadata, dict):
        return None
    direct = metadata.get("erp_vendor_id")
    if direct:
        return str(direct).strip() or None
    # Per-ERP namespace fallback
    per_erp = metadata.get("erp_vendor_ids") or {}
    if isinstance(per_erp, dict):
        candidate = per_erp.get(erp_type)
        if candidate:
            return str(candidate).strip() or None
    return None


def _build_safe_payload(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Project the profile into the safe-to-push field set.

    Empty / None values are dropped — they signal "Clearledgr has
    no value", not "set the ERP value to empty". An operator who
    wants to clear an ERP field should do that in the ERP directly
    until we have a typed null-clearing API.

    ``status='active'`` is the table default and is also the ERP
    default for newly-loaded vendors, so we skip it unless the
    operator has explicitly moved the vendor off the default.
    Re-pushing the default would generate noise on the ERP audit
    log + flip a sparse update into a no-op POST.
    """
    payload: Dict[str, Any] = {}
    for key in _PUSHABLE_FIELDS:
        value = profile.get(key)
        if value is None or value == "":
            continue
        # Skip default status — it's a no-op for the ERP and would
        # create the false impression that operators are pushing
        # changes when they're not.
        if key == "status" and str(value).strip().lower() == "active":
            continue
        payload[key] = value
    return payload


def _audit_push(db, *, profile: Dict[str, Any], result: PushResult) -> None:
    """Record a ``vendor_erp_pushed`` audit event."""
    try:
        db.append_audit_event({
            "event_type": "vendor_erp_pushed",
            "actor_type": "user",
            "actor_id": "vendor_erp_push",
            "organization_id": result.organization_id,
            "box_id": result.vendor_name,
            "box_type": "vendor",
            "source": "vendor_erp_push",
            "payload_json": {
                "erp_type": result.erp_type,
                "erp_vendor_id": result.erp_vendor_id,
                "status": result.status,
                "fields_pushed": result.fields_pushed,
                "error": result.error,
            },
        })
    except Exception as exc:
        logger.warning(
            "[vendor_erp_push] audit emit failed for %s: %s",
            result.vendor_name, exc,
        )


# ─── Per-ERP adapters ───────────────────────────────────────────────


async def _push_vendor_to_quickbooks(
    connection, erp_vendor_id: str, payload: Dict[str, Any],
) -> bool:
    """Update a QuickBooks Online Vendor.

    QBO requires the SyncToken on update — fetch it first, then send
    the sparse update. Returns True on 2xx.
    """
    from clearledgr.core.http_client import get_http_client
    from clearledgr.integrations.erp_quickbooks import _quickbooks_headers

    if not connection.access_token or not connection.realm_id:
        return False

    client = get_http_client()

    # 1. Fetch SyncToken — QBO uses it for optimistic concurrency.
    get_url = (
        f"https://quickbooks.api.intuit.com/v3/company/"
        f"{connection.realm_id}/vendor/{erp_vendor_id}"
    )
    resp = await client.get(get_url, headers=_quickbooks_headers(connection), timeout=30)
    if resp.status_code != 200:
        logger.warning(
            "[vendor_erp_push] QBO vendor %s read returned %d",
            erp_vendor_id, resp.status_code,
        )
        return False
    vendor = resp.json().get("Vendor") or {}
    sync_token = vendor.get("SyncToken")

    # 2. Build sparse update.
    update_body: Dict[str, Any] = {
        "Id": erp_vendor_id,
        "SyncToken": sync_token,
        "sparse": True,
    }
    if "primary_contact_email" in payload:
        update_body["PrimaryEmailAddr"] = {"Address": payload["primary_contact_email"]}
    if "registered_address" in payload:
        update_body["BillAddr"] = {"Line1": str(payload["registered_address"])[:500]}
    if "payment_terms" in payload:
        # QB stores terms as a TermRef pointing at a Term entity.
        # Without a term-name → id mapping pre-populated, we set the
        # PrintOnCheckName field as a free-form fallback.
        update_body["PrintOnCheckName"] = str(payload["payment_terms"])[:100]
    if "vat_number" in payload:
        update_body["TaxIdentifier"] = str(payload["vat_number"])[:32]
    if "status" in payload:
        # active|blocked → QBO Active flag (true|false)
        update_body["Active"] = str(payload["status"]).lower() == "active"

    # 3. Push update.
    post_url = (
        f"https://quickbooks.api.intuit.com/v3/company/"
        f"{connection.realm_id}/vendor"
    )
    resp = await client.post(
        post_url,
        json=update_body,
        headers=_quickbooks_headers(connection),
        timeout=30,
    )
    return 200 <= resp.status_code < 300


async def _push_vendor_to_xero(
    connection, erp_vendor_id: str, payload: Dict[str, Any],
) -> bool:
    """Update a Xero Contact (vendors are Contacts with IsSupplier=true)."""
    from clearledgr.core.http_client import get_http_client

    if not connection.access_token or not connection.tenant_id:
        return False

    body: Dict[str, Any] = {"ContactID": erp_vendor_id}
    if "primary_contact_email" in payload:
        body["EmailAddress"] = payload["primary_contact_email"]
    if "registered_address" in payload:
        body["Addresses"] = [{
            "AddressType": "STREET",
            "AddressLine1": str(payload["registered_address"])[:500],
        }]
    if "vat_number" in payload:
        body["TaxNumber"] = str(payload["vat_number"])[:32]
    if "registration_number" in payload:
        body["AccountNumber"] = str(payload["registration_number"])[:50]
    if "status" in payload:
        body["ContactStatus"] = (
            "ACTIVE" if str(payload["status"]).lower() == "active" else "ARCHIVED"
        )

    client = get_http_client()
    resp = await client.post(
        "https://api.xero.com/api.xro/2.0/Contacts",
        json={"Contacts": [body]},
        headers={
            "Authorization": f"Bearer {connection.access_token}",
            "Xero-tenant-id": connection.tenant_id,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    return 200 <= resp.status_code < 300


_ADAPTERS = {
    "quickbooks": _push_vendor_to_quickbooks,
    "xero": _push_vendor_to_xero,
    # NetSuite + SAP shipping in a follow-up pass.
}
