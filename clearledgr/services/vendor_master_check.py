"""Vendor master check — AP-side guardrail.

Solden does NOT onboard vendors (product call 2026-04-30, see
``memory/project_vendor_onboarding_subordinate.md``). When an AP item
lands, this module looks up the vendor in the customer's ERP master.
If they're not there, the AP item routes to ``needs_info`` with
``exception_code=vendor_not_in_erp_master`` and a clear operator
message.

The lookup runs three tiers in order, returning ``found`` on the
first hit and falling through on miss:

  1. **Exact match via the ERP adapter's ``find_vendor``** — the cheap
     happy-path (handles QB/Xero/NetSuite/SAP via ``erp_router``).
  2. **Fuzzy match against the local ``vendor_profiles`` cache**
     using ``vendor_similarity`` (token + sequence ratio). Catches
     "Cisco Systems Inc" → "CISCO SYSTEMS, INCORPORATED" and similar
     spelling drift between extraction and the ERP master, without a
     second ERP API call. Threshold pinned at 0.85 to keep false
     positives out of the AP-write path.
  3. **Domain fallback through ``find_vendor`` again** — same exact-
     match path but with the sender's domain. Catches the case where
     the email body lacks a vendor name but the sender domain is the
     vendor's billing address.

Three return states keep the caller honest about uncertainty:

  - ``found``      — vendor exists; AP can proceed.
  - ``not_found``  — vendor does not exist anywhere; gate to needs_info.
  - ``skipped``    — we couldn't perform the check (no ERP connection,
                     transient failure). Don't gate — the resume hook
                     retries on the next workflow re-fire.

The result is a ``VendorMasterCheckResult`` dataclass — string ``status``
plus structured fields (``matched_via``, ``matched_name``,
``similarity_score``) so callers can log + audit which tier resolved
the lookup.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


VENDOR_NOT_IN_ERP_MASTER = "vendor_not_in_erp_master"


# Confidence floor for the local-fuzzy tier. Below this, we'd rather
# gate to ``needs_info`` and let the operator confirm than auto-bind
# the AP item to the wrong vendor record. 0.85 is conservative enough
# that "Cisco Systems Inc" / "CISCO SYSTEMS, INCORPORATED" passes
# (~0.92) but "Acme" / "Apex" doesn't (~0.55).
_FUZZY_MATCH_THRESHOLD = 0.85

# How many local vendor profiles to fetch as fuzzy candidates. Capped
# so a customer with 5,000 vendors doesn't pay for a full table scan
# on every intake. 200 most-recently-active covers the typical AP
# concentration where the top quartile of vendors gets >90% of the
# invoice volume.
_FUZZY_CANDIDATE_CAP = 200


_NEEDS_INFO_OPERATOR_MESSAGE = (
    "Vendor {vendor} isn't in your ERP yet. Add them in your ERP, "
    "then this invoice will resume on the next sync."
)


@dataclass
class VendorMasterCheckResult:
    """Structured result for the master-check gate.

    Callers typically read ``status`` for routing decisions and
    ``matched_*`` fields for audit + logging. The dataclass shape lets
    new tiers (LLM fuzzy, cross-org pattern) be added without
    breaking existing callers — they keep reading the string status.
    """

    status: str  # "found" | "not_found" | "skipped"
    matched_via: Optional[str] = None  # "exact" | "fuzzy_local" | "domain"
    matched_name: Optional[str] = None
    similarity_score: Optional[float] = None
    extras: Dict[str, Any] = field(default_factory=dict)


def needs_info_message(vendor_name: str) -> str:
    """Render the operator-facing copy for a vendor-not-in-ERP gate."""
    return _NEEDS_INFO_OPERATOR_MESSAGE.format(vendor=vendor_name or "this sender")


async def check_vendor_in_erp_master(
    organization_id: str,
    vendor_name: Optional[str],
    sender_email: Optional[str] = None,
) -> str:
    """Look up the vendor in the org's ERP master through three tiers.

    Returns the canonical string status (``"found"`` /
    ``"not_found"`` / ``"skipped"``) for callers that only need the
    gating decision. Use :func:`check_vendor_in_erp_master_full`
    when you need the structured result with ``matched_via`` etc.
    """
    result = await check_vendor_in_erp_master_full(
        organization_id, vendor_name, sender_email,
    )
    return result.status


async def check_vendor_in_erp_master_full(
    organization_id: str,
    vendor_name: Optional[str],
    sender_email: Optional[str] = None,
) -> VendorMasterCheckResult:
    """Three-tier master check returning the full structured result."""
    name = (vendor_name or "").strip()
    email = (sender_email or "").strip()
    if not name and not email:
        return VendorMasterCheckResult(status="not_found", matched_via=None)

    try:
        from clearledgr.integrations.erp_router import find_vendor, get_erp_connection

        connection = get_erp_connection(organization_id)
        if not connection:
            # No ERP wired — can't gate against a master that doesn't
            # exist. Customer may still be on Solden-only shape.
            return VendorMasterCheckResult(status="skipped", matched_via=None)

        # ── Tier 1: exact match via the ERP adapter ────────────
        # Name-only on purpose: the email path is Tier 3, and combining
        # them here would make Tier 3 unreachable for any vendor whose
        # ERP record happens to also be email-indexed.
        if name:
            exact = await find_vendor(organization_id, name=name)
            if exact and exact.get("vendor_id"):
                return VendorMasterCheckResult(
                    status="found",
                    matched_via="exact",
                    matched_name=str(exact.get("name") or name),
                    similarity_score=1.0,
                    extras={"vendor_id": exact.get("vendor_id")},
                )

        # ── Tier 2: fuzzy match against local vendor_profiles ───
        if name:
            fuzzy = _fuzzy_match_local(organization_id, name)
            if fuzzy is not None:
                return VendorMasterCheckResult(
                    status="found",
                    matched_via="fuzzy_local",
                    matched_name=fuzzy["name"],
                    similarity_score=fuzzy["score"],
                )

        # ── Tier 3: sender-domain fallback ─────────────────────
        if email:
            domain_hit = await find_vendor(organization_id, email=email)
            if domain_hit and domain_hit.get("vendor_id"):
                return VendorMasterCheckResult(
                    status="found",
                    matched_via="domain",
                    matched_name=str(domain_hit.get("name") or ""),
                    similarity_score=1.0,
                    extras={"vendor_id": domain_hit.get("vendor_id")},
                )

        return VendorMasterCheckResult(status="not_found", matched_via=None)

    except Exception as exc:
        logger.warning(
            "[vendor_master_check] lookup raised for org=%s vendor=%r: %s",
            organization_id, name or email, exc,
        )
        return VendorMasterCheckResult(status="skipped", matched_via=None)


def _fuzzy_match_local(
    organization_id: str,
    vendor_name: str,
) -> Optional[Dict[str, Any]]:
    """Fuzzy-match against the local ``vendor_profiles`` cache.

    Returns ``{"name": str, "score": float}`` for the best candidate
    above ``_FUZZY_MATCH_THRESHOLD``, or ``None`` when no candidate
    clears the bar.

    Why local-only (no ERP API fan-out):

    - Listing every ERP vendor on every intake is too expensive and
      most ERPs rate-limit the bulk endpoints.
    - The local cache is populated by every successful intake, so
      after the first run the vendors a customer actually transacts
      with are present locally. The first-ever invoice from a vendor
      whose name was extracted differently from the ERP master will
      still gate to ``needs_info`` — that's the cost of the local-
      only design and we accept it for the API-cost win.
    """
    try:
        from clearledgr.core.database import get_db
        from clearledgr.services.fuzzy_matching import vendor_similarity

        db = get_db()
        if not hasattr(db, "list_vendor_profiles"):
            return None
        candidates: List[Dict[str, Any]] = db.list_vendor_profiles(
            organization_id, limit=_FUZZY_CANDIDATE_CAP,
        ) or []

        best_score = 0.0
        best_name: Optional[str] = None
        for row in candidates:
            candidate_name = str(row.get("vendor_name") or "").strip()
            if not candidate_name:
                continue
            score = vendor_similarity(vendor_name, candidate_name)
            if score > best_score:
                best_score = score
                best_name = candidate_name

        if best_name and best_score >= _FUZZY_MATCH_THRESHOLD:
            logger.info(
                "[vendor_master_check] fuzzy-matched %r → %r (score=%.2f)",
                vendor_name, best_name, best_score,
            )
            return {"name": best_name, "score": best_score}
        return None
    except Exception as exc:
        logger.debug("[vendor_master_check] fuzzy-match failed: %s", exc)
        return None
