"""Per-entity audit log scoping — Module 9 §300.

Resolves the entity scope for an authenticated user reading the
audit log. Returns ``None`` when the user has org-wide access (the
common case — admins and unrestricted users see every event in
their tenant); returns a list of entity_ids when the user is
restricted to a subset.

The list comes from two sources:

  1. ``user_entity_roles`` — per-(user, entity) role overrides from
     Module 6 Pass B. If the user has rows here AND no org-level
     admin role, those entities are their scope.
  2. ``users.entity_restrictions_json`` — invite-time restriction
     applied at signup. If the column is set on the user's row,
     those entities are added to the scope.

Org-level roles ``owner``, ``cfo``, and ``financial_controller`` are
treated as "see everything" — these are the people the auditing
contract assumes can read the full trail. Other roles fall back to
the entity-roles table; if that's empty too, the user has no
restrictions and sees the full org log.

The result is fed into audit-search SQL as a ``AND (entity_id IS
NULL OR entity_id IN (...))`` clause — enforced at query time, not
application time, per spec §307.
"""
from __future__ import annotations

import json
import logging
from typing import Any, List, Optional

logger = logging.getLogger(__name__)


_ORG_WIDE_ROLES = frozenset({"owner", "cfo", "financial_controller"})


def resolve_audit_entity_scope(
    db: Any, user: Any,
) -> Optional[List[str]]:
    """Return the list of entity_ids the user is restricted to, or
    None if they have full org access.

    Returning None is the common case — the SQL caller skips the
    extra WHERE clause and the query plan stays clean.
    """
    if user is None:
        return []  # Anonymous can see nothing — defensive.

    role = (getattr(user, "role", "") or "").strip().lower()
    if role in _ORG_WIDE_ROLES:
        return None

    user_id = getattr(user, "user_id", "") or ""
    if not user_id:
        return None  # Can't restrict without a user_id; fall back to org-wide.

    scope_set: set = set()

    # Per-(user, entity) roles
    try:
        if hasattr(db, "list_user_entity_roles"):
            rows = db.list_user_entity_roles(user_id) or []
            for row in rows:
                eid = row.get("entity_id") if isinstance(row, dict) else None
                if eid:
                    scope_set.add(str(eid))
    except Exception as exc:
        logger.debug(
            "[audit_scope] list_user_entity_roles failed for %s: %s",
            user_id, exc,
        )

    # Invite-time entity_restrictions on the user row
    try:
        user_row = db.get_user(user_id) if hasattr(db, "get_user") else None
        if user_row:
            raw = (
                user_row.get("entity_restrictions_json")
                or user_row.get("entity_restrictions")
            )
            if isinstance(raw, str) and raw:
                try:
                    parsed = json.loads(raw)
                except (ValueError, TypeError):
                    parsed = None
                if isinstance(parsed, list):
                    for eid in parsed:
                        if eid:
                            scope_set.add(str(eid))
            elif isinstance(raw, list):
                for eid in raw:
                    if eid:
                        scope_set.add(str(eid))
    except Exception as exc:
        logger.debug(
            "[audit_scope] entity_restrictions lookup failed for %s: %s",
            user_id, exc,
        )

    if not scope_set:
        # No per-entity restriction recorded anywhere — they see the
        # full org log. Returning None tells the caller to skip the
        # extra filter clause.
        return None

    return sorted(scope_set)


def build_entity_scope_clause(
    scope: Optional[List[str]], param_offset: int = 0,
) -> tuple:
    """Build the SQL fragment + bind args for the scope filter.

    Returns ``("", ())`` when scope is None (org-wide access).
    Returns ``("AND (entity_id IS NULL OR entity_id IN (%s, %s, ...))", (...))``
    when scope is restricted.

    Empty scope (``[]``) means "no entities" → returns a clause that
    matches only org-level events (entity_id IS NULL). That's the
    defensive default for a logged-out caller (returning [] from
    the resolver).
    """
    if scope is None:
        return ("", ())
    if not scope:
        return ("AND entity_id IS NULL", ())
    placeholders = ", ".join(["%s"] * len(scope))
    clause = f"AND (entity_id IS NULL OR entity_id IN ({placeholders}))"
    return (clause, tuple(scope))
