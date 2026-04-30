"""Sanctions screening service (Wave 3 / E1).

Single entry point for "screen this vendor against sanctions /
PEP / adverse-media lists":

  * Calls the configured KYC provider's :py:meth:`sanctions_screen`
    (today: ComplyAdvantageProvider, falls back to NotConfigured).
  * Persists the raw provider response to ``vendor_sanctions_checks``
    so SOC 2 + 6AMLD audit can reconstruct what was returned.
  * Rolls the disposition up onto ``vendor_profiles.sanctions_status``
    so the pre-payment gate is a single column read.
  * On a hit, fans out via the existing
    :func:`revalidate_in_flight_ap_items` so any in-flight bills for
    that vendor get the ``vendor_sanctions_hit`` exception.

The pre-payment gate (:func:`gate_payment_against_sanctions`) is the
hard guardrail used by ``record_payment_confirmation`` to refuse a
payment to a blocked vendor — defence-in-depth against an operator
manually clicking through despite the AP-item-level exception.

Re-screen scheduler hook (:func:`vendors_due_for_rescreen`) returns
the vendors whose latest screen is older than the cadence
(default: 30 days). The Celery beat schedule wires this up.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


_DEFAULT_RESCREEN_DAYS = 30


class SanctionsBlockedError(Exception):
    """Raised by :func:`gate_payment_against_sanctions` when the
    vendor's rolled-up sanctions_status is 'blocked'."""

    def __init__(self, vendor_name: str, latest_check_id: Optional[str]):
        self.vendor_name = vendor_name
        self.latest_check_id = latest_check_id
        super().__init__(
            f"sanctions_blocked: vendor={vendor_name!r} "
            f"latest_check={latest_check_id!r}"
        )


@dataclass
class ScreeningResult:
    vendor_name: str
    status: str                # clear | hit | inconclusive | error | provider_adapter_pending
    sanctions_status: str      # rolled-up disposition stored on the vendor profile
    check_id: Optional[str]
    matches_count: int = 0
    revalidated_ap_items: int = 0
    error: Optional[str] = None


def _vendor_profile_disposition(provider_status: str) -> str:
    """Translate a provider-side status into the vendor_profiles
    rolled-up disposition. ``hit`` always lands as ``review`` (an
    operator must look at it before it becomes ``blocked``)."""
    if provider_status == "clear":
        return "clear"
    if provider_status == "hit":
        return "review"
    # error / inconclusive / provider_adapter_pending: don't overwrite
    # any prior disposition with a transient failure. Caller layer
    # decides whether to keep the existing status or set 'unscreened'.
    return "unscreened"


