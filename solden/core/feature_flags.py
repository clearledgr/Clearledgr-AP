"""V1 product-scope feature flags — DESIGN_THESIS.md §12.

The thesis draws two hard boundaries for V1:

  §12 #6 Outlook — "The answer in V1 is no. Solden does Gmail
    exceptionally. It does not do Outlook at all in V1. This boundary
    must be stated explicitly in every sales conversation."

  §6.8 Teams — "The finance team chooses their platform — Slack or
    Microsoft Teams." Taken in isolation this reads as "both
    supported in V1", but we've scoped Teams out of V1 intentionally.
    Slack is the V1 approval surface; Teams lights up post-launch
    alongside Outlook.

The codebase still carries the Outlook and Teams integrations — the
autopilot loops, the OAuth flows, the adapter cards, the webhook
routes. They were built for post-V1 and we don't want to throw them
away. But they cannot be accidentally live in a V1 deployment, and a
salesperson in a procurement conversation cannot be able to surface
them without a deliberate deployment-level flag flip.

This module is the single source of truth for those flags. Nowhere
else in the codebase should read ``os.environ`` for these — all
gating goes through ``is_outlook_enabled()`` / ``is_teams_enabled()``
so the behaviour is consistent across routes, autopilot loops,
bootstrap responses, and the strict-profile allowlist.

Both flags default to ``False`` to match the V1 boundary. Flip to
``true`` only when the corresponding surface is ready to ship.
"""
from __future__ import annotations

import os


_TRUTHY = frozenset({"1", "true", "yes", "on", "enabled"})


def _env_flag(name: str, default: bool = False) -> bool:
    """Return True iff env var ``name`` is set to a recognised truthy
    value. Missing, empty, or any other value returns ``default``.
    """
    raw = str(os.environ.get(name, "")).strip().lower()
    if not raw:
        return bool(default)
    return raw in _TRUTHY


def is_outlook_enabled() -> bool:
    """§12 #6 — Outlook routes + autopilot are disabled in V1.

    Flip ``FEATURE_OUTLOOK_ENABLED=true`` only when Outlook moves from
    scaffolding to an intentional, shippable product surface.
    """
    return _env_flag("FEATURE_OUTLOOK_ENABLED", default=False)


def is_teams_enabled() -> bool:
    """§6.8 / §12 — Microsoft Teams integration is disabled in V1.

    Flip ``FEATURE_TEAMS_ENABLED=true`` when Teams is ready to ship
    alongside Outlook as the post-launch approval surface for
    Microsoft-first customers.
    """
    return _env_flag("FEATURE_TEAMS_ENABLED", default=False)


def is_procurement_chat_enabled() -> bool:
    """Procurement (purchase_order) chat approval cards.

    Off by default: the outbound PO approval card + decision routing is
    built and tested with mocked Slack, but wiring it into the live
    Slack/Teams interactive handler + a real workspace needs live
    validation. Flip ``FEATURE_PROCUREMENT_CHAT=true`` once that lands.
    """
    return _env_flag("FEATURE_PROCUREMENT_CHAT", default=False)


def is_procurement_erp_write_enabled() -> bool:
    """Procurement PO write-back to the ERP (create a PO in QB/Xero/etc).

    Off by default: the dispatch + adapters are built and tested with
    mocked HTTP, but no PO-create path has been validated against a live
    ERP sandbox. Flip ``FEATURE_PROCUREMENT_ERP_WRITE=true`` only after
    per-ERP live validation.
    """
    return _env_flag("FEATURE_PROCUREMENT_ERP_WRITE", default=False)


def is_erp_settlement_write_enabled() -> bool:
    """ERP settlement write-back: recording a COMPLETED payment/receipt/refund
    against a posted bill (apply_settlement → QB billpayment / Xero Payment /
    NetSuite vendorPayment / SAP VendorPayment).

    OFF by default. This is the single most sensitive ERP write Solden makes —
    it authors the cash-side accounting entry (debit AP / credit bank), and on
    a connection with payment rails it can move money. The intent is
    RECONCILIATION (record a settlement that already happened externally), not
    initiation; the gate keeps it dark until that distinction is validated
    per-ERP against a sandbox. Flip ``FEATURE_ERP_SETTLEMENT_WRITE=true`` only
    after that validation. Until then "Solden never moves money" holds because
    the write never fires.
    """
    return _env_flag("FEATURE_ERP_SETTLEMENT_WRITE", default=False)


def is_sap_live_write_enabled() -> bool:
    """Live SAP S/4HANA document writes (park AP invoice / journal entry).

    OFF by default. The ``SAPAdapter`` (solden/services/erp/sap.py) builds and
    validates SAP-shaped payloads but has no live HTTP write path — so a
    non-dry-run park must fail closed (never report ``status="parked"`` for a
    document that was never sent). Flip ``FEATURE_SAP_LIVE_WRITE=true`` only
    once the real live write path is implemented and validated against an
    S/4HANA sandbox. (The separate vendor-bill posting path
    ``integrations/erp_sap.py:post_bill_to_sap`` is already wired and is NOT
    gated by this flag.)
    """
    return _env_flag("FEATURE_SAP_LIVE_WRITE", default=False)


def is_workflow_hooks_enabled() -> bool:
    """Customer code hooks + conditions + effects on declarative Box types.

    Off by default. When off, the hook dispatcher is a complete no-op, so the
    generic Box transition path behaves exactly as if no hooks existed — this
    is what keeps the feature dark in prod and inert in the test suite.

    The condition (expression) tier is safe on its own, but the full surface
    includes customer code executed in the WASM sandbox; both stay behind this
    single flag, which must not be flipped for any tenant until the sandbox has
    passed an adversarial security review. Flip ``FEATURE_WORKFLOW_HOOKS=true``
    only then.
    """
    return _env_flag("FEATURE_WORKFLOW_HOOKS", default=False)


# Canonical V1 rejection responses. Shared so every gated surface
# returns the same shape — makes observability and client-side error
# handling straightforward.

_OUTLOOK_DISABLED_PAYLOAD = {
    "detail": "outlook_disabled_in_v1",
    "reason": "DESIGN_THESIS §12 #6 — V1 is Google Workspace only; Outlook ships post-launch.",
}

_TEAMS_DISABLED_PAYLOAD = {
    "detail": "teams_disabled_in_v1",
    "reason": "DESIGN_THESIS §12 — Teams is scoped post-launch; Slack is the V1 approval surface.",
}


def outlook_disabled_payload() -> dict:
    """Canonical 404 body for Outlook routes when the flag is off."""
    return dict(_OUTLOOK_DISABLED_PAYLOAD)


def teams_disabled_payload() -> dict:
    """Canonical 404 body for Teams routes when the flag is off."""
    return dict(_TEAMS_DISABLED_PAYLOAD)
