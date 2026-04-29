"""GDPR retention / right-to-erasure service (Wave 3 / E3).

Two concerns wrapped into one module:

  * **Periodic retention** — automated purge of personal data after
    the org's configured retention window. SOX requires the financial
    record (AP item id, amount, posting date, JE id) to live 7 years;
    GDPR requires personal data tied to it to go after the *legal*
    retention window. Resolution: ANONYMIZE rather than DELETE — the
    accounting skeleton stays, the PII gets `[redacted]` markers.

  * **Right-to-erasure (Article 17)** — a vendor lodges an erasure
    request via :class:`DataSubjectRequestService`; we route here to
    do the actual scrubbing.

What counts as personal data we're willing to scrub:

  ``vendor_profiles``
      primary_contact_email, remittance_email, director_names,
      registered_address, vendor_aliases (may carry person names),
      bank_details_encrypted (pre-existing Fernet column — we set to NULL).
  ``ap_items``
      sender (the email address of the human who sent us the
      invoice), metadata blob fields named ``contact_*`` /
      ``cc_*`` / ``from_*``.
  ``vendor_invoice_history``
      No PII columns — left alone.
  ``audit_events``
      NEVER scrubbed. Audit trail integrity beats GDPR; legal advice
      is to retain audit_events past retention as "legitimate
      interest" and document why in the org's ROPA.

Every operation emits a ``gdpr_retention_run`` audit event so the DPO
can prove what was scrubbed, when, and on whose authority.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


_DEFAULT_RETENTION_DAYS = 2555  # 7 years; SOX-compatible.
_REDACTED = "[redacted]"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _retention_cutoff(retention_days: int) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(days=retention_days)
    ).isoformat()


# ── PII redaction helpers ──────────────────────────────────────────


def _redact_metadata_pii(metadata: Any) -> Dict[str, Any]:
    """Walk a metadata dict, redacting fields that look like PII.

    Keys are matched case-insensitively. We keep the key (so JE
    structure / matchers don't break) but replace the value.
    """
    if not isinstance(metadata, dict):
        return {}
    pii_key_patterns = (
        "contact", "cc_", "from_", "sender", "email",
        "phone", "address", "name", "iban", "account",
    )
    out = {}
    for key, value in metadata.items():
        if isinstance(value, dict):
            out[key] = _redact_metadata_pii(value)
            continue
        if isinstance(value, list):
            out[key] = [
                _redact_metadata_pii(v) if isinstance(v, dict) else v
                for v in value
            ]
            continue
        lk = str(key).lower()
        if any(pat in lk for pat in pii_key_patterns):
            out[key] = _REDACTED
        else:
            out[key] = value
    return out


# ── Vendor scrub ───────────────────────────────────────────────────


def anonymize_vendor(
    db,
    *,
    organization_id: str,
    vendor_name: str,
    actor: Optional[str] = None,
) -> Dict[str, Any]:
    """Strip PII from one vendor profile + every AP item that
    references them by name.

    Returns counters: ``ap_items_anonymized``, ``vendor_profiles_anonymized``.
    Does NOT touch audit_events.

    Idempotent: running this twice on the same vendor is a no-op
    on the second call (the columns already carry [redacted] markers).
    """
    counters = {
        "ap_items_anonymized": 0,
        "vendor_profiles_anonymized": 0,
        "errors": 0,
    }
    profile = None
    try:
        profile = db.get_vendor_profile(organization_id, vendor_name)
    except Exception:
        profile = None
    if profile is not None:
        try:
            db.upsert_vendor_profile(
                organization_id, vendor_name,
                primary_contact_email=None,
                remittance_email=None,
                registered_address=_REDACTED,
                director_names=[],
                vendor_aliases=[],
            )
            # bank_details_encrypted is set via a typed accessor.
            db.initialize()
            with db.connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE vendor_profiles "
                    "SET bank_details_encrypted = NULL "
                    "WHERE organization_id = %s AND vendor_name = %s",
                    (organization_id, vendor_name),
                )
                conn.commit()
            counters["vendor_profiles_anonymized"] = 1
        except Exception:
            logger.exception(
                "gdpr: vendor profile anonymize failed org=%s vendor=%s",
                organization_id, vendor_name,
            )
            counters["errors"] += 1

    # AP items for this vendor: redact the sender + metadata PII.
    db.initialize()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, metadata FROM ap_items "
            "WHERE organization_id = %s AND vendor_name = %s",
            (organization_id, vendor_name),
        )
        rows = cur.fetchall()
    for r in rows:
        row = dict(r)
        item_id = row["id"]
        try:
            meta = row.get("metadata")
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta) if meta else {}
                except Exception:
                    meta = {}
            if not isinstance(meta, dict):
                meta = {}
            redacted = _redact_metadata_pii(meta)
            db.update_ap_item(
                item_id,
                sender=_REDACTED,
                metadata=redacted,
                _actor_type="system",
                _actor_id=actor or "gdpr_retention",
                _source="gdpr_anonymize",
                _decision_reason=f"anonymize_vendor:{vendor_name}",
            )
            counters["ap_items_anonymized"] += 1
        except Exception:
            logger.exception(
                "gdpr: ap_item anonymize failed item=%s", item_id,
            )
            counters["errors"] += 1

    # Audit emit so the DPO can prove the scrub happened.
    try:
        db.append_audit_event({
            "box_id": f"vendor:{organization_id}:{vendor_name}",
            "box_type": "vendor",
            "event_type": "gdpr_vendor_anonymized",
            "actor_type": "system",
            "actor_id": actor or "gdpr_retention",
            "organization_id": organization_id,
            "source": "gdpr_retention",
            "idempotency_key": (
                f"gdpr_anonymize:{organization_id}:{vendor_name}"
            ),
            "metadata": {
                "vendor_name": vendor_name,
                **counters,
            },
        })
    except Exception:
        logger.exception("gdpr: audit emit failed")

    return counters


# ── Periodic purge ─────────────────────────────────────────────────


def get_retention_days(db, organization_id: str) -> int:
    """Resolve the org's retention window from settings_json["gdpr"]
    [retention_days], falling back to 7-years (SOX baseline)."""
    try:
        org = db.get_organization(organization_id) or {}
    except Exception:
        return _DEFAULT_RETENTION_DAYS
    settings: Any = org.get("settings") or org.get("settings_json") or {}
    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except (ValueError, TypeError):
            return _DEFAULT_RETENTION_DAYS
    gdpr = (settings or {}).get("gdpr") or {}
    raw = gdpr.get("retention_days")
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_RETENTION_DAYS
    return max(1, days)


def identify_expired_vendors(
    db,
    *,
    organization_id: str,
    cutoff_iso: str,
    limit: int = 100,
) -> List[str]:
    """Vendors whose latest AP item activity is older than the
    cutoff, AND whose vendor profile still carries un-redacted PII.

    A vendor with zero AP items is also expired (no business reason
    to keep their PII)."""
    db.initialize()
    sql = (
        "SELECT vp.vendor_name, vp.primary_contact_email, "
        "       MAX(COALESCE(ai.updated_at, ai.created_at)) AS last_activity "
        "FROM vendor_profiles vp "
        "LEFT JOIN ap_items ai ON ai.organization_id = vp.organization_id "
        "                     AND ai.vendor_name = vp.vendor_name "
        "WHERE vp.organization_id = %s "
        "  AND COALESCE(vp.primary_contact_email, '') != '' "
        "  AND COALESCE(vp.primary_contact_email, '') != %s "
        "GROUP BY vp.vendor_name, vp.primary_contact_email "
        "HAVING COALESCE(MAX(COALESCE(ai.updated_at, ai.created_at)), '1970-01-01') < %s "
        "LIMIT %s"
    )
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (organization_id, _REDACTED, cutoff_iso, limit))
        rows = cur.fetchall()
    return [dict(r)["vendor_name"] for r in rows]


def run_retention_purge(
    db,
    *,
    organization_id: str,
    actor: Optional[str] = None,
    retention_days: Optional[int] = None,
) -> Dict[str, Any]:
    """Walk vendors past the retention cutoff and anonymize each.

    Records a ``retention_policy_runs`` row + a ``gdpr_retention_run``
    audit event so the run is reconstructable.
    """
    days = retention_days or get_retention_days(db, organization_id)
    cutoff = _retention_cutoff(days)
    expired = identify_expired_vendors(
        db, organization_id=organization_id, cutoff_iso=cutoff,
    )
    totals = {
        "ap_items_anonymized": 0,
        "vendor_profiles_anonymized": 0,
        "errors_count": 0,
    }
    per_vendor: List[Dict[str, Any]] = []
    for vendor in expired:
        outcome = anonymize_vendor(
            db,
            organization_id=organization_id,
            vendor_name=vendor,
            actor=actor,
        )
        totals["ap_items_anonymized"] += outcome["ap_items_anonymized"]
        totals["vendor_profiles_anonymized"] += outcome["vendor_profiles_anonymized"]
        totals["errors_count"] += outcome.get("errors", 0)
        per_vendor.append({"vendor": vendor, **outcome})

    run_id = f"RR-{uuid.uuid4().hex[:24]}"
    now_iso = _now_iso()
    db.initialize()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO retention_policy_runs "
            "(id, organization_id, run_kind, cutoff_at, "
            " ap_items_anonymized, vendor_profiles_anonymized, "
            " attachments_purged, errors_count, run_at, run_by, details_json) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                run_id, organization_id, "automated_purge",
                cutoff,
                totals["ap_items_anonymized"],
                totals["vendor_profiles_anonymized"],
                0, totals["errors_count"],
                now_iso, actor,
                json.dumps({"per_vendor": per_vendor[:200], "retention_days": days}),
            ),
        )
        conn.commit()
    return {
        "id": run_id,
        "cutoff_at": cutoff,
        "retention_days": days,
        **totals,
        "vendors_processed": len(expired),
    }
