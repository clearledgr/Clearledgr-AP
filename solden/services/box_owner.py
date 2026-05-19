"""Box owner resolution — explicit, enforceable ownership.

The manifesto promises: "Who acts next. Not implicit through forwarding
rules and PTO calendars. When an attestor goes on leave, the workflow
knows. When a delegate picks up half, the workflow tracks the split.
Ownership is explicit, enforceable, auditable."

This module backs that promise. Given a Box (currently always an
``ap_item``), it answers two questions:

  1. Who *should* own this Box right now?
  2. If a delegation is active for that owner, who picks up the work?

The resolver is **pure**: it takes the Box, the org config, and a
delegation oracle, and returns an :class:`OwnerAssignment` with the
provenance trail (original → delegate, with reason). The caller
decides whether to persist the assignment — see
:func:`apply_resolved_owner` for the canonical write path.

The org's settings_json drives the state→default-owner mapping under
the ``routing_owners`` key::

    settings_json = {
        "routing_owners": {
            "needs_approval":         "controller@example.com",
            "needs_second_approval":  "cfo@example.com",
            "needs_info":             "ap-clerk@example.com",
            "failed_post":            "ap-lead@example.com",
        },
        ...
    }

A missing entry means "no default owner for this state" — the Box
stays unassigned and a human routes it manually.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from solden.core.ap_states import APState

logger = logging.getLogger(__name__)


# AP states that REQUIRE an explicit human owner. Transitions into
# these states should trigger owner resolution; transitions out of
# them can clear or rotate the owner.
HUMAN_ACTION_STATES = frozenset({
    APState.NEEDS_INFO.value,
    APState.NEEDS_APPROVAL.value,
    APState.NEEDS_SECOND_APPROVAL.value,
    APState.FAILED_POST.value,
})


# State classes group transitions where a manual owner assignment
# should stick. Sticky-manual doctrine (Mo's call 2026-05-14):
# manual owners survive WITHIN a class but get re-resolved on a
# cross-class transition because the operator's deliberate choice
# was for the prior class's role, not the new one's.
#
#   needs_info → needs_info (same class, sticky)
#   needs_approval → needs_second_approval (same class, sticky)
#   needs_info → needs_approval (cross-class, re-resolve)
#   needs_approval → failed_post (cross-class, re-resolve)
#
# Intentional omissions:
#
#   ready_to_post is NOT in the table. ready_to_post is normally an
#   auto-progressable handoff state on the way to posted_to_erp,
#   not a human-action state — HUMAN_ACTION_STATES gates resolve_owner
#   so a manual owner can't be set on ready_to_post via the
#   coordinator hook. The approval-revert path
#   (ready_to_post → needs_approval) therefore evaluates the prior
#   class as "" — which fails the same-class check and triggers
#   re-resolution. That's the desired UX: revert ends the approval
#   cycle and the next cycle gets fresh routing per the org's
#   policy. If a future product call requires "revert preserves
#   the original approver," add ready_to_post → "approval" here.
STATE_CLASSES: Dict[str, str] = {
    APState.NEEDS_INFO.value: "info",
    APState.NEEDS_APPROVAL.value: "approval",
    APState.NEEDS_SECOND_APPROVAL.value: "approval",
    APState.FAILED_POST.value: "post",
}


def state_class(state: str) -> str:
    """Return the manual-owner stickiness class for a state.

    Empty string means "no class" — typically a non-human-action
    state (received, validated, approved, etc.) where no manual
    owner can persist anyway.
    """
    return STATE_CLASSES.get(str(state or ""), "")


@dataclass(frozen=True)
class OwnerAssignment:
    """Resolved ownership for a Box.

    Fields
    ------
    owner_id, owner_email:
        The user the work currently lands on. After delegation
        resolution, this is the *delegate*, not the original assignee.
    owner_source:
        One of ``auto``, ``delegate``, ``manual``, ``escalation`` —
        see ``ap_items.owner_source`` schema notes.
    original_owner_email:
        The owner the Box would have had absent any delegation. Equal
        to ``owner_email`` when no delegation is in effect. Used for
        audit-trail context ("originally routed to A, delegated to B").
    delegation_reason:
        Free-text reason supplied on the active delegation rule. ``""``
        when no delegation is in effect.
    delegation_chain:
        Ordered list of delegate emails walked from ``original_owner_email``
        to ``owner_email``. Empty when no delegation is in effect.
        For A→B→C this is ``["B", "C"]``; for A→B→A (cycle) the walk
        stops at B with chain ``["B"]``.
    """

    owner_id: Optional[str]
    owner_email: str
    owner_source: str
    original_owner_email: str
    delegation_reason: str = ""
    delegation_chain: tuple = ()

    def to_audit_payload(self) -> Dict[str, Any]:
        """Shape for the ``owner_changed`` audit event payload_json."""
        return {
            "owner_id": self.owner_id,
            "owner_email": self.owner_email,
            "owner_source": self.owner_source,
            "original_owner_email": self.original_owner_email,
            "delegation_reason": self.delegation_reason,
            "delegation_chain": list(self.delegation_chain),
        }


def _load_org_routing_owners(db: Any, organization_id: str) -> Dict[str, str]:
    """Extract the state→default-owner mapping from settings_json.

    Returns an empty dict when the org has no mapping configured —
    the caller's fallback path takes over.
    """
    try:
        org = db.get_organization(organization_id) if hasattr(db, "get_organization") else None
    except Exception as exc:
        logger.debug(
            "[box_owner] get_organization failed for %s: %s",
            organization_id, exc,
        )
        return {}
    if not org:
        return {}
    raw_settings = org.get("settings_json") or org.get("settings") or {}
    if isinstance(raw_settings, str):
        try:
            raw_settings = json.loads(raw_settings)
        except json.JSONDecodeError:
            return {}
    routing = raw_settings.get("routing_owners") if isinstance(raw_settings, dict) else None
    if not isinstance(routing, dict):
        return {}
    return {str(k): str(v) for k, v in routing.items() if v}


def _lookup_user_id_by_email(db: Any, organization_id: str, email: str) -> Optional[str]:
    """Best-effort user_id lookup. Returns None if the user can't be
    resolved — owner_email is still populated, owner_id stays NULL."""
    if not email:
        return None
    try:
        if hasattr(db, "get_user_by_email"):
            user = db.get_user_by_email(email)
            if user:
                return str(user.get("id") or user.get("user_id") or "") or None
    except Exception as exc:
        logger.debug(
            "[box_owner] get_user_by_email failed for %s: %s",
            email, exc,
        )
    return None


def resolve_owner(
    *,
    box: Dict[str, Any],
    organization_id: str,
    db: Any,
    source: str = "auto",
) -> Optional[OwnerAssignment]:
    """Resolve the owner for a Box at its current state.

    Returns ``None`` when:
      * the Box's current state is not in :data:`HUMAN_ACTION_STATES`
        (no human action required yet), or
      * the org has no configured default owner for that state.

    Otherwise returns an :class:`OwnerAssignment` that names the
    current delegate-aware owner. Callers persist it via
    :func:`apply_resolved_owner`.
    """
    state = str(box.get("state") or "")
    if state not in HUMAN_ACTION_STATES:
        return None

    routing = _load_org_routing_owners(db, organization_id)
    base_email = routing.get(state)
    if not base_email:
        return None

    # Walk active delegations with cycle detection. A→B→C delivers
    # to C; A→B→A is detected via the visited set and stops at B
    # (the last cycle-free hop). Without this, prior behaviour stopped
    # after one hop — a three-link chain silently misrouted to the
    # middle link.
    #
    # The walk is in-memory off a single ``list_rules`` SELECT. The
    # previous implementation called ``get_delegate_for(cursor)``
    # inside the loop, which re-runs the SELECT per hop — an N+1
    # query for an N-link chain. We pay one query, build a
    # ``{delegator → delegate}`` map filtered by the active date
    # window, then walk in memory.
    delegate_email: Optional[str] = None
    delegation_reason = ""
    delegation_chain: list[str] = []
    try:
        from solden.services.approval_delegation import get_delegation_service
        delegation = get_delegation_service(organization_id=organization_id)
        active_rules = delegation.list_rules(active_only=True)
        # Build the effective {delegator → (delegate, reason)} map
        # once, applying the same date-window filter
        # ``get_delegate_for`` would have applied. UNIQUE constraint
        # on (organization_id, delegator_email, delegate_email)
        # guarantees one effective delegate per delegator within an
        # org, so a single mapping is well-defined.
        now = datetime.now(timezone.utc)
        effective: Dict[str, tuple] = {}
        for rule in active_rules:
            starts = rule.get("starts_at")
            ends = rule.get("ends_at")
            if starts:
                try:
                    starts_dt = datetime.fromisoformat(str(starts).replace("Z", "+00:00"))
                    if now < starts_dt:
                        continue
                except (ValueError, TypeError):
                    pass
            if ends:
                try:
                    ends_dt = datetime.fromisoformat(str(ends).replace("Z", "+00:00"))
                    if now > ends_dt:
                        continue
                except (ValueError, TypeError):
                    pass
            delegator = rule.get("delegator_email")
            delegate = rule.get("delegate_email")
            if not delegator or not delegate:
                continue
            effective[delegator] = (delegate, str(rule.get("reason") or ""))

        visited = {base_email}
        cursor = base_email
        # Cap the walk at len(effective) + 1 hops as a belt-and-braces
        # guard against malformed data (a rule whose delegate_email
        # equals its delegator_email, for instance).
        for _ in range(len(effective) + 1):
            hop = effective.get(cursor)
            if hop is None:
                break
            next_hop, hop_reason = hop
            if next_hop in visited:
                # Cycle detected — stop at the current cursor (the
                # deepest cycle-free hop).
                break
            delegation_chain.append(next_hop)
            visited.add(next_hop)
            # Reason of the most-recent hop wins; an auditor reading
            # the chain sees why work landed at its final destination.
            delegation_reason = hop_reason
            cursor = next_hop
        if delegation_chain:
            delegate_email = delegation_chain[-1]
    except Exception as exc:
        logger.warning(
            "[box_owner] delegation lookup failed for %s/%s: %s",
            organization_id, base_email, exc,
        )

    final_email = delegate_email or base_email
    resolved_source = "delegate" if delegate_email else source
    owner_id = _lookup_user_id_by_email(db, organization_id, final_email)

    return OwnerAssignment(
        owner_id=owner_id,
        owner_email=final_email,
        owner_source=resolved_source,
        original_owner_email=base_email,
        delegation_reason=delegation_reason,
        delegation_chain=tuple(delegation_chain),
    )


def apply_resolved_owner(
    *,
    db: Any,
    ap_item_id: str,
    organization_id: str,
    assignment: OwnerAssignment,
    actor_id: str,
    correlation_id: Optional[str] = None,
) -> None:
    """Persist an :class:`OwnerAssignment` to ``ap_items`` and the audit trail.

    Atomic: the ap_items UPDATE and the owner_changed audit event
    share a single transaction via :meth:`ApStore.set_ap_item_owner_atomic`.
    Either both writes commit or neither does. The previous
    implementation ran them as two separate transactions, leaving a
    partial-write hazard where the AP row could land with the new
    owner but the audit trail could disagree.

    Safe to call repeatedly — the audit event uses a deterministic
    idempotency key. A second call with the same args at the same
    timestamp returns the prior event (UNIQUE violation handled
    inside the store).
    """
    decision_reason = (
        f"owner_source={assignment.owner_source}"
        + (
            f"; delegated from {assignment.original_owner_email}"
            if assignment.owner_email != assignment.original_owner_email
            else ""
        )
    )
    db.set_ap_item_owner_atomic(
        ap_item_id,
        owner_id=assignment.owner_id,
        owner_email=assignment.owner_email,
        owner_source=assignment.owner_source,
        organization_id=organization_id,
        actor_id=actor_id,
        actor_type="system" if assignment.owner_source != "manual" else "user",
        audit_payload=assignment.to_audit_payload(),
        decision_reason=decision_reason,
        correlation_id=correlation_id,
    )


def reassign_manually(
    *,
    db: Any,
    ap_item_id: str,
    organization_id: str,
    new_owner_email: str,
    reason: str,
    actor_id: str,
) -> OwnerAssignment:
    """Manual reassignment by an operator. Bypasses delegation walk.

    The reason an operator-triggered reassignment doesn't walk
    delegation_rules: the operator has chosen the assignee
    deliberately. If the assignee is OOO, the operator either knows
    and accepts it, or the delegation rule should fire on the *next*
    auto-resolution cycle. Surprise-rerouting an explicit human
    decision would violate the manifesto's enforceability promise.
    """
    owner_id = _lookup_user_id_by_email(db, organization_id, new_owner_email)
    assignment = OwnerAssignment(
        owner_id=owner_id,
        owner_email=new_owner_email,
        owner_source="manual",
        original_owner_email=new_owner_email,
        delegation_reason=reason,
    )
    apply_resolved_owner(
        db=db,
        ap_item_id=ap_item_id,
        organization_id=organization_id,
        assignment=assignment,
        actor_id=actor_id,
    )
    return assignment
