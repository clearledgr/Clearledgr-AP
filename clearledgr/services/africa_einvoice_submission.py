"""Africa e-invoice transmission layer (Wave 4 / F4 carry-over).

F4 shipped the FIRS / KRA / SARS payload generators. This module
adds the actual transmit-to-tax-authority surface — wired through
a pluggable :class:`TaxAuthoritySubmitter` interface so each
customer's certified Access/Service Provider (Sovos, Pwani Tech,
the KRA-issued TIMS device, etc.) plugs in as a sibling adapter
without touching the AP-cycle code.

Layers:

  * ``TaxAuthoritySubmitter`` — abstract base. ``submit(payload)``
    returns a :class:`SubmissionResult` (accepted / rejected /
    error + provider-issued reference).
  * ``NotConfiguredSubmitter`` — default. Returns ``error`` with
    reason ``provider_not_configured`` so the ledger still
    captures the attempt.
  * Per-country selectors (``settings_json[einvoice_provider][NG] =
    'sovos'`` etc.) → factory returns the configured adapter.
  * :func:`submit_africa_einvoice` — orchestrator that:
      - Generates the payload via build_einvoice_from_ap_item().
      - Inserts a ``tax_authority_submissions`` row in 'pending'.
      - Calls the configured submitter.
      - Updates the row with the provider response + status.
      - Stamps the provider reference onto the AP item's metadata
        (so the JE preview / audit chain link is in one place).
      - Emits an audit event keyed by (org, ap_item, country).
"""
from __future__ import annotations

import json
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


_SUPPORTED_COUNTRIES = ("NG", "KE", "ZA")


# ── Result shape ───────────────────────────────────────────────────


@dataclass
class SubmissionResult:
    """Outcome of one submission attempt."""

    status: str               # accepted | rejected | error
    provider: str
    provider_reference: Optional[str] = None  # FIRS IRN, KRA CUIN, etc.
    response: Dict[str, Any] = field(default_factory=dict)
    error_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "provider": self.provider,
            "provider_reference": self.provider_reference,
            "response": dict(self.response),
            "error_reason": self.error_reason,
        }


# ── Submitter interface + default ──────────────────────────────────


class TaxAuthoritySubmitter(ABC):
    """Pluggable submission interface.

    Concrete implementations wrap a certified Access Service Provider
    SDK / REST API. The orchestrator ``submit_africa_einvoice``
    never sees provider-specific code — it only sees this interface.
    """

    provider: str = "unknown"

    @abstractmethod
    async def submit(
        self,
        *,
        country: str,
        payload: Dict[str, Any],
    ) -> SubmissionResult:
        ...


class NotConfiguredSubmitter(TaxAuthoritySubmitter):
    """Default — no ASP/PSP configured for the workspace."""

    provider = "not_configured"

    async def submit(
        self,
        *,
        country: str,
        payload: Dict[str, Any],
    ) -> SubmissionResult:
        return SubmissionResult(
            status="error",
            provider=self.provider,
            error_reason="provider_not_configured",
            response={"country": country},
        )


# ── Adapter registry ──────────────────────────────────────────────


_ADAPTERS_REGISTRY: Dict[str, Callable[..., TaxAuthoritySubmitter]] = {}


def register_submitter(name: str, factory_fn) -> None:
    _ADAPTERS_REGISTRY[name.strip().lower()] = factory_fn


def get_submitter_for_country(
    db,
    *,
    organization_id: str,
    country: str,
) -> TaxAuthoritySubmitter:
    """Resolve the org's configured submitter for the country.

    Resolution order:
      1. settings_json['einvoice_provider'][country.upper()]
      2. settings_json['einvoice_provider']['default']
      3. NotConfiguredSubmitter
    """
    code = (country or "").upper()
    configured: Optional[str] = None
    try:
        org = db.get_organization(organization_id) or {}
        settings: Any = org.get("settings") or org.get("settings_json") or {}
        if isinstance(settings, str):
            try:
                settings = json.loads(settings)
            except (ValueError, TypeError):
                settings = {}
        block = (settings or {}).get("einvoice_provider") or {}
        if isinstance(block, dict):
            configured = (
                str(block.get(code) or "").strip().lower() or None
            ) or (
                str(block.get("default") or "").strip().lower() or None
            )
    except Exception:
        configured = None

    if configured and configured in _ADAPTERS_REGISTRY:
        try:
            return _ADAPTERS_REGISTRY[configured](
                organization_id=organization_id,
                country=code,
            )
        except Exception:
            logger.exception(
                "africa_einvoice_submission: adapter %r init failed",
                configured,
            )

    return NotConfiguredSubmitter()


