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
    # Hard ceiling on input tokens. Claude 4.x context windows are 200k,
    # but leaving margin for system prompts, tool defs, and the output
    # reservation makes 150k the practical cap. Override per-action for
    # anything known to be shorter (classification, decisioning) so a
    # runaway OCR dump doesn't silently pay for 100k tokens of noise.
    max_input_tokens: int = 150_000


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


# Char-to-token ratio. Anthropic's tokenizer is not distributed as a
# standalone lib (as of 2026-04), so we estimate from character count.
# English averages ~4 chars/token; JSON and structured text come in a
# bit denser. 3.5 is a conservative multiplier that errs on "assume
# more tokens than we have" — the worst case is truncating slightly
# early, which is fine. The alternative (underestimate → blow past
# context window) would turn into an API error or silent cost spike.
_CHARS_PER_TOKEN_ESTIMATE = 3.5


def _estimate_text_tokens(text: str) -> int:
    """Approximate token count for a piece of text without a tokenizer."""
    if not text:
        return 0
    return int(len(text) / _CHARS_PER_TOKEN_ESTIMATE) + 1


def _estimate_message_tokens(messages: List[Dict[str, Any]], system_prompt: str = "") -> int:
    """Estimate total input tokens for a messages payload + system prompt.

    Handles both plain-text ("content": str) and structured content
    ("content": [{"type": "text", "text": "..."}, ...]). Image blocks
    (multimodal) contribute a flat 1500 tokens per image — an
    order-of-magnitude estimate sufficient to keep images from
    sneaking through the budget even though the actual vision-token
    count is model-dependent.
    """
    total = _estimate_text_tokens(system_prompt)
    for msg in messages or []:
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, str):
            total += _estimate_text_tokens(content)
            continue
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    total += _estimate_text_tokens(str(block.get("text") or ""))
                elif btype == "image":
                    total += 1500  # flat per-image estimate
                elif btype == "tool_result":
                    total += _estimate_text_tokens(str(block.get("content") or ""))
                elif btype == "tool_use":
                    total += _estimate_text_tokens(str(block.get("input") or ""))
    return total


def _truncate_messages_to_budget(
    messages: List[Dict[str, Any]],
    system_prompt: str,
    max_input_tokens: int,
) -> tuple[List[Dict[str, Any]], bool]:
    """Return (possibly-truncated messages, truncated_flag).

    Strategy: if we're over budget, shrink the LAST user text block
    (the freshly-added request — typically the OCR dump or the large
    email body). We leave system + earlier conversation intact because
    those carry instructions and context the caller built up
    deliberately. Shrinks by trimming the string tail and appending a
    visible marker so the model + our own logs know truncation
    happened. This is a safety net, not a precise budget manager —
    callers that need exact control should size their inputs.
    """
    current = _estimate_message_tokens(messages, system_prompt)
    if current <= max_input_tokens:
        return messages, False

    # Reserve budget for system + everything but the last message's
    # biggest text block; truncate that block down to fit.
    system_tokens = _estimate_text_tokens(system_prompt)
    other_tokens = 0
    last_text_block_ref: Optional[tuple[int, int]] = None  # (msg_index, block_index)
    last_text_block_len = -1

    for i, msg in enumerate(messages or []):
        content = msg.get("content")
        if isinstance(content, str):
            tokens = _estimate_text_tokens(content)
            if tokens > last_text_block_len:
                last_text_block_ref = (i, -1)
                last_text_block_len = tokens
            other_tokens += tokens
        elif isinstance(content, list):
            for j, block in enumerate(content):
                if isinstance(block, dict) and block.get("type") == "text":
                    tokens = _estimate_text_tokens(str(block.get("text") or ""))
                    if tokens > last_text_block_len:
                        last_text_block_ref = (i, j)
                        last_text_block_len = tokens
                    other_tokens += tokens
                elif isinstance(block, dict) and block.get("type") == "image":
                    other_tokens += 1500
    if last_text_block_ref is None:
        # Nothing shrinkable (all images / no text) — can't truncate
        # sensibly; return unchanged and let the API decide.
        return messages, False

    # Budget remaining for the biggest text block. Leave a 100-token
    # safety margin for estimator rounding ("+1 per string" etc.).
    remaining = max_input_tokens - system_tokens - (other_tokens - last_text_block_len) - 100
    if remaining < 500:
        # Extreme case — even the non-target payload alone is already
        # near or past budget. Clamp to 500 tokens of the target block
        # so the call at least fires with something meaningful.
        remaining = 500
    target_chars = int(remaining * _CHARS_PER_TOKEN_ESTIMATE)
    truncation_marker = "\n\n[TRUNCATED BY GATEWAY — input exceeded context budget]"
    target_chars = max(target_chars - len(truncation_marker), 200)

    msg_idx, block_idx = last_text_block_ref
    new_messages = [dict(m) for m in messages]
    if block_idx == -1:
        original = str(new_messages[msg_idx].get("content") or "")
        new_messages[msg_idx]["content"] = original[:target_chars] + truncation_marker
    else:
        content_list = [dict(b) if isinstance(b, dict) else b for b in new_messages[msg_idx]["content"]]
        original = str(content_list[block_idx].get("text") or "")
        content_list[block_idx]["text"] = original[:target_chars] + truncation_marker
        new_messages[msg_idx]["content"] = content_list
    return new_messages, True


