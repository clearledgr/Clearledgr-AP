"""Agent runtime for AP financial reasoning with tool-use style outputs.

The runtime is advisory only. Deterministic validators remain authoritative for
state transitions and ERP posting.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    from anthropic import AsyncAnthropic  # type: ignore
except Exception:  # pragma: no cover
    AsyncAnthropic = None

try:
    from openai import AsyncOpenAI  # type: ignore
except Exception:  # pragma: no cover
    AsyncOpenAI = None

logger = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AgentResult:
    extraction: Dict[str, Any]
    validation: Dict[str, Any]
    approval_routing: Dict[str, Any]
    posting_plan: Dict[str, Any]
    trace: List[Dict[str, Any]]
    browser_commands: List[Dict[str, Any]]


class AgentRuntime:
    def __init__(self) -> None:
        self.mode = os.getenv("AP_AGENT_MODE", "mock").strip().lower()
        self.anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        self.openai_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.anthropic_model = os.getenv("AP_AGENT_ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
        self.openai_model = os.getenv("AP_AGENT_OPENAI_MODEL", "gpt-4o-mini")
        self.default_surface = os.getenv("AP_APPROVAL_SURFACE", "hybrid").strip().lower() or "hybrid"

    async def analyze(self, context: Dict[str, Any]) -> AgentResult:
        if self.mode == "mock":
            return self._mock_result(context)

        prompt = self._build_prompt(context)
        if self.anthropic_key and AsyncAnthropic is not None:
            try:
                result = await self._run_anthropic(prompt)
                return self._normalize_result(result, provider="anthropic", context=context)
            except Exception as exc:  # pragma: no cover
                logger.warning("Anthropic runtime failed, falling back: %s", exc)

        if self.openai_key and AsyncOpenAI is not None:
            try:
                result = await self._run_openai(prompt)
                return self._normalize_result(result, provider="openai", context=context)
            except Exception as exc:  # pragma: no cover
                logger.warning("OpenAI runtime failed, falling back to mock: %s", exc)

        return self._mock_result(context)

    def _build_prompt(self, context: Dict[str, Any]) -> str:
        return (
            "You are Clearledgr AP agent. Return strict JSON with keys: extraction, validation, "
            "approval_routing, posting_plan, trace, browser_commands.\n"
            "Do not invent values.\n"
            f"Context JSON:\n{json.dumps(context, ensure_ascii=True)}"
        )

    async def _run_anthropic(self, prompt: str) -> Dict[str, Any]:
        client = AsyncAnthropic(api_key=self.anthropic_key)
        msg = await client.messages.create(
            model=self.anthropic_model,
            max_tokens=1200,
            temperature=0,
            system="Return only valid JSON.",
            messages=[{"role": "user", "content": prompt}],
        )
        content = ""
        for part in getattr(msg, "content", []) or []:
            text = getattr(part, "text", None)
            if text:
                content += text
        return json.loads(content)

    async def _run_openai(self, prompt: str) -> Dict[str, Any]:
        client = AsyncOpenAI(api_key=self.openai_key)
        completion = await client.chat.completions.create(
            model=self.openai_model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
        )
        content = completion.choices[0].message.content if completion.choices else "{}"
        return json.loads(content or "{}")

    def _normalize_result(self, payload: Dict[str, Any], provider: str, context: Optional[Dict[str, Any]] = None) -> AgentResult:
        extraction = payload.get("extraction") if isinstance(payload, dict) else {}
        validation = payload.get("validation") if isinstance(payload, dict) else {}
        approval = payload.get("approval_routing") if isinstance(payload, dict) else {}
        posting = payload.get("posting_plan") if isinstance(payload, dict) else {}
        trace = payload.get("trace") if isinstance(payload, dict) else []
        browser_commands = payload.get("browser_commands") if isinstance(payload, dict) else []

        if not isinstance(extraction, dict):
            extraction = {}
        if not isinstance(validation, dict):
            validation = {}
        if not isinstance(approval, dict):
            approval = {}
        if not isinstance(posting, dict):
            posting = {}
        if not isinstance(trace, list):
            trace = []
        if not isinstance(browser_commands, list):
            browser_commands = []

        for entry in trace:
            if isinstance(entry, dict):
                entry.setdefault("provider", provider)
                entry.setdefault("ts", _utcnow())

        if not trace:
            trace = [{
                "step": "agent_reasoning",
                "role": "agent",
                "summary": "Processed by foundation model and normalized by deterministic guardrails.",
                "provider": provider,
                "ts": _utcnow(),
            }]

        approval.setdefault("surface", self.default_surface)

        default_correlation_id = None
        if isinstance(context, dict):
            metadata = context.get("metadata")
            if isinstance(metadata, dict):
                default_correlation_id = metadata.get("correlation_id")

        normalized_commands: List[Dict[str, Any]] = []
        for idx, command in enumerate(browser_commands):
            normalized = self._normalize_browser_command(
                command=command,
                index=idx + 1,
                default_correlation_id=default_correlation_id,
            )
            if normalized:
                normalized_commands.append(normalized)

        if not normalized_commands:
            normalized_commands = self._build_default_browser_command_graph(context or {})

        return AgentResult(
            extraction=extraction,
            validation=validation,
            approval_routing=approval,
            posting_plan=posting,
            trace=trace,
            browser_commands=normalized_commands,
        )

    def _normalize_browser_command(
        self,
        command: Any,
        index: int,
        default_correlation_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(command, dict):
            return None

        tool_name = str(command.get("tool_name") or "").strip().lower()
        if not tool_name:
            return None

        command_id = str(command.get("command_id") or "").strip() or f"agent_cmd_{index}"
        target = command.get("target")
        params = command.get("params")
        depends_on = command.get("depends_on") or command.get("dependsOn") or []
        sequence = command.get("sequence")
        correlation_id = command.get("correlation_id") or default_correlation_id

        if not isinstance(target, dict):
            target = {}
        if not isinstance(params, dict):
            params = {}
        if isinstance(depends_on, str):
            depends_on = [depends_on]
        if not isinstance(depends_on, list):
            depends_on = []
        normalized_depends_on = [str(dep).strip() for dep in depends_on if str(dep).strip()]

        try:
            normalized_sequence = int(sequence)
        except Exception:
            normalized_sequence = index

        return {
            "tool_name": tool_name,
            "command_id": command_id,
            "target": target,
            "params": params,
            "correlation_id": correlation_id,
            "depends_on": normalized_depends_on,
            "sequence": normalized_sequence,
            "step": str(command.get("step") or command.get("intent") or command_id),
        }

    def _build_default_browser_command_graph(self, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
        correlation_id = metadata.get("correlation_id")
        thread_id = str(context.get("thread_id") or "").strip()
        message_id = str(context.get("message_id") or "").strip()

        source_url = "https://mail.google.com/mail/u/0/#inbox"
        if thread_id:
            source_url = f"https://mail.google.com/mail/u/0/#inbox/{thread_id}"
        elif message_id:
            source_url = f"https://mail.google.com/mail/u/0/#search/{message_id}"

        commands: List[Dict[str, Any]] = [
            {
                "tool_name": "read_page",
                "command_id": "read_source_email",
                "target": {"url": source_url},
                "params": {"include_tables": True, "include_headings": True},
                "correlation_id": correlation_id,
                "depends_on": [],
                "sequence": 1,
                "step": "Read current source email content",
            },
            {
                "tool_name": "extract_table",
                "command_id": "extract_invoice_tables",
                "target": {"url": source_url},
                "params": {"selector": "table"},
                "correlation_id": correlation_id,
                "depends_on": ["read_source_email"],
                "sequence": 2,
                "step": "Extract candidate invoice tables from email",
            },
            {
                "tool_name": "find_element",
                "command_id": "find_invoice_attachment_link",
                "target": {"url": source_url},
                "params": {"selector": "a[href*='.pdf'], [aria-label*='.pdf'], [data-tooltip*='.pdf']"},
                "correlation_id": correlation_id,
                "depends_on": ["read_source_email"],
                "sequence": 3,
                "step": "Locate invoice PDF attachment link in the email view",
            },
            {
                "tool_name": "capture_evidence",
                "command_id": "capture_source_email_evidence",
                "target": {"url": source_url},
                "params": {"selector": "body"},
                "correlation_id": correlation_id,
                "depends_on": ["read_source_email"],
                "sequence": 4,
                "step": "Capture source email evidence for audit trail",
            },
        ]

        portal_url = str(metadata.get("invoice_portal_url") or metadata.get("vendor_portal_url") or "").strip()
        if portal_url:
            commands.extend(
                [
                    {
                        "tool_name": "open_tab",
                        "command_id": "open_vendor_portal_tab",
                        "target": {"url": portal_url},
                        "params": {"background": True},
                        "correlation_id": correlation_id,
                        "depends_on": ["capture_source_email_evidence"],
                        "sequence": 5,
                        "step": "Open vendor portal in a dedicated tab",
                    },
                    {
                        "tool_name": "read_page",
                        "command_id": "read_vendor_portal_page",
                        "target": {"url": portal_url},
                        "params": {"include_tables": True},
                        "correlation_id": correlation_id,
                        "depends_on": ["open_vendor_portal_tab"],
                        "sequence": 6,
                        "step": "Read vendor portal page for cross-tab comparison",
                    },
                    {
                        "tool_name": "capture_evidence",
                        "command_id": "capture_vendor_portal_evidence",
                        "target": {"url": portal_url},
                        "params": {"selector": "body"},
                        "correlation_id": correlation_id,
                        "depends_on": ["read_vendor_portal_page"],
                        "sequence": 7,
                        "step": "Capture vendor portal evidence for audit trail",
                    },
                ]
            )

        return commands

    def _mock_result(self, context: Dict[str, Any]) -> AgentResult:
        extracted = {
            "vendor": context.get("vendor_name") or context.get("vendor") or context.get("sender"),
            "amount": context.get("amount"),
            "currency": context.get("currency") or "USD",
            "invoice_number": context.get("invoice_number"),
            "invoice_date": context.get("invoice_date"),
            "due_date": context.get("due_date"),
            "confidence": context.get("confidence", 0),
        }
        validation = {
            "status": "ok",
            "notes": ["Deterministic policy engine remains source of truth."],
        }
        approval = {
            "surface": self.default_surface,
            "reason": "approval_required_by_policy",
        }
        posting = {
            "strategy": "post_after_human_approval",
            "connector": os.getenv("ERP_CONNECTOR", "mock"),
        }
        trace = [
            {
                "step": "document_extraction",
                "role": "DocumentExtractionAgent",
                "summary": "Extracted invoice candidate fields from Gmail body and attachments.",
                "provider": "mock",
                "ts": _utcnow(),
            },
            {
                "step": "validation_reasoning",
                "role": "ValidationReasoningAgent",
                "summary": "Prepared policy and duplicate checks for deterministic validator.",
                "provider": "mock",
                "ts": _utcnow(),
            },
            {
                "step": "approval_routing",
                "role": "ApprovalRoutingAgent",
                "summary": f"Selected {self.default_surface} approval surface.",
                "provider": "mock",
                "ts": _utcnow(),
            },
            {
                "step": "posting_plan",
                "role": "PostingPlannerAgent",
                "summary": "Prepared ERP payload strategy pending approval.",
                "provider": "mock",
                "ts": _utcnow(),
            },
        ]
        browser_commands = self._build_default_browser_command_graph(context)
        return AgentResult(
            extraction=extracted,
            validation=validation,
            approval_routing=approval,
            posting_plan=posting,
            trace=trace,
            browser_commands=browser_commands,
        )


_AGENT_RUNTIME: Optional[AgentRuntime] = None


def get_agent_runtime() -> AgentRuntime:
    global _AGENT_RUNTIME
    if _AGENT_RUNTIME is None:
        _AGENT_RUNTIME = AgentRuntime()
    return _AGENT_RUNTIME