# ── Persistence ────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_meta(item: Dict[str, Any]) -> Dict[str, Any]:
    raw = item.get("metadata")
    if isinstance(raw, str):
        try:
            return json.loads(raw) if raw else {}
        except Exception:
            return {}
    return raw if isinstance(raw, dict) else {}


def _insert_submission_row(
    db,
    *,
    organization_id: str,
    ap_item_id: str,
    country: str,
    provider: str,
    document_type: str,
    payload: Dict[str, Any],
    actor_id: Optional[str],
) -> str:
    submission_id = f"TS-{uuid.uuid4().hex[:24]}"
    sql = (
        "INSERT INTO tax_authority_submissions "
        "(id, organization_id, ap_item_id, country, provider, "
        " document_type, payload_json, status, review_status, "
        " created_at, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', 'open', %s, %s)"
    )
    db.initialize()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (
            submission_id, organization_id, ap_item_id, country, provider,
            document_type, json.dumps(payload),
            _now_iso(), actor_id,
        ))
        conn.commit()
    return submission_id


def _update_submission_with_result(
    db,
    *,
    submission_id: str,
    result: SubmissionResult,
) -> None:
    db.initialize()
    sql = (
        "UPDATE tax_authority_submissions "
        "SET status = %s, provider_reference = %s, "
        "    provider_response_json = %s, error_reason = %s, "
        "    submitted_at = %s "
        "WHERE id = %s"
    )
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (
            result.status,
            result.provider_reference,
            json.dumps(result.response or {}),
            result.error_reason,
            _now_iso(),
            submission_id,
        ))
        conn.commit()


def get_submission(db, submission_id: str) -> Optional[Dict[str, Any]]:
    db.initialize()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM tax_authority_submissions WHERE id = %s",
            (submission_id,),
        )
        row = cur.fetchone()
    return _decode_row(row)


def list_submissions_for_ap_item(
    db,
    *,
    organization_id: str,
    ap_item_id: str,
) -> List[Dict[str, Any]]:
    db.initialize()
    sql = (
        "SELECT * FROM tax_authority_submissions "
        "WHERE organization_id = %s AND ap_item_id = %s "
        "ORDER BY created_at DESC"
    )
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (organization_id, ap_item_id))
        rows = cur.fetchall()
    return [d for d in (_decode_row(r) for r in rows) if d is not None]