# ---------------------------------------------------------------------------
# Process-local circuit breaker for Anthropic rate limits.
#
# The existing retry loop bounds a single caller's behaviour: at most 3
# retries over ~2.5 minutes, then raise. That's fine in isolation but
# degenerate at scale — if the API is returning 429 globally (account
# quota exhausted, sustained traffic spike), 100 concurrent callers each
# run their own 3-retry loop against an already-throttling endpoint,
# burning credits on calls that will 429 again and adding load that
# makes the throttling worse.
#
# The circuit breaker flips the switch for the whole process: when any
# call sees a 429, we stash a "cooldown until" timestamp. While the
# cooldown is live, other callers fail fast with the same error shape
# as an exhausted retry — no API call, no spend, no added load. The
# cooldown honours Anthropic's Retry-After header when present, falling
# back to a conservative default.
#
# Process-local is intentional: we don't need cross-worker coordination
# here. Each worker has its own view of "is Anthropic throttling us
# right now?", and once one caller trips the breaker the next few
# callers in the same process get the fast-fail. If the throttle ends,
# the cooldown expires and traffic resumes organically.
# ---------------------------------------------------------------------------

_DEFAULT_COOLDOWN_SECONDS = 30
_MAX_COOLDOWN_SECONDS = 300  # cap so a buggy Retry-After doesn't stall us forever
_rate_limit_cooldown_until: float = 0.0


def _circuit_open() -> bool:
    return time.monotonic() < _rate_limit_cooldown_until


def _circuit_remaining() -> int:
    return max(int(_rate_limit_cooldown_until - time.monotonic()), 0)


def _trip_circuit(retry_after_header: Optional[str]) -> None:
    """Open the circuit for ``retry_after_header`` seconds (clamped).

    Anthropic returns Retry-After as seconds when they want us to back
    off. We honour it up to _MAX_COOLDOWN_SECONDS so a malformed or
    hostile header can't keep us offline for hours.
    """
    global _rate_limit_cooldown_until
    cooldown = _DEFAULT_COOLDOWN_SECONDS
    if retry_after_header:
        try:
            cooldown = int(float(str(retry_after_header).strip()))
        except (TypeError, ValueError):
            cooldown = _DEFAULT_COOLDOWN_SECONDS
    cooldown = max(1, min(cooldown, _MAX_COOLDOWN_SECONDS))
    new_until = time.monotonic() + cooldown
    if new_until > _rate_limit_cooldown_until:
        _rate_limit_cooldown_until = new_until
        logger.warning(
            "[LLMGateway] circuit breaker OPEN for %ds (Anthropic 429)", cooldown
        )


def _reset_circuit() -> None:
    """Clear the cooldown — call this after a successful API call."""
    global _rate_limit_cooldown_until
    _rate_limit_cooldown_until = 0.0


