"""VendorDomainLockService — Phase 2.2.

DESIGN_THESIS.md §8: *"Vendor domain lock — an invoice arriving from a
sender domain that doesn't match the vendor's known domains is treated
as potential vendor impersonation and blocked."*

Phase 1.2a (fraud controls) shipped 5 of 7 Group A primitives as
blocking gates. Phase 2.2 closes the sixth: vendor domain lock. This
service owns the sender-domain match logic on top of the
``sender_domains`` allowlist already tracked on ``vendor_profiles``.

Matching semantics:
  - Case-insensitive
  - Exact match OR suffix match (``billing.acme.com`` matches
    ``acme.com`` because ``acme.com`` is a suffix on a dot boundary)
  - Payment-processor domains (Stripe, PayPal, Paddle, Bill.com, etc.)
    bypass the lock because the "sender" for processor-routed invoices
    is the processor, not the underlying merchant. Processors are a
    separate trust mechanism Clearledgr already recognizes; Phase 2.2
    does not redefine that boundary.

Bootstrap semantics:
  - Vendors with NO known sender domains are NOT blocked by this
    service. The first invoice for a brand-new vendor is already
    blocked by ``first_payment_hold`` (Phase 1.2a) and routes to
    human review. When the human approves and the AP item reaches
    ``posted_to_erp``, the ``VendorDomainTrackingObserver`` records
    the sender domain as trusted. Second invoice onwards is
    protected. This is TOFU (trust on first use) — the first-payment
    gate is the human-verification boundary that makes TOFU safe.

The service exposes two entry points:
  - ``check_sender_domain`` — pure read used by the validation gate
  - ``extract_sender_domain`` — helper for parsing the raw sender field
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from email.utils import parseaddr
from typing import Any, Iterable, List, Optional

logger = logging.getLogger(__name__)


# Payment processor domains that bypass the vendor domain lock. Mirrors
# ``llm_email_parser._PAYMENT_PROCESSOR_DOMAINS`` but re-declared here
# so the lock has its own authoritative source and isn't coupled to the
# extractor's internal state.
PAYMENT_PROCESSOR_DOMAINS: frozenset[str] = frozenset(
    {
        "stripe.com",
        "paypal.com",
        "square.com",
        "squareup.com",
        "braintree.com",
        "paddle.com",
        "chargebee.com",
        "recurly.com",
        "fastspring.com",
        "gumroad.com",
        "lemonsqueezy.com",
        "bill.com",
        "payoneer.com",
        "wise.com",
        "transferwise.com",
    }
)


# Outcomes the validation gate can branch on.
STATUS_MATCH = "match"
STATUS_MISMATCH = "mismatch"
STATUS_PROCESSOR_BYPASS = "processor_bypass"
STATUS_NO_KNOWN_DOMAINS = "no_known_domains"
STATUS_NO_SENDER = "no_sender"
STATUS_NO_VENDOR = "no_vendor"


@dataclass(frozen=True)
class DomainCheckResult:
    """Result of a vendor sender-domain check."""

    status: str
    sender_domain: str
    known_domains: List[str]
    vendor_name: Optional[str] = None

    @property
    def should_block(self) -> bool:
        """Only ``mismatch`` triggers the gate block.

        ``no_known_domains`` is a legitimate bootstrap path covered by
        first-payment-hold. ``processor_bypass`` means the sender is
        a known payment processor that we trust at ingest. ``no_sender``
        and ``no_vendor`` are no-signal states.
        """
        return self.status == STATUS_MISMATCH


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


_DOMAIN_CLEAN_RE = re.compile(r"[^a-z0-9.\-]+")


def extract_sender_domain(raw_sender: Any) -> str:
    """Parse a raw Gmail ``From`` header and return the normalized domain.

    Handles the common shapes:
      - ``billing@acme.com``
      - ``Acme Corp <billing@acme.com>``
      - ``"Acme Corp" <billing@acme.com>``

    Returns an empty string when the input is malformed or has no ``@``.
    The returned domain is lowercased and stripped of any characters
    that can't appear in a valid DNS label — this prevents a sender
    with display-name injection from polluting downstream comparisons.
    """
    if not raw_sender:
        return ""
    raw = str(raw_sender).strip()
    # parseaddr handles the display-name-wrapped shapes
    _, email_addr = parseaddr(raw)
    candidate = email_addr or raw
    if "@" not in candidate:
        return ""
    domain = candidate.rsplit("@", 1)[-1].strip().strip(">").strip("<").strip()
    domain = domain.lower()
    # Strip any characters that can't appear in a valid DNS label.
    cleaned = _DOMAIN_CLEAN_RE.sub("", domain)
    return cleaned.strip(".")


def _registrable_base(domain: str) -> str:
    """Return the last two labels of a domain (e.g. ``acme.com``).

    Naive but safe — we don't need a full PSL (Public Suffix List)
    lookup here because the allowlist comparison itself is suffix-
    matching on dot boundaries. This helper is used only to identify
    payment-processor domains, which are always two-label.
    """
    if not domain:
        return ""
    parts = domain.split(".")
    if len(parts) <= 2:
        return domain
    return ".".join(parts[-2:])


def is_payment_processor(domain: str) -> bool:
    """True iff the domain (or its registrable base) is a known processor."""
    if not domain:
        return False
    base = _registrable_base(domain)
    return domain in PAYMENT_PROCESSOR_DOMAINS or base in PAYMENT_PROCESSOR_DOMAINS


def domain_matches_allowlist(
    sender_domain: str,
    allowlist: Iterable[str],
) -> bool:
    """Return True iff ``sender_domain`` is allowed.

    A match is one of:
      - Exact equality with any entry in the allowlist, OR
      - ``sender_domain`` ends with ``.{entry}`` (dot-boundary suffix)

    Dot-boundary suffix matching means:
      - ``acme.com`` allows ``billing.acme.com`` ✓
      - ``acme.com`` does NOT allow ``fake-acme.com`` ✗ (no dot before ``acme.com``)
      - ``acme.com`` does NOT allow ``acme.com.evil`` ✗ (not a suffix)

    Both sides are compared after normalization (lowercase + strip).
    """
    if not sender_domain:
        return False
    sender = sender_domain.strip().lower()
    if not sender:
        return False
    for entry in allowlist or ():
        normalized = str(entry or "").strip().lower().strip(".")
        if not normalized:
            continue
        if sender == normalized:
            return True
        if sender.endswith("." + normalized):
            return True
    return False


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class VendorDomainLockService:
    """Vendor domain lock check + audit emission."""

    def __init__(self, organization_id: str, db: Any = None) -> None:
        from clearledgr.core.database import get_db
        self.organization_id = organization_id
        self.db = db or get_db()

    def check_sender_domain(
        self,
        *,
        vendor_name: Optional[str],
        sender: Any,
    ) -> DomainCheckResult:
        """Validate the invoice sender against the vendor's allowlist.

        Returns a ``DomainCheckResult``. Only ``status=="mismatch"``
        triggers the gate's blocking reason code; the other statuses
        (``match``, ``processor_bypass``, ``no_known_domains``,
        ``no_sender``, ``no_vendor``) are informational signals the
        gate silently accepts.

        This method does NOT mutate any state. The auto-populate of
        the allowlist on first post is the ``VendorDomainTracking
        Observer``'s responsibility.
        """
        if not vendor_name:
            return DomainCheckResult(
                status=STATUS_NO_VENDOR,
                sender_domain="",
                known_domains=[],
                vendor_name=None,
            )

        sender_domain = extract_sender_domain(sender)
        if not sender_domain:
            return DomainCheckResult(
                status=STATUS_NO_SENDER,
                sender_domain="",
                known_domains=[],
                vendor_name=vendor_name,
            )

        if is_payment_processor(sender_domain):
            return DomainCheckResult(
                status=STATUS_PROCESSOR_BYPASS,
                sender_domain=sender_domain,
                known_domains=[],
                vendor_name=vendor_name,
            )

        try:
            allowlist = self.db.get_trusted_sender_domains(
                self.organization_id, vendor_name
            )
        except Exception as exc:
            logger.warning(
                "[VendorDomainLock] get_trusted_sender_domains failed for %s/%s: %s",
                self.organization_id, vendor_name, exc,
            )
            allowlist = []

        if not allowlist:
            # Bootstrap path — first-payment-hold covers this.
            return DomainCheckResult(
                status=STATUS_NO_KNOWN_DOMAINS,
                sender_domain=sender_domain,
                known_domains=[],
                vendor_name=vendor_name,
            )

        if domain_matches_allowlist(sender_domain, allowlist):
            return DomainCheckResult(
                status=STATUS_MATCH,
                sender_domain=sender_domain,
                known_domains=list(allowlist),
                vendor_name=vendor_name,
            )

        return DomainCheckResult(
            status=STATUS_MISMATCH,
            sender_domain=sender_domain,
            known_domains=list(allowlist),
            vendor_name=vendor_name,
        )

    # ------------------------------------------------------------------ #
    # Write paths with audit events
    # ------------------------------------------------------------------ #

    def add_trusted_domain(
        self,
        *,
        vendor_name: str,
        domain: str,
        actor_id: str,
    ) -> bool:
        """Add a trusted domain and emit an audit event.

        Role gating is enforced at the API boundary via
        ``require_cfo`` — this method trusts the
        caller's authorization.
        """
        if not vendor_name or not domain:
            return False
        existing = self.db.get_trusted_sender_domains(
            self.organization_id, vendor_name
        )
        normalized = str(domain).strip().lower()
        already = normalized in existing
        ok = self.db.add_trusted_sender_domain(
            self.organization_id, vendor_name, domain, actor_id=actor_id
        )
        if ok and not already:
            self._emit_audit_event(
                event_type="vendor_trusted_domain_added",
                vendor_name=vendor_name,
                actor_id=actor_id,
                metadata={"domain": normalized},
            )
        return ok

    def remove_trusted_domain(
        self,
        *,
        vendor_name: str,
        domain: str,
        actor_id: str,
    ) -> bool:
        """Remove a trusted domain and emit an audit event.

        Returns True only when the domain was actually removed (not a
        no-op).
        """
        if not vendor_name or not domain:
            return False
        removed = self.db.remove_trusted_sender_domain(
            self.organization_id, vendor_name, domain
        )
        if removed:
            self._emit_audit_event(
                event_type="vendor_trusted_domain_removed",
                vendor_name=vendor_name,
                actor_id=actor_id,
                metadata={"domain": str(domain).strip().lower()},
            )
        return removed

    def list_trusted_domains(self, vendor_name: str) -> List[str]:
        """Return the current allowlist (for the GET endpoint)."""
        if not vendor_name:
            return []
        return self.db.get_trusted_sender_domains(
            self.organization_id, vendor_name
        )

    # ------------------------------------------------------------------ #
    # Observer hook — first-sighting auto-populate
    # ------------------------------------------------------------------ #

    def record_domain_on_first_post(
        self,
        *,
        vendor_name: str,
        sender: Any,
    ) -> bool:
        """Record the sender domain as trusted IF this is the first
        post for a vendor with no known domains yet.

        Called by ``VendorDomainTrackingObserver`` on ``posted_to_erp``
        transitions. Returns True only when a new domain was recorded.
        """
        if not vendor_name:
            return False
        sender_domain = extract_sender_domain(sender)
        if not sender_domain:
            return False
        if is_payment_processor(sender_domain):
            return False
        recorded = self.db.ensure_trusted_sender_domain_tracked(
            self.organization_id, vendor_name, sender_domain
        )
        if recorded:
            self._emit_audit_event(
                event_type="vendor_trusted_domain_auto_recorded",
                vendor_name=vendor_name,
                actor_id="system:domain_tracking_observer",
                metadata={
                    "domain": sender_domain,
                    "reason": "first_successful_post",
                },
            )
        return recorded

    # ------------------------------------------------------------------ #
    # Audit plumbing
    # ------------------------------------------------------------------ #

    def _emit_audit_event(
        self,
        *,
        event_type: str,
        vendor_name: str,
        actor_id: str,
        metadata: dict,
    ) -> None:
        try:
            self.db.append_ap_audit_event(
                {
                    "ap_item_id": "",
                    "event_type": event_type,
                    "actor_type": (
                        "system" if actor_id.startswith("system:") else "user"
                    ),
                    "actor_id": actor_id,
                    "reason": f"Vendor domain lock: {event_type} for {vendor_name}",
                    "metadata": {
                        **metadata,
                        "vendor_name": vendor_name,
                    },
                    "organization_id": self.organization_id,
                    "source": "vendor_domain_lock_service",
                }
            )
        except Exception as exc:
            logger.warning(
                "[VendorDomainLock] Audit event %s emission failed: %s",
                event_type, exc,
            )


def get_vendor_domain_lock_service(
    organization_id: str, db: Any = None
) -> VendorDomainLockService:
    """Factory mirror of the other service modules."""
    return VendorDomainLockService(organization_id, db=db)
