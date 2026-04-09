"""IbanChangeFreezeService — three-factor IBAN change verification.

DESIGN_THESIS.md §8: *"IBAN change freeze with three-factor verification
(vendor email domain + phone confirmation + AP Manager sign-off). IBAN
changes trigger an immediate payment hold for the affected vendor — no
payment is scheduled to any new IBAN until the change is verified."*

This service is the single owner of the freeze workflow on top of the
VendorStore freeze accessors added in Phase 2.1.b. It handles:

  - **Detection**: comparing an invoice's extracted bank details
    against the vendor's verified details and starting a freeze when
    the IBAN (or other sensitive field) differs.
  - **Three-factor progression**: recording each factor via typed
    payloads, enforcing role gating at the API boundary.
  - **Completion**: verifying all three factors are checked before
    promoting the pending details to verified.
  - **Rejection**: discarding pending details without lifting the
    verified value.
  - **Audit trail**: emitting ``iban_change_freeze_started``,
    ``iban_change_factor_recorded``, ``iban_change_freeze_lifted``,
    ``iban_change_freeze_rejected`` events through the existing
    ``ap_audit_events`` store.

The service does NOT enforce role gating itself — that happens at the
API layer via ``require_cfo``. Service methods trust
the caller has already passed the role check, and always accept an
``actor_id`` for the audit trail.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


REQUIRED_FACTORS = ("email_domain_factor", "phone_factor", "sign_off_factor")


@dataclass(frozen=True)
class FreezeDetectionResult:
    """Result of running IBAN-change detection against a vendor."""

    status: str  # "no_vendor" | "no_change" | "already_frozen" | "frozen" | "error"
    vendor_name: Optional[str]
    mismatched_fields: List[str]
    verification_state: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class FactorRecordResult:
    """Result of recording a single verification factor."""

    status: str  # "recorded" | "unknown_factor" | "not_frozen" | "error"
    verification_state: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class CompletionResult:
    """Result of completing or rejecting a freeze."""

    status: str  # "completed" | "rejected" | "not_frozen" | "missing_factors" | "error"
    verification_state: Optional[Dict[str, Any]] = None
    missing_factors: List[str] = None  # type: ignore[assignment]
    reason: Optional[str] = None


class IbanChangeFreezeService:
    """Single owner of the IBAN change freeze workflow for an organization."""

    def __init__(self, organization_id: str, db: Any = None) -> None:
        from clearledgr.core.database import get_db
        self.organization_id = organization_id
        self.db = db or get_db()

    # ------------------------------------------------------------------ #
    # Detection
    # ------------------------------------------------------------------ #

    def detect_and_maybe_freeze(
        self,
        *,
        vendor_name: str,
        extracted_bank_details: Optional[Dict[str, Any]],
        sender_domain: str,
        triggering_ap_item_id: Optional[str] = None,
    ) -> FreezeDetectionResult:
        """Compare extracted invoice bank details against the vendor's
        verified details and start a freeze if they differ.

        Called by the validation gate when it sees bank details on an
        invoice. Idempotent: if the vendor is already frozen, returns
        the existing state without re-freezing.

        Returns ``status="no_change"`` when there's nothing to compare
        (no stored details, or no new details) or when the details
        match. Returns ``status="frozen"`` when a new freeze was
        started. Returns ``status="already_frozen"`` when a freeze was
        already in progress.
        """
        from clearledgr.core.stores.bank_details import (
            diff_bank_details_field_names,
            normalize_bank_details,
        )

        if not vendor_name:
            return FreezeDetectionResult(
                status="no_vendor",
                vendor_name=None,
                mismatched_fields=[],
                reason="missing_vendor_name",
            )

        extracted_clean = normalize_bank_details(extracted_bank_details)
        if not extracted_clean:
            return FreezeDetectionResult(
                status="no_change",
                vendor_name=vendor_name,
                mismatched_fields=[],
                reason="no_extracted_details",
            )

        # If already frozen, return the existing state — the validation
        # gate will still add the blocking reason code from the
        # ``is_pending_for_vendor`` check below.
        if self.db.is_iban_change_pending(self.organization_id, vendor_name):
            state = self.db.get_iban_change_verification_state(
                self.organization_id, vendor_name
            )
            return FreezeDetectionResult(
                status="already_frozen",
                vendor_name=vendor_name,
                mismatched_fields=[],
                verification_state=state,
            )

        stored = self.db.get_vendor_bank_details(
            self.organization_id, vendor_name
        )
        if not stored:
            # No verified baseline yet — first-time record, not a change.
            # The first-payment-hold check (Phase 1.2a) handles this
            # case separately; we don't duplicate that enforcement here.
            return FreezeDetectionResult(
                status="no_change",
                vendor_name=vendor_name,
                mismatched_fields=[],
                reason="no_verified_baseline",
            )

        mismatched = diff_bank_details_field_names(extracted_clean, stored)
        if not mismatched:
            return FreezeDetectionResult(
                status="no_change",
                vendor_name=vendor_name,
                mismatched_fields=[],
            )

        # A real change — start the freeze
        try:
            state = self.db.start_iban_change_freeze(
                self.organization_id,
                vendor_name,
                pending_bank_details=extracted_clean,
                sender_domain=sender_domain,
            )
        except Exception as exc:
            logger.error(
                "[IbanChangeFreeze] start_iban_change_freeze raised for %s: %s",
                vendor_name, exc,
            )
            return FreezeDetectionResult(
                status="error",
                vendor_name=vendor_name,
                mismatched_fields=mismatched,
                reason="start_freeze_exception",
            )

        if state is None:
            return FreezeDetectionResult(
                status="error",
                vendor_name=vendor_name,
                mismatched_fields=mismatched,
                reason="start_freeze_failed",
            )

        logger.warning(
            "[IbanChangeFreeze] Vendor %s/%s frozen: mismatched_fields=%s "
            "sender_domain=%s triggering_ap_item=%s",
            self.organization_id, vendor_name, mismatched,
            sender_domain, triggering_ap_item_id,
        )

        self._emit_audit_event(
            event_type="iban_change_freeze_started",
            vendor_name=vendor_name,
            ap_item_id=triggering_ap_item_id,
            actor_id="system:invoice_validation",
            actor_type="system",
            metadata={
                # Field names only — NEVER the values. DESIGN_THESIS.md §19.
                "mismatched_fields": mismatched,
                "sender_domain": sender_domain,
                "email_domain_auto_verified": bool(
                    state.get("email_domain_factor", {}).get("verified")
                ),
            },
        )

        return FreezeDetectionResult(
            status="frozen",
            vendor_name=vendor_name,
            mismatched_fields=mismatched,
            verification_state=state,
        )

    def is_pending_for_vendor(self, vendor_name: str) -> bool:
        """Shortcut used by the validation gate's blocking reason code."""
        if not vendor_name:
            return False
        return self.db.is_iban_change_pending(self.organization_id, vendor_name)

    # ------------------------------------------------------------------ #
    # Factor recording
    # ------------------------------------------------------------------ #

    def record_factor(
        self,
        *,
        vendor_name: str,
        factor: str,
        payload: Dict[str, Any],
        actor_id: str,
    ) -> FactorRecordResult:
        """Record a single verification factor.

        Valid ``factor`` values: ``"email_domain_factor"``,
        ``"phone_factor"``, ``"sign_off_factor"``.

        The payload is merged into the factor's sub-dict. For the phone
        factor, callers should provide: ``verified_phone_number``,
        ``caller_name_at_vendor``, ``notes``. For the sign-off factor,
        the actor_id is sufficient. For the email_domain factor,
        manual override is permitted when the auto-check failed.
        """
        if factor not in REQUIRED_FACTORS:
            return FactorRecordResult(
                status="unknown_factor",
                reason=f"factor must be one of {REQUIRED_FACTORS}",
            )

        if not self.db.is_iban_change_pending(self.organization_id, vendor_name):
            return FactorRecordResult(
                status="not_frozen",
                reason="vendor_not_frozen",
            )

        now = datetime.now(timezone.utc).isoformat()
        enriched_payload: Dict[str, Any] = dict(payload or {})
        # Stamp actor + timestamp on every factor so the audit trail is
        # complete regardless of what the caller passed.
        enriched_payload["verified_by"] = actor_id
        enriched_payload["verified_at"] = now

        try:
            state = self.db.record_iban_change_factor(
                self.organization_id,
                vendor_name,
                factor=factor,
                payload=enriched_payload,
            )
        except Exception as exc:
            logger.error(
                "[IbanChangeFreeze] record_iban_change_factor raised: %s", exc
            )
            return FactorRecordResult(status="error", reason=str(exc))

        if state is None:
            return FactorRecordResult(status="error", reason="record_failed")

        # Audit event — factor names only, never verification values
        self._emit_audit_event(
            event_type="iban_change_factor_recorded",
            vendor_name=vendor_name,
            ap_item_id=None,
            actor_id=actor_id,
            actor_type="user",
            metadata={
                "factor": factor,
                "all_factors_verified": self._all_factors_verified(state),
            },
        )

        return FactorRecordResult(status="recorded", verification_state=state)

    # ------------------------------------------------------------------ #
    # Completion / rejection
    # ------------------------------------------------------------------ #

    def complete_freeze(
        self, *, vendor_name: str, actor_id: str
    ) -> CompletionResult:
        """Lift the freeze after all three factors are verified.

        Returns ``status="missing_factors"`` with the list of
        unverified factors if the caller tries to complete early.
        Role gating is enforced at the API boundary, not here.
        """
        if not self.db.is_iban_change_pending(self.organization_id, vendor_name):
            return CompletionResult(
                status="not_frozen",
                missing_factors=[],
                reason="vendor_not_frozen",
            )

        state = self.db.get_iban_change_verification_state(
            self.organization_id, vendor_name
        )
        if not isinstance(state, dict):
            return CompletionResult(
                status="error",
                missing_factors=[],
                reason="missing_verification_state",
            )

        missing = [
            name
            for name in REQUIRED_FACTORS
            if not (state.get(name) or {}).get("verified")
        ]
        if missing:
            return CompletionResult(
                status="missing_factors",
                verification_state=state,
                missing_factors=missing,
                reason="factors_incomplete",
            )

        try:
            ok = self.db.complete_iban_change_freeze(
                self.organization_id, vendor_name
            )
        except Exception as exc:
            logger.error(
                "[IbanChangeFreeze] complete_iban_change_freeze raised: %s", exc
            )
            return CompletionResult(
                status="error",
                missing_factors=[],
                reason=str(exc),
            )

        if not ok:
            return CompletionResult(
                status="error",
                missing_factors=[],
                reason="complete_failed",
            )

        self._emit_audit_event(
            event_type="iban_change_freeze_lifted",
            vendor_name=vendor_name,
            ap_item_id=None,
            actor_id=actor_id,
            actor_type="user",
            metadata={"verification_state": state},
        )

        return CompletionResult(
            status="completed",
            verification_state=state,
            missing_factors=[],
        )

    def reject_freeze(
        self,
        *,
        vendor_name: str,
        actor_id: str,
        reason: str,
    ) -> CompletionResult:
        """Reject the unverified change and clear the freeze.

        Does NOT require any factor progress — a rejection can happen
        immediately when the verifier spots something suspicious.
        """
        if not self.db.is_iban_change_pending(self.organization_id, vendor_name):
            return CompletionResult(
                status="not_frozen",
                missing_factors=[],
                reason="vendor_not_frozen",
            )

        state = self.db.get_iban_change_verification_state(
            self.organization_id, vendor_name
        )

        try:
            ok = self.db.reject_iban_change_freeze(
                self.organization_id, vendor_name
            )
        except Exception as exc:
            logger.error(
                "[IbanChangeFreeze] reject_iban_change_freeze raised: %s", exc
            )
            return CompletionResult(
                status="error",
                missing_factors=[],
                reason=str(exc),
            )

        if not ok:
            return CompletionResult(
                status="error",
                missing_factors=[],
                reason="reject_failed",
            )

        self._emit_audit_event(
            event_type="iban_change_freeze_rejected",
            vendor_name=vendor_name,
            ap_item_id=None,
            actor_id=actor_id,
            actor_type="user",
            metadata={
                "rejection_reason": reason,
                "verification_state_at_rejection": state,
            },
        )

        return CompletionResult(
            status="rejected",
            verification_state=state,
            missing_factors=[],
        )

    # ------------------------------------------------------------------ #
    # Read helpers for the API layer
    # ------------------------------------------------------------------ #

    def get_freeze_status(self, vendor_name: str) -> Dict[str, Any]:
        """Return the full freeze status for an API response.

        Shape:
          {
            "frozen": bool,
            "detected_at": iso | null,
            "verified_bank_details_masked": dict | null,
            "pending_bank_details_masked": dict | null,
            "verification_state": dict | null,
            "missing_factors": list[str]
          }
        """
        profile = self.db.get_vendor_profile(
            self.organization_id, vendor_name
        )
        if not profile:
            return {
                "frozen": False,
                "detected_at": None,
                "verified_bank_details_masked": None,
                "pending_bank_details_masked": None,
                "verification_state": None,
                "missing_factors": [],
            }

        frozen = bool(profile.get("iban_change_pending"))
        state = profile.get("iban_change_verification_state")
        if not isinstance(state, dict):
            state = None

        missing = (
            [
                name
                for name in REQUIRED_FACTORS
                if not (state.get(name) or {}).get("verified")
            ]
            if state
            else []
        )

        verified_masked = self.db.get_vendor_bank_details_masked(
            self.organization_id, vendor_name
        )
        pending_masked = (
            self.db.get_pending_bank_details_masked(
                self.organization_id, vendor_name
            )
            if frozen
            else None
        )

        return {
            "frozen": frozen,
            "detected_at": profile.get("iban_change_detected_at"),
            "verified_bank_details_masked": verified_masked,
            "pending_bank_details_masked": pending_masked,
            "verification_state": state,
            "missing_factors": missing,
        }

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _all_factors_verified(state: Optional[Dict[str, Any]]) -> bool:
        if not isinstance(state, dict):
            return False
        return all(
            (state.get(name) or {}).get("verified")
            for name in REQUIRED_FACTORS
        )

    def _emit_audit_event(
        self,
        *,
        event_type: str,
        vendor_name: str,
        ap_item_id: Optional[str],
        actor_id: str,
        actor_type: str,
        metadata: Dict[str, Any],
    ) -> None:
        """Emit a structured audit event. Non-fatal on failure."""
        try:
            self.db.append_ap_audit_event(
                {
                    "ap_item_id": ap_item_id or "",
                    "event_type": event_type,
                    "actor_type": actor_type,
                    "actor_id": actor_id,
                    "reason": (
                        f"IBAN change freeze workflow: {event_type} "
                        f"for vendor {vendor_name}"
                    ),
                    "metadata": {
                        **metadata,
                        "vendor_name": vendor_name,
                    },
                    "organization_id": self.organization_id,
                    "source": "iban_change_freeze_service",
                }
            )
        except Exception as exc:
            logger.warning(
                "[IbanChangeFreeze] Audit event %s emission failed (non-fatal): %s",
                event_type, exc,
            )


def get_iban_change_freeze_service(
    organization_id: str, db: Any = None
) -> IbanChangeFreezeService:
    """Factory used by the API layer + validation gate."""
    return IbanChangeFreezeService(organization_id, db=db)
