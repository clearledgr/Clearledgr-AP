"""Helpers for mailbox-scoped Gmail defaults.

These helpers keep mailbox-specific routing/default logic in one place so
approval routing, reminder routing, and queue surfaces all resolve the same
mailbox settings from the same AP item context.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from clearledgr.core.ap_entity_routing import (
    match_entity_candidate,
    normalize_entity_candidate,
    normalize_entity_routing_settings,
)
from clearledgr.core.utils import safe_float_or_none
from clearledgr.services.policy_compliance import parse_approval_automation_config


_VALID_PIPELINE_SLICES = {
    "all_open",
    "waiting_on_approval",
    "ready_to_post",
    "needs_info",
    "failed_post",
    "blocked_exception",
    "due_soon",
    "overdue",
}

_PIPELINE_SLICE_ALIASES = {
    "all": "all_open",
    "approval_backlog": "waiting_on_approval",
    "approval_chase": "waiting_on_approval",
    "blocker_review": "blocked_exception",
    "exception_triage": "blocked_exception",
    "exceptions": "blocked_exception",
    "posting_watch": "ready_to_post",
    "urgent_due": "due_soon",
}


def _parse_json_dict(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _parse_json_list(raw: Any) -> List[Any]:
    if isinstance(raw, list):
        return list(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _normalize_confidence_threshold(value: Any) -> Optional[float]:
    parsed = safe_float_or_none(value)
    if parsed is None:
        return None
    if parsed > 1.0 and parsed <= 100.0:
        parsed = parsed / 100.0
    return max(0.0, min(parsed, 1.0))


def normalize_mailbox_slice_id(value: Any) -> Optional[str]:
    token = str(value or "").strip().lower()
    if not token:
        return None
    normalized = _PIPELINE_SLICE_ALIASES.get(token, token)
    return normalized if normalized in _VALID_PIPELINE_SLICES else None


def normalize_mailbox_slice_defaults(raw: Any) -> List[str]:
    values = raw if isinstance(raw, list) else _parse_json_list(raw)
    normalized: List[str] = []
    seen = set()
    for value in values:
        slice_id = normalize_mailbox_slice_id(value)
        if not slice_id or slice_id in seen:
            continue
        seen.add(slice_id)
        normalized.append(slice_id)
    return normalized


def normalize_mailbox_approval_thresholds(raw: Any) -> List[Dict[str, Any]]:
    values = raw if isinstance(raw, list) else _parse_json_list(raw)
    normalized: List[Dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        min_amount = safe_float_or_none(
            value.get("min_amount")
            if value.get("min_amount") not in (None, "")
            else value.get("threshold")
        )
        if min_amount is None:
            continue
        max_amount = safe_float_or_none(value.get("max_amount"))
        if max_amount is not None and max_amount < min_amount:
            max_amount = min_amount
        approver_channel = str(value.get("approver_channel") or value.get("channel") or "").strip() or None
        approver_role = str(value.get("approver_role") or "").strip() or None
        approvers_raw = value.get("approvers") or value.get("required_approvers") or []
        if not isinstance(approvers_raw, list):
            approvers_raw = [approvers_raw] if approvers_raw not in (None, "") else []
        approvers: List[str] = []
        seen: set[str] = set()
        for raw_approver in approvers_raw:
            token = str(raw_approver or "").strip()
            lowered = token.casefold()
            if not token or lowered in seen:
                continue
            seen.add(lowered)
            approvers.append(token)
        entry: Dict[str, Any] = {
            "min_amount": float(min_amount),
            "max_amount": float(max_amount) if max_amount is not None else None,
            "approver_channel": approver_channel,
            "approver_role": approver_role,
            "approvers": approvers,
            "auto_approve": _coerce_bool(value.get("auto_approve")),
        }
        confidence_threshold = _normalize_confidence_threshold(value.get("confidence_threshold"))
        if confidence_threshold is not None:
            entry["confidence_threshold"] = confidence_threshold
        normalized.append(entry)
    normalized.sort(
        key=lambda row: (
            float(row.get("min_amount") or 0.0),
            float(row.get("max_amount")) if row.get("max_amount") is not None else float("inf"),
            str(row.get("approver_channel") or ""),
        )
    )
    return normalized


def normalize_mailbox_approval_automation(raw: Any) -> Dict[str, Any]:
    config = _parse_json_dict(raw)
    if not config:
        return {}
    normalized, _ = parse_approval_automation_config({"approval_automation": config})
    overrides: Dict[str, Any] = {}
    if "reminder_hours" in config:
        overrides["reminder_hours"] = int(normalized.get("reminder_hours") or 4)
    if "escalation_hours" in config:
        overrides["escalation_hours"] = int(
            normalized.get("escalation_hours") or overrides.get("reminder_hours") or 24
        )
    if "escalation_channel" in config:
        overrides["escalation_channel"] = str(normalized.get("escalation_channel") or "").strip()
    return overrides


def normalize_mailbox_policy_overrides(raw: Any) -> Dict[str, Any]:
    config = _parse_json_dict(raw)
    if not config:
        return {}

    normalized: Dict[str, Any] = {}

    explicit_rules: List[Dict[str, Any]] = []
    for key in ("policies", "rules"):
        values = config.get(key) if isinstance(config.get(key), list) else _parse_json_list(config.get(key))
        for value in values:
            if isinstance(value, dict):
                explicit_rules.append(dict(value))
    if explicit_rules:
        normalized["policies"] = explicit_rules

    for key in ("vendor_rules", "budget_rules"):
        values = config.get(key) if isinstance(config.get(key), list) else _parse_json_list(config.get(key))
        rows = [dict(value) for value in values if isinstance(value, dict)]
        if rows:
            normalized[key] = rows

    approval_thresholds = normalize_mailbox_approval_thresholds(config.get("approval_thresholds"))
    if approval_thresholds:
        normalized["approval_thresholds"] = approval_thresholds

    approval_automation = normalize_mailbox_approval_automation(config.get("approval_automation"))
    if approval_automation:
        normalized["approval_automation"] = approval_automation

    return normalized


def normalize_mailbox_settings(raw: Any) -> Dict[str, Any]:
    settings = _parse_json_dict(raw)
    approval_channel_default = str(settings.get("approval_channel_default") or "").strip() or None
    label_scope = str(settings.get("label_scope") or "").strip() or None
    policy_overrides = normalize_mailbox_policy_overrides(settings.get("policy_overrides"))

    entity_defaults: List[str] = []
    seen_entities = set()
    for value in (
        settings.get("entity_defaults")
        if isinstance(settings.get("entity_defaults"), list)
        else _parse_json_list(settings.get("entity_defaults"))
    ):
        token = str(value or "").strip()
        if not token or token in seen_entities:
            continue
        seen_entities.add(token)
        entity_defaults.append(token)

    return {
        "settings": settings,
        "approval_channel_default": approval_channel_default,
        "label_scope": label_scope,
        "entity_defaults": entity_defaults,
        "slice_defaults": normalize_mailbox_slice_defaults(settings.get("slice_defaults")),
        "approval_thresholds": normalize_mailbox_approval_thresholds(settings.get("approval_thresholds")),
        "approval_automation": normalize_mailbox_approval_automation(settings.get("approval_automation")),
        "policy_overrides": policy_overrides,
    }


def resolve_mailbox_approval_target(
    amount: Any,
    organization_settings: Any,
    mailbox_settings: Any,
    *,
    fallback_channel: Optional[str] = None,
) -> Dict[str, Any]:
    org_settings = dict(organization_settings or {}) if isinstance(organization_settings, dict) else {}
    normalized_mailbox = (
        mailbox_settings
        if isinstance(mailbox_settings, dict) and "approval_thresholds" in mailbox_settings
        else normalize_mailbox_settings(mailbox_settings)
    )
    thresholds = list(normalized_mailbox.get("approval_thresholds") or [])
    if not thresholds:
        policy_overrides = normalized_mailbox.get("policy_overrides") or {}
        if isinstance(policy_overrides, dict):
            thresholds = list(policy_overrides.get("approval_thresholds") or [])
    source = "mailbox" if thresholds else "organization"
    if not thresholds:
        thresholds = normalize_mailbox_approval_thresholds(org_settings.get("approval_thresholds"))

    default_channel = (
        str(fallback_channel or "").strip()
        or str(
            ((org_settings.get("slack_channels") or {}).get("invoices") if isinstance(org_settings.get("slack_channels"), dict) else "")
            or ""
        ).strip()
        or None
    )
    routing: Dict[str, Any] = {
        "channel": default_channel,
        "approvers": [],
        "source": "default" if not thresholds else source,
    }

    try:
        amount_value = float(amount or 0.0)
    except (TypeError, ValueError):
        amount_value = 0.0

    for threshold in thresholds:
        min_amount = safe_float_or_none(threshold.get("min_amount"))
        max_amount = safe_float_or_none(threshold.get("max_amount"))
        if min_amount is None:
            continue
        if amount_value < min_amount:
            continue
        if max_amount is not None and amount_value >= max_amount:
            continue
        routing.update(
            {
                "channel": str(threshold.get("approver_channel") or default_channel or "").strip() or default_channel,
                "approvers": [
                    str(value).strip()
                    for value in (threshold.get("approvers") or [])
                    if str(value).strip()
                ],
                "source": source,
                "approver_role": str(threshold.get("approver_role") or "").strip() or None,
                "auto_approve": bool(threshold.get("auto_approve")),
                "confidence_threshold": threshold.get("confidence_threshold"),
            }
        )
        return routing

    return routing


def merge_mailbox_approval_automation(
    base_policy: Any,
    mailbox_settings: Any,
) -> Dict[str, Any]:
    normalized_base, _ = parse_approval_automation_config({"approval_automation": dict(base_policy or {})})
    normalized_mailbox = (
        mailbox_settings
        if isinstance(mailbox_settings, dict) and "approval_automation" in mailbox_settings
        else normalize_mailbox_settings(mailbox_settings)
    )
    overrides = dict(normalized_mailbox.get("approval_automation") or {})
    if not overrides:
        policy_overrides = normalized_mailbox.get("policy_overrides") or {}
        if isinstance(policy_overrides, dict):
            overrides = dict(policy_overrides.get("approval_automation") or {})
    if not overrides:
        return normalized_base
    merged = dict(normalized_base)
    merged.update(overrides)
    merged_policy, _ = parse_approval_automation_config({"approval_automation": merged})
    return merged_policy


def extract_mailbox_policy_config(mailbox_settings: Any) -> Dict[str, Any]:
    normalized_mailbox = (
        mailbox_settings
        if isinstance(mailbox_settings, dict) and "settings" in mailbox_settings
        else normalize_mailbox_settings(mailbox_settings)
    )

    config: Dict[str, Any] = {}
    if normalized_mailbox.get("approval_thresholds"):
        config["approval_thresholds"] = list(normalized_mailbox.get("approval_thresholds") or [])
    if normalized_mailbox.get("approval_automation"):
        config["approval_automation"] = dict(normalized_mailbox.get("approval_automation") or {})

    policy_overrides = normalized_mailbox.get("policy_overrides") or {}
    if isinstance(policy_overrides, dict):
        if policy_overrides.get("policies"):
            config["policies"] = [dict(item) for item in policy_overrides.get("policies") or [] if isinstance(item, dict)]
        for key in ("vendor_rules", "budget_rules", "approval_thresholds"):
            if policy_overrides.get(key):
                config[key] = [
                    dict(item) for item in policy_overrides.get(key) or [] if isinstance(item, dict)
                ]
        if policy_overrides.get("approval_automation"):
            config["approval_automation"] = dict(policy_overrides.get("approval_automation") or {})
    return config


def apply_mailbox_entity_defaults(
    organization_settings: Any,
    mailbox_settings: Any,
) -> Dict[str, Any]:
    base_settings = dict(organization_settings or {}) if isinstance(organization_settings, dict) else {}
    normalized_mailbox = (
        mailbox_settings
        if isinstance(mailbox_settings, dict) and "entity_defaults" in mailbox_settings
        else normalize_mailbox_settings(mailbox_settings)
    )
    mailbox_entity_defaults = [
        str(value or "").strip()
        for value in (normalized_mailbox.get("entity_defaults") or [])
        if str(value or "").strip()
    ]
    if not mailbox_entity_defaults:
        return base_settings

    config = normalize_entity_routing_settings(base_settings)
    configured_entities = config.get("entities") if isinstance(config.get("entities"), list) else []
    configured_rules = config.get("rules") if isinstance(config.get("rules"), list) else []

    scoped_entities: List[Dict[str, Any]] = []
    for value in mailbox_entity_defaults:
        candidate = match_entity_candidate(
            configured_entities,
            selection=value,
            entity_id=value,
            entity_code=value,
            entity_name=value,
        )
        if not candidate:
            candidate = normalize_entity_candidate(value)
        if not candidate:
            continue
        if match_entity_candidate(
            scoped_entities,
            entity_id=candidate.get("entity_id"),
            entity_code=candidate.get("entity_code"),
            entity_name=candidate.get("entity_name"),
        ):
            continue
        scoped_entities.append(dict(candidate))

    if not scoped_entities:
        return base_settings

    scoped_rules: List[Dict[str, Any]] = []
    for rule in configured_rules:
        matched = match_entity_candidate(
            scoped_entities,
            entity_id=rule.get("entity_id"),
            entity_code=rule.get("entity_code"),
            entity_name=rule.get("entity_name"),
        )
        if matched:
            scoped_rules.append(dict(rule))

    merged_settings = dict(base_settings)
    routing = dict(base_settings.get("entity_routing") or {}) if isinstance(base_settings.get("entity_routing"), dict) else {}
    routing["entities"] = scoped_entities
    routing["rules"] = scoped_rules
    merged_settings["entity_routing"] = routing
    merged_settings["entity_routing_entities"] = scoped_entities
    merged_settings["entity_routing_rules"] = scoped_rules
    merged_settings["legal_entities"] = scoped_entities
    return merged_settings


def resolve_ap_item_mailbox(
    db: Any,
    organization_id: str,
    *,
    ap_item: Optional[Dict[str, Any]] = None,
    ap_item_id: Optional[str] = None,
    sources: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    item = dict(ap_item or {})
    normalized_ap_item_id = str(ap_item_id or item.get("id") or "").strip()
    normalized_org_id = str(organization_id or item.get("organization_id") or "").strip()

    if not item and normalized_ap_item_id and callable(getattr(db, "get_ap_item", None)):
        try:
            fetched = db.get_ap_item(normalized_ap_item_id)
            item = dict(fetched or {})
        except Exception:
            item = {}

    mailbox_id = str(item.get("mailbox_id") or "").strip()
    mailbox_email = str(item.get("mailbox_email") or "").strip()

    source_rows = list(sources or [])
    if (not mailbox_id or not mailbox_email) and not source_rows and normalized_ap_item_id and callable(getattr(db, "list_ap_item_sources", None)):
        try:
            source_rows = list(db.list_ap_item_sources(normalized_ap_item_id) or [])
        except Exception:
            source_rows = []

    for source in source_rows:
        if not mailbox_id:
            mailbox_id = str(source.get("mailbox_id") or "").strip()
        if not mailbox_email:
            mailbox_email = str(source.get("mailbox_email") or "").strip()
        if mailbox_id and mailbox_email:
            break

    mailbox = None
    if mailbox_id and callable(getattr(db, "get_gmail_mailbox", None)):
        try:
            mailbox = db.get_gmail_mailbox(mailbox_id)
        except Exception:
            mailbox = None
    if mailbox is None and normalized_org_id and mailbox_email and callable(getattr(db, "get_gmail_mailbox_by_email", None)):
        try:
            mailbox = db.get_gmail_mailbox_by_email(normalized_org_id, mailbox_email)
        except Exception:
            mailbox = None

    if mailbox:
        mailbox_id = str(mailbox.get("id") or mailbox_id or "").strip()
        mailbox_email = str(mailbox.get("email") or mailbox_email or "").strip()

    return {
        "mailbox": mailbox if isinstance(mailbox, dict) else None,
        "mailbox_id": mailbox_id or None,
        "mailbox_email": mailbox_email or None,
    }


def resolve_ap_item_mailbox_settings(
    db: Any,
    organization_id: str,
    *,
    ap_item: Optional[Dict[str, Any]] = None,
    ap_item_id: Optional[str] = None,
    sources: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    mailbox_context = resolve_ap_item_mailbox(
        db,
        organization_id,
        ap_item=ap_item,
        ap_item_id=ap_item_id,
        sources=sources,
    )
    mailbox = mailbox_context.get("mailbox") or {}
    normalized = normalize_mailbox_settings(mailbox.get("settings") or mailbox.get("settings_json"))
    normalized.update(mailbox_context)
    return normalized


def resolve_preferred_mailbox_slice(
    default_slice: Any,
    mailbox_slice_defaults: Any,
    *,
    compatible_slices: Optional[List[Any]] = None,
) -> str:
    normalized_default = normalize_mailbox_slice_id(default_slice) or "all_open"
    defaults = normalize_mailbox_slice_defaults(mailbox_slice_defaults)
    if not defaults:
        return normalized_default

    normalized_compatible = [
        slice_id
        for slice_id in (
            normalize_mailbox_slice_id(value)
            for value in (compatible_slices or [])
        )
        if slice_id
    ]
    if normalized_compatible:
        for slice_id in defaults:
            if slice_id in normalized_compatible:
                return slice_id
        if normalized_default in normalized_compatible:
            return normalized_default
        return normalized_compatible[0]

    if normalized_default in defaults:
        return normalized_default
    return defaults[0]
