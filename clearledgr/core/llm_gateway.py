"""Centralized LLM Gateway — Agent Design Specification §7.

All Claude API calls in the system go through this gateway. It enforces:
1. Action registry with DET/LLM boundary (only registered LLM actions may call Claude)
2. Token budget per action (input truncation with logging)
3. 4-section system prompt template (Role, Output format, Constraints, Guardrail)
4. Cost tracking (input/output tokens, latency, cost estimate per call)
5. Retry with exponential backoff (429, 500, 502, 503)

Usage:
    from clearledgr.core.llm_gateway import get_llm_gateway, LLMAction
    gateway = get_llm_gateway()
    response = await gateway.call(
        LLMAction.EXTRACT_INVOICE_FIELDS,
        messages=[{"role": "user", "content": "..."}],
        organization_id="org-123",
    )
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Action Registry
# ---------------------------------------------------------------------------

# Cost per 1M tokens (approximate, for tracking)
_COST_PER_1M_INPUT = {"haiku": 0.25, "sonnet": 3.00}
_COST_PER_1M_OUTPUT = {"haiku": 1.25, "sonnet": 15.00}

# Defaults point at the latest Claude 4 family. Environments that
# need to pin a specific version override via ANTHROPIC_MODEL (sonnet
# tier) and ANTHROPIC_EXTRACTION_MODEL (haiku tier) on Railway/local.
_MODEL_HAIKU = os.environ.get("ANTHROPIC_EXTRACTION_MODEL", "claude-haiku-4-5-20251001")
_MODEL_SONNET = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")


class LLMAction(str, Enum):
    """Every permitted LLM action. Deterministic actions are NOT listed here
    and CANNOT call Claude through the gateway."""

    # §7.1 — the five spec-defined LLM actions
    CLASSIFY_EMAIL = "classify_email"
    EXTRACT_INVOICE_FIELDS = "extract_invoice_fields"
    GENERATE_EXCEPTION = "generate_exception_reason"
    CLASSIFY_VENDOR = "classify_vendor_response"
    DRAFT_VENDOR_RESPONSE = "draft_vendor_response"

    # Extended actions (beyond spec — codebase already uses these)
    AP_DECISION = "ap_decision"
    AGENT_PLANNING = "agent_planning"
    DUPLICATE_EVALUATION = "duplicate_evaluation"
    PO_LINE_MATCH = "po_line_match"
    EXPLAIN_STATE = "explain_state"
    SLACK_QUERY = "slack_query"
    SINGLE_PASS_EXTRACT = "single_pass_extract"


@dataclass(frozen=True)
class ActionConfig:
    """Per-action configuration enforced by the gateway."""
    max_output_tokens: int
    model_tier: str  # "haiku" or "sonnet"
    temperature: float = 0.1
    timeout_seconds: int = 30


# Immutable registry — adding a new LLM action requires updating this dict
ACTION_REGISTRY: Dict[LLMAction, ActionConfig] = {
    LLMAction.CLASSIFY_EMAIL:         ActionConfig(max_output_tokens=2000, model_tier="haiku"),
    LLMAction.EXTRACT_INVOICE_FIELDS: ActionConfig(max_output_tokens=4000, model_tier="sonnet"),
    LLMAction.GENERATE_EXCEPTION:     ActionConfig(max_output_tokens=1000, model_tier="haiku"),
    LLMAction.CLASSIFY_VENDOR:        ActionConfig(max_output_tokens=2000, model_tier="haiku"),
    LLMAction.DRAFT_VENDOR_RESPONSE:  ActionConfig(max_output_tokens=3000, model_tier="sonnet"),
    LLMAction.AP_DECISION:            ActionConfig(max_output_tokens=512,  model_tier="sonnet"),
    LLMAction.AGENT_PLANNING:         ActionConfig(max_output_tokens=4096, model_tier="sonnet", timeout_seconds=120),
    LLMAction.DUPLICATE_EVALUATION:   ActionConfig(max_output_tokens=500,  model_tier="haiku", timeout_seconds=15),
    LLMAction.PO_LINE_MATCH:          ActionConfig(max_output_tokens=100,  model_tier="haiku", timeout_seconds=10),
    LLMAction.EXPLAIN_STATE:          ActionConfig(max_output_tokens=512,  model_tier="sonnet"),
    LLMAction.SLACK_QUERY:            ActionConfig(max_output_tokens=600,  model_tier="sonnet"),
    LLMAction.SINGLE_PASS_EXTRACT:    ActionConfig(max_output_tokens=2000, model_tier="sonnet"),
}


# ---------------------------------------------------------------------------
# 4-Section System Prompt Template (§7.2)
# ---------------------------------------------------------------------------

DEFAULT_SYSTEM_PROMPT_SECTIONS = {
    "role": (
        "You are a precise finance data extraction and reasoning assistant. "
        "You process accounts payable documents for professional finance teams. "
        "Your outputs are used in automated financial workflows where accuracy is critical."
    ),
    "output_format": (
        "Return only valid JSON. No preamble. No explanation outside the JSON. "
        "No markdown formatting."
    ),
    "constraints": (
        "Do not infer values that are not present in the document. "
        "If a field is not found, return null for that field rather than guessing. "
        "Do not convert currencies. Return amounts exactly as they appear."
    ),
    "guardrail_reminder": (
        "If you are uncertain about any numeric value, set the confidence for that "
        "field to below 0.5 rather than returning a value you are not confident in. "
        "A low-confidence extraction that surfaces to a human is safer than a "
        "high-confidence incorrect extraction."
    ),
}


def build_system_prompt(
    *,
    role: Optional[str] = None,
    output_format: Optional[str] = None,
    constraints: Optional[str] = None,
    guardrail_reminder: Optional[str] = None,
) -> str:
    """Build a 4-section system prompt per §7.2.

    Pass None for any section to use the default. Pass a string to override.
    """
    sections = [
        role or DEFAULT_SYSTEM_PROMPT_SECTIONS["role"],
        output_format or DEFAULT_SYSTEM_PROMPT_SECTIONS["output_format"],
        constraints or DEFAULT_SYSTEM_PROMPT_SECTIONS["constraints"],
        guardrail_reminder or DEFAULT_SYSTEM_PROMPT_SECTIONS["guardrail_reminder"],
    ]
    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Response dataclass
# ---------------------------------------------------------------------------

@dataclass
class LLMResponse:
    """Structured response from the gateway."""
    content: Any  # str or list (for tool_use responses)
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    model: str = ""
    action: str = ""
    cost_estimate_usd: float = 0.0
    stop_reason: str = ""
    raw_response: Optional[Dict[str, Any]] = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------

_MAX_RETRIES = 3
_RETRY_DELAYS = [5, 30, 120]  # seconds
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503}
_API_URL = "https://api.anthropic.com/v1/messages"


class LLMGateway:
    """Centralized Claude API gateway.

    All LLM calls go through ``call()`` or ``call_sync()``.
    Deterministic actions that attempt to use the gateway are rejected.
    """

    def __init__(self, api_key: Optional[str] = None, db: Any = None):
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._db = db

    def _resolve_model(self, config: ActionConfig) -> str:
        if config.model_tier == "haiku":
            return os.environ.get("ANTHROPIC_EXTRACTION_MODEL", _MODEL_HAIKU)
        return os.environ.get("ANTHROPIC_MODEL", _MODEL_SONNET)

    def _estimate_cost(self, config: ActionConfig, input_tokens: int, output_tokens: int) -> float:
        tier = config.model_tier
        input_cost = (input_tokens / 1_000_000) * _COST_PER_1M_INPUT.get(tier, 3.0)
        output_cost = (output_tokens / 1_000_000) * _COST_PER_1M_OUTPUT.get(tier, 15.0)
        return round(input_cost + output_cost, 6)

    def _log_call(
        self,
        *,
        action: LLMAction,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
        cost_estimate: float,
        truncated: bool,
        error: Optional[str],
        organization_id: str,
    ) -> None:
        """Persist call metadata to llm_call_log table."""
        if not self._db:
            try:
                from clearledgr.core.database import get_db
                self._db = get_db()
            except Exception:
                return

        try:
            self._db.initialize()
            now = datetime.now(timezone.utc).isoformat()
            call_id = f"LLM-{uuid.uuid4().hex[:12]}"
            sql = self._db._prepare_sql(
                "INSERT INTO llm_call_log "
                "(id, organization_id, action, model, input_tokens, output_tokens, "
                "latency_ms, cost_estimate_usd, truncated, error, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            )
            with self._db.connect() as conn:
                conn.execute(sql, (
                    call_id, organization_id, action.value, model,
                    input_tokens, output_tokens, latency_ms, cost_estimate,
                    1 if truncated else 0, error, now,
                ))
                conn.commit()
        except Exception as exc:
            logger.debug("[LLMGateway] Failed to log call: %s", exc)

    async def call(
        self,
        action: LLMAction,
        messages: List[Dict[str, Any]],
        *,
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Dict[str, Any]] = None,
        organization_id: str = "default",
        temperature: Optional[float] = None,
        max_tokens_override: Optional[int] = None,
        model_override: Optional[str] = None,
    ) -> LLMResponse:
        """Make a Claude API call through the gateway.

        Args:
            action: The registered LLM action (must be in ACTION_REGISTRY).
            messages: Claude messages array.
            system_prompt: Optional override. If None, uses the default 4-section template.
            tools: Optional tool definitions for tool_use.
            tool_choice: Optional tool_choice constraint.
            organization_id: For cost tracking.
            temperature: Override action default.
            max_tokens_override: Override action budget (use sparingly).
            model_override: Override action model (use sparingly).

        Returns:
            LLMResponse with content, usage, and cost tracking.

        Raises:
            ValueError: If action is not in ACTION_REGISTRY.
            RuntimeError: If all retries exhausted.
        """
        if action not in ACTION_REGISTRY:
            raise ValueError(
                f"Action {action!r} is not registered in the LLM Gateway. "
                f"Only registered LLM actions may call Claude. "
                f"Valid actions: {sorted(a.value for a in ACTION_REGISTRY)}"
            )

        config = ACTION_REGISTRY[action]
        model = model_override or self._resolve_model(config)
        max_tokens = max_tokens_override or config.max_output_tokens
        temp = temperature if temperature is not None else config.temperature

        # Build request body
        body: Dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temp,
            "messages": messages,
        }
        if system_prompt:
            body["system"] = system_prompt
        elif action not in (LLMAction.AGENT_PLANNING, LLMAction.AP_DECISION):
            # Use default 4-section template for most actions
            body["system"] = build_system_prompt()
        if tools:
            body["tools"] = tools
        if tool_choice:
            body["tool_choice"] = tool_choice

        # Retry loop
        import httpx

        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        last_error: Optional[str] = None
        truncated = False
        start_time = time.monotonic()

        for attempt in range(_MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
                    resp = await client.post(_API_URL, headers=headers, json=body)

                if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < _MAX_RETRIES:
                    delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                    logger.warning(
                        "[LLMGateway] %s returned %d, retrying in %ds (attempt %d/%d)",
                        action.value, resp.status_code, delay, attempt + 1, _MAX_RETRIES,
                    )
                    import asyncio
                    await asyncio.sleep(delay)
                    continue

                if resp.status_code >= 400:
                    error_text = resp.text[:200]
                    last_error = f"{resp.status_code}: {error_text}"
                    latency_ms = int((time.monotonic() - start_time) * 1000)
                    self._log_call(
                        action=action, model=model,
                        input_tokens=0, output_tokens=0,
                        latency_ms=latency_ms, cost_estimate=0.0,
                        truncated=truncated, error=last_error,
                        organization_id=organization_id,
                    )
                    raise RuntimeError(
                        f"[LLMGateway] {action.value} failed: {last_error}"
                    )

                data = resp.json()
                usage = data.get("usage", {})
                input_tokens = usage.get("input_tokens", 0)
                output_tokens = usage.get("output_tokens", 0)
                latency_ms = int((time.monotonic() - start_time) * 1000)
                cost = self._estimate_cost(config, input_tokens, output_tokens)

                # Extract content
                content_blocks = data.get("content", [])
                stop_reason = data.get("stop_reason", "")

                # For tool_use responses, return the full content blocks
                if any(b.get("type") == "tool_use" for b in content_blocks):
                    content = content_blocks
                else:
                    # Text response — concatenate text blocks
                    content = "".join(
                        b.get("text", "") for b in content_blocks if b.get("type") == "text"
                    )

                self._log_call(
                    action=action, model=model,
                    input_tokens=input_tokens, output_tokens=output_tokens,
                    latency_ms=latency_ms, cost_estimate=cost,
                    truncated=truncated, error=None,
                    organization_id=organization_id,
                )

                logger.info(
                    "[LLMGateway] %s | %s | %d in / %d out | %dms | $%.4f",
                    action.value, model, input_tokens, output_tokens, latency_ms, cost,
                )

                return LLMResponse(
                    content=content,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=latency_ms,
                    model=model,
                    action=action.value,
                    cost_estimate_usd=cost,
                    stop_reason=stop_reason,
                    raw_response=data,
                )

            except httpx.TimeoutException:
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                    logger.warning(
                        "[LLMGateway] %s timed out, retrying in %ds (attempt %d/%d)",
                        action.value, delay, attempt + 1, _MAX_RETRIES,
                    )
                    import asyncio
                    await asyncio.sleep(delay)
                    continue
                last_error = "timeout"

        # All retries exhausted
        latency_ms = int((time.monotonic() - start_time) * 1000)
        self._log_call(
            action=action, model=model,
            input_tokens=0, output_tokens=0,
            latency_ms=latency_ms, cost_estimate=0.0,
            truncated=truncated, error=last_error or "max_retries_exhausted",
            organization_id=organization_id,
        )
        raise RuntimeError(f"[LLMGateway] {action.value} failed after {_MAX_RETRIES} retries: {last_error}")

    def call_sync(
        self,
        action: LLMAction,
        messages: List[Dict[str, Any]],
        **kwargs: Any,
    ) -> LLMResponse:
        """Synchronous wrapper around ``call()`` for non-async contexts."""
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # Already in an async context — create a new thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, self.call(action, messages, **kwargs))
                return future.result()
        else:
            return asyncio.run(self.call(action, messages, **kwargs))


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_gateway_instance: Optional[LLMGateway] = None


def get_llm_gateway() -> LLMGateway:
    """Get or create the singleton LLM Gateway instance."""
    global _gateway_instance
    if _gateway_instance is None:
        _gateway_instance = LLMGateway()
    return _gateway_instance


def reset_llm_gateway() -> None:
    """Reset the singleton (for tests)."""
    global _gateway_instance
    _gateway_instance = None