def supersede_submission(
    db,
    *,
    organization_id: str,
    submission_id: str,
    reason: str,
    actor_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Mark an existing submission superseded so a fresh attempt
    is allowed (the partial-unique index permits one open per
    org+ap_item+country)."""
    db.initialize()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE tax_authority_submissions "
            "SET review_status = 'superseded', "
            "    superseded_at = %s, superseded_reason = %s "
            "WHERE id = %s AND organization_id = %s",
            (_now_iso(), reason, submission_id, organization_id),
        )
        conn.commit()
    return get_submission(db, submission_id)


def _decode_row(row) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    out = dict(row)
    for col in ("payload_json", "provider_response_json"):
        raw = out.pop(col, None)
        target = col.removesuffix("_json")
        if raw:
            try:
                out[target] = (
                    json.loads(raw) if isinstance(raw, str) else raw
                )
            except Exception:
                out[target] = None
        else:
            out[target] = None
    return out


# ── Orchestrator ───────────────────────────────────────────────────


@dataclass
class SubmitOutcome:
    submission_id: str
    status: str
    provider: str
    provider_reference: Optional[str] = None
    error_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "submission_id": self.submission_id,
            "status": self.status,
            "provider": self.provider,
            "provider_reference": self.provider_reference,
            "error_reason": self.error_reason,
        }


def submit_africa_einvoice(
    db,
    *,
    organization_id: str,
    ap_item_id: str,
    country: str,
    document_type: str = "invoice",
    actor_id: Optional[str] = None,
) -> SubmitOutcome:
    """Generate the payload + submit + record the outcome.

    Returns a :class:`SubmitOutcome`. Idempotent at the DB layer:
    the partial unique index on (org, ap_item, country) WHERE
    review_status='open' rejects a second open attempt — caller
    must supersede the prior open row first.
    """
    from clearledgr.services.africa_einvoice import (
        build_einvoice_from_ap_item,
    )

    code = (country or "").upper()
    if code not in _SUPPORTED_COUNTRIES:
        raise ValueError(
            f"unsupported_country:{code!r}; "
            f"supported={list(_SUPPORTED_COUNTRIES)}"
        )

    item = db.get_ap_item(ap_item_id)
    if item is None or item.get("organization_id") != organization_id:
        raise ValueError(f"ap_item_not_found:{ap_item_id!r}")

    org = db.get_organization(organization_id) or {
        "id": organization_id,
        "name": organization_id,
    }

    payload = build_einvoice_from_ap_item(
        country_code=code,
        ap_item=item,
        organization=org,
        document_type=document_type,
    )

    submitter = get_submitter_for_country(
        db, organization_id=organization_id, country=code,
    )

    # Insert pending row first so even submitter-failure paths
    # leave a ledger entry (the partial unique index keeps active
    # state consistent).
    try:
        submission_id = _insert_submission_row(
            db,
            organization_id=organization_id,
            ap_item_id=ap_item_id,
            country=code,
            provider=submitter.provider,
            document_type=document_type,
            payload=payload,
            actor_id=actor_id,
        )
    except Exception as exc:
        msg = str(exc).lower()
        if "duplicate key" in msg or "unique constraint" in msg:
            raise ValueError(
                "active_submission_exists: an open submission for "
                "this (org, ap_item, country) already exists. "
                "Supersede it before re-submitting."
            )
        raise

    # Drive the async submitter from sync.
    import asyncio
    import threading
    holder: Dict[str, Any] = {}

    def _runner():
        new_loop = asyncio.new_event_loop()
        try:
            holder["value"] = new_loop.run_until_complete(
                submitter.submit(country=code, payload=payload),
            )
        except Exception as exc:
            holder["value"] = SubmissionResult(
                status="error",
                provider=submitter.provider,
                error_reason=str(exc)[:500],
            )
        finally:
            new_loop.close()

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(timeout=60)
    result: SubmissionResult = holder.get("value") or SubmissionResult(
        status="error",
        provider=submitter.provider,
        error_reason="submitter_timed_out",
    )

    _update_submission_with_result(
        db, submission_id=submission_id, result=result,
    )

    # Stamp provider_reference on the AP item's metadata so audit /
    # JE-preview / vendor-inquiry surfaces have it in one query.
    if result.provider_reference:
        try:
            meta = _normalize_meta(item)
            ts_block = meta.setdefault("tax_authority_submissions", {})
            ts_block[code] = {
                "submission_id": submission_id,
                "provider": submitter.provider,
                "provider_reference": result.provider_reference,
                "submitted_at": _now_iso(),
            }
            db.update_ap_item(
                ap_item_id,
                metadata=meta,
                _actor_type="user" if actor_id else "system",
                _actor_id=actor_id or "africa_einvoice_submission",
                _source="africa_einvoice_submission",
                _decision_reason=(
                    f"Submitted to {submitter.provider} for {code}"
                ),
            )
        except Exception:
            logger.exception(
                "africa_einvoice_submission: AP-item meta stamp failed",
            )

    # Audit emit.
    try:
        db.append_audit_event({
            "ap_item_id": ap_item_id,
            "box_id": ap_item_id,
            "box_type": "ap_item",
            "event_type": (
                "tax_authority_submission_accepted"
                if result.status == "accepted"
                else "tax_authority_submission_attempted"
            ),
            "actor_type": "user" if actor_id else "system",
            "actor_id": actor_id or "africa_einvoice_submission",
            "organization_id": organization_id,
            "source": "africa_einvoice_submission",
            "idempotency_key": (
                f"tax_authority_submission:{organization_id}:"
                f"{ap_item_id}:{code}:{submission_id}"
            ),
            "metadata": {
                "submission_id": submission_id,
                "country": code,
                "provider": submitter.provider,
                "provider_reference": result.provider_reference,
                "status": result.status,
                "error_reason": result.error_reason,
            },
        })
    except Exception:
        logger.exception(
            "africa_einvoice_submission: audit emit failed",
        )

    return SubmitOutcome(
        submission_id=submission_id,
        status=result.status,
        provider=submitter.provider,
        provider_reference=result.provider_reference,
        error_reason=result.error_reason,
    )
