"""Per-(user, entity) role + permission resolver (Module 6 Pass B).

The dashboard's authorization model has two layers:
  1. **Org-level role** stored on the user row — authoritative when no
     entity is in scope.
  2. **Per-entity role** stored on ``user_entity_roles`` — overrides
     the org-level role for a specific legal entity.

This module is the single point that resolves "what can this user do
in this entity?" — backend gates and the frontend's permission probe
both call into ``resolve_role`` so the answer is consistent.

Reads happen on every approval gate; the (user_id, entity_id)
primary-key lookup is index-served. Resolver returns a
``ResolvedRole`` with the role token, the permission set, and the
optional approval ceiling — never raises so callers can safely
chain ``can_approve`` on the result.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import FrozenSet, Optional

from clearledgr.core.permissions import (
    PERMISSION_APPROVE_INVOICES,
    standard_role_permissions,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedRole:
    """Output of the resolver — what this user can do in this scope.

    Attributes:
        role: The effective role token (standard or ``cr_*`` custom id).
        permissions: The frozen permission set granted by ``role``.
        approval_ceiling: Optional max amount the user can approve.
            ``None`` means "no per-amount cap" — the role's permissions
            decide.
        scope: Either ``"entity"`` (resolved from ``user_entity_roles``)
            or ``"org"`` (fell back to the user's org-level role).
    """

    role: str
    permissions: FrozenSet[str]
    approval_ceiling: Optional[Decimal]
    scope: str

    def has(self, permission: str) -> bool:
        """Convenience: ``rr.has('approve_invoices')``."""
        return permission in self.permissions

    def can_approve(self, amount: Optional[Decimal]) -> bool:
        """True iff the role can approve invoices AND the amount is
        within the ceiling.

        ``amount=None`` is treated as "no specific amount" — only the
        permission gate is checked. Amount > 0 with no ceiling set
        passes the ceiling check trivially.
        """
        if PERMISSION_APPROVE_INVOICES not in self.permissions:
            return False
        if self.approval_ceiling is None or amount is None:
            return True
        try:
            return Decimal(str(amount)) <= self.approval_ceiling
        except (InvalidOperation, ValueError):
            # Bad amount is treated as a fail-closed signal so a
            # malformed approve attempt doesn't accidentally pass.
            return False


def resolve_role(
    db,
    *,
    user_id: str,
    org_role: str,
    entity_id: Optional[str] = None,
) -> ResolvedRole:
    """Resolve the effective role for one (user, entity) pair.

    Order of resolution:
      1. If ``entity_id`` is provided AND ``user_entity_roles`` has a
         row for this pair → use that (per-entity scope).
      2. Otherwise → use ``org_role`` (org-level scope).

    Custom role ids (``cr_<hex>``) resolve via
    ``custom_roles.permissions``; unknown / deleted custom ids
    collapse to the empty permission set (the assignment stays in
    place but grants nothing — safer than silently falling back to
    the user's org role for an admin who explicitly downgraded them).

    The function is read-only and never raises — DB hiccups log and
    fall through to the org-role path so an authorization decision
    can always be made.
    """
    if entity_id and hasattr(db, "get_user_entity_role"):
        try:
            row = db.get_user_entity_role(user_id, entity_id)
        except Exception as exc:
            logger.warning(
                "[resolve_role] DB error reading user_entity_roles "
                "for user=%s entity=%s: %s",
                user_id, entity_id, exc,
            )
            row = None
        if row:
            entity_role = str(row.get("role") or "").strip().lower()
            ceiling = row.get("approval_ceiling")
            if isinstance(ceiling, (int, float)):
                ceiling = Decimal(str(ceiling))
            elif ceiling is not None and not isinstance(ceiling, Decimal):
                try:
                    ceiling = Decimal(str(ceiling))
                except (InvalidOperation, ValueError):
                    ceiling = None
            perms = _resolve_permissions_for_role(db, entity_role)
            return ResolvedRole(
                role=entity_role,
                permissions=perms,
                approval_ceiling=ceiling,
                scope="entity",
            )

    # Fall back to the org-level role.
    fallback_role = (org_role or "").strip().lower()
    return ResolvedRole(
        role=fallback_role,
        permissions=_resolve_permissions_for_role(db, fallback_role),
        approval_ceiling=None,
        scope="org",
    )


def can_approve(
    db,
    *,
    user_id: str,
    org_role: str,
    entity_id: Optional[str],
    amount: Optional[Decimal],
) -> bool:
    """Convenience: resolve + check approve_invoices + check ceiling.

    Equivalent to::

        resolve_role(db, ...).can_approve(amount)
    """
    return resolve_role(
        db, user_id=user_id, org_role=org_role, entity_id=entity_id,
    ).can_approve(amount)


# ─── helpers ────────────────────────────────────────────────────────


def _resolve_permissions_for_role(db, role_token: str) -> FrozenSet[str]:
    """Map a role token to its permission set.

    Custom role tokens (``cr_*``) read from ``custom_roles``. Standard
    tokens (``owner``, ``cfo`` ...) read from the canonical
    ``ROLE_PERMISSIONS`` map. Unknown tokens collapse to ``frozenset()``.
    """
    if not role_token:
        return frozenset()
    if role_token.startswith("cr_"):
        if hasattr(db, "resolve_custom_role_permissions"):
            try:
                return db.resolve_custom_role_permissions(role_token)
            except Exception as exc:
                logger.warning(
                    "[resolve_role] custom-role lookup failed for %s: %s",
                    role_token, exc,
                )
        return frozenset()
    return standard_role_permissions(role_token)
