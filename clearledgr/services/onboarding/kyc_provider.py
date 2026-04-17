"""Pluggable KYC provider — vendor-onboarding-spec §4.2.

Clearledgr does not run its own KYB/KYC pipeline. It integrates with
one. The KYC provider answers four questions about a vendor: does
this company exist in the local registry, is anyone on the beneficial
ownership chain sanctioned, is anyone politically exposed, is there
adverse media worth flagging. ComplyAdvantage, Sumsub, and Trulioo are
the candidate providers; which one a workspace uses is a configuration
decision (provider coverage varies meaningfully by jurisdiction).

This module defines the abstract :class:`KYCProvider` interface the
planner calls. Concrete adapters land as sibling modules and register
themselves via :func:`register_kyc_provider`. Today the only concrete
implementation is :class:`NotConfiguredKYCProvider` which short-
circuits every call with a neutral ``provider_adapter_pending`` result
— the rest of the pipeline runs end-to-end against it while the
provider contracts and sandbox integrations are being worked through.

The method set mirrors the four KYC tiers in
:mod:`clearledgr.services.onboarding.kyc_policy`. Which methods the
planner calls for a given vendor is driven by the workspace's
configured tier:

  completeness
      No provider calls. Document completeness only.

  basic
      ``company_registry_lookup`` + ``sanctions_screen``.

  full
      Basic + ``pep_check`` + ``adverse_media_screen`` + ``resolve_ubo``.

Each method returns a :class:`KYCCheckResult` with a ``status``
(``clear`` / ``hit`` / ``inconclusive`` / ``provider_adapter_pending``
/ ``error``), provider-attested evidence the planner persists to the
audit trail, and the raw provider payload for review. Hits route to
the AP Manager via the standard escalation flow; inconclusive results
route to manual review.
"""
from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class KYCCheckResult:
    """Outcome of a single KYC check.

    ``status`` is the canonical disposition the planner dispatches on:

      clear
          No hits. Advance.

      hit
          Provider returned a match on sanctions / PEP / adverse media.
          Route to AP Manager review with ``matches`` populated.

      inconclusive
          Provider could not determine (e.g. registry timeout, partial
          data). Route to manual review.

      provider_adapter_pending
          No provider is configured for this workspace. Treated as
          "skip — needs manual verification" by the planner so the
          pipeline does not silently pass vendors through unchecked.

      error
          Transient provider failure. Planner retries with backoff.
    """

    status: str  # "clear" | "hit" | "inconclusive" | "provider_adapter_pending" | "error"
    check_type: str  # "company_registry" | "sanctions" | "pep" | "adverse_media" | "ubo"
    provider: str
    provider_reference: Optional[str] = None
    matches: List[Dict[str, Any]] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)
    checked_at: Optional[str] = None
    error: Optional[str] = None
    raw_payload: Dict[str, Any] = field(default_factory=dict)


class KYCProvider(ABC):
    """Pluggable KYC provider interface.

    Concrete implementations wrap a provider SDK (ComplyAdvantage,
    Sumsub, Trulioo, etc.) and translate between that provider's
    shapes and :class:`KYCCheckResult`. The planner never touches
    provider-specific code — it only sees this interface.
    """

    provider: str = "unknown"

    @abstractmethod
    async def company_registry_lookup(
        self,
        *,
        legal_name: str,
        country: str,
        registration_number: Optional[str] = None,
    ) -> KYCCheckResult:
        """Confirm the company exists in the local registry and the name matches."""
        ...

    @abstractmethod
    async def sanctions_screen(
        self,
        *,
        legal_name: str,
        country: str,
        aliases: Optional[List[str]] = None,
    ) -> KYCCheckResult:
        """Screen against OFAC / UN / EU / UK HM Treasury sanctions lists (minimum)."""
        ...

    @abstractmethod
    async def pep_check(
        self,
        *,
        legal_name: str,
        country: str,
        beneficial_owners: Optional[List[Dict[str, Any]]] = None,
    ) -> KYCCheckResult:
        """Politically-exposed-person check for the entity and its UBOs."""
        ...

    @abstractmethod
    async def adverse_media_screen(
        self,
        *,
        legal_name: str,
        country: str,
    ) -> KYCCheckResult:
        """Adverse media / negative news screen."""
        ...

    @abstractmethod
    async def resolve_ubo(
        self,
        *,
        legal_name: str,
        country: str,
        registration_number: Optional[str] = None,
    ) -> KYCCheckResult:
        """Resolve ultimate beneficial owners to the depth the provider supports."""
        ...