def screen_vendor(
    db,
    *,
    organization_id: str,
    vendor_name: str,
    country: Optional[str] = None,
    actor: Optional[str] = None,
) -> ScreeningResult:
    """Run a sanctions screen for one vendor.

    Resolves the workspace's KYC provider, calls
    :py:meth:`sanctions_screen` (async — wrapped in a sync entry
    here since the rest of the AP cycle is sync), persists the
    result, updates the vendor profile, and triggers in-flight
    revalidation on a hit.

    Synchronous to fit the existing payment-tracking + onboarding
    call sites. The provider's HTTP call still uses the async client
    underneath; we drive it through ``asyncio.run`` only when no
    loop is already running.
    """
    from clearledgr.services.onboarding.kyc_provider import get_kyc_provider
    # Adapter registration is import-side-effect — make sure it runs
    # so settings_json="complyadvantage" routes here, not to the
    # NotConfigured fallback.
    import clearledgr.services.onboarding.complyadvantage_provider  # noqa: F401

    profile = None
    try:
        profile = db.get_vendor_profile(organization_id, vendor_name)
    except Exception:
        profile = None

    resolved_country = country
    if not resolved_country and profile:
        addr = (profile.get("registered_address") or "").strip()
        # Best-effort country code extraction: last 2 chars of trimmed
        # address. Operators who care about correctness send the
        # explicit ``country`` kwarg; this fallback exists for the
        # periodic re-screener which doesn't have it.
        if addr and len(addr) >= 2:
            resolved_country = addr[-2:].upper()
    if not resolved_country:
        resolved_country = "GB"  # Default to UK for the EU/UK launch.

    provider = get_kyc_provider(organization_id, db=db)

    async def _run() -> Any:
        return await provider.sanctions_screen(
            legal_name=vendor_name,
            country=resolved_country,
        )

    # Drive the async provider call from a sync caller.
    try:
        asyncio.get_running_loop()
        # We're inside an existing event loop (ASGI handler). Schedule
        # via run_coroutine_threadsafe-like detour — but since
        # FastAPI handlers are async themselves, the typical caller
        # awaits a wrapper. For sync fallback, we spin a fresh loop
        # in a thread.
        import threading
        result_holder: Dict[str, Any] = {}
        def _runner() -> None:
            new_loop = asyncio.new_event_loop()
            try:
                result_holder["value"] = new_loop.run_until_complete(_run())
            finally:
                new_loop.close()
        t = threading.Thread(target=_runner, daemon=True)
        t.start()
        t.join(timeout=60)
        kyc_result = result_holder.get("value")
    except RuntimeError:
        kyc_result = asyncio.run(_run())

    if kyc_result is None:
        return ScreeningResult(
            vendor_name=vendor_name,
            status="error",
            sanctions_status=(
                (profile or {}).get("sanctions_status") or "unscreened"
            ),
            check_id=None,
            error="provider_call_failed",
        )

    # Persist the screening row first so the audit trail captures
    # every call, including errors.
    check_row = db.record_sanctions_check(
        organization_id=organization_id,
        vendor_name=vendor_name,
        check_type=kyc_result.check_type or "sanctions",
        provider=kyc_result.provider,
        status=kyc_result.status,
        provider_reference=kyc_result.provider_reference,
        matches=list(kyc_result.matches or []),
        evidence=dict(kyc_result.evidence or {}),
        raw_payload=dict(kyc_result.raw_payload or {}),
        checked_at=kyc_result.checked_at,
        checked_by=actor,
    )
    check_id = check_row.get("id")

    # Roll up to the vendor profile.
    new_disposition = _vendor_profile_disposition(kyc_result.status)
    if (
        kyc_result.status in ("error", "inconclusive", "provider_adapter_pending")
        and profile
    ):
        # Preserve the prior disposition on transient failure so a
        # provider outage doesn't accidentally re-flip a 'blocked'
        # vendor back to 'unscreened'.
        new_disposition = profile.get("sanctions_status") or "unscreened"
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        db.upsert_vendor_profile(
            organization_id, vendor_name,
            sanctions_status=new_disposition,
            last_sanctions_check_at=now_iso,
        )
    except Exception:
        logger.exception(
            "sanctions_screening: vendor_profile rollup failed "
            "org=%s vendor=%s", organization_id, vendor_name,
        )

    revalidated_count = 0
    if kyc_result.status == "hit":
        try:
            from clearledgr.services.vendor_revalidation import (
                revalidate_in_flight_ap_items,
            )
            rv = revalidate_in_flight_ap_items(
                db,
                organization_id=organization_id,
                vendor_name=vendor_name,
                reason="vendor_sanctions_hit",
                actor=actor or "sanctions_screening",
            )
            revalidated_count = len(rv.affected_ap_item_ids)
        except Exception:
            logger.exception(
                "sanctions_screening: revalidation fan-out failed "
                "org=%s vendor=%s", organization_id, vendor_name,
            )

    return ScreeningResult(
        vendor_name=vendor_name,
        status=kyc_result.status,
        sanctions_status=new_disposition,
        check_id=check_id,
        matches_count=len(kyc_result.matches or []),
        revalidated_ap_items=revalidated_count,
        error=kyc_result.error,
    )


# ── Pre-payment gate ───────────────────────────────────────────────


def gate_payment_against_sanctions(
    db,
    *,
    organization_id: str,
    vendor_name: Optional[str],
) -> None:
    """Hard guardrail used by record_payment_confirmation.

    Raises :class:`SanctionsBlockedError` when the rolled-up
    disposition on ``vendor_profiles`` is 'blocked'. Other
    dispositions ('unscreened', 'review', 'clear') do NOT raise —
    they're surfaced via the AP-item-level exception system that
    the auditor / operator already monitors.
    """
    if not vendor_name:
        return
    profile = None
    try:
        profile = db.get_vendor_profile(organization_id, vendor_name)
    except Exception:
        profile = None
    if not profile:
        return
    if str(profile.get("sanctions_status") or "").lower() != "blocked":
        return
    latest = None
    try:
        latest = db.get_latest_sanctions_check(
            organization_id, vendor_name,
        )
    except Exception:
        latest = None
    raise SanctionsBlockedError(
        vendor_name=vendor_name,
        latest_check_id=(latest or {}).get("id"),
    )


# ── Re-screen scheduler ────────────────────────────────────────────


def vendors_due_for_rescreen(
    db,
    *,
    organization_id: str,
    rescreen_days: int = _DEFAULT_RESCREEN_DAYS,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Return vendor profiles whose latest sanctions check is older
    than ``rescreen_days`` (or has never been screened).

    Excludes vendors with status='blocked' (already at the most
    severe disposition; re-screening them won't change the gate)
    and vendors with status='archived' from the M4 allowlist work.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=rescreen_days)
    ).isoformat()
    safe_limit = max(1, min(int(limit or 100), 1000))

    sql = (
        "SELECT vendor_name, sanctions_status, last_sanctions_check_at "
        "FROM vendor_profiles "
        "WHERE organization_id = %s "
        "  AND COALESCE(status, 'active') NOT IN ('archived', 'blocked') "
        "  AND COALESCE(sanctions_status, 'unscreened') NOT IN ('blocked') "
        "  AND (last_sanctions_check_at IS NULL "
        "       OR last_sanctions_check_at < %s) "
        "ORDER BY COALESCE(last_sanctions_check_at, '1970-01-01') ASC "
        "LIMIT %s"
    )
    db.initialize()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (organization_id, cutoff, safe_limit))
        rows = cur.fetchall()
    return [dict(r) for r in rows]
