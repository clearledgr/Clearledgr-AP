"""Agent-action credit pool — DESIGN_THESIS.md §13.

"A pooled credit system for compute-intensive agent actions:
extracting data from non-standard invoice formats, adverse media
checks during KYC, multi-entity ERP reconciliation. Credits are
pooled across the team, purchased in advance, and consumed per
action. Failed actions do not consume credits. A confirmation prompt
appears before any action that would consume a significant number of
credits."

The ledger (``agent_credit_ledger``) is the source of truth for the
balance. Service functions here are the only sanctioned writers;
direct INSERTs bypass the balance invariants and must not be used.

Balance invariant:
  balance = Σ credits where entry_type ∈ {auto_grant, purchase, refund}
          - Σ credits where entry_type == 'consume'

entry_type semantics:
  auto_grant — monthly tier allowance, recorded once per org per
    billing period. Starter = 500, Professional = 3000, Enterprise
    = unlimited (Enterprise skips the ledger entirely and uses the
    unlimited sentinel path in consume_credit).
  purchase — admin top-up via the Gmail Settings > Billing > Buy
    credits surface. Records the Stripe charge id in metadata.
  consume — agent action succeeded and used credits.
  refund — agent action failed; reverses a specific prior consume
    entry (linked via related_entry_id). Thesis: "failed actions
    do not consume credits".
  expire — unused purchased credits past the retention window.
    Reserved for a post-V1 expiry policy; no code writes expire
    entries today.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# §13: "A confirmation prompt appears before any action that would
# consume a significant number of credits." The thesis doesn't pin
# an exact threshold — we default to 10 credits per single action as
# "significant" since typical per-action consumption is 1. The
# customer can raise it via a future settings knob; the default is
# picked so operators aren't interrupted on routine 1-credit
# extractions but ARE interrupted on 20-credit full-onboarding UBO
# resolutions.
_DEFAULT_CONFIRMATION_THRESHOLD = 10


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _entry_id() -> str:
    return f"credit-{uuid.uuid4().hex[:16]}"


# Entry type sentinels.
ENTRY_AUTO_GRANT = "auto_grant"
ENTRY_PURCHASE = "purchase"
ENTRY_CONSUME = "consume"
ENTRY_REFUND = "refund"
ENTRY_EXPIRE = "expire"

_GRANT_TYPES = frozenset({ENTRY_AUTO_GRANT, ENTRY_PURCHASE, ENTRY_REFUND})
_DEBIT_TYPES = frozenset({ENTRY_CONSUME, ENTRY_EXPIRE})


@dataclass(frozen=True)
class ConsumePreview:
    """Result of ``preview_consume`` — does not mutate the ledger.

    ``requires_confirmation`` is True when the caller should show the
    human an explicit "consume N credits?" prompt before proceeding.
    ``allowed`` is True when the current balance can cover the
    request (balance + unlimited=True for Enterprise).
    """

    allowed: bool
    credits: int
    balance_before: int
    balance_after: int
    requires_confirmation: bool
    unlimited: bool = False
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_unlimited(org_id: str) -> bool:
    """Enterprise tier treats the pool as unlimited — ledger entries
    aren't written, consume calls always succeed. Check via the
    subscription service so the tier is the single source of truth.
    """
    try:
        from clearledgr.services.subscription import get_subscription_service
        sub = get_subscription_service().get_subscription(org_id)
        return bool(sub.limits and sub.limits.ai_credits_per_month == -1)
    except Exception:
        return False


def _insert_entry(
    db: Any,
    *,
    organization_id: str,
    entry_type: str,
    credits: int,
    action_type: Optional[str] = None,
    ap_item_id: Optional[str] = None,
    related_entry_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    created_by: Optional[str] = None,
) -> str:
    """Low-level insert. Returns the new entry id."""
    entry_id = _entry_id()
    metadata_json = json.dumps(metadata or {})
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            (
                "INSERT INTO agent_credit_ledger "
                "(id, organization_id, entry_type, credits, action_type, "
                " ap_item_id, related_entry_id, metadata, created_at, created_by) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            ),
            (
                entry_id, organization_id, entry_type, int(credits),
                action_type, ap_item_id, related_entry_id,
                metadata_json, _now(), created_by or "system",
            ),
        )
        conn.commit()
    return entry_id


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_balance(organization_id: str, db: Any = None) -> int:
    """Return the current credit pool balance for the org.

    Enterprise (unlimited) returns -1 as the unlimited sentinel —
    callers MUST check for -1 before doing arithmetic. Non-enterprise
    tiers walk the ledger and sum grants - consumes.
    """
    if _is_unlimited(organization_id):
        return -1

    if db is None:
        from clearledgr.core.database import get_db
        db = get_db()

    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                (
                    "SELECT entry_type, COALESCE(SUM(credits), 0) AS total "
                    "FROM agent_credit_ledger WHERE organization_id = %s "
                    "GROUP BY entry_type"
                ),
                (organization_id,),
            )
            rows = cur.fetchall()
    except Exception as exc:
        logger.debug("[credit_pool] get_balance failed for %s: %s", organization_id, exc)
        return 0

    balance = 0
    for row in rows or []:
        entry_type = row["entry_type"] if hasattr(row, "__getitem__") else row[0]
        total = int(row["total"] if hasattr(row, "__getitem__") else row[1])
        if entry_type in _GRANT_TYPES:
            balance += total
        elif entry_type in _DEBIT_TYPES:
            balance -= total
    return max(0, balance)


def ensure_monthly_grant(organization_id: str, db: Any = None) -> Optional[str]:
    """Ensure the current billing period's auto-grant has been recorded.

    Called lazily from consume/preview so the ledger picks up each
    month's allowance without a scheduled cron. Returns the grant
    entry id if one was written, None if the grant already exists
    for this period or the tier is unlimited.

    Period boundary: calendar month (UTC). Enterprise skips entirely
    because its unlimited-sentinel path doesn't touch the ledger.
    """
    if _is_unlimited(organization_id):
        return None

    if db is None:
        from clearledgr.core.database import get_db
        db = get_db()

    # Resolve the tier's monthly allowance.
    try:
        from clearledgr.services.subscription import get_subscription_service
        sub = get_subscription_service().get_subscription(organization_id)
        allowance = int((sub.limits.ai_credits_per_month if sub.limits else 0) or 0)
    except Exception:
        allowance = 0
    if allowance <= 0:
        return None

    # Idempotency: look for an auto_grant entry in the current month.
    now = datetime.now(timezone.utc)
    period_start_iso = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                (
                    "SELECT id FROM agent_credit_ledger "
                    "WHERE organization_id = %s AND entry_type = %s AND created_at >= %s "
                    "LIMIT 1"
                ),
                (organization_id, ENTRY_AUTO_GRANT, period_start_iso),
            )
            existing = cur.fetchone()
    except Exception as exc:
        logger.debug("[credit_pool] ensure_monthly_grant lookup failed: %s", exc)
        return None

    if existing:
        return None  # Already granted for this period.

    return _insert_entry(
        db,
        organization_id=organization_id,
        entry_type=ENTRY_AUTO_GRANT,
        credits=allowance,
        metadata={"period_start": period_start_iso, "allowance_source": "tier"},
        created_by="system:monthly_grant",
    )


def preview_consume(
    organization_id: str,
    credits: int,
    *,
    confirmation_threshold: int = _DEFAULT_CONFIRMATION_THRESHOLD,
    db: Any = None,
) -> ConsumePreview:
    """Non-mutating check for a prospective consume.

    Used by the agent's confirmation-prompt flow: if
    ``requires_confirmation`` is True, the surface must ask the
    human to approve the consumption before the agent calls
    ``consume_credit``.
    """
    if _is_unlimited(organization_id):
        return ConsumePreview(
            allowed=True, credits=int(credits), balance_before=-1,
            balance_after=-1, requires_confirmation=False, unlimited=True,
        )

    if credits < 0:
        return ConsumePreview(
            allowed=False, credits=int(credits), balance_before=0,
            balance_after=0, requires_confirmation=False,
            reason="negative_credits_not_allowed",
        )

    ensure_monthly_grant(organization_id, db=db)
    balance = get_balance(organization_id, db=db)
    if balance == -1:  # unlimited resolved after monthly grant
        return ConsumePreview(
            allowed=True, credits=int(credits), balance_before=-1,
            balance_after=-1, requires_confirmation=False, unlimited=True,
        )

    after = balance - int(credits)
    allowed = after >= 0
    requires_confirmation = allowed and int(credits) >= int(confirmation_threshold)

    return ConsumePreview(
        allowed=allowed,
        credits=int(credits),
        balance_before=balance,
        balance_after=after,
        requires_confirmation=requires_confirmation,
        reason=None if allowed else "insufficient_credits",
    )


def consume_credit(
    organization_id: str,
    *,
    credits: int,
    action_type: str,
    ap_item_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    created_by: Optional[str] = None,
    db: Any = None,
) -> Dict[str, Any]:
    """Record a successful consumption of credits.

    Returns a dict carrying the ledger entry id (so the caller can
    reference it in a subsequent :func:`refund_credit` call if the
    action ends up rolled back) plus the balance after the write.

    The thesis rule — "Failed actions do not consume credits" — is
    enforced by the caller's workflow: consume is only written when
    the action succeeds. If the action fails AFTER consume was
    written (rare, but possible with multi-step actions), the caller
    uses :func:`refund_credit` to reverse the entry.
    """
    if _is_unlimited(organization_id):
        return {
            "ok": True,
            "entry_id": None,
            "credits": int(credits),
            "balance_after": -1,
            "unlimited": True,
        }

    if credits < 0:
        return {"ok": False, "reason": "negative_credits_not_allowed"}

    if db is None:
        from clearledgr.core.database import get_db
        db = get_db()

    ensure_monthly_grant(organization_id, db=db)
    balance_before = get_balance(organization_id, db=db)
    if balance_before < int(credits):
        return {
            "ok": False,
            "reason": "insufficient_credits",
            "balance": balance_before,
            "requested": int(credits),
        }

    entry_id = _insert_entry(
        db,
        organization_id=organization_id,
        entry_type=ENTRY_CONSUME,
        credits=int(credits),
        action_type=action_type,
        ap_item_id=ap_item_id,
        metadata=metadata,
        created_by=created_by,
    )
    return {
        "ok": True,
        "entry_id": entry_id,
        "credits": int(credits),
        "balance_after": balance_before - int(credits),
    }


def refund_credit(
    organization_id: str,
    *,
    original_entry_id: str,
    reason: str,
    db: Any = None,
) -> Dict[str, Any]:
    """Reverse a prior consume entry — §13 "failed actions do not consume credits".

    Writes a refund entry linked to the original consume via
    ``related_entry_id``. Looks up the original's credit amount so
    the refund is exactly the right size; the caller only needs to
    pass the entry id plus a reason string that surfaces in the
    audit metadata.
    """
    if _is_unlimited(organization_id):
        return {"ok": True, "refund_entry_id": None, "unlimited": True}

    if db is None:
        from clearledgr.core.database import get_db
        db = get_db()

    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                (
                    "SELECT entry_type, credits, action_type, ap_item_id "
                    "FROM agent_credit_ledger "
                    "WHERE id = %s AND organization_id = %s LIMIT 1"
                ),
                (original_entry_id, organization_id),
            )
            row = cur.fetchone()
    except Exception as exc:
        logger.debug("[credit_pool] refund lookup failed: %s", exc)
        return {"ok": False, "reason": "lookup_failed"}

    if not row:
        return {"ok": False, "reason": "original_entry_not_found"}

    # Row indexing tolerant of sqlite3.Row + tuple fallbacks.
    entry_type = row["entry_type"] if hasattr(row, "__getitem__") else row[0]
    credits = int(row["credits"] if hasattr(row, "__getitem__") else row[1])
    action_type = (row["action_type"] if hasattr(row, "__getitem__") else row[2]) or ""
    ap_item_id = (row["ap_item_id"] if hasattr(row, "__getitem__") else row[3]) or None

    if entry_type != ENTRY_CONSUME:
        return {
            "ok": False,
            "reason": "original_not_consume_entry",
            "entry_type": entry_type,
        }

    refund_entry_id = _insert_entry(
        db,
        organization_id=organization_id,
        entry_type=ENTRY_REFUND,
        credits=credits,
        action_type=action_type,
        ap_item_id=ap_item_id,
        related_entry_id=original_entry_id,
        metadata={"reason": reason},
        created_by="system:refund",
    )
    return {
        "ok": True,
        "refund_entry_id": refund_entry_id,
        "refunded_credits": credits,
        "original_entry_id": original_entry_id,
    }


def purchase_credits(
    organization_id: str,
    *,
    credits: int,
    actor_id: str,
    stripe_charge_id: Optional[str] = None,
    price_usd_cents: Optional[int] = None,
    db: Any = None,
) -> Dict[str, Any]:
    """Record an admin top-up — §13 "Purchase additional agent action credits."

    The actual payment integration (Stripe charge) is handled
    upstream in the billing surface; this function records the
    ledger entry once the payment has cleared. Metadata carries the
    Stripe charge id and the price so the audit trail can reconcile
    ledger credits against revenue.
    """
    if _is_unlimited(organization_id):
        return {
            "ok": False,
            "reason": "tier_is_unlimited",
            "message": "Enterprise tier does not use the purchased-pool model.",
        }

    if credits <= 0:
        return {"ok": False, "reason": "non_positive_credits"}

    if db is None:
        from clearledgr.core.database import get_db
        db = get_db()

    # Lazy monthly-grant fire also runs on purchases. If the admin
    # tops up mid-period before any agent action has consumed, the
    # balance displayed back must include both the period's allowance
    # AND the fresh purchase — otherwise the admin sees "1000 credits
    # added, balance 1000" on a 500-credit tier and wonders where
    # their allowance went.
    ensure_monthly_grant(organization_id, db=db)

    entry_id = _insert_entry(
        db,
        organization_id=organization_id,
        entry_type=ENTRY_PURCHASE,
        credits=int(credits),
        metadata={
            "stripe_charge_id": stripe_charge_id,
            "price_usd_cents": price_usd_cents,
        },
        created_by=actor_id,
    )
    return {
        "ok": True,
        "entry_id": entry_id,
        "credits_added": int(credits),
        "balance_after": get_balance(organization_id, db=db),
    }


def list_recent_entries(
    organization_id: str,
    *,
    limit: int = 50,
    db: Any = None,
) -> List[Dict[str, Any]]:
    """Return the org's most recent ledger entries for admin UI /
    audit view. Newest first, capped at the passed limit.
    """
    if db is None:
        from clearledgr.core.database import get_db
        db = get_db()

    safe_limit = max(1, min(int(limit or 50), 500))
    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                (
                    "SELECT * FROM agent_credit_ledger "
                    "WHERE organization_id = %s "
                    "ORDER BY created_at DESC LIMIT %s"
                ),
                (organization_id, safe_limit),
            )
            rows = cur.fetchall()
    except Exception as exc:
        logger.debug("[credit_pool] list_recent_entries failed: %s", exc)
        return []

    out: List[Dict[str, Any]] = []
    for row in rows or []:
        entry = dict(row)
        raw_meta = entry.get("metadata")
        if isinstance(raw_meta, str):
            try:
                entry["metadata"] = json.loads(raw_meta)
            except (ValueError, TypeError):
                entry["metadata"] = {}
        out.append(entry)
    return out
