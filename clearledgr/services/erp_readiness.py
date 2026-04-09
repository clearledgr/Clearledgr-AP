"""ERP connector readiness evaluator for GA/pilot launch control gates."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from clearledgr.core.database import ClearledgrDB, get_db
from clearledgr.core.launch_controls import get_ga_readiness, get_rollback_controls
from clearledgr.services.erp_connector_strategy import get_erp_connector_strategy


GA_CONNECTOR_SCOPE: tuple[str, ...] = ("netsuite", "quickbooks", "xero", "sap")


def _normalized_scope(raw_scope: Optional[Iterable[str]]) -> List[str]:
    if raw_scope is None:
        return list(GA_CONNECTOR_SCOPE)
    seen = set()
    scope: List[str] = []
    for token in raw_scope:
        value = str(token or "").strip().lower()
        if value and value not in seen:
            scope.append(value)
            seen.add(value)
    return scope or list(GA_CONNECTOR_SCOPE)


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def evaluate_erp_connector_readiness(
    organization_id: str,
    *,
    db: Optional[ClearledgrDB] = None,
    connector_scope: Optional[Iterable[str]] = None,
    require_full_ga_scope: bool = False,
) -> Dict[str, Any]:
    """Evaluate readiness of ERP connectors for an organization.

    This combines:
    - declared connector capabilities (API-first strategy)
    - launch readiness checklist evidence
    - org-level active ERP connections
    - rollback controls that can disable posting/connectors
    """

    resolved_db = db or get_db()
    org_id = str(organization_id or "default").strip() or "default"
    scope = _normalized_scope(connector_scope)
    strategy = get_erp_connector_strategy()
    ga_readiness = get_ga_readiness(org_id, db=resolved_db)
    rollback = get_rollback_controls(org_id, db=resolved_db)
    checklist_map = _safe_dict(ga_readiness.get("connector_checklists"))

    configured_connectors: set[str] = set()
    if hasattr(resolved_db, "get_erp_connections"):
        try:
            for row in _safe_list(resolved_db.get_erp_connections(org_id)):
                token = str((row or {}).get("erp_type") or "").strip().lower()
                if token:
                    configured_connectors.add(token)
        except Exception:
            configured_connectors = set()

    posting_disabled = bool(rollback.get("erp_posting_disabled"))
    disabled_connectors = {
        str(token or "").strip().lower()
        for token in _safe_list(rollback.get("erp_connectors_disabled"))
        if str(token or "").strip()
    }

    rows: List[Dict[str, Any]] = []
    blocked_reasons: List[str] = []
    for connector in scope:
        capability = strategy.resolve(connector)
        checklist = _safe_dict(checklist_map.get(connector))
        has_checklist = bool(checklist)
        checklist_completed = bool(checklist.get("completed") or checklist.get("signed_off"))
        checklist_signed_off = bool(checklist.get("signed_off"))
        checklist_blocked = bool(checklist.get("blocked"))
        checklist_status = (
            "completed"
            if checklist_completed
            else ("in_progress" if has_checklist else "not_started")
        )
        connection_present = connector in configured_connectors
        rollback_blocked = posting_disabled or connector in disabled_connectors

        if rollback_blocked:
            readiness_status = "disabled_by_rollback"
            ready = False
            blocked_reasons.append(f"{connector}:disabled_by_rollback")
        elif checklist_blocked:
            readiness_status = "blocked"
            ready = False
            blocked_reasons.append(f"{connector}:checklist_blocked")
        elif not capability.supports_api_post_bill:
            readiness_status = "unsupported"
            ready = False
            blocked_reasons.append(f"{connector}:api_unsupported")
        elif checklist_completed:
            readiness_status = "ready"
            ready = True
        else:
            readiness_status = "pending_readiness"
            ready = False
            blocked_reasons.append(f"{connector}:checklist_incomplete")

        rows.append(
            {
                "erp_type": connector,
                "rollout_stage": capability.rollout_stage,
                "api_supported": bool(capability.supports_api_post_bill),
                "connection_present": connection_present,
                "checklist_status": checklist_status,
                "checklist_completed": checklist_completed,
                "checklist_signed_off": checklist_signed_off,
                "rollback_blocked": rollback_blocked,
                "readiness_status": readiness_status,
                "ready": ready,
                "notes": capability.notes,
            }
        )

    enabled_rows = [row for row in rows if row["connection_present"] and not row["rollback_blocked"]]
    enabled_total = len(enabled_rows)
    enabled_ready = sum(1 for row in enabled_rows if row["ready"])
    enabled_readiness_rate = (
        round(enabled_ready / max(1, enabled_total), 4)
        if enabled_total > 0
        else None
    )

    ga_total = len(rows)
    ga_ready = sum(1 for row in rows if row["ready"])
    ga_readiness_rate = round(ga_ready / max(1, ga_total), 4) if ga_total > 0 else None

    configured_total = len(configured_connectors)
    if require_full_ga_scope:
        overall_status = "pass" if ga_total > 0 and ga_ready == ga_total else "blocked"
    elif configured_total == 0:
        overall_status = "not_verifiable"
    elif enabled_total == 0:
        overall_status = "blocked"
    else:
        overall_status = "pass" if enabled_ready == enabled_total else "blocked"

    return {
        "organization_id": org_id,
        "connector_scope": scope,
        "connectors": rows,
        "summary": {
            "status": overall_status,
            "require_full_ga_scope": bool(require_full_ga_scope),
            "configured_connectors": sorted(configured_connectors),
            "configured_connectors_total": configured_total,
            "enabled_connectors_total": enabled_total,
            "enabled_connectors_ready": enabled_ready,
            "enabled_readiness_rate": enabled_readiness_rate,
            "ga_scope_total": ga_total,
            "ga_scope_ready": ga_ready,
            "ga_scope_readiness_rate": ga_readiness_rate,
            "blocked_reasons": blocked_reasons,
        },
    }
