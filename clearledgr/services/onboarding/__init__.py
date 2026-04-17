"""Vendor onboarding subpackage.

Contains the pluggable provider abstractions the vendor-onboarding
agent depends on: KYC providers (company registry, sanctions, PEP,
adverse media, UBO resolution) and bank account verifiers (open
banking via Adyen / TrueLayer / Plaid). Each provider is behind an
interface so swapping providers per workspace is a configuration
change rather than a code change.

Today each interface has a ``NotConfigured`` default that short-
circuits every call with a neutral result — this lets the rest of
the onboarding pipeline run end-to-end against mock data while the
real provider contracts and sandbox integrations are being scoped.
Real adapters land when the customer-side provider selection and
contracting is complete; they plug into the same interface without
touching the planner or state machine.
"""
from __future__ import annotations

from clearledgr.services.onboarding.kyc_policy import (
    KYCPolicyTier,
    resolve_kyc_tier,
)
from clearledgr.services.onboarding.bank_verifier import (
    BankVerifier,
    BankVerificationResult,
    NotConfiguredBankVerifier,
    get_bank_verifier,
)
from clearledgr.services.onboarding.kyc_provider import (
    KYCProvider,
    KYCCheckResult,
    NotConfiguredKYCProvider,
    get_kyc_provider,
)

__all__ = [
    "KYCPolicyTier",
    "resolve_kyc_tier",
    "BankVerifier",
    "BankVerificationResult",
    "NotConfiguredBankVerifier",
    "get_bank_verifier",
    "KYCProvider",
    "KYCCheckResult",
    "NotConfiguredKYCProvider",
    "get_kyc_provider",
]
