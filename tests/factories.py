"""Test data builders for Clearledgr.

Test files used to hand-roll the same setup boilerplate over and over:

    payload = {
        "organization_id": "default",
        "gmail_id": f"msg-{uuid.uuid4().hex}",
        "vendor_name": "Acme",
        "amount": 100.0,
        "currency": "USD",
        "state": "received",
        ...
    }
    db.create_ap_item(payload)

This module wraps that into composable builders so each test is just
the *meaningful* deviation from a sane default. The helpers are
deliberately thin — no factory_boy dependency, no hidden magic. Each
function takes a ``db`` (a ``ClearledgrDB``) plus the fields a test
actually cares about, fills in defaults, and returns the canonical row
dict so tests can read back the id.

Conventions:

- Every builder takes ``db`` as the first positional arg.
- Every builder accepts ``**overrides`` so a test can override any
  default with one keyword.
- Every builder generates a fresh UUID for the primary key by default
  so tests in the same DB session don't collide.
- Builders return the persisted dict (the same shape ``db.get_*``
  returns), not the input payload.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Organizations
# ---------------------------------------------------------------------------


def make_organization(
    db,
    *,
    organization_id: Optional[str] = None,
    name: Optional[str] = None,
    domain: Optional[str] = None,
    settings: Optional[Dict[str, Any]] = None,
    integration_mode: str = "shared",
) -> Dict[str, Any]:
    """Create an organization row with defaults filled in."""
    org_id = organization_id or f"ORG-{uuid.uuid4().hex[:12]}"
    return db.create_organization(
        organization_id=org_id,
        name=name or org_id.replace("-", " ").title(),
        domain=domain,
        settings=settings or {},
        integration_mode=integration_mode,
    )


# ---------------------------------------------------------------------------
# AP items
# ---------------------------------------------------------------------------


_DEFAULT_AP_STATE = "received"


def make_ap_item(
    db,
    *,
    organization_id: str = "default",
    vendor_name: str = "Acme Corp",
    amount: float = 100.0,
    currency: str = "USD",
    state: str = _DEFAULT_AP_STATE,
    invoice_number: Optional[str] = None,
    gmail_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    confidence: float = 0.95,
    metadata: Optional[Dict[str, Any]] = None,
    **overrides: Any,
) -> Dict[str, Any]:
    """Create an AP item with sane defaults.

    Defaults to the happy-path shape — a high-confidence USD invoice
    in ``received`` state, ready for the workflow to pick up. Tests
    that need a specific state (e.g. ``failed_post`` to exercise the
    retry path) should pass ``state=`` explicitly.
    """
    ap_id = overrides.pop("id", None) or f"AP-{uuid.uuid4().hex[:16]}"
    payload: Dict[str, Any] = {
        "id": ap_id,
        "organization_id": organization_id,
        "vendor_name": vendor_name,
        "amount": amount,
        "currency": currency,
        "state": state,
        "confidence": confidence,
        "invoice_number": invoice_number or f"INV-{ap_id[-6:]}",
        "gmail_id": gmail_id or f"msg-{uuid.uuid4().hex[:12]}",
        "thread_id": thread_id,
        "metadata": metadata or {},
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    payload.update(overrides)
    db.create_ap_item(payload)
    return db.get_ap_item(ap_id) or payload


# ---------------------------------------------------------------------------
# Vendor profiles
# ---------------------------------------------------------------------------


def make_vendor_profile(
    db,
    *,
    organization_id: str = "default",
    vendor_name: str = "Acme Corp",
    sender_domains: Optional[list] = None,
    typical_gl_code: Optional[str] = None,
    requires_po: bool = False,
    invoice_count: int = 0,
    avg_invoice_amount: Optional[float] = None,
    metadata: Optional[Dict[str, Any]] = None,
    **overrides: Any,
) -> Dict[str, Any]:
    """Upsert a vendor profile with sensible defaults."""
    fields: Dict[str, Any] = {
        "sender_domains": sender_domains or [],
        "requires_po": requires_po,
        "invoice_count": invoice_count,
        "metadata": metadata or {},
    }
    if typical_gl_code is not None:
        fields["typical_gl_code"] = typical_gl_code
    if avg_invoice_amount is not None:
        fields["avg_invoice_amount"] = avg_invoice_amount
    fields.update(overrides)
    return db.upsert_vendor_profile(organization_id, vendor_name, **fields)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


def make_user(
    db,
    *,
    email: Optional[str] = None,
    organization_id: str = "default",
    role: str = "ap_clerk",
    name: Optional[str] = None,
    is_active: bool = True,
) -> Dict[str, Any]:
    """Create a test user. Uses the same upsert path Google sign-in uses
    so the resulting row is shaped the same as in production."""
    email_addr = email or f"user-{uuid.uuid4().hex[:8]}@example.com"
    google_id = f"test:{uuid.uuid4().hex}"
    return db.upsert_google_user(
        email=email_addr,
        google_id=google_id,
        organization_id=organization_id,
        name=name or email_addr.split("@")[0].replace(".", " ").title(),
        role=role,
    )
