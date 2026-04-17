"""Pluggable bank account verifier — vendor-onboarding-spec §4.3 + §12.

The vendor-onboarding agent does not run payment rails. It orchestrates
one. Bank account verification is delegated to an open banking
provider (Adyen for EU customers anchored on Booking.com, TrueLayer
for UK + rest-of-world, Plaid for US when that market opens). The
provider returns a signed confirmation with the account holder name,
which we fuzzy-match against the KYC legal entity to disposition the
verification.

This module defines the abstract :class:`BankVerifier` interface the
planner calls. Concrete adapters live as sibling modules
(``bank_verifier_adyen.py``, ``bank_verifier_truelayer.py``, etc.) and
implement the provider-specific auth, link generation, webhook handling,
and signature verification. Today the only concrete implementation is
:class:`NotConfiguredBankVerifier` which short-circuits every call with
a ``provider_adapter_pending`` result — the rest of the pipeline runs
end-to-end against it while real adapters are being contracted and
integrated.

The contract is deliberately narrow. Three operations:

  check_coverage(country, bank_identifier?) -> (covered, provider, reason?)
      Tell the planner whether this bank account is in-scope for the
      configured provider. Out-of-scope vendors route to the manual
      bank-letter flow — see vendor-onboarding-spec §6.3.

  initiate_verification(box_id, vendor_email) -> (link_dispatched, provider_reference)
      Generate a verification session and return a link for the
      vendor to complete. The portal embeds the link in the
      bank-verify step.

  result(provider_reference) -> BankVerificationResult
      Fetch the verification result when the provider's webhook
      fires. Returns the account holder name, masked identifier, and
      a signed confirmation that the planner can use for audit.
"""
from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class BankVerificationCoverage:
    """Whether a bank account is in-scope for the configured verifier."""

    covered: bool
    provider: str  # "adyen" | "truelayer" | "plaid" | "not_configured"
    unsupported_reason: Optional[str] = None


@dataclass
class BankVerificationInitResult:
    """Outcome of initiating a verification session."""

    link_dispatched: bool
    provider: str
    provider_reference: Optional[str] = None
    link_url: Optional[str] = None
    error: Optional[str] = None


@dataclass
class BankVerificationResult:
    """Final verification result returned when the vendor completes the flow.

    Signed by the provider. The signature is validated before this
    object is constructed — callers can trust the fields as
    provider-attested.
    """

    success: bool
    provider: str
    provider_reference: Optional[str] = None
    account_holder_name: Optional[str] = None
    account_identifier_masked: Optional[str] = None  # last 4 of IBAN, etc.
    verified_at: Optional[str] = None
    error: Optional[str] = None
    raw_payload: Dict[str, Any] = field(default_factory=dict)


class BankVerifier(ABC):
    """Pluggable bank account verifier interface."""

    provider: str = "unknown"

    @abstractmethod
    async def check_coverage(
        self,
        bank_country: str,
        bank_identifier: Optional[str] = None,
    ) -> BankVerificationCoverage:
        ...

    @abstractmethod
    async def initiate_verification(
        self,
        *,
        box_id: str,
        vendor_email: str,
        workspace_id: str,
    ) -> BankVerificationInitResult:
        ...

    @abstractmethod
    async def get_result(
        self,
        provider_reference: str,
    ) -> BankVerificationResult:
        ...


class NotConfiguredBankVerifier(BankVerifier):
    """Default verifier when no provider is configured for the workspace.

    Returns neutral ``provider_adapter_pending`` results for every
    call. The planner treats these as "route to manual bank
    verification" per vendor-onboarding-spec §6.3 so onboarding does
    not silently stall — the AP Manager gets a named blocker and can
    complete verification out-of-band until the provider adapter
    lands.
    """

    provider = "not_configured"

    async def check_coverage(
        self,
        bank_country: str,
        bank_identifier: Optional[str] = None,
    ) -> BankVerificationCoverage:
        return BankVerificationCoverage(
            covered=False,
            provider=self.provider,
            unsupported_reason="bank_verifier_not_configured",
        )

    async def initiate_verification(
        self,
        *,
        box_id: str,
        vendor_email: str,
        workspace_id: str,
    ) -> BankVerificationInitResult:
        return BankVerificationInitResult(
            link_dispatched=False,
            provider=self.provider,
            error="bank_verifier_not_configured",
        )

    async def get_result(
        self,
        provider_reference: str,
    ) -> BankVerificationResult:
        return BankVerificationResult(
            success=False,
            provider=self.provider,
            error="bank_verifier_not_configured",
        )


# ---------------------------------------------------------------------
# Factory / resolver
# ---------------------------------------------------------------------

# Which provider a workspace uses is read from
# settings_json["onboarding"]["bank_verifier"] on the organizations
# row. Candidates: "adyen", "truelayer", "plaid". Anything else or
# missing resolves to NotConfiguredBankVerifier.
#
# The real Adyen / TrueLayer / Plaid adapters will live as sibling
# modules and register themselves via this factory when they land.
_ADAPTERS_REGISTRY: Dict[str, Any] = {}


def register_bank_verifier(name: str, factory_fn) -> None:
    """Register a concrete BankVerifier factory. Called by adapter modules at import time."""
    _ADAPTERS_REGISTRY[name.strip().lower()] = factory_fn


def get_bank_verifier(organization_id: str, db: Any = None) -> BankVerifier:
    """Return the BankVerifier configured for the workspace.

    Resolution order:
      1. ``settings_json["onboarding"]["bank_verifier"]`` on the org
      2. ``CLEARLEDGR_DEFAULT_BANK_VERIFIER`` env var (dev override)
      3. :class:`NotConfiguredBankVerifier`
    """
    if db is None:
        from clearledgr.core.database import get_db
        db = get_db()

    configured = None
    try:
        org = db.get_organization(organization_id) or {}
        settings: Any = org.get("settings") or org.get("settings_json") or {}
        if isinstance(settings, str):
            import json
            try:
                settings = json.loads(settings)
            except (ValueError, TypeError):
                settings = {}
        if isinstance(settings, dict):
            onboarding = settings.get("onboarding") or {}
            if isinstance(onboarding, dict):
                configured = str(onboarding.get("bank_verifier") or "").strip().lower() or None
    except Exception as exc:  # noqa: BLE001
        logger.debug("[bank_verifier] get_organization failed for %s: %s", organization_id, exc)

    if not configured:
        configured = os.getenv("CLEARLEDGR_DEFAULT_BANK_VERIFIER", "").strip().lower() or None

    if configured and configured in _ADAPTERS_REGISTRY:
        try:
            return _ADAPTERS_REGISTRY[configured](organization_id=organization_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[bank_verifier] adapter %r instantiation failed for %s: %s — "
                "falling back to NotConfigured",
                configured, organization_id, exc,
            )

    return NotConfiguredBankVerifier()
