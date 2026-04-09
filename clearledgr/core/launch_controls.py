"""GA readiness evidence and rollback controls stored in organization settings."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from clearledgr.core.database import ClearledgrDB, get_db


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dict(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}
    return {}


def _parse_iso(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        text = str(raw)
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _load_org_settings(db: ClearledgrDB, organization_id: str) -> Dict[str, Any]:
    org = db.ensure_organization(organization_id, organization_name=organization_id)
    settings = org.get("settings_json") or org.get("settings") or {}
    return _parse_dict(settings)


def _save_org_settings(db: ClearledgrDB, organization_id: str, settings: Dict[str, Any]) -> None:
    db.update_organization(organization_id, settings=settings)


def _controls_active(controls: Dict[str, Any]) -> bool:
    expires_at = _parse_iso(controls.get("expires_at"))
    if expires_at and expires_at < datetime.now(timezone.utc):
        return False
    return True


def _normalize_channel_flags(raw: Any) -> Dict[str, bool]:
    flags = {"all": False, "slack": False, "teams": False}
    if isinstance(raw, bool):
        flags["all"] = bool(raw)
        return flags
    if isinstance(raw, dict):
        for key in list(flags.keys()):
            if key in raw:
                flags[key] = bool(raw.get(key))
    return flags


def _normalize_str_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    values: list[str] = []
    for item in raw:
        text = str(item or "").strip().lower()
        if text and text not in values:
            values.append(text)
    return values


def default_rollback_controls() -> Dict[str, Any]:
    return {
        "erp_posting_disabled": False,
        "channel_actions_disabled": {"all": False, "slack": False, "teams": False},
        "erp_connectors_disabled": [],
        "reason": None,
        "expires_at": None,
        "updated_at": None,
        "updated_by": None,
    }


def get_rollback_controls(organization_id: str, db: Optional[ClearledgrDB] = None) -> Dict[str, Any]:
    resolved_db = db or get_db()
    settings = _load_org_settings(resolved_db, organization_id)
    raw = _parse_dict(settings.get("rollback_controls"))
    defaults = default_rollback_controls()
    merged = dict(defaults)
    merged["erp_posting_disabled"] = bool(raw.get("erp_posting_disabled", defaults["erp_posting_disabled"]))
    merged["channel_actions_disabled"] = _normalize_channel_flags(raw.get("channel_actions_disabled"))
    merged["erp_connectors_disabled"] = _normalize_str_list(raw.get("erp_connectors_disabled"))
    merged["reason"] = str(raw.get("reason")).strip() if raw.get("reason") else None
    merged["expires_at"] = str(raw.get("expires_at")).strip() if raw.get("expires_at") else None
    merged["updated_at"] = str(raw.get("updated_at")).strip() if raw.get("updated_at") else None
    merged["updated_by"] = str(raw.get("updated_by")).strip() if raw.get("updated_by") else None
    merged["active"] = _controls_active(merged)
    return merged


def set_rollback_controls(
    organization_id: str,
    controls_patch: Dict[str, Any],
    *,
    updated_by: Optional[str] = None,
    db: Optional[ClearledgrDB] = None,
) -> Dict[str, Any]:
    resolved_db = db or get_db()
    settings = _load_org_settings(resolved_db, organization_id)
    current = get_rollback_controls(organization_id, db=resolved_db)
    merged = dict(current)

    patch = _parse_dict(controls_patch)
    if "erp_posting_disabled" in patch:
        merged["erp_posting_disabled"] = bool(patch.get("erp_posting_disabled"))
    if "channel_actions_disabled" in patch:
        flags = _normalize_channel_flags(patch.get("channel_actions_disabled"))
        merged["channel_actions_disabled"] = {
            **_normalize_channel_flags(merged.get("channel_actions_disabled")),
            **flags,
        }
    if "erp_connectors_disabled" in patch:
        merged["erp_connectors_disabled"] = _normalize_str_list(patch.get("erp_connectors_disabled"))
    if "reason" in patch:
        merged["reason"] = str(patch.get("reason")).strip() if patch.get("reason") else None
    if "expires_at" in patch:
        merged["expires_at"] = str(patch.get("expires_at")).strip() if patch.get("expires_at") else None

    merged["updated_at"] = _now_iso()
    merged["updated_by"] = updated_by or patch.get("updated_by") or merged.get("updated_by")
    merged["active"] = _controls_active(merged)

    persisted = {k: v for k, v in merged.items() if k != "active"}
    settings["rollback_controls"] = persisted
    _save_org_settings(resolved_db, organization_id, settings)
    return get_rollback_controls(organization_id, db=resolved_db)


def get_channel_action_block_reason(
    organization_id: str,
    channel: str,
    *,
    db: Optional[ClearledgrDB] = None,
) -> Optional[str]:
    controls = get_rollback_controls(organization_id, db=db)
    if not controls.get("active", True):
        return None
    channel_key = str(channel or "").strip().lower()
    flags = _normalize_channel_flags(controls.get("channel_actions_disabled"))
    if flags.get("all"):
        return str(controls.get("reason") or "channel_actions_disabled_all")
    if channel_key and flags.get(channel_key):
        return str(controls.get("reason") or f"{channel_key}_actions_disabled")
    return None


def get_erp_posting_block_reason(
    organization_id: str,
    *,
    erp_type: Optional[str] = None,
    db: Optional[ClearledgrDB] = None,
) -> Optional[str]:
    controls = get_rollback_controls(organization_id, db=db)
    if not controls.get("active", True):
        return None
    if controls.get("erp_posting_disabled"):
        return str(controls.get("reason") or "erp_posting_disabled")
    normalized_erp = str(erp_type or "").strip().lower()
    if normalized_erp and normalized_erp in set(_normalize_str_list(controls.get("erp_connectors_disabled"))):
        return str(controls.get("reason") or f"erp_connector_disabled:{normalized_erp}")
    return None


def default_ga_readiness() -> Dict[str, Any]:
    return {
        "source_of_record": {
            "kind": "in_app_settings",
            "location": "organizations.settings_json.ga_readiness",
            "external_url": None,
        },
        "connector_checklists": {},
        "runbooks": [],
        "parity_evidence": [],
        "signoffs": [],
        "notes": [],
        "updated_at": None,
        "updated_by": None,
    }


def _normalize_list_of_dicts(raw: Any) -> list[dict]:
    if not isinstance(raw, list):
        return []
    result: list[dict] = []
    for item in raw:
        if isinstance(item, dict):
            result.append(dict(item))
    return result


def get_ga_readiness(organization_id: str, db: Optional[ClearledgrDB] = None) -> Dict[str, Any]:
    resolved_db = db or get_db()
    settings = _load_org_settings(resolved_db, organization_id)
    raw = _parse_dict(settings.get("ga_readiness"))
    defaults = default_ga_readiness()
    evidence = dict(defaults)
    evidence["source_of_record"] = {
        **_parse_dict(defaults.get("source_of_record")),
        **_parse_dict(raw.get("source_of_record")),
    }
    connector_checklists = _parse_dict(raw.get("connector_checklists"))
    normalized_checklists: Dict[str, Dict[str, Any]] = {}
    for key, value in connector_checklists.items():
        if isinstance(value, dict):
            normalized_checklists[str(key).strip().lower()] = dict(value)
    evidence["connector_checklists"] = normalized_checklists
    evidence["runbooks"] = _normalize_list_of_dicts(raw.get("runbooks"))
    evidence["parity_evidence"] = _normalize_list_of_dicts(raw.get("parity_evidence"))
    evidence["signoffs"] = _normalize_list_of_dicts(raw.get("signoffs"))
    notes = raw.get("notes")
    if isinstance(notes, list):
        evidence["notes"] = [str(n) for n in notes if str(n).strip()]
    evidence["updated_at"] = str(raw.get("updated_at")).strip() if raw.get("updated_at") else None
    evidence["updated_by"] = str(raw.get("updated_by")).strip() if raw.get("updated_by") else None
    return evidence


def set_ga_readiness(
    organization_id: str,
    evidence_patch: Dict[str, Any],
    *,
    updated_by: Optional[str] = None,
    db: Optional[ClearledgrDB] = None,
) -> Dict[str, Any]:
    resolved_db = db or get_db()
    settings = _load_org_settings(resolved_db, organization_id)
    current = get_ga_readiness(organization_id, db=resolved_db)
    patch = _parse_dict(evidence_patch)
    merged = dict(current)

    if "source_of_record" in patch:
        merged["source_of_record"] = {
            **_parse_dict(current.get("source_of_record")),
            **_parse_dict(patch.get("source_of_record")),
        }
    if "connector_checklists" in patch:
        connector_patch = _parse_dict(patch.get("connector_checklists"))
        normalized = dict(_parse_dict(current.get("connector_checklists")))
        for key, value in connector_patch.items():
            if isinstance(value, dict):
                normalized[str(key).strip().lower()] = dict(value)
        merged["connector_checklists"] = normalized
    if "runbooks" in patch:
        merged["runbooks"] = _normalize_list_of_dicts(patch.get("runbooks"))
    if "parity_evidence" in patch:
        merged["parity_evidence"] = _normalize_list_of_dicts(patch.get("parity_evidence"))
    if "signoffs" in patch:
        merged["signoffs"] = _normalize_list_of_dicts(patch.get("signoffs"))
    if "notes" in patch:
        notes = patch.get("notes")
        merged["notes"] = [str(n) for n in notes] if isinstance(notes, list) else []

    merged["updated_at"] = _now_iso()
    merged["updated_by"] = updated_by or patch.get("updated_by") or current.get("updated_by")
    settings["ga_readiness"] = merged
    _save_org_settings(resolved_db, organization_id, settings)
    return get_ga_readiness(organization_id, db=resolved_db)


def summarize_ga_readiness(
    evidence: Dict[str, Any],
    rollback_controls: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    checklists = _parse_dict(evidence.get("connector_checklists"))
    checklist_rows = [v for v in checklists.values() if isinstance(v, dict)]
    checklist_total = len(checklist_rows)
    checklist_complete = sum(1 for row in checklist_rows if bool(row.get("completed") or row.get("signed_off")))
    signoffs = _normalize_list_of_dicts(evidence.get("signoffs"))
    parity = _normalize_list_of_dicts(evidence.get("parity_evidence"))
    runbooks = _normalize_list_of_dicts(evidence.get("runbooks"))
    controls = rollback_controls or default_rollback_controls()
    return {
        "has_source_of_record": bool(_parse_dict(evidence.get("source_of_record")).get("kind")),
        "has_runbooks": bool(runbooks),
        "has_parity_evidence": bool(parity),
        "has_signoffs": bool(signoffs),
        "connector_checklists_total": checklist_total,
        "connector_checklists_completed": checklist_complete,
        "rollback_controls_defined": True,
        "rollback_controls_active": bool(controls.get("active")),
        "ready_for_ga": bool(
            runbooks
            and parity
            and signoffs
            and (checklist_total == 0 or checklist_complete == checklist_total)
        ),
    }