class LLMBudgetExceededError(RuntimeError):
    """Raised when a workspace has crossed its monthly LLM cost hard cap.

    The gateway's pre-flight budget check raises this to stop calls
    that would otherwise push past the tenant's runaway-spend guard.
    Message is kept deliberately neutral ("cost budget exceeded...")
    so the coordination engine's substring-based failure classifier
    (_classify_failure) buckets this as persistent (abort + move Box
    to exception) rather than transient (retry) or llm (template
    fallback). Retrying won't help — the cap only resets on a new
    billing month or an explicit override.
    """


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

    def _enforce_budget_cap(self, organization_id: str) -> None:
        """Runaway-spend guard — pre-flight check against the monthly cap.

        Fast path (already paused this month): raises immediately
        without querying llm_call_log. The tombstone is the answer.

        Slow path (not paused): queries month-to-date cost, compares
        to ``get_effective_llm_cost_cap``. If over, stamps
        ``organizations.llm_cost_paused_at``, alerts CS, emits the
        ``billing.llm_budget_exceeded`` webhook, writes an audit
        event, then raises :class:`LLMBudgetExceededError`.

        If the tombstone is set but from a prior calendar month,
        clears it (new billing cycle) and proceeds.

        Best-effort: any failure to load the org / subscription
        / cost total falls through to "allow the call" rather than
        blocking work on a DB hiccup. The runaway guard is a safety
        net, not a strict business gate — missing a single call
        because of a transient DB error would be worse than briefly
        continuing over cap while ops investigates.
        """
        # Lazy DB bind — mirrors _log_call's pattern so tests can
        # inject self._db before the first call.
        if not self._db:
            try:
                from clearledgr.core.database import get_db
                self._db = get_db()
            except Exception:
                return

        # Load the org row once; used for both tombstone check and
        # (inside get_effective_llm_cost_cap) the settings override.
        try:
            self._db.initialize()
            org = self._db.get_organization(organization_id)
        except Exception as exc:
            logger.debug(
                "[LLMGateway] budget-cap: org load failed for %s: %s "
                "(allowing call)", organization_id, exc,
            )
            return

        if not isinstance(org, dict):
            return

        now = datetime.now(timezone.utc)

        # --- Fast path: already paused? ---
        paused_at_raw = org.get("llm_cost_paused_at")
        if paused_at_raw:
            try:
                paused_at = datetime.fromisoformat(
                    str(paused_at_raw).replace("Z", "+00:00")
                )
                if (paused_at.year, paused_at.month) == (now.year, now.month):
                    # Still the same billing month — stay paused.
                    raise LLMBudgetExceededError(
                        f"Cost budget exceeded for organization "
                        f"{organization_id}: paused at {paused_at_raw}"
                    )
                # Different month — cycle rolled over, clear the tombstone
                # and fall through to the fresh cost check.
                try:
                    self._db.update_organization(
                        organization_id, llm_cost_paused_at=None,
                    )
                except Exception as exc:
                    logger.debug(
                        "[LLMGateway] budget-cap: tombstone clear failed for "
                        "%s: %s", organization_id, exc,
                    )
            except LLMBudgetExceededError:
                raise
            except Exception:
                # Malformed timestamp in the column — treat as not paused.
                pass

        # --- Slow path: query cost, compare to cap ---
        try:
            from clearledgr.services.subscription import get_subscription_service
            sub_svc = get_subscription_service()
            cap_usd = float(sub_svc.get_effective_llm_cost_cap(organization_id))
            cost_row = sub_svc._get_llm_cost_this_month(organization_id) or {}
            cost_usd = float(cost_row.get("total_cost_usd") or 0.0)
        except Exception as exc:
            logger.debug(
                "[LLMGateway] budget-cap: cost/cap lookup failed for "
                "%s: %s (allowing call)", organization_id, exc,
            )
            return

        if cost_usd < cap_usd:
            return  # Within budget — normal path.

        # --- Over cap: pause + alert + webhook + audit, then raise ---
        self._trip_budget_cap(
            organization_id=organization_id,
            cost_usd=cost_usd,
            cap_usd=cap_usd,
            now_iso=now.isoformat(),
        )
        raise LLMBudgetExceededError(
            f"Cost budget exceeded for organization {organization_id}: "
            f"${cost_usd:.2f} >= ${cap_usd:.2f}"
        )

    def _trip_budget_cap(
        self,
        *,
        organization_id: str,
        cost_usd: float,
        cap_usd: float,
        now_iso: str,
    ) -> None:
        """Record the trip: stamp tombstone, alert CS, webhook, audit.

        Every side effect is best-effort — the raise in the caller is
        what actually stops the bleed. If any of these notifications
        fail, we still raise so the bad call doesn't complete; the
        notifications are observability, not correctness.
        """
        # 1. Tombstone — the durable signal the fast path reads.
        try:
            self._db.update_organization(
                organization_id, llm_cost_paused_at=now_iso,
            )
        except Exception as exc:
            logger.warning(
                "[LLMGateway] budget-cap: tombstone stamp failed for %s: %s",
                organization_id, exc,
            )

        # 2. CS alert — Slack + log, non-blocking.
        try:
            from clearledgr.services.monitoring import alert_cs_team
            alert_cs_team(
                severity="error",
                title="LLM budget exceeded — workspace paused",
                detail=(
                    f"org={organization_id} cost=${cost_usd:.2f} "
                    f"cap=${cap_usd:.2f}. Further Claude calls will fast-fail "
                    f"until an override or the new billing month begins."
                ),
                organization_id=organization_id,
            )
        except Exception as exc:
            logger.debug("[LLMGateway] budget-cap CS alert failed: %s", exc)

        # 3. Webhook to the customer's Backoffice.
        try:
            import asyncio
            from clearledgr.services.webhook_delivery import emit_webhook_event

            payload = {
                "organization_id": organization_id,
                "cost_usd": round(cost_usd, 4),
                "cap_usd": round(cap_usd, 4),
                "paused_at": now_iso,
            }
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(emit_webhook_event(
                    organization_id=organization_id,
                    event_type="billing.llm_budget_exceeded",
                    payload=payload,
                ))
            except RuntimeError:
                # No running loop (sync context) — run it.
                asyncio.run(emit_webhook_event(
                    organization_id=organization_id,
                    event_type="billing.llm_budget_exceeded",
                    payload=payload,
                ))
        except Exception as exc:
            logger.debug("[LLMGateway] budget-cap webhook emit failed: %s", exc)

        # 4. Audit event — durable record on the org timeline.
        # append_audit_event requires (box_id, box_type). Org-level
        # events use box_type="organization" + box_id=org_id so the
        # audit row stays queryable by org-level consumers.
        try:
            self._db.append_audit_event({
                "event_type": "llm_budget_paused",
                "box_id": organization_id,
                "box_type": "organization",
                "actor_type": "system",
                "actor_id": "llm_gateway",
                "organization_id": organization_id,
                "decision_reason": "monthly cost hard cap exceeded",
                "payload_json": {
                    "cost_usd": cost_usd,
                    "cap_usd": cap_usd,
                    "paused_at": now_iso,
                },
            })
        except Exception as exc:
            logger.debug("[LLMGateway] budget-cap audit write failed: %s", exc)

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
        ap_item_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        box_id: Optional[str] = None,
        box_type: Optional[str] = None,
    ) -> Optional[str]:
        """Persist call metadata to llm_call_log table.

        Returns the call id on success, None on failure. The row is
        Box-keyed via ``box_id`` + ``box_type`` so auditors can join
        llm_call_log → audit_events on the same Box. The
        ``ap_item_id`` kwarg is an AP-convenience: if passed without
        an explicit ``box_id``, it's used as the box_id for
        ``box_type='ap_item'``. Classification calls that run before
        a Box exists may pass nothing and the columns stay null.
        """
        if not self._db:
            try:
                from clearledgr.core.database import get_db
                self._db = get_db()
            except Exception:
                return None

        # AP convenience: if the caller passed ap_item_id, that's the
        # box_id for type ap_item. Explicit box_id/box_type kwargs
        # always win over the AP shortcut.
        if box_id is None and ap_item_id:
            box_id = ap_item_id
        if box_type is None and box_id is not None:
            box_type = "ap_item"

        try:
            self._db.initialize()
            now = datetime.now(timezone.utc).isoformat()
            call_id = f"LLM-{uuid.uuid4().hex[:12]}"
            sql = (
                "INSERT INTO llm_call_log "
                "(id, organization_id, action, model, input_tokens, output_tokens, "
                "latency_ms, cost_estimate_usd, truncated, error, "
                "correlation_id, created_at, box_id, box_type) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            )
            with self._db.connect() as conn:
                conn.execute(sql, (
                    call_id, organization_id, action.value, model,
                    input_tokens, output_tokens, latency_ms, cost_estimate,
                    1 if truncated else 0, error,
                    correlation_id,
                    now,
                    box_id, box_type,
                ))
                conn.commit()
            return call_id
        except Exception as exc:
            logger.debug("[LLMGateway] Failed to log call: %s", exc)
            return None

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
        ap_item_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        box_id: Optional[str] = None,
        box_type: Optional[str] = None,
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
            LLMBudgetExceededError: If the workspace has crossed its
                monthly LLM cost hard cap (runaway-spend guard).
            RuntimeError: If all retries exhausted.
        """
        if action not in ACTION_REGISTRY:
            raise ValueError(
                f"Action {action!r} is not registered in the LLM Gateway. "
                f"Only registered LLM actions may call Claude. "
                f"Valid actions: {sorted(a.value for a in ACTION_REGISTRY)}"
            )

        # Runaway-spend guard: refuse the call if this workspace has
        # crossed its monthly LLM cost hard cap. See migration v44 +
        # PlanLimits.monthly_llm_cost_usd_hard_cap. This is a disaster
        # guard (catches bugs, retry loops, prompt injection), not a
        # pricing tier. Override via customer CFO endpoint or ops
        # endpoint clears the tombstone.
        self._enforce_budget_cap(organization_id)

        config = ACTION_REGISTRY[action]
        model = model_override or self._resolve_model(config)
        max_tokens = max_tokens_override or config.max_output_tokens
        temp = temperature if temperature is not None else config.temperature

        # Resolve the system prompt before truncation so it counts
        # against the budget.
        if system_prompt:
            effective_system = system_prompt
        elif action not in (LLMAction.AGENT_PLANNING, LLMAction.AP_DECISION):
            effective_system = build_system_prompt()
        else:
            effective_system = ""

        # Enforce input token budget BEFORE hitting the wire.
        # A 100-page OCR dump would otherwise either blow past Claude's
        # 200k window (hard error) or cost ~$0.60 per call silently.
        # Truncation is logged via the `truncated` flag on the call
        # record so we can spot callers that need to shrink their
        # inputs instead of relying on the gateway's safety net.
        messages, input_truncated = _truncate_messages_to_budget(
            messages, effective_system, config.max_input_tokens,
        )
        if input_truncated:
            logger.warning(
                "[LLMGateway] %s input exceeded %d-token budget — truncated",
                action.value, config.max_input_tokens,
            )

        # Build request body
        body: Dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temp,
            "messages": messages,
        }
        if effective_system:
            body["system"] = effective_system
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
        truncated = bool(input_truncated)
        start_time = time.monotonic()

        # Fast-fail if another caller in this process recently tripped
        # the rate-limit circuit breaker. Avoid the outbound call, avoid
        # the retry loop, avoid adding load to an already-throttling
        # endpoint. Callers see the same RuntimeError shape as a real
        # 429 so no special handling is needed upstream.
        if _circuit_open():
            remaining = _circuit_remaining()
            last_error = f"429: circuit breaker open, retry in {remaining}s"
            self._log_call(
                action=action, model=model,
                input_tokens=0, output_tokens=0,
                latency_ms=0, cost_estimate=0.0,
                truncated=False, error=last_error,
                organization_id=organization_id,
                ap_item_id=ap_item_id,
                correlation_id=correlation_id,
                box_id=box_id,
                box_type=box_type,
            )
            raise RuntimeError(
                f"[LLMGateway] {action.value} skipped: Anthropic rate limit "
                f"cooldown active ({remaining}s remaining)"
            )

        from clearledgr.core.http_client import get_http_client
        client = get_http_client()
        for attempt in range(_MAX_RETRIES + 1):
            try:
                # Shared client — keeps TLS session + TCP connection
                # alive across LLM calls. Per-call timeout override
                # preserves the existing action-level budget.
                resp = await client.post(
                    _API_URL, headers=headers, json=body,
                    timeout=config.timeout_seconds,
                )

                if resp.status_code == 429:
                    # Trip the process-local breaker so the next N
                    # callers don't each repeat this dance. Honour
                    # Retry-After if the server gave us one.
                    _trip_circuit(resp.headers.get("Retry-After"))

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
                ap_item_id=ap_item_id,
                correlation_id=correlation_id,
                box_id=box_id,
                box_type=box_type,
                    )
                    raise RuntimeError(
                        f"[LLMGateway] {action.value} failed: {last_error}"
                    )

                # Size gate before JSON parse. Anthropic normal responses
                # are well under 1MB, but a malformed / MITM'd / replay
                # injected response could balloon resp.content beyond
                # reasonable bounds and OOM the parser. Cap at 10MB —
                # generous enough for any legitimate long-form
                # generation, tight enough to fail fast on garbage.
                raw_body = resp.content
                if len(raw_body) > 10_000_000:
                    last_error = f"response_too_large:{len(raw_body)}_bytes"
                    latency_ms = int((time.monotonic() - start_time) * 1000)
                    self._log_call(
                        action=action, model=model,
                        input_tokens=0, output_tokens=0,
                        latency_ms=latency_ms, cost_estimate=0.0,
                        truncated=truncated, error=last_error,
                        organization_id=organization_id,
                ap_item_id=ap_item_id,
                correlation_id=correlation_id,
                box_id=box_id,
                box_type=box_type,
                    )
                    raise RuntimeError(
                        f"[LLMGateway] {action.value} refused oversized response: {last_error}"
                    )
                data = resp.json()
                # Successful call — close the circuit if it was open
                # from a previous caller. Anthropic is answering us
                # again, so subsequent calls in this process don't need
                # to fast-fail.
                _reset_circuit()
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
                ap_item_id=ap_item_id,
                correlation_id=correlation_id,
                box_id=box_id,
                box_type=box_type,
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
                ap_item_id=ap_item_id,
                correlation_id=correlation_id,
                box_id=box_id,
                box_type=box_type,
        )
        raise RuntimeError(f"[LLMGateway] {action.value} failed after {_MAX_RETRIES} retries: {last_error}")

    async def stream(
        self,
        action: LLMAction,
        messages: List[Dict[str, Any]],
        *,
        system_prompt: Optional[str] = None,
        organization_id: str = "default",
        temperature: Optional[float] = None,
        max_tokens_override: Optional[int] = None,
        model_override: Optional[str] = None,
    ):
        """Async generator that yields text chunks as Claude emits them.

        Uses Anthropic's SSE streaming endpoint
        (https://docs.anthropic.com/claude/reference/messages-streaming).
        Each yielded value is a string fragment of the assistant's
        response. Usage/cost is logged once at the end via the same
        _log_call path as call(). Tool-use streaming is NOT supported
        here — this is for pure text responses (sidebar Q&A, explain
        state, etc.).

        Retries are NOT applied to stream() — if the initial POST fails,
        the caller is expected to fall back to a non-streaming path.
        This keeps the first-chunk latency predictable and avoids mid-
        stream retry complexity.
        """
        if action not in ACTION_REGISTRY:
            raise ValueError(f"Action {action!r} is not registered in the LLM Gateway.")

        config = ACTION_REGISTRY[action]
        model = model_override or self._resolve_model(config)
        max_tokens = max_tokens_override or config.max_output_tokens
        temp = temperature if temperature is not None else config.temperature

        body: Dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temp,
            "messages": messages,
            "stream": True,
        }
        if system_prompt:
            body["system"] = system_prompt

        import httpx
        import json as _json
        import time as _time

        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        start_time = _time.monotonic()
        input_tokens = 0
        output_tokens = 0
        error: Optional[str] = None

        try:
            from clearledgr.core.http_client import get_http_client
            client = get_http_client()
            async with client.stream(
                "POST", _API_URL, headers=headers, json=body,
                timeout=config.timeout_seconds,
            ) as resp:
                if resp.status_code >= 400:
                    body_text = await resp.aread()
                    err = f"{resp.status_code}: {body_text[:200].decode('utf-8', errors='replace')}"
                    error = err
                    raise RuntimeError(f"[LLMGateway] {action.value} stream failed: {err}")
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload_raw = line[5:].strip()
                    if not payload_raw or payload_raw == "[DONE]":
                        continue
                    try:
                        payload = _json.loads(payload_raw)
                    except ValueError:
                        continue
                    event_type = payload.get("type")
                    if event_type == "content_block_delta":
                        delta = payload.get("delta") or {}
                        if delta.get("type") == "text_delta":
                            text = delta.get("text") or ""
                            if text:
                                yield text
                    elif event_type == "message_start":
                        usage = (payload.get("message") or {}).get("usage") or {}
                        input_tokens = int(usage.get("input_tokens") or 0)
                    elif event_type == "message_delta":
                        usage = payload.get("usage") or {}
                        output_tokens = int(usage.get("output_tokens") or 0)
        finally:
            latency_ms = int((_time.monotonic() - start_time) * 1000)
            cost = self._estimate_cost(config, input_tokens, output_tokens)
            self._log_call(
                action=action, model=model,
                input_tokens=input_tokens, output_tokens=output_tokens,
                latency_ms=latency_ms, cost_estimate=cost,
                truncated=False, error=error,
                organization_id=organization_id,
                ap_item_id=ap_item_id,
                correlation_id=correlation_id,
                box_id=box_id,
                box_type=box_type,
            )

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
