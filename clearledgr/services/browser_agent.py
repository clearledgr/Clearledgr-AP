"""Browser-native agent control plane primitives for AP v1."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from clearledgr.core.database import get_db


TOOL_REGISTRY: Dict[str, Dict[str, str]] = {
    "read_page": {"risk": "read_only", "category": "read"},
    "extract_table": {"risk": "read_only", "category": "read"},
    "find_element": {"risk": "read_only", "category": "read"},
    "query_selector_all": {"risk": "read_only", "category": "read"},
    "capture_evidence": {"risk": "read_only", "category": "audit"},
    "switch_tab": {"risk": "read_only", "category": "navigation"},
    "open_tab": {"risk": "mutating", "category": "navigation"},
    "click": {"risk": "high_risk", "category": "interaction"},
    "type": {"risk": "high_risk", "category": "interaction"},
    "select": {"risk": "high_risk", "category": "interaction"},
    "upload_file": {"risk": "high_risk", "category": "interaction"},
    "drag_drop": {"risk": "high_risk", "category": "interaction"},
}

SUPPORTED_TOOLS = set(TOOL_REGISTRY.keys())
READ_ONLY_TOOLS = {name for name, meta in TOOL_REGISTRY.items() if meta.get("risk") == "read_only"}

DEFAULT_POLICY = {
    "allowed_domains": [
        "mail.google.com",
        "gmail.google.com",
        "*.netsuite.com",
    ],
    "blocked_actions": [],
    "require_confirmation_for": [
        "click",
        "type",
        "select",
        "open_tab",
        "upload_file",
        "drag_drop",
    ],
    "auto_approve_read_only": True,
    "role_overrides": {},
    "workflow_overrides": {},
}

SUPPORTED_MACROS = {
    "ingest_invoice_match_po",
    "collect_w9",
    "post_invoice_to_erp",
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _host_matches(pattern: str, host: str) -> bool:
    pattern = (pattern or "").strip().lower()
    host = (host or "").strip().lower()
    if not pattern or not host:
        return False
    if pattern == "*":
        return True
    if pattern.startswith("*."):
        suffix = pattern[1:]
        return host.endswith(suffix)
    return host == pattern


def _extract_url(command: Dict[str, Any]) -> str:
    target = command.get("target") or {}
    if isinstance(target, dict) and target.get("url"):
        return str(target.get("url"))
    if command.get("url"):
        return str(command.get("url"))
    params = command.get("params") or {}
    if isinstance(params, dict) and params.get("url"):
        return str(params.get("url"))
    return ""


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_tool_values(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    normalized = []
    seen = set()
    for value in values:
        tool = str(value or "").strip().lower()
        if not tool or tool in seen:
            continue
        normalized.append(tool)
        seen.add(tool)
    return normalized


def _normalize_domain_values(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    normalized = []
    seen = set()
    for value in values:
        host = str(value or "").strip().lower()
        if not host or host in seen:
            continue
        normalized.append(host)
        seen.add(host)
    return normalized


def _normalize_scope_overrides(raw: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    normalized: Dict[str, Dict[str, Any]] = {}
    for scope_key, scope_value in raw.items():
        scope_name = str(scope_key or "").strip()
        if not scope_name or not isinstance(scope_value, dict):
            continue
        normalized[scope_name] = {
            "allowed_domains": _normalize_domain_values(scope_value.get("allowed_domains")),
            "blocked_actions": _normalize_tool_values(scope_value.get("blocked_actions")),
            "require_confirmation_for": _normalize_tool_values(scope_value.get("require_confirmation_for")),
            "auto_approve_read_only": bool(
                scope_value.get("auto_approve_read_only", DEFAULT_POLICY["auto_approve_read_only"])
            ),
        }
    return normalized


def _normalize_policy_config(config: Any) -> Dict[str, Any]:
    if not isinstance(config, dict):
        config = {}
    return {
        "allowed_domains": _normalize_domain_values(config.get("allowed_domains") or DEFAULT_POLICY["allowed_domains"]),
        "blocked_actions": _normalize_tool_values(config.get("blocked_actions")),
        "require_confirmation_for": _normalize_tool_values(
            config.get("require_confirmation_for") or DEFAULT_POLICY["require_confirmation_for"]
        ),
        "auto_approve_read_only": bool(config.get("auto_approve_read_only", DEFAULT_POLICY["auto_approve_read_only"])),
        "role_overrides": _normalize_scope_overrides(config.get("role_overrides")),
        "workflow_overrides": _normalize_scope_overrides(config.get("workflow_overrides")),
    }


def _merge_policy_scope(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    merged["allowed_domains"] = sorted(
        set(_normalize_domain_values(base.get("allowed_domains")) + _normalize_domain_values(override.get("allowed_domains")))
    )
    merged["blocked_actions"] = sorted(
        set(_normalize_tool_values(base.get("blocked_actions")) + _normalize_tool_values(override.get("blocked_actions")))
    )
    merged["require_confirmation_for"] = sorted(
        set(
            _normalize_tool_values(base.get("require_confirmation_for"))
            + _normalize_tool_values(override.get("require_confirmation_for"))
        )
    )
    if "auto_approve_read_only" in override:
        merged["auto_approve_read_only"] = bool(override.get("auto_approve_read_only"))
    return merged


def _parse_metadata(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


@dataclass
class PolicyDecision:
    allowed: bool
    requires_confirmation: bool
    reason: str
    host: Optional[str] = None
    scope: str = "default"
    tool_risk: str = "unknown"
    tool_category: str = "unknown"


class BrowserAgentService:
    def __init__(self) -> None:
        self.db = get_db()
        self.enabled = str(os.getenv("AP_BROWSER_AGENT_ENABLED", "true")).strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }

    def _ensure_policy(self, organization_id: str) -> Dict[str, Any]:
        existing = self.db.get_agent_policy(organization_id, "browser_agent_v1")
        if existing:
            return existing
        return self.db.upsert_agent_policy(
            organization_id=organization_id,
            policy_name="browser_agent_v1",
            config=DEFAULT_POLICY,
            updated_by="system_bootstrap",
            enabled=True,
        )

    def get_policy(self, organization_id: str) -> Dict[str, Any]:
        policy = self._ensure_policy(organization_id)
        config = _normalize_policy_config(policy.get("config_json"))
        return {
            "id": policy.get("id"),
            "organization_id": organization_id,
            "enabled": bool(policy.get("enabled", 1)),
            "policy_name": "browser_agent_v1",
            "config": config,
            "updated_at": policy.get("updated_at"),
        }

    def _resolve_scoped_policy(
        self,
        config: Dict[str, Any],
        actor_role: Optional[str],
        workflow_id: Optional[str],
    ) -> Tuple[Dict[str, Any], str]:
        resolved = {
            "allowed_domains": list(config.get("allowed_domains") or []),
            "blocked_actions": list(config.get("blocked_actions") or []),
            "require_confirmation_for": list(config.get("require_confirmation_for") or []),
            "auto_approve_read_only": bool(config.get("auto_approve_read_only", True)),
        }
        scope_parts: List[str] = []

        workflow_name = _normalize_text(workflow_id)
        workflow_overrides = config.get("workflow_overrides") or {}
        if workflow_name and workflow_name in workflow_overrides:
            resolved = _merge_policy_scope(resolved, workflow_overrides.get(workflow_name) or {})
            scope_parts.append(f"workflow:{workflow_name}")

        role_name = _normalize_text(actor_role)
        role_overrides = config.get("role_overrides") or {}
        if role_name and role_name in role_overrides:
            resolved = _merge_policy_scope(resolved, role_overrides.get(role_name) or {})
            scope_parts.append(f"role:{role_name}")

        return resolved, ",".join(scope_parts) if scope_parts else "default"

    def evaluate_command_policy(
        self,
        organization_id: str,
        command: Dict[str, Any],
        actor_role: Optional[str] = None,
        workflow_id: Optional[str] = None,
    ) -> PolicyDecision:
        tool_name = str(command.get("tool_name") or "").strip().lower()
        if tool_name not in SUPPORTED_TOOLS:
            return PolicyDecision(False, False, f"unsupported_tool:{tool_name}")

        policy = self.get_policy(organization_id)
        if not policy.get("enabled", True):
            return PolicyDecision(False, False, "policy_disabled")

        config = policy.get("config") or {}
        scoped_config, scope_label = self._resolve_scoped_policy(config, actor_role=actor_role, workflow_id=workflow_id)
        blocked_actions = {str(v).strip().lower() for v in (scoped_config.get("blocked_actions") or [])}
        if tool_name in blocked_actions:
            meta = TOOL_REGISTRY.get(tool_name, {})
            return PolicyDecision(
                False,
                False,
                f"blocked_action:{tool_name}",
                scope=scope_label,
                tool_risk=str(meta.get("risk") or "unknown"),
                tool_category=str(meta.get("category") or "unknown"),
            )

        raw_url = _extract_url(command)
        host = ""
        if raw_url:
            try:
                host = urlparse(raw_url).hostname or ""
            except Exception:
                return PolicyDecision(False, False, "invalid_url", scope=scope_label)

        allowed_domains = [str(v).strip().lower() for v in (scoped_config.get("allowed_domains") or [])]
        if host and allowed_domains:
            if not any(_host_matches(pattern, host) for pattern in allowed_domains):
                meta = TOOL_REGISTRY.get(tool_name, {})
                return PolicyDecision(
                    False,
                    False,
                    f"blocked_domain:{host}",
                    host=host,
                    scope=scope_label,
                    tool_risk=str(meta.get("risk") or "unknown"),
                    tool_category=str(meta.get("category") or "unknown"),
                )

        meta = TOOL_REGISTRY.get(tool_name, {})
        tool_risk = str(meta.get("risk") or "unknown")
        explicit_confirmation = tool_name in {
            str(v).strip().lower() for v in (scoped_config.get("require_confirmation_for") or [])
        }
        auto_approve_read_only = bool(scoped_config.get("auto_approve_read_only", True))

        requires_confirmation = explicit_confirmation
        if tool_risk == "high_risk":
            requires_confirmation = True
        elif tool_risk == "read_only" and not explicit_confirmation and auto_approve_read_only:
            requires_confirmation = False

        return PolicyDecision(
            True,
            requires_confirmation,
            "allowed",
            host=host or None,
            scope=scope_label,
            tool_risk=tool_risk,
            tool_category=str(meta.get("category") or "unknown"),
        )

    def _resolve_scope_context(
        self,
        session: Dict[str, Any],
        command: Dict[str, Any],
        actor_role: Optional[str] = None,
        workflow_id: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        session_metadata = _parse_metadata(session.get("metadata"))
        resolved_role = (
            actor_role
            or _normalize_text(command.get("actor_role"))
            or _normalize_text(session_metadata.get("actor_role"))
            or None
        )
        resolved_workflow = (
            workflow_id
            or _normalize_text(command.get("workflow_id"))
            or _normalize_text(session_metadata.get("workflow_id"))
            or None
        )
        return resolved_role, resolved_workflow

    def _build_preview_payload(
        self,
        command: Dict[str, Any],
        decision: PolicyDecision,
        actor_role: Optional[str],
        workflow_id: Optional[str],
        session_id: str,
        session_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        tool_name = str(command.get("tool_name") or "").strip().lower()
        selector = str((command.get("params") or {}).get("selector") or (command.get("target") or {}).get("selector") or "").strip()
        value = str((command.get("params") or {}).get("value") or "")
        url = _extract_url(command)
        summary_parts = [f"Tool `{tool_name}` ({decision.tool_risk.replace('_', ' ')})"]
        if decision.host:
            summary_parts.append(f"on `{decision.host}`")
        if selector:
            summary_parts.append(f"selector `{selector}`")
        if value:
            summary_parts.append(f"value length {len(value)}")
        memory_context = session_metadata or {}
        context_snapshot = memory_context.get("context_snapshot")
        if isinstance(context_snapshot, dict):
            source_count = context_snapshot.get("source_count")
            budget_status = _normalize_text(context_snapshot.get("budget_status"))
            if source_count not in (None, ""):
                summary_parts.append(f"{source_count} linked sources")
            if budget_status:
                summary_parts.append(f"budget `{budget_status}`")
        summary = " · ".join(summary_parts)

        warnings: List[str] = []
        if not decision.allowed:
            warnings.append(f"blocked by policy: {decision.reason}")
        if decision.requires_confirmation:
            warnings.append("explicit human confirmation required")
        if decision.tool_risk == "high_risk":
            warnings.append("high-risk mutating action")
        if isinstance(context_snapshot, dict) and bool(context_snapshot.get("has_context_conflict")):
            warnings.append("context conflict is present on this invoice")

        return {
            "session_id": session_id,
            "generated_at": _utcnow(),
            "command": {
                "tool_name": tool_name,
                "command_id": command.get("command_id"),
                "target": command.get("target") or {},
                "params": command.get("params") or {},
                "correlation_id": command.get("correlation_id"),
                "depends_on": command.get("depends_on") or [],
            },
            "decision": {
                "allowed": decision.allowed,
                "requires_confirmation": decision.requires_confirmation,
                "reason": decision.reason,
                "scope": decision.scope,
                "host": decision.host,
                "tool_risk": decision.tool_risk,
                "tool_category": decision.tool_category,
                "actor_role": actor_role,
                "workflow_id": workflow_id,
            },
            "summary": summary,
            "warnings": warnings,
            "target_url": url or None,
            "context_snapshot": context_snapshot if isinstance(context_snapshot, dict) else {},
        }

    def preview_command(
        self,
        session_id: str,
        command: Dict[str, Any],
        actor_id: str = "agent_runtime",
        actor_role: Optional[str] = None,
        workflow_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        del actor_id  # Included for interface symmetry and future audit extensions.
        if not self.enabled:
            raise ValueError("browser_agent_disabled")
        session = self.db.get_agent_session(session_id)
        if not session:
            raise ValueError("session_not_found")
        organization_id = str(session.get("organization_id") or "default")
        resolved_role, resolved_workflow = self._resolve_scope_context(
            session=session,
            command=command,
            actor_role=actor_role,
            workflow_id=workflow_id,
        )
        decision = self.evaluate_command_policy(
            organization_id,
            command,
            actor_role=resolved_role,
            workflow_id=resolved_workflow,
        )
        session_metadata = _parse_metadata(session.get("metadata"))
        return self._build_preview_payload(
            command=command,
            decision=decision,
            actor_role=resolved_role,
            workflow_id=resolved_workflow,
            session_id=session_id,
            session_metadata=session_metadata,
        )

    def create_session(
        self,
        organization_id: str,
        ap_item_id: str,
        created_by: str = "agent_runtime",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not self.enabled:
            raise ValueError("browser_agent_disabled")
        existing = self.db.get_agent_session_by_item(organization_id, ap_item_id)
        if existing and existing.get("state") in {"running", "blocked_for_approval"}:
            return existing

        session = self.db.create_agent_session(
            {
                "organization_id": organization_id,
                "ap_item_id": ap_item_id,
                "state": "running",
                "created_by": created_by,
                "metadata": metadata or {},
            }
        )
        self.db.append_ap_audit_event(
            {
                "ap_item_id": ap_item_id,
                "event_type": "browser_session_created",
                "from_state": None,
                "to_state": None,
                "actor_type": "agent",
                "actor_id": created_by,
                "reason": "browser_session_created",
                "metadata": {"session_id": session.get("id")},
                "idempotency_key": f"browser_session_created:{session.get('id')}",
                "organization_id": organization_id,
            }
        )
        return session

    def _build_macro_commands(
        self,
        session: Dict[str, Any],
        macro_name: str,
        params: Optional[Dict[str, Any]],
        correlation_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        macro = str(macro_name or "").strip().lower()
        if macro not in SUPPORTED_MACROS:
            raise ValueError("macro_not_supported")

        ap_item = self.db.get_ap_item(str(session.get("ap_item_id") or ""))
        metadata = _parse_metadata((ap_item or {}).get("metadata"))
        thread_id = str((ap_item or {}).get("thread_id") or "")
        source_url = "https://mail.google.com/mail/u/0/#inbox"
        if thread_id:
            source_url = f"https://mail.google.com/mail/u/0/#inbox/{thread_id}"

        parameters = params if isinstance(params, dict) else {}
        workflow_id = (
            _normalize_text(parameters.get("workflow_id"))
            or _normalize_text(metadata.get("workflow_id"))
            or None
        )
        role_hint = _normalize_text(parameters.get("actor_role")) or None
        portal_url = (
            _normalize_text(parameters.get("vendor_portal_url"))
            or _normalize_text(metadata.get("invoice_portal_url"))
            or _normalize_text(metadata.get("vendor_portal_url"))
            or ""
        )

        commands: List[Dict[str, Any]] = []
        if macro == "ingest_invoice_match_po":
            commands = [
                {
                    "tool_name": "read_page",
                    "command_id": "macro_ingest_read_email",
                    "target": {"url": source_url},
                    "params": {"include_tables": True},
                    "depends_on": [],
                    "step": "Read invoice email for intake context",
                },
                {
                    "tool_name": "extract_table",
                    "command_id": "macro_ingest_extract_tables",
                    "target": {"url": source_url},
                    "params": {"selector": "table"},
                    "depends_on": ["macro_ingest_read_email"],
                    "step": "Extract structured table rows",
                },
                {
                    "tool_name": "find_element",
                    "command_id": "macro_ingest_find_po",
                    "target": {"url": source_url},
                    "params": {"selector": "a[href*='po'], [data-po], [aria-label*='PO']"},
                    "depends_on": ["macro_ingest_read_email"],
                    "step": "Find purchase-order reference on source surfaces",
                },
                {
                    "tool_name": "capture_evidence",
                    "command_id": "macro_ingest_capture_email",
                    "target": {"url": source_url},
                    "params": {"selector": "body"},
                    "depends_on": ["macro_ingest_extract_tables", "macro_ingest_find_po"],
                    "step": "Capture evidence snapshot for intake",
                },
            ]
            if portal_url:
                commands.extend(
                    [
                        {
                            "tool_name": "open_tab",
                            "command_id": "macro_ingest_open_portal",
                            "target": {"url": portal_url},
                            "params": {"background": True},
                            "depends_on": ["macro_ingest_capture_email"],
                            "step": "Open vendor portal tab for cross-check",
                        },
                        {
                            "tool_name": "read_page",
                            "command_id": "macro_ingest_read_portal",
                            "target": {"url": portal_url},
                            "params": {"include_tables": True},
                            "depends_on": ["macro_ingest_open_portal"],
                            "step": "Read vendor portal invoice context",
                        },
                    ]
                )
        elif macro == "collect_w9":
            profile_url = (
                _normalize_text(parameters.get("profile_url"))
                or _normalize_text(parameters.get("vendor_url"))
                or portal_url
                or source_url
            )
            commands = [
                {
                    "tool_name": "open_tab",
                    "command_id": "macro_w9_open_profile",
                    "target": {"url": profile_url},
                    "params": {"background": False},
                    "depends_on": [],
                    "step": "Open vendor profile or portal page",
                },
                {
                    "tool_name": "query_selector_all",
                    "command_id": "macro_w9_find_docs",
                    "target": {"url": profile_url},
                    "params": {"selector": "a[href*='w9'], a[href*='W-9'], [data-doc*='w9']", "limit": 8},
                    "depends_on": ["macro_w9_open_profile"],
                    "step": "Locate W-9 document links",
                },
                {
                    "tool_name": "capture_evidence",
                    "command_id": "macro_w9_capture",
                    "target": {"url": profile_url},
                    "params": {"selector": "body"},
                    "depends_on": ["macro_w9_find_docs"],
                    "step": "Capture W-9 collection evidence",
                },
            ]
        elif macro == "post_invoice_to_erp":
            erp_url = (
                _normalize_text(parameters.get("erp_url"))
                or _normalize_text(parameters.get("vendor_portal_url"))
                or portal_url
                or source_url
            )
            invoice_number = _normalize_text(parameters.get("invoice_number"))
            vendor_name = _normalize_text(parameters.get("vendor_name"))
            amount = parameters.get("amount")
            currency = _normalize_text(parameters.get("currency")) or "USD"

            commands = [
                {
                    "tool_name": "open_tab",
                    "command_id": "macro_post_open_erp",
                    "target": {"url": erp_url},
                    "params": {"background": False},
                    "depends_on": [],
                    "step": "Open ERP posting surface",
                },
                {
                    "tool_name": "query_selector_all",
                    "command_id": "macro_post_find_entry",
                    "target": {"url": erp_url},
                    "params": {
                        "selector": "[data-test='new-bill'], button[aria-label*='Bill'], button[aria-label*='Invoice']",
                        "limit": 5,
                    },
                    "depends_on": ["macro_post_open_erp"],
                    "step": "Locate create-bill entry point",
                },
                {
                    "tool_name": "click",
                    "command_id": "macro_post_open_form",
                    "target": {"url": erp_url},
                    "params": {
                        "selector": "[data-test='new-bill'], button[aria-label*='Bill'], button[aria-label*='Invoice']",
                        "selector_candidates": [
                            "[data-test='new-bill']",
                            "button[aria-label*='New Bill']",
                            "button[aria-label*='New Invoice']",
                            "button[type='submit']",
                        ],
                    },
                    "depends_on": ["macro_post_find_entry"],
                    "step": "Open ERP invoice posting form",
                },
                {
                    "tool_name": "type",
                    "command_id": "macro_post_invoice_number",
                    "target": {"url": erp_url},
                    "params": {
                        "selector": "input[name='invoice_number']",
                        "selector_candidates": [
                            "input[name='invoice_number']",
                            "input[name='DocNumber']",
                            "input[aria-label*='Invoice Number']",
                            "input[placeholder*='Invoice']",
                        ],
                        "value": invoice_number,
                    },
                    "depends_on": ["macro_post_open_form"],
                    "step": "Fill invoice number",
                },
                {
                    "tool_name": "type",
                    "command_id": "macro_post_vendor",
                    "target": {"url": erp_url},
                    "params": {
                        "selector": "input[name='vendor']",
                        "selector_candidates": [
                            "input[name='vendor']",
                            "input[name='CardCode']",
                            "input[aria-label*='Vendor']",
                            "input[placeholder*='Supplier']",
                        ],
                        "value": vendor_name,
                    },
                    "depends_on": ["macro_post_open_form"],
                    "step": "Fill vendor",
                },
                {
                    "tool_name": "type",
                    "command_id": "macro_post_amount",
                    "target": {"url": erp_url},
                    "params": {
                        "selector": "input[name='amount']",
                        "selector_candidates": [
                            "input[name='amount']",
                            "input[name='DocTotal']",
                            "input[aria-label*='Amount']",
                            "input[placeholder*='Total']",
                        ],
                        "value": f"{amount or ''}",
                    },
                    "depends_on": ["macro_post_open_form"],
                    "step": f"Fill amount ({currency})",
                },
                {
                    "tool_name": "click",
                    "command_id": "macro_post_submit",
                    "target": {"url": erp_url},
                    "params": {
                        "selector": "button[type='submit']",
                        "selector_candidates": [
                            "button[type='submit']",
                            "button[data-test='post-bill']",
                            "button[aria-label*='Post']",
                            "button[aria-label*='Save']",
                        ],
                    },
                    "depends_on": ["macro_post_invoice_number", "macro_post_vendor", "macro_post_amount"],
                    "step": "Submit bill to ERP",
                },
                {
                    "tool_name": "capture_evidence",
                    "command_id": "macro_post_capture_result",
                    "target": {"url": erp_url},
                    "params": {"selector": "body"},
                    "depends_on": ["macro_post_submit"],
                    "step": "Capture ERP post confirmation evidence",
                },
            ]

        for command in commands:
            command["correlation_id"] = correlation_id
            if workflow_id:
                command["workflow_id"] = workflow_id
            if role_hint:
                command["actor_role"] = role_hint
        return commands

    def dispatch_macro(
        self,
        session_id: str,
        macro_name: str,
        actor_id: str = "agent_runtime",
        actor_role: Optional[str] = None,
        workflow_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        if not self.enabled:
            raise ValueError("browser_agent_disabled")
        session = self.db.get_agent_session(session_id)
        if not session:
            raise ValueError("session_not_found")
        commands = self._build_macro_commands(session, macro_name, params=params, correlation_id=correlation_id)
        previews = [
            self.preview_command(
                session_id=session_id,
                command=command,
                actor_id=actor_id,
                actor_role=actor_role,
                workflow_id=workflow_id,
            )
            for command in commands
        ]
        if dry_run:
            return {
                "status": "preview",
                "session_id": session_id,
                "macro_name": macro_name,
                "commands": previews,
            }

        events: List[Dict[str, Any]] = []
        for command in commands:
            event = self.enqueue_command(
                session_id=session_id,
                command=command,
                actor_id=actor_id,
                confirm=False,
                confirmed_by=None,
                actor_role=actor_role,
                workflow_id=workflow_id,
            )
            events.append(event)

        ap_item_id = str(session.get("ap_item_id") or "")
        organization_id = str(session.get("organization_id") or "default")
        queued = len([event for event in events if event.get("status") == "queued"])
        blocked = len([event for event in events if event.get("status") == "blocked_for_approval"])
        denied = len([event for event in events if event.get("status") == "denied_policy"])
        self.db.append_ap_audit_event(
            {
                "ap_item_id": ap_item_id,
                "event_type": "browser_macro_dispatched",
                "from_state": session.get("state"),
                "to_state": session.get("state"),
                "actor_type": "agent",
                "actor_id": actor_id,
                "reason": macro_name,
                "metadata": {
                    "session_id": session_id,
                    "macro_name": macro_name,
                    "queued": queued,
                    "blocked": blocked,
                    "denied": denied,
                    "command_count": len(events),
                },
                "idempotency_key": f"browser_macro_dispatched:{session_id}:{macro_name}:{hashlib.sha1(_utcnow().encode('utf-8')).hexdigest()[:10]}",
                "organization_id": organization_id,
            }
        )

        return {
            "status": "dispatched",
            "session_id": session_id,
            "macro_name": macro_name,
            "queued": queued,
            "blocked": blocked,
            "denied": denied,
            "events": events,
        }

    def enqueue_command(
        self,
        session_id: str,
        command: Dict[str, Any],
        actor_id: str = "agent_runtime",
        confirm: bool = False,
        confirmed_by: Optional[str] = None,
        actor_role: Optional[str] = None,
        workflow_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.enabled:
            raise ValueError("browser_agent_disabled")
        session = self.db.get_agent_session(session_id)
        if not session:
            raise ValueError("session_not_found")

        organization_id = str(session.get("organization_id") or "default")
        ap_item_id = str(session.get("ap_item_id") or "")
        tool_name = str(command.get("tool_name") or "").strip().lower()
        command_id = str(command.get("command_id") or "")
        if not command_id:
            digest = hashlib.sha256(json.dumps(command, sort_keys=True).encode("utf-8")).hexdigest()[:20]
            command_id = f"cmd_{digest}"

        idempotency_key = command.get("idempotency_key") or f"browser:{ap_item_id}:{session_id}:{command_id}"
        by_command = self.db.get_browser_action_event(session_id, command_id)
        if by_command and by_command.get("status") == "blocked_for_approval" and confirm:
            updated = self.db.upsert_browser_action_event(
                {
                    "session_id": session_id,
                    "command_id": command_id,
                    "status": "queued",
                    "approved_by": confirmed_by or actor_id,
                    "approved_at": _utcnow(),
                    "policy_reason": "confirmed_by_user",
                }
            )
            self.db.update_agent_session(session_id, state="running")
            self.db.append_ap_audit_event(
                {
                    "ap_item_id": ap_item_id,
                    "event_type": "browser_command_confirmed",
                    "from_state": session.get("state"),
                    "to_state": "running",
                    "actor_type": "human",
                    "actor_id": confirmed_by or actor_id,
                    "reason": "command_confirmed",
                    "metadata": {
                        "session_id": session_id,
                        "command_id": command_id,
                    },
                    "idempotency_key": f"browser_command_confirmed:{session_id}:{command_id}",
                    "organization_id": organization_id,
                }
            )
            return updated

        existing = self.db.get_browser_action_event_by_idempotency_key(idempotency_key)
        if existing:
            return existing

        resolved_role, resolved_workflow = self._resolve_scope_context(
            session=session,
            command=command,
            actor_role=actor_role,
            workflow_id=workflow_id,
        )
        decision = self.evaluate_command_policy(
            organization_id=organization_id,
            command=command,
            actor_role=resolved_role,
            workflow_id=resolved_workflow,
        )
        status = "queued"
        approved_by = None
        approved_at = None
        policy_reason = decision.reason
        if not decision.allowed:
            status = "denied_policy"
        elif decision.requires_confirmation:
            if confirm:
                status = "queued"
                approved_by = confirmed_by or actor_id
                approved_at = _utcnow()
            else:
                status = "blocked_for_approval"

        request_payload = dict(command)
        request_payload.setdefault("tool_name", tool_name)
        request_payload["workflow_id"] = resolved_workflow
        request_payload["actor_role"] = resolved_role
        request_payload["policy_scope"] = decision.scope
        request_payload["tool_risk"] = decision.tool_risk
        request_payload["tool_category"] = decision.tool_category

        event = self.db.upsert_browser_action_event(
            {
                "organization_id": organization_id,
                "ap_item_id": ap_item_id,
                "session_id": session_id,
                "command_id": command_id,
                "tool_name": tool_name,
                "status": status,
                "requires_confirmation": decision.requires_confirmation,
                "approved_by": approved_by,
                "approved_at": approved_at,
                "policy_reason": policy_reason,
                "request_payload": request_payload,
                "idempotency_key": idempotency_key,
                "correlation_id": command.get("correlation_id"),
            }
        )

        self.db.append_ap_audit_event(
            {
                "ap_item_id": ap_item_id,
                "event_type": "browser_command_enqueued",
                "from_state": session.get("state"),
                "to_state": session.get("state"),
                "actor_type": "agent",
                "actor_id": actor_id,
                "reason": status,
                "metadata": {
                    "session_id": session_id,
                    "command_id": command_id,
                    "tool_name": tool_name,
                    "requires_confirmation": decision.requires_confirmation,
                    "policy_reason": policy_reason,
                    "policy_scope": decision.scope,
                    "tool_risk": decision.tool_risk,
                    "tool_category": decision.tool_category,
                },
                "idempotency_key": f"browser_command_enqueued:{session_id}:{command_id}",
                "organization_id": organization_id,
            }
        )

        if status == "blocked_for_approval":
            self.db.update_agent_session(session_id, state="blocked_for_approval")
        elif status in {"queued", "denied_policy"}:
            self.db.update_agent_session(session_id, state="running")
        return event

    def submit_result(
        self,
        session_id: str,
        command_id: str,
        status: str,
        result_payload: Dict[str, Any],
        actor_id: str = "extension_runner",
    ) -> Dict[str, Any]:
        if not self.enabled:
            raise ValueError("browser_agent_disabled")
        session = self.db.get_agent_session(session_id)
        if not session:
            raise ValueError("session_not_found")
        event = self.db.get_browser_action_event(session_id, command_id)
        if not event:
            raise ValueError("command_not_found")
        if event.get("status") in {"completed", "failed"}:
            return event

        updated = self.db.upsert_browser_action_event(
            {
                "session_id": session_id,
                "command_id": command_id,
                "status": status,
                "result_payload": result_payload,
            }
        )
        ap_item_id = str(session.get("ap_item_id") or "")
        organization_id = str(session.get("organization_id") or "default")
        self.db.append_ap_audit_event(
            {
                "ap_item_id": ap_item_id,
                "event_type": "browser_command_result",
                "from_state": session.get("state"),
                "to_state": session.get("state"),
                "actor_type": "system",
                "actor_id": actor_id,
                "reason": status,
                "metadata": {
                    "session_id": session_id,
                    "command_id": command_id,
                    "tool_name": updated.get("tool_name"),
                    "result": result_payload,
                },
                "idempotency_key": f"browser_command_result:{session_id}:{command_id}:{status}",
                "organization_id": organization_id,
            }
        )

        pending = self.db.list_browser_action_events(session_id, status="blocked_for_approval")
        if pending:
            self.db.update_agent_session(session_id, state="blocked_for_approval")
        else:
            self.db.update_agent_session(session_id, state="running")
        return updated

    def get_session(self, session_id: str) -> Dict[str, Any]:
        session = self.db.get_agent_session(session_id)
        if not session:
            raise ValueError("session_not_found")
        events = self.db.list_browser_action_events(session_id)
        pending = [event for event in events if event.get("status") == "blocked_for_approval"]
        queued = [event for event in events if event.get("status") == "queued"]
        return {
            "session": session,
            "events": events,
            "pending_approvals": pending,
            "queued_commands": queued,
        }


_SERVICE: Optional[BrowserAgentService] = None


def get_browser_agent_service() -> BrowserAgentService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = BrowserAgentService()
    return _SERVICE
