"""Bounded catalog of mappable Clearledgr → ERP fields (Module 5).

The Clearledgr workspace exposes a "Custom field mapping" UI for ERP
admins whose chart-of-accounts or document layouts deviate from the
defaults. The scope spec (``Clearledgr_Workspace_Scope_GA.md`` §Module 5)
explicitly calls for a *bounded* surface — not a free-form
{any-clearledgr-field → any-erp-field} matrix — to avoid customer
configurations that the agent runtime cannot reason about.

This module is the single source of truth for what is mappable. The
HTTP layer (``erp_connections.py``) reads ``CATALOG`` to render the
options the operator sees, and to validate inbound updates. The ERP
posters (``erp_router.py``) read the persisted mapping at post time
to resolve any non-default field IDs they need.

Adding a new mappable field is intentionally a code change: the
catalog is the contract between Clearledgr and the ERP, and a new
entry usually requires the corresponding poster to know how to use it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


# Regex patterns enforced at the API boundary. Each ERP has a
# convention for custom-field identifiers; rejecting violations early
# keeps a typo from silently surfacing as an opaque ERP-side error
# during posting.
_NETSUITE_FIELD_RE = re.compile(r"^[a-z][a-z0-9_]{1,49}$")
# SAP S/4 custom fields are conventionally upper-snake; some standard
# fields (CostCenter, ProfitCenter) are CamelCase. Allow either.
_SAP_FIELD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,49}$")
# QB / Xero use display names or short codes — keep permissive.
_GENERIC_FIELD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_\.\-]{1,79}$")


@dataclass(frozen=True)
class FieldMapping:
    """One mappable Clearledgr → ERP field.

    Attributes:
        key: Clearledgr-side identifier (stable; UI + persistence key).
        label: Human-readable label shown in the dashboard.
        description: One-line "what does this control" explanation.
        default: Suggested ERP field id (informational; not auto-applied
            unless the operator explicitly clicks "use default").
        pattern: Regex the user-supplied ERP field id must match.
        category: UX grouping ("identity" / "dimension" / "workflow").
    """

    key: str
    label: str
    description: str
    default: str
    pattern: re.Pattern
    category: str


# Per-ERP catalogs. Order is preserved in the UI.
CATALOG: Dict[str, Tuple[FieldMapping, ...]] = {
    "netsuite": (
        FieldMapping(
            key="state_field",
            label="State mirror field",
            description=(
                "NetSuite custom body field that mirrors the Clearledgr "
                "AP state (received → posted). Lets NetSuite reports "
                "filter on agent-managed work."
            ),
            default="custbody_clearledgr_state",
            pattern=_NETSUITE_FIELD_RE,
            category="workflow",
        ),
        FieldMapping(
            key="box_id_field",
            label="Box ID field",
            description=(
                "Custom body field for the Clearledgr Box id. Use this "
                "to deep-link from a NetSuite vendor bill back to the "
                "agent timeline."
            ),
            default="custbody_clearledgr_box_id",
            pattern=_NETSUITE_FIELD_RE,
            category="identity",
        ),
        FieldMapping(
            key="approver_field",
            label="Final approver field",
            description=(
                "Custom body field for the email of the human who "
                "approved the bill in Clearledgr's policy gate."
            ),
            default="custbody_clearledgr_approver",
            pattern=_NETSUITE_FIELD_RE,
            category="workflow",
        ),
        FieldMapping(
            key="correlation_id_field",
            label="Agent correlation field",
            description=(
                "Custom body field for the agent run correlation id. "
                "Required when reconciling NetSuite postings against "
                "the Clearledgr audit log during incident response."
            ),
            default="custbody_clearledgr_correlation",
            pattern=_NETSUITE_FIELD_RE,
            category="workflow",
        ),
        FieldMapping(
            key="department_field",
            label="Department dimension",
            description=(
                "Standard field name for the department dimension. "
                "Override if your subsidiary uses a non-default field."
            ),
            default="department",
            pattern=_NETSUITE_FIELD_RE,
            category="dimension",
        ),
        FieldMapping(
            key="class_field",
            label="Class dimension",
            description="Standard NetSuite class field for cost-center attribution.",
            default="class",
            pattern=_NETSUITE_FIELD_RE,
            category="dimension",
        ),
        FieldMapping(
            key="location_field",
            label="Location dimension",
            description="Standard NetSuite location/subsidiary field.",
            default="location",
            pattern=_NETSUITE_FIELD_RE,
            category="dimension",
        ),
    ),
    "sap": (
        FieldMapping(
            key="state_field",
            label="State mirror field",
            description=(
                "S/4HANA Z-field that mirrors the Clearledgr AP state. "
                "Lets SAP cockpit views filter on agent-managed work."
            ),
            default="ZZ_CLEARLEDGR_STATE",
            pattern=_SAP_FIELD_RE,
            category="workflow",
        ),
        FieldMapping(
            key="box_id_field",
            label="Box ID field",
            description="Z-field for the Clearledgr Box id.",
            default="ZZ_CLEARLEDGR_BOX_ID",
            pattern=_SAP_FIELD_RE,
            category="identity",
        ),
        FieldMapping(
            key="approver_field",
            label="Final approver field",
            description="Z-field for the human approver's email.",
            default="ZZ_CLEARLEDGR_APPROVER",
            pattern=_SAP_FIELD_RE,
            category="workflow",
        ),
        FieldMapping(
            key="cost_center_field",
            label="Cost center dimension",
            description="Standard cost-center field. Override if you use a Z-field instead.",
            default="CostCenter",
            pattern=_SAP_FIELD_RE,
            category="dimension",
        ),
        FieldMapping(
            key="profit_center_field",
            label="Profit center dimension",
            description="Standard profit-center field.",
            default="ProfitCenter",
            pattern=_SAP_FIELD_RE,
            category="dimension",
        ),
        FieldMapping(
            key="wbs_field",
            label="WBS element field",
            description=(
                "WBS element field for project-accounting line items. "
                "Leave blank if you don't use WBS billing."
            ),
            default="WBSElement",
            pattern=_SAP_FIELD_RE,
            category="dimension",
        ),
    ),
    "quickbooks": (
        FieldMapping(
            key="class_field",
            label="Class dimension",
            description=(
                "QuickBooks Online class name to attribute lines to "
                "(when class tracking is enabled at the company level)."
            ),
            default="Class",
            pattern=_GENERIC_FIELD_RE,
            category="dimension",
        ),
        FieldMapping(
            key="department_field",
            label="Department / Location dimension",
            description=(
                "QuickBooks Online department or location name. "
                "QBO names this 'Location' under Account & Settings."
            ),
            default="Location",
            pattern=_GENERIC_FIELD_RE,
            category="dimension",
        ),
        FieldMapping(
            key="custom_field_1",
            label="Custom field 1",
            description=(
                "Optional QBO custom field id (DefinitionId from QB) "
                "to populate on every posted bill."
            ),
            default="",
            pattern=_GENERIC_FIELD_RE,
            category="workflow",
        ),
    ),
    "xero": (
        FieldMapping(
            key="tracking_category_1_field",
            label="Tracking category 1",
            description="Name of the first Xero tracking category to attribute lines to.",
            default="",
            pattern=_GENERIC_FIELD_RE,
            category="dimension",
        ),
        FieldMapping(
            key="tracking_category_2_field",
            label="Tracking category 2",
            description="Name of the second Xero tracking category (optional).",
            default="",
            pattern=_GENERIC_FIELD_RE,
            category="dimension",
        ),
    ),
}


def list_supported_erps() -> List[str]:
    """Return the ERP types that have a field mapping catalog."""
    return list(CATALOG.keys())


def get_catalog(erp_type: str) -> Tuple[FieldMapping, ...]:
    """Return the catalog for an ERP, or an empty tuple if unknown.

    Unknown ERPs are not an error — the UI falls back to "no custom
    fields supported for this ERP type" — so callers can pass any
    string without guarding.
    """
    return CATALOG.get(str(erp_type or "").strip().lower(), ())


def serialize_catalog(erp_type: str) -> List[Dict[str, str]]:
    """Catalog as plain dicts for HTTP responses.

    The compiled regex pattern is exposed as its source string so
    the SPA can do a first-pass client-side validation before the
    user submits.
    """
    return [
        {
            "key": fm.key,
            "label": fm.label,
            "description": fm.description,
            "default": fm.default,
            "pattern": fm.pattern.pattern,
            "category": fm.category,
        }
        for fm in get_catalog(erp_type)
    ]


def validate_mapping(
    erp_type: str, mapping: Dict[str, str]
) -> Tuple[Dict[str, str], List[str]]:
    """Validate + normalise a user-supplied mapping.

    Returns ``(clean_mapping, errors)``. ``clean_mapping`` only
    contains keys that exist in the catalog and have non-empty values
    that match the field pattern. Empty values are dropped (they
    represent "use the default"). ``errors`` is a list of
    user-facing strings — empty list means the input was valid.
    """
    catalog = get_catalog(erp_type)
    if not catalog:
        # Unknown ERP type — the API treats this as a 400 since the
        # operator sent something we have no schema for.
        return {}, [f"unsupported_erp_type:{erp_type}"]

    by_key = {fm.key: fm for fm in catalog}
    cleaned: Dict[str, str] = {}
    errors: List[str] = []

    if not isinstance(mapping, dict):
        return {}, ["mapping_must_be_object"]

    for raw_key, raw_value in mapping.items():
        key = str(raw_key or "").strip()
        if key not in by_key:
            errors.append(f"unknown_field:{key}")
            continue
        value = str(raw_value or "").strip()
        if not value:
            # Empty = revert to default; persist nothing.
            continue
        if not by_key[key].pattern.match(value):
            errors.append(f"invalid_field_id:{key}={value}")
            continue
        cleaned[key] = value

    return cleaned, errors


def diff_mappings(
    before: Optional[Dict[str, str]], after: Dict[str, str]
) -> Dict[str, Dict[str, Optional[str]]]:
    """Compute a per-key {before, after} diff for audit payloads.

    Empty/missing values normalise to None so the diff is symmetric
    and stable across "key absent" vs "key present with empty
    string". Audit consumers compare keys, not order.
    """
    before = dict(before or {})
    keys = set(before.keys()) | set(after.keys())
    diff: Dict[str, Dict[str, Optional[str]]] = {}
    for k in sorted(keys):
        b = before.get(k) or None
        a = after.get(k) or None
        if b != a:
            diff[k] = {"before": b, "after": a}
    return diff
