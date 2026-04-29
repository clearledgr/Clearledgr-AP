"""Canonical permission catalog (Module 6 Pass A).

The Clearledgr workspace exposes a bounded set of granular permissions
that compose into both the six standard roles
(``Clearledgr_Workspace_Scope_GA.md`` §Module 6) and customer-defined
custom roles. Standard roles are hard-coded here as the canonical
mapping; custom roles persist as ``custom_roles`` rows and override
the standard mapping when assigned.

The permissions are deliberately *bounded*. Each one corresponds to a
distinct workspace surface: rules, vendors, approvals, reports,
integrations, audit log, exports, users. New permission strings ship
as code changes — every API surface that gates on a permission needs
to know about it, so the catalog can't be open-ended.
"""
from __future__ import annotations

from typing import Dict, FrozenSet, Iterable, Optional

from clearledgr.core.auth import (
    ROLE_AP_CLERK,
    ROLE_AP_MANAGER,
    ROLE_CFO,
    ROLE_FINANCIAL_CONTROLLER,
    ROLE_OWNER,
    ROLE_READ_ONLY,
)


# ─── Permission constants ───────────────────────────────────────────
# Strings rather than an Enum so JSON serialisation + DB persistence
# stays simple and the constants are interchangeable at API boundaries.

PERMISSION_CONFIGURE_RULES = "configure_rules"
PERMISSION_MANAGE_VENDORS = "manage_vendors"
PERMISSION_APPROVE_INVOICES = "approve_invoices"
PERMISSION_SEE_REPORTS = "see_reports"
PERMISSION_MANAGE_INTEGRATIONS = "manage_integrations"
PERMISSION_VIEW_AUDIT_LOG = "view_audit_log"
PERMISSION_EXPORT_DATA = "export_data"
PERMISSION_MANAGE_USERS = "manage_users"


PERMISSION_CATALOG: Dict[str, str] = {
    PERMISSION_CONFIGURE_RULES: (
        "Configure approval rules, validation policies, and exception thresholds."
    ),
    PERMISSION_MANAGE_VENDORS: (
        "Add, edit, block vendors, manage onboarding sessions, set trusted domains."
    ),
    PERMISSION_APPROVE_INVOICES: (
        "Approve / reject invoices in the queue. Ceiling-bounded for AP Clerks."
    ),
    PERMISSION_SEE_REPORTS: (
        "View dashboards, KPI tiles, vendor performance, exception aging."
    ),
    PERMISSION_MANAGE_INTEGRATIONS: (
        "Connect / disconnect Gmail, Slack, Teams, ERP. Rotate credentials."
    ),
    PERMISSION_VIEW_AUDIT_LOG: (
        "Read the append-only audit log + export attempts."
    ),
    PERMISSION_EXPORT_DATA: (
        "Export invoices, audit log, vendor history to CSV / sheets."
    ),
    PERMISSION_MANAGE_USERS: (
        "Invite, deactivate, change role of workspace users; manage custom roles."
    ),
}

ALL_PERMISSIONS: FrozenSet[str] = frozenset(PERMISSION_CATALOG.keys())


# ─── Standard role → permission set ──────────────────────────────────
#
# Per scope §Module 6 §202-211. The mapping is the canonical answer to
# "what can this role do" and survives every code change unless the
# scope spec itself moves. New built-in roles are scope-spec changes.

ROLE_PERMISSIONS: Dict[str, FrozenSet[str]] = {
    ROLE_OWNER: ALL_PERMISSIONS,
    ROLE_CFO: ALL_PERMISSIONS,
    ROLE_FINANCIAL_CONTROLLER: frozenset({
        PERMISSION_CONFIGURE_RULES,
        PERMISSION_MANAGE_VENDORS,
        PERMISSION_MANAGE_INTEGRATIONS,
        PERMISSION_VIEW_AUDIT_LOG,
        PERMISSION_EXPORT_DATA,
        PERMISSION_MANAGE_USERS,
        PERMISSION_SEE_REPORTS,
    }),
    ROLE_AP_MANAGER: frozenset({
        PERMISSION_APPROVE_INVOICES,
        PERMISSION_MANAGE_VENDORS,
        PERMISSION_VIEW_AUDIT_LOG,
        PERMISSION_SEE_REPORTS,
    }),
    ROLE_AP_CLERK: frozenset({
        # AP Clerk can approve up to a per-amount ceiling — the
        # ceiling is enforced at the approve gate (Pass B), the
        # base permission is intact.
        PERMISSION_APPROVE_INVOICES,
        PERMISSION_SEE_REPORTS,
    }),
    ROLE_READ_ONLY: frozenset({
        PERMISSION_VIEW_AUDIT_LOG,
        PERMISSION_SEE_REPORTS,
    }),
}


# ─── Custom-role limits ──────────────────────────────────────────────
# Bounded to 10 per customer per scope spec to prevent permission
# sprawl. The limit is enforced at create time in custom_roles_store.

CUSTOM_ROLES_PER_ORG_LIMIT = 10


def normalize_permission(value: object) -> Optional[str]:
    """Trim + lowercase + validate against the catalog.

    Returns ``None`` for unknown strings — callers that filter user
    input should drop unknowns rather than 500.
    """
    if value is None:
        return None
    token = str(value).strip().lower()
    return token if token in ALL_PERMISSIONS else None


def normalize_permission_set(values: Iterable[object]) -> FrozenSet[str]:
    """Collect a set of valid permission strings.

    Any unknown / empty entries are silently dropped. Order is not
    preserved — permissions are a set semantically.
    """
    out: set = set()
    for v in values or []:
        norm = normalize_permission(v)
        if norm:
            out.add(norm)
    return frozenset(out)


def standard_role_permissions(role: Optional[str]) -> FrozenSet[str]:
    """Return the permission set for a standard role.

    Unknown role tokens collapse to the empty set. Callers should
    typically use ``has_permission`` rather than reading this dict
    directly.
    """
    if not role:
        return frozenset()
    return ROLE_PERMISSIONS.get(str(role).strip().lower(), frozenset())


def has_permission(
    role: Optional[str],
    permission: str,
    *,
    custom_role_permissions: Optional[Iterable[str]] = None,
) -> bool:
    """Return True iff the role grants the named permission.

    When the user is assigned a custom role, ``custom_role_permissions``
    is the permission set persisted on the ``custom_roles`` row; the
    standard role taxonomy is bypassed and the custom set wins. This
    keeps the resolver decision linear: standard role OR custom role,
    not "the union of both."

    The standard mapping remains live for users without a custom role
    so legacy + new tenants behave identically.
    """
    perm = normalize_permission(permission)
    if not perm:
        return False
    if custom_role_permissions is not None:
        return perm in normalize_permission_set(custom_role_permissions)
    return perm in standard_role_permissions(role)


def serialize_catalog() -> list:
    """Catalog for HTTP responses — list of {key, label, description}.

    Stable ordering matches the constants order in this module so the
    UI renders the same column order across page loads.
    """
    return [
        {"key": k, "description": v}
        for k, v in PERMISSION_CATALOG.items()
    ]


def serialize_role_permissions() -> Dict[str, list]:
    """Standard-role → sorted-permission-list, for the workspace UI."""
    return {
        role: sorted(perms)
        for role, perms in ROLE_PERMISSIONS.items()
    }
