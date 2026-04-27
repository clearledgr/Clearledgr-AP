"""Seed an "Acme Manufacturing" demo tenant with synthetic AP data.

This is the demo Mo points sales prospects at: realistic vendor profiles,
~40 AP items spanning every state including exceptions and posted bills,
3 entities, 5 users with different roles. Idempotent — safe to re-run.

Usage:
    railway run --service api python scripts/seed_demo_tenant.py
    railway run --service api python scripts/seed_demo_tenant.py --reset

The default password for every demo user is "Demo!2026" — fine for a
non-production sandbox. Override with `--password XYZ`.

NEVER run this against a tenant with real data. The script only writes
under `organization_id = "acme-demo"`, but the `--reset` flag truncates
that org's AP items, vendors, entities, and users before reseeding.
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from clearledgr.core.auth import (
    ROLE_AP_CLERK,
    ROLE_AP_MANAGER,
    ROLE_FINANCIAL_CONTROLLER,
    ROLE_OWNER,
    create_user,
    get_user_by_email,
    hash_password,
)
from clearledgr.core.ap_states import APState
from clearledgr.core.database import get_db

logger = logging.getLogger("seed_demo_tenant")

DEMO_ORG_ID = "acme-demo"
DEMO_ORG_NAME = "Acme Manufacturing"
DEFAULT_PASSWORD = "Demo!2026"


# --------------------------------------------------------------------------- #
# Static fixture data
# --------------------------------------------------------------------------- #

ENTITIES: List[Dict[str, Any]] = [
    {"name": "Acme HQ (US)", "code": "US-HQ", "currency": "USD"},
    {"name": "Acme Europe GmbH", "code": "EU-DE", "currency": "EUR"},
    {"name": "Acme APAC Pte Ltd", "code": "SG-APAC", "currency": "SGD"},
]

USERS: List[Dict[str, Any]] = [
    {
        "email": "owner@acme-demo.clearledgr.dev",
        "name": "Sara Chen",
        "role": ROLE_OWNER,
    },
    {
        "email": "controller@acme-demo.clearledgr.dev",
        "name": "Marcus Patel",
        "role": ROLE_FINANCIAL_CONTROLLER,
    },
    {
        "email": "ap.manager@acme-demo.clearledgr.dev",
        "name": "Priya Rao",
        "role": ROLE_AP_MANAGER,
    },
    {
        "email": "ap.clerk1@acme-demo.clearledgr.dev",
        "name": "James Okafor",
        "role": ROLE_AP_CLERK,
    },
    {
        "email": "ap.clerk2@acme-demo.clearledgr.dev",
        "name": "Yuki Tanaka",
        "role": ROLE_AP_CLERK,
    },
]

# Vendor profiles. `gl` is the typical posting account; `terms` is days net.
VENDORS: List[Dict[str, Any]] = [
    {"name": "Steel Dynamics Inc", "category": "raw_materials", "gl": "5010-cogs-materials", "terms": 30, "avg_amount": 28500.0, "domain": "steeldynamics.com", "po_required": True},
    {"name": "Pacific Logistics", "category": "freight", "gl": "5200-freight", "terms": 45, "avg_amount": 12400.0, "domain": "pacificlogistics.com", "po_required": True},
    {"name": "Slack Technologies", "category": "saas", "gl": "6310-software", "terms": 30, "avg_amount": 1850.0, "domain": "slack.com", "po_required": False},
    {"name": "Notion Labs", "category": "saas", "gl": "6310-software", "terms": 30, "avg_amount": 480.0, "domain": "notion.so", "po_required": False},
    {"name": "GitHub Inc", "category": "saas", "gl": "6310-software", "terms": 30, "avg_amount": 4200.0, "domain": "github.com", "po_required": False},
    {"name": "Anthropic PBC", "category": "saas", "gl": "6310-software", "terms": 30, "avg_amount": 7500.0, "domain": "anthropic.com", "po_required": False},
    {"name": "KPMG LLP", "category": "professional_services", "gl": "6500-professional-fees", "terms": 60, "avg_amount": 18000.0, "domain": "kpmg.com", "po_required": False},
    {"name": "WeWork Companies", "category": "real_estate", "gl": "6700-rent", "terms": 30, "avg_amount": 22000.0, "domain": "wework.com", "po_required": False},
    {"name": "PG&E Corporation", "category": "utilities", "gl": "6800-utilities", "terms": 30, "avg_amount": 3400.0, "domain": "pge.com", "po_required": False},
    {"name": "Comcast Business", "category": "utilities", "gl": "6810-internet", "terms": 30, "avg_amount": 690.0, "domain": "comcast.com", "po_required": False},
    {"name": "Costco Business", "category": "office_supplies", "gl": "6100-office-supplies", "terms": 15, "avg_amount": 1240.0, "domain": "costco.com", "po_required": False},
    {"name": "Staples Advantage", "category": "office_supplies", "gl": "6100-office-supplies", "terms": 30, "avg_amount": 380.0, "domain": "staples.com", "po_required": False},
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _short_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _maybe_jitter(amount: float, pct: float = 0.15) -> float:
    delta = amount * pct
    return round(amount + random.uniform(-delta, delta), 2)


# --------------------------------------------------------------------------- #
# Reset helpers
# --------------------------------------------------------------------------- #

def reset_demo_org(db) -> None:
    """Truncate every demo-org-scoped row so reseed lands on a blank slate."""
    sql_statements = [
        "DELETE FROM audit_events WHERE organization_id = %s",
        "DELETE FROM ap_items WHERE organization_id = %s",
        "DELETE FROM vendor_profiles WHERE organization_id = %s",
        "DELETE FROM entities WHERE organization_id = %s",
        "DELETE FROM users WHERE organization_id = %s",
    ]
    with db.connect() as conn:
        cur = conn.cursor()
        for sql in sql_statements:
            try:
                cur.execute(sql, (DEMO_ORG_ID,))
            except Exception as exc:
                logger.warning("reset skipped %s: %s", sql, exc)
        conn.commit()
    logger.info("reset complete for org=%s", DEMO_ORG_ID)


# --------------------------------------------------------------------------- #
# Bootstrap pieces
# --------------------------------------------------------------------------- #

def ensure_organization(db) -> None:
    db.ensure_organization(
        organization_id=DEMO_ORG_ID,
        organization_name=DEMO_ORG_NAME,
    )
    # Mark onboarding complete so the SPA doesn't hijack the route into
    # the wizard when sales clicks around.
    settings = {
        "onboarding": {
            "completed": True,
            "skipped_steps": [],
        },
        "industry": "Manufacturing",
        "country": "US",
    }
    try:
        db.update_organization(DEMO_ORG_ID, settings=settings)
    except Exception as exc:
        logger.warning("could not stamp onboarding-complete settings: %s", exc)


def seed_users(db, password: str) -> Dict[str, str]:
    """Create or refresh the demo users. Returns email -> user_id map."""
    pw_hash = hash_password(password)
    out: Dict[str, str] = {}
    for spec in USERS:
        existing = get_user_by_email(spec["email"])
        if existing is None:
            user = create_user(
                email=spec["email"],
                password=password,
                name=spec["name"],
                organization_id=DEMO_ORG_ID,
                role=spec["role"],
            )
            out[spec["email"]] = user.id
            logger.info("created user %s (%s)", spec["email"], spec["role"])
        else:
            db.update_user(
                existing.id,
                password_hash=pw_hash,
                role=spec["role"],
                organization_id=DEMO_ORG_ID,
                is_active=True,
                name=spec["name"],
            )
            out[spec["email"]] = existing.id
            logger.info("refreshed user %s", spec["email"])
    return out


def seed_entities(db) -> List[Dict[str, Any]]:
    """Create one entity per ENTITIES row. Skips if (org, code) already exists."""
    created: List[Dict[str, Any]] = []
    for spec in ENTITIES:
        existing = db.get_entity_by_code(DEMO_ORG_ID, spec["code"])
        if existing:
            created.append(existing)
            continue
        ent = db.create_entity(
            organization_id=DEMO_ORG_ID,
            name=spec["name"],
            code=spec["code"],
            currency=spec["currency"],
            gl_mapping={"default": "5000-cogs"},
            approval_rules={"threshold_amount": 5000.0, "threshold_currency": spec["currency"]},
        )
        created.append(ent)
        logger.info("created entity %s (%s)", spec["name"], spec["code"])
    return created


def seed_vendors(db) -> Dict[str, Dict[str, Any]]:
    """Upsert vendor profiles. Returns name -> profile dict."""
    out: Dict[str, Dict[str, Any]] = {}
    for v in VENDORS:
        prof = db.upsert_vendor_profile(
            organization_id=DEMO_ORG_ID,
            vendor_name=v["name"],
            sender_domains=[v["domain"]],
            typical_gl_code=v["gl"],
            requires_po=v["po_required"],
            payment_terms=f"net{v['terms']}",
            avg_invoice_amount=v["avg_amount"],
            metadata={"category": v["category"]},
        )
        out[v["name"]] = prof
    logger.info("seeded %d vendors", len(out))
    return out


# --------------------------------------------------------------------------- #
# AP item generation — a realistic distribution across states
# --------------------------------------------------------------------------- #

# (state, count, days_ago_min, days_ago_max, override_attrs)
DISTRIBUTION: List[Tuple[APState, int, int, int, Dict[str, Any]]] = [
    (APState.RECEIVED,        4, 0, 1,   {}),
    (APState.VALIDATED,       6, 1, 3,   {}),
    (APState.NEEDS_INFO,      4, 2, 10,  {"exception_code": "missing_po", "exception_severity": "warning"}),
    (APState.NEEDS_APPROVAL,  8, 1, 5,   {"approval_surface": "slack"}),
    (APState.APPROVED,        4, 1, 3,   {}),
    (APState.READY_TO_POST,   3, 0, 1,   {}),
    (APState.POSTED_TO_ERP,   6, 3, 30,  {}),
    (APState.FAILED_POST,     2, 1, 4,   {"last_error": "ERP authentication failed (token expired)"}),
    (APState.REJECTED,        2, 5, 20,  {"rejection_reason": "Duplicate of prior month invoice"}),
    (APState.CLOSED,          3, 30, 90, {}),
]


def _vendor_pool() -> List[Dict[str, Any]]:
    return list(VENDORS)


def _build_invoice_payload(
    *,
    state: APState,
    vendor: Dict[str, Any],
    entity_id: Optional[str],
    user_id: Optional[str],
    days_ago: int,
    override: Dict[str, Any],
) -> Dict[str, Any]:
    now = _now()
    invoice_date = now - timedelta(days=days_ago)
    due_date = invoice_date + timedelta(days=vendor["terms"])
    amount = _maybe_jitter(vendor["avg_amount"])
    invoice_number = f"INV-{random.randint(10000, 99999)}"
    invoice_key = f"{vendor['name']}|{invoice_number}|{amount:.2f}"

    payload: Dict[str, Any] = {
        "vendor_name": vendor["name"],
        "amount": amount,
        "currency": "USD",
        "invoice_number": invoice_number,
        "invoice_key": invoice_key,
        "invoice_date": invoice_date.date().isoformat(),
        "due_date": due_date.date().isoformat(),
        "subject": f"Invoice {invoice_number} from {vendor['name']}",
        "sender": f"ar@{vendor['domain']}",
        "confidence": round(random.uniform(0.82, 0.99), 3),
        "approval_required": amount >= 5000,
        "state": state.value,
        "organization_id": DEMO_ORG_ID,
        "user_id": user_id,
        "entity_id": entity_id,
        "document_type": "invoice",
        "thread_id": f"demo-thread-{uuid.uuid4().hex[:12]}",
        "message_id": f"<demo-{uuid.uuid4().hex[:12]}@acme-demo>",
        "metadata": {
            "demo_seed": True,
            "vendor_category": vendor["category"],
            "gl_code": vendor["gl"],
            "po_required": vendor["po_required"],
        },
    }

    # Per-state finalization
    if state == APState.NEEDS_APPROVAL:
        payload["approval_surface"] = "slack"
        payload["slack_channel_id"] = "C-DEMO-AP"
        payload["slack_thread_id"] = f"1700000000.{random.randint(100000, 999999)}"
    if state == APState.APPROVED:
        payload["approved_at"] = _iso(now - timedelta(hours=random.randint(1, 24)))
        payload["approved_by"] = "controller@acme-demo.clearledgr.dev"
    if state == APState.READY_TO_POST:
        payload["approved_at"] = _iso(now - timedelta(hours=random.randint(2, 8)))
        payload["approved_by"] = "controller@acme-demo.clearledgr.dev"
    if state == APState.POSTED_TO_ERP:
        payload["approved_at"] = _iso(now - timedelta(days=days_ago, hours=-2))
        payload["approved_by"] = "controller@acme-demo.clearledgr.dev"
        payload["erp_reference"] = f"NS-BILL-{random.randint(100000, 999999)}"
        payload["erp_posted_at"] = _iso(now - timedelta(days=max(0, days_ago - 1)))
    if state == APState.FAILED_POST:
        payload["approved_at"] = _iso(now - timedelta(hours=random.randint(2, 12)))
        payload["approved_by"] = "controller@acme-demo.clearledgr.dev"
        payload["post_attempted_at"] = _iso(now - timedelta(hours=random.randint(1, 6)))
    if state == APState.REJECTED:
        payload["rejected_at"] = _iso(now - timedelta(days=days_ago, hours=-3))
        payload["rejected_by"] = "ap.manager@acme-demo.clearledgr.dev"
    if state == APState.CLOSED:
        payload["approved_at"] = _iso(now - timedelta(days=days_ago, hours=-4))
        payload["approved_by"] = "controller@acme-demo.clearledgr.dev"
        payload["erp_reference"] = f"NS-BILL-{random.randint(100000, 999999)}"
        payload["erp_posted_at"] = _iso(now - timedelta(days=max(0, days_ago - 1)))
        payload["payment_reference"] = f"PMT-{random.randint(100000, 999999)}"

    payload.update(override)
    return payload


def seed_ap_items(
    db,
    entities: Sequence[Dict[str, Any]],
    user_map: Dict[str, str],
) -> int:
    """Generate the full AP item distribution. Returns count created."""
    pool = _vendor_pool()
    entity_ids = [e["id"] for e in entities] or [None]
    clerk_user_id = (
        user_map.get("ap.clerk1@acme-demo.clearledgr.dev")
        or user_map.get("owner@acme-demo.clearledgr.dev")
    )
    total = 0
    for state, count, dmin, dmax, override in DISTRIBUTION:
        for _ in range(count):
            vendor = random.choice(pool)
            entity_id = random.choice(entity_ids)
            days_ago = random.randint(dmin, dmax)
            payload = _build_invoice_payload(
                state=state,
                vendor=vendor,
                entity_id=entity_id,
                user_id=clerk_user_id,
                days_ago=days_ago,
                override=override,
            )
            try:
                db.create_ap_item(payload)
                total += 1
            except Exception as exc:
                logger.exception("create_ap_item failed: %s", exc)
    logger.info("seeded %d ap_items across %d states", total, len(DISTRIBUTION))
    return total


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def main() -> int:
    parser = argparse.ArgumentParser(description="Seed Acme Manufacturing demo tenant")
    parser.add_argument("--reset", action="store_true", help="truncate the demo org before seeding")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="password for every demo user")
    parser.add_argument("--seed", type=int, default=42, help="random seed (deterministic invoice numbers/amounts)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    random.seed(args.seed)

    db = get_db()
    db.initialize()

    if args.reset:
        reset_demo_org(db)

    ensure_organization(db)
    user_map = seed_users(db, args.password)
    entities = seed_entities(db)
    seed_vendors(db)
    ap_count = seed_ap_items(db, entities, user_map)

    print()
    print("=" * 70)
    print(f"Demo tenant ready — org={DEMO_ORG_ID}")
    print(f"  Org name:        {DEMO_ORG_NAME}")
    print(f"  Entities:        {len(entities)}")
    print(f"  Users:           {len(user_map)}")
    print(f"  Vendors:         {len(VENDORS)}")
    print(f"  AP items:        {ap_count}")
    print()
    print("Sign in at https://workspace.clearledgr.com/login as any of:")
    for spec in USERS:
        print(f"  - {spec['email']:50s} ({spec['role']})")
    print(f"  Password: {args.password}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
