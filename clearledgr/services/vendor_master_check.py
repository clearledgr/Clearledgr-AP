"""Vendor master check — AP-side guardrail.

Solden does NOT onboard vendors (product call 2026-04-30, see
``memory/project_vendor_onboarding_subordinate.md``). When an AP item
lands, this module looks up the vendor in the customer's ERP master.
If they're not there, the AP item routes to ``needs_info`` with
``exception_code=vendor_not_in_erp_master`` and a clear operator
message: the customer adds the vendor in their own ERP, then the
invoice resumes.

Three return states keep the caller honest about uncertainty:

  - ``found``      — vendor exists in the ERP master; AP can proceed.
  - ``not_found``  — vendor does not exist; gate to needs_info.
  - ``skipped``    — we couldn't perform the check (no ERP connection,
                     ERP rate-limited, transient failure). Don't gate
                     — the resume hook will retry on the next workflow
                     re-fire, so a missed check is self-healing.

The lookup tries vendor name first, sender email second. Most invoice
emails carry a vendor display name in the From header that matches
the ERP master record; the email fallback catches cases where the
display name is generic (``billing@vendor.com`` without a name).
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


VENDOR_NOT_IN_ERP_MASTER = "vendor_not_in_erp_master"


_NEEDS_INFO_OPERATOR_MESSAGE = (
    "Vendor {vendor} isn't in your ERP yet. Add them in your ERP, "
    "then this invoice will resume on the next sync."
)


def needs_info_message(vendor_name: str) -> str:
    """Render the operator-facing copy for a vendor-not-in-ERP gate."""
    return _NEEDS_INFO_OPERATOR_MESSAGE.format(vendor=vendor_name or "this sender")


async def check_vendor_in_erp_master(
    organization_id: str,
    vendor_name: Optional[str],
    sender_email: Optional[str] = None,
) -> str:
    """Look up the vendor in the org's ERP master.

    Returns one of ``"found"``, ``"not_found"``, ``"skipped"``.
    Never raises — transient errors return ``"skipped"`` so the AP
    workflow keeps moving and the resume hook retries on next tick.
    """
    name = (vendor_name or "").strip()
    email = (sender_email or "").strip()
    if not name and not email:
        # Nothing to look up. The extraction layer didn't yield a
        # vendor name and we don't have a sender email either —
        # treating this as "not found" rather than "skipped" so the
        # AP item gets surfaced for review (operator decides).
        return "not_found"

    try:
        from clearledgr.integrations.erp_router import find_vendor, get_erp_connection

        connection = get_erp_connection(organization_id)
        if not connection:
            # No ERP wired yet — can't gate against a master that
            # doesn't exist. Customer may still be on Solden-only
            # shape. Skip the check; AP advances normally.
            return "skipped"

        result = await find_vendor(organization_id, name=name or None, email=email or None)
        if result and result.get("vendor_id"):
            return "found"
        # `find_vendor` returns None on either rate-limit or
        # not-found. We can't distinguish today, so we treat both
        # as "not_found". The resume hook on workflow re-fire will
        # retry and self-heal if this was a rate-limit miss.
        return "not_found"
    except Exception as exc:
        logger.warning(
            "[vendor_master_check] lookup raised for org=%s vendor=%r: %s",
            organization_id, name or email, exc,
        )
        return "skipped"