class NotConfiguredKYCProvider(KYCProvider):
    """Default provider when no adapter is configured for the workspace.

    Returns neutral ``provider_adapter_pending`` results for every
    call. The planner treats these as "route to manual KYC review" per
    vendor-onboarding-spec §6.3 so onboarding does not silently stall
    or silently pass — the AP Manager gets a named blocker and can
    complete the relevant checks out-of-band until the provider
    adapter lands.
    """

    provider = "not_configured"

    def _pending(self, check_type: str) -> KYCCheckResult:
        return KYCCheckResult(
            status="provider_adapter_pending",
            check_type=check_type,
            provider=self.provider,
            error="kyc_provider_not_configured",
        )

    async def company_registry_lookup(
        self,
        *,
        legal_name: str,
        country: str,
        registration_number: Optional[str] = None,
    ) -> KYCCheckResult:
        return self._pending("company_registry")

    async def sanctions_screen(
        self,
        *,
        legal_name: str,
        country: str,
        aliases: Optional[List[str]] = None,
    ) -> KYCCheckResult:
        return self._pending("sanctions")

    async def pep_check(
        self,
        *,
        legal_name: str,
        country: str,
        beneficial_owners: Optional[List[Dict[str, Any]]] = None,
    ) -> KYCCheckResult:
        return self._pending("pep")

    async def adverse_media_screen(
        self,
        *,
        legal_name: str,
        country: str,
    ) -> KYCCheckResult:
        return self._pending("adverse_media")

    async def resolve_ubo(
        self,
        *,
        legal_name: str,
        country: str,
        registration_number: Optional[str] = None,
    ) -> KYCCheckResult:
        return self._pending("ubo")


# ---------------------------------------------------------------------
# Factory / resolver
# ---------------------------------------------------------------------

# Which provider a workspace uses is read from
# settings_json["onboarding"]["kyc_provider"] on the organizations
# row. Candidates: "complyadvantage", "sumsub", "trulioo". Anything
# else or missing resolves to NotConfiguredKYCProvider.
#
# Real adapters will live as sibling modules and register themselves
# via this factory at import time.
_ADAPTERS_REGISTRY: Dict[str, Any] = {}


def register_kyc_provider(name: str, factory_fn) -> None:
    """Register a concrete KYCProvider factory. Called by adapter modules at import time."""
    _ADAPTERS_REGISTRY[name.strip().lower()] = factory_fn


def get_kyc_provider(organization_id: str, db: Any = None) -> KYCProvider:
    """Return the KYCProvider configured for the workspace.

    Resolution order:
      1. ``settings_json["onboarding"]["kyc_provider"]`` on the org
      2. ``CLEARLEDGR_DEFAULT_KYC_PROVIDER`` env var (dev override)
      3. :class:`NotConfiguredKYCProvider`
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
                configured = str(onboarding.get("kyc_provider") or "").strip().lower() or None
    except Exception as exc:  # noqa: BLE001
        logger.debug("[kyc_provider] get_organization failed for %s: %s", organization_id, exc)

    if not configured:
        configured = os.getenv("CLEARLEDGR_DEFAULT_KYC_PROVIDER", "").strip().lower() or None

    if configured and configured in _ADAPTERS_REGISTRY:
        try:
            return _ADAPTERS_REGISTRY[configured](organization_id=organization_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[kyc_provider] adapter %r instantiation failed for %s: %s — "
                "falling back to NotConfigured",
                configured, organization_id, exc,
            )

    return NotConfiguredKYCProvider()
