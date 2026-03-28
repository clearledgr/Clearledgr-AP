"""Helpers for surfacing and resolving AP entity-routing state."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def _token(value: Any) -> str:
    return str(value or "").strip()


def _token_lower(value: Any) -> str:
    return _token(value).lower()


def _candidate_label(candidate: Dict[str, Any]) -> str:
    code = _token(candidate.get("entity_code"))
    name = _token(candidate.get("entity_name"))
    if code and name:
        return f"{code} - {name}"
    return code or name


def normalize_entity_candidate(value: Any) -> Dict[str, Any]:
    if isinstance(value, str):
        name = _token(value)
        return {
            "entity_id": "",
            "entity_code": "",
            "entity_name": name,
            "label": name,
        } if name else {}
    if not isinstance(value, dict):
        return {}

    entity_id = _token(
        value.get("entity_id")
        or value.get("id")
        or value.get("subsidiary_id")
    )
    entity_code = _token(
        value.get("entity_code")
        or value.get("code")
        or value.get("subsidiary_code")
        or value.get("legal_entity_code")
    )
    entity_name = _token(
        value.get("entity_name")
        or value.get("name")
        or value.get("display_name")
        or value.get("legal_entity")
        or value.get("subsidiary_name")
    )
    label = _token(value.get("label")) or _candidate_label(
        {
            "entity_code": entity_code,
            "entity_name": entity_name,
        }
    )
    candidate = {
        "entity_id": entity_id,
        "entity_code": entity_code,
        "entity_name": entity_name,
        "label": label,
    }
    if not any(candidate.values()):
        return {}
    return candidate


def normalize_entity_candidates(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, dict):
        raw = raw.get("candidates") or raw.get("options") or []
    if not isinstance(raw, list):
        return []

    normalized: List[Dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for entry in raw:
        candidate = normalize_entity_candidate(entry)
        if not candidate:
            continue
        dedupe_key = (
            _token_lower(candidate.get("entity_id")),
            _token_lower(candidate.get("entity_code")),
            _token_lower(candidate.get("entity_name")),
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized.append(candidate)
    return normalized


def _token_list(value: Any) -> List[str]:
    if isinstance(value, list):
        parts = value
    elif isinstance(value, str):
        parts = value.replace("\n", ",").split(",")
    else:
        return []

    normalized: List[str] = []
    seen: set[str] = set()
    for entry in parts:
        token = _token(entry)
        lowered = token.lower()
        if not token or lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(token)
    return normalized


def normalize_entity_routing_rule(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    try:
        priority = int(value.get("priority") or 100)
    except (TypeError, ValueError):
        priority = 100
    rule = {
        "entity_id": _token(value.get("entity_id") or value.get("id")),
        "entity_code": _token(value.get("entity_code") or value.get("code")),
        "entity_name": _token(value.get("entity_name") or value.get("name")),
        "sender_domains": _token_list(value.get("sender_domains") or value.get("sender_domain")),
        "vendor_contains": _token_list(value.get("vendor_contains") or value.get("vendor_matchers")),
        "subject_contains": _token_list(value.get("subject_contains") or value.get("subject_matchers")),
        "currencies": [token.upper() for token in _token_list(value.get("currencies") or value.get("currency"))],
        "priority": max(1, min(priority, 9999)),
    }
    has_entity = bool(rule["entity_id"] or rule["entity_code"] or rule["entity_name"])
    has_matcher = bool(
        rule["sender_domains"]
        or rule["vendor_contains"]
        or rule["subject_contains"]
        or rule["currencies"]
    )
    if not has_entity or not has_matcher:
        return {}
    return rule


def normalize_entity_routing_rules(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    normalized: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for entry in raw:
        rule = normalize_entity_routing_rule(entry)
        if not rule:
            continue
        dedupe_key = json.dumps(rule, sort_keys=True)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized.append(rule)
    normalized.sort(key=lambda entry: (int(entry.get("priority") or 100), _token_lower(entry.get("entity_code"))))
    return normalized


def normalize_entity_routing_settings(raw: Any) -> Dict[str, Any]:
    settings = raw if isinstance(raw, dict) else {}
    routing = settings.get("entity_routing") if isinstance(settings.get("entity_routing"), dict) else {}
    entities = normalize_entity_candidates(
        routing.get("entities")
        or settings.get("legal_entities")
        or settings.get("entity_routing_entities")
    )
    rules = normalize_entity_routing_rules(
        routing.get("rules")
        or settings.get("entity_routing_rules")
    )
    return {
        "entities": entities,
        "rules": rules,
        "enabled": bool(entities or rules),
    }


def normalize_entity_selection(raw: Any) -> Dict[str, Any]:
    return normalize_entity_candidate(raw)


def match_entity_candidate(
    candidates: List[Dict[str, Any]],
    *,
    selection: Any = None,
    entity_id: Any = None,
    entity_code: Any = None,
    entity_name: Any = None,
) -> Optional[Dict[str, Any]]:
    target_tokens = {
        token
        for token in {
            _token_lower(selection),
            _token_lower(entity_id),
            _token_lower(entity_code),
            _token_lower(entity_name),
        }
        if token
    }
    if not target_tokens:
        return None

    for candidate in candidates:
        candidate_tokens = {
            token
            for token in {
                _token_lower(candidate.get("entity_id")),
                _token_lower(candidate.get("entity_code")),
                _token_lower(candidate.get("entity_name")),
                _token_lower(candidate.get("label")),
            }
            if token
        }
        if candidate_tokens & target_tokens:
            return candidate
    return None


def _sender_domain(value: Any) -> str:
    sender = _token_lower(value)
    if "@" not in sender:
        return ""
    return sender.rsplit("@", 1)[-1]


def _matches_domain(domain: str, patterns: List[str]) -> bool:
    normalized_domain = _token_lower(domain)
    if not normalized_domain:
        return False
    for pattern in patterns:
        lowered = _token_lower(pattern)
        if not lowered:
            continue
        if normalized_domain == lowered or normalized_domain.endswith(f".{lowered}"):
            return True
    return False


def _contains_any(value: Any, needles: List[str]) -> bool:
    haystack = _token_lower(value)
    if not haystack:
        return False
    return any(_token_lower(needle) in haystack for needle in needles if _token_lower(needle))


def _matches_currency(value: Any, allowed: List[str]) -> bool:
    token = _token(value).upper()
    if not token:
        return False
    return token in {entry.upper() for entry in allowed if _token(entry)}


def _rule_matches(rule: Dict[str, Any], payload: Dict[str, Any]) -> bool:
    sender_domain = _sender_domain(payload.get("sender"))
    vendor_name = _token(payload.get("vendor_name") or payload.get("vendor"))
    subject = _token(payload.get("subject"))
    currency = _token(payload.get("currency"))

    if rule.get("sender_domains") and not _matches_domain(sender_domain, rule.get("sender_domains") or []):
        return False
    if rule.get("vendor_contains") and not _contains_any(vendor_name, rule.get("vendor_contains") or []):
        return False
    if rule.get("subject_contains") and not _contains_any(subject, rule.get("subject_contains") or []):
        return False
    if rule.get("currencies") and not _matches_currency(currency, rule.get("currencies") or []):
        return False
    return True


def resolve_entity_routing(
    metadata: Dict[str, Any],
    item: Optional[Dict[str, Any]] = None,
    organization_settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = item if isinstance(item, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    routing = metadata.get("entity_routing")
    routing = routing if isinstance(routing, dict) else {}

    candidates = normalize_entity_candidates(
        routing.get("candidates")
        or metadata.get("entity_candidates")
        or payload.get("entity_candidates")
    )
    config = normalize_entity_routing_settings(organization_settings)
    configured_entities = config.get("entities") if isinstance(config.get("entities"), list) else []
    configured_rules = config.get("rules") if isinstance(config.get("rules"), list) else []
    matched_configured_candidates: List[Dict[str, Any]] = []
    if configured_rules:
        for rule in configured_rules:
            if not _rule_matches(rule, payload):
                continue
            candidate = match_entity_candidate(
                configured_entities,
                entity_id=rule.get("entity_id"),
                entity_code=rule.get("entity_code"),
                entity_name=rule.get("entity_name"),
            )
            if not candidate:
                candidate = normalize_entity_candidate(
                    {
                        "entity_id": rule.get("entity_id"),
                        "entity_code": rule.get("entity_code"),
                        "entity_name": rule.get("entity_name"),
                    }
                )
            if candidate and not match_entity_candidate(
                matched_configured_candidates,
                entity_id=candidate.get("entity_id"),
                entity_code=candidate.get("entity_code"),
                entity_name=candidate.get("entity_name"),
            ):
                matched_configured_candidates.append(candidate)

    config_required = False
    config_reason = ""
    if matched_configured_candidates:
        candidates = matched_configured_candidates
        if len(matched_configured_candidates) > 1:
            config_required = True
            config_reason = "Multiple entity routing rules matched this invoice."
    elif configured_entities:
        if len(configured_entities) == 1:
            candidates = configured_entities
        else:
            candidates = configured_entities
            config_required = True
            config_reason = "No entity routing rule matched this invoice."

    selected = normalize_entity_selection(
        routing.get("selected")
        or metadata.get("entity_selection")
        or metadata.get("selected_entity")
        or {
            "entity_id": payload.get("entity_id"),
            "entity_code": payload.get("entity_code"),
            "entity_name": payload.get("entity_name"),
        }
    )

    status_hint = _token_lower(
        routing.get("status")
        or metadata.get("entity_routing_status")
    )
    required_hint = bool(
        routing.get("requires_review")
        or metadata.get("entity_route_review_required")
        or metadata.get("entity_routing_required")
    )
    reason = _token(
        routing.get("reason")
        or metadata.get("entity_route_reason")
        or metadata.get("entity_routing_reason")
    )

    can_auto_select_configured = bool(matched_configured_candidates or len(configured_entities) == 1)
    if not selected and len(candidates) == 1 and (
        status_hint not in {"needs_review", "ambiguous"} or can_auto_select_configured
    ):
        selected = dict(candidates[0])

    if selected:
        status = "resolved"
    elif status_hint in {"needs_review", "ambiguous", "pending", "unresolved"}:
        status = "needs_review"
    elif config_required or required_hint or len(candidates) > 1:
        status = "needs_review"
    else:
        status = "not_needed"

    fallback_reason = ""
    if config_reason:
        fallback_reason = config_reason
    elif status == "needs_review" and len(candidates) > 1:
        fallback_reason = "Multiple legal entities matched this invoice."

    return {
        "status": status,
        "reason": reason or fallback_reason,
        "selected": selected or {},
        "candidates": candidates,
    }
