"""Internal planning engine used by the finance agent runtime.

This module is an internal execution primitive:
- Skills registry: planner tool-catalogue registration
- Planning loop: Claude tool-use step selection/execution
- Durable execution: each tool call is checkpointed to DB before + after execution
  so the server can crash and resume from where it left off

Usage:
    planner = get_planning_engine()
    planner.register_skill(APSkill("acme-corp"))

    task = AgentTask(
        task_type="ap_invoice_processing",
        organization_id="acme-corp",
        payload={"invoice": invoice.__dict__},
        idempotency_key=f"invoice:{invoice.gmail_id}",
    )
    result = await planner.run_task(task)
    # result.status in ("completed", "awaiting_human", "failed", "max_steps_exceeded")
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from clearledgr.core.skills.base import AgentTask, FinanceSkill, SkillResult

logger = logging.getLogger(__name__)

MAX_PLANNING_STEPS = 10
MAX_TASK_SECONDS = int(os.getenv("AGENT_MAX_TASK_SECONDS", "600"))
CLAUDE_MODEL = os.getenv("AGENT_RUNTIME_MODEL", "claude-sonnet-4-6")


class AgentPlanningEngine:
    """Skills registry + Claude tool-use planning loop + durable task execution.

    One per process (singleton via get_planning_engine()). Skills are registered
    at startup and looked up by task_type on each run_task() call.
    """

    def __init__(self) -> None:
        self._skills: Dict[str, FinanceSkill] = {}

    # ------------------------------------------------------------------
    # Skills registry
    # ------------------------------------------------------------------

    def register_skill(self, skill: FinanceSkill) -> None:
        """Register a skill.  Replaces any previous skill with the same name."""
        self._skills[skill.skill_name] = skill
        logger.info("[AgentRuntime] registered skill: %s", skill.skill_name)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def run_task(self, task: AgentTask) -> SkillResult:
        """Execute a task using the matching skill's planning loop.

        Creates a durable task_run row in DB (or returns the existing one for
        the same idempotency_key), then enters the planning loop.
        """
        skill = self._skills.get(task.task_type)
        if not skill:
            raise ValueError(
                f"No skill registered for task_type={task.task_type!r}. "
                f"Registered: {list(self._skills)}"
            )

        from clearledgr.core.database import get_db
        db = get_db()

        task_run = db.create_task_run(
            id=str(uuid.uuid4()),
            org_id=task.organization_id,
            task_type=task.task_type,
            input_payload=json.dumps(task.payload),
            idempotency_key=task.idempotency_key,
            correlation_id=task.correlation_id,
        )
        return await self._planning_loop(task, skill, task_run)

    # ------------------------------------------------------------------
    # Planning loop
    # ------------------------------------------------------------------

    async def _planning_loop(
        self,
        task: AgentTask,
        skill: FinanceSkill,
        task_run: Dict[str, Any],
    ) -> SkillResult:
        """Claude tool-use loop with per-step DB checkpointing.

        On each iteration:
        1. Call Claude with the full message history and tool catalogue
        2. If Claude returns no tool_use on the first step → fail (fake-completion guard)
        3. If Claude returns no tool_use after prior tool execution → complete
        4. If Claude returns a tool_use:
           a. Checkpoint BEFORE executing (crash safety)
           b. Execute the tool handler (never raises)
           c. Checkpoint the result
           d. Feed result back into messages
        5. If tool returns is_awaiting_human=True → pause for human input
        6. After MAX_PLANNING_STEPS → surface max_steps_exceeded
        """
        from clearledgr.core.database import get_db
        db = get_db()

        task_run_id = task_run["id"]
        already_done = int(task_run.get("current_step") or 0)

        # Rebuild message history from stored checkpoints (for resume after crash)
        messages = self._rebuild_from_checkpoint(task_run, task)
        tools = [t.to_claude_spec() for t in skill.get_tools()]
        tool_map = {t.name: t for t in skill.get_tools()}
        system_prompt = skill.build_system_prompt(task)

        step = already_done
        start_time = time.monotonic()
        while step < MAX_PLANNING_STEPS:
            elapsed = time.monotonic() - start_time
            if elapsed > MAX_TASK_SECONDS:
                logger.warning(
                    "[AgentRuntime] task %s timed out after %.0fs (limit %ds)",
                    task_run_id, elapsed, MAX_TASK_SECONDS,
                )
                db.fail_task_run(task_run_id, "max_execution_time_exceeded")
                return SkillResult(
                    status="failed",
                    task_run_id=task_run_id,
                    outcome={"steps": step, "elapsed_seconds": round(elapsed)},
                    step_count=step,
                    error="max_execution_time_exceeded",
                )
            try:
                response = await self._call_claude_with_tools(system_prompt, messages, tools)
            except Exception as exc:
                logger.warning("[AgentRuntime] Claude call failed at step %d: %s", step, exc)
                db.fail_task_run(task_run_id, str(exc))
                return SkillResult(
                    status="failed",
                    task_run_id=task_run_id,
                    outcome={},
                    step_count=step,
                    error=str(exc),
                )

            content = response.get("content") or []
            if not isinstance(content, list):
                logger.warning("[AgentRuntime] Unexpected content type %s, treating as empty", type(content).__name__)
                content = []

            stop_reason = response.get("stop_reason", "")
            if stop_reason == "max_tokens":
                logger.warning("[AgentRuntime] Response truncated (max_tokens) at step %d", step)

            tool_calls = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]

            if not tool_calls:
                # Prevent fake completion: at least one tool execution is required
                # before a task can be marked completed.
                text = " ".join(
                    b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
                )
                if step <= 0:
                    error = "planning_returned_no_tool_use"
                    db.fail_task_run(task_run_id, error)
                    return SkillResult(
                        status="failed",
                        task_run_id=task_run_id,
                        outcome={"response": text, "steps": step},
                        step_count=step,
                        error=error,
                    )
                outcome = {"response": text, "steps": step}
                db.complete_task_run(task_run_id, outcome, status="completed")
                return SkillResult(
                    status="completed",
                    task_run_id=task_run_id,
                    outcome=outcome,
                    step_count=step,
                )

            # Take first tool call (AP tools are sequential)
            tc = tool_calls[0]
            tool_name = tc.get("name", "")
            tool_call_id = tc.get("id", f"call_{step}")
            input_args = tc.get("input") or {}
            if not isinstance(input_args, dict):
                logger.warning("[AgentRuntime] Tool %s returned non-dict input: %s", tool_name, type(input_args).__name__)
                input_args = {}

            # Checkpoint BEFORE executing — if we crash here, we retry this step on resume
            try:
                db.update_task_run_step(
                    task_run_id, step, tool_name, input_args, {}, status="running"
                )
            except Exception as exc:
                logger.error("[AgentRuntime] Pre-exec checkpoint failed (step %d): %s", step, exc)

            # Execute tool (NEVER raises — returns {"ok": False, "error": "..."} on failure)
            tool = tool_map.get(tool_name)
            if tool:
                try:
                    output = await tool.handler(
                        **input_args,
                        organization_id=task.organization_id,
                    )
                except Exception as exc:
                    logger.warning("[AgentRuntime] tool %s raised unexpectedly: %s", tool_name, exc)
                    output = {"ok": False, "error": str(exc)}
            else:
                output = {"ok": False, "error": f"Unknown tool: {tool_name!r}"}

            is_hitl = bool(output.get("is_awaiting_human"))
            next_status = "awaiting_human" if is_hitl else "running"

            # Checkpoint result
            try:
                db.update_task_run_step(
                    task_run_id, step + 1, tool_name, input_args, output, status=next_status
                )
            except Exception as exc:
                logger.error("[AgentRuntime] Post-exec checkpoint failed (step %d): %s", step, exc)

            # Feed back into message history
            messages.append({"role": "assistant", "content": content})
            messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": json.dumps(output),
                }],
            })

            step += 1

            if is_hitl:
                hitl_ctx = output.get("hitl_context") or {}
                db.complete_task_run(task_run_id, hitl_ctx, status="awaiting_human")
                return SkillResult(
                    status="awaiting_human",
                    task_run_id=task_run_id,
                    outcome=hitl_ctx,
                    step_count=step,
                    hitl_context=hitl_ctx,
                )

        # Exhausted max steps
        db.complete_task_run(task_run_id, {}, status="max_steps_exceeded")
        return SkillResult(
            status="max_steps_exceeded",
            task_run_id=task_run_id,
            outcome={},
            step_count=step,
        )

    # ------------------------------------------------------------------
    # Claude API (raw httpx — consistent with ap_decision.py)
    # ------------------------------------------------------------------

    _RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503})
    _MAX_RETRIES = 3
    _BASE_DELAY = 1.0  # seconds

    async def _call_claude_with_tools(
        self,
        system: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """POST to Anthropic messages API with tool_use enabled.

        Retries up to ``_MAX_RETRIES`` times with exponential backoff for
        transient failures (429, 500, 502, 503).  Returns the raw response
        dict.  Raises RuntimeError when the API key is missing.
        """
        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("anthropic_api_key_missing")

        import httpx
        payload = {
            "model": CLAUDE_MODEL,
            "max_tokens": 4096,
            "system": system,
            "tools": tools,
            "messages": messages,
        }
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        last_exc: Optional[Exception] = None
        async with httpx.AsyncClient(timeout=120.0) as client:
            for attempt in range(self._MAX_RETRIES + 1):
                try:
                    resp = await client.post(
                        "https://api.anthropic.com/v1/messages",
                        headers=headers,
                        json=payload,
                    )
                    if resp.status_code in self._RETRYABLE_STATUS_CODES and attempt < self._MAX_RETRIES:
                        delay = self._BASE_DELAY * (2 ** attempt)
                        logger.warning(
                            "[AgentRuntime] Claude API returned %s, retrying in %.1fs (attempt %d/%d)",
                            resp.status_code, delay, attempt + 1, self._MAX_RETRIES,
                        )
                        await asyncio.sleep(delay)
                        continue
                    resp.raise_for_status()
                    return resp.json()
                except httpx.HTTPStatusError:
                    raise  # non-retryable status or exhausted retries
                except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as exc:
                    last_exc = exc
                    if attempt < self._MAX_RETRIES:
                        delay = self._BASE_DELAY * (2 ** attempt)
                        logger.warning(
                            "[AgentRuntime] Claude API network error (%s), retrying in %.1fs (attempt %d/%d)",
                            type(exc).__name__, delay, attempt + 1, self._MAX_RETRIES,
                        )
                        await asyncio.sleep(delay)
                        continue
                    raise RuntimeError(f"Claude API failed after {self._MAX_RETRIES} retries: {last_exc}") from last_exc
        raise RuntimeError(f"Claude API failed after {self._MAX_RETRIES} retries: {last_exc}")

    # ------------------------------------------------------------------
    # Checkpoint resume
    # ------------------------------------------------------------------

    def _rebuild_from_checkpoint(
        self,
        task_run: Dict[str, Any],
        task: AgentTask,
    ) -> List[Dict[str, Any]]:
        """Reconstruct message history from stored step_results.

        For a fresh task run (current_step=0), returns just the opening user message.
        For a resumed task run, replays all completed steps as assistant tool_use
        + user tool_result pairs so Claude has full context.
        """
        messages: List[Dict[str, Any]] = [
            {
                "role": "user",
                "content": (
                    f"Process this finance task ({task.task_type}):\n"
                    f"{task_run.get('input_payload', json.dumps(task.payload))}"
                ),
            }
        ]

        current_step = int(task_run.get("current_step") or 0)
        if current_step == 0:
            return messages

        step_results: Dict[str, Any] = {}
        try:
            step_results = json.loads(task_run.get("step_results") or "{}")
        except Exception:
            pass

        for i in range(current_step):
            sr = step_results.get(str(i))
            if not sr:
                break
            synthetic_id = f"resume_step_{i}"
            messages.append({
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": synthetic_id,
                    "name": sr.get("tool", "unknown"),
                    "input": sr.get("input", {}),
                }],
            })
            messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": synthetic_id,
                    "content": json.dumps(sr.get("output", {})),
                }],
            })

        return messages

    # ------------------------------------------------------------------
    # Startup resume
    # ------------------------------------------------------------------

    async def resume_pending_tasks(self) -> int:
        """Called on server startup. Re-enters the planning loop for any
        task_runs that were left in status=running (server crashed mid-step).

        Returns the number of tasks resumed.
        """
        from clearledgr.core.database import get_db
        db = get_db()
        pending = db.list_pending_task_runs(statuses=("running",))
        count = 0
        for row in pending:
            skill = self._skills.get(row.get("task_type", ""))
            if not skill:
                logger.warning(
                    "[AgentRuntime] resume: no skill for task_type=%s (id=%s)",
                    row.get("task_type"), row.get("id"),
                )
                continue
            try:
                payload = json.loads(row.get("input_payload") or "{}")
            except Exception as exc:
                logger.error(
                    "[AgentRuntime] resume: corrupted input_payload for task_run id=%s: %s",
                    row.get("id"), exc,
                )
                try:
                    db.fail_task_run(row["id"], error="input_payload_corrupted")
                except Exception:
                    pass
                continue
            task = AgentTask(
                task_type=row["task_type"],
                organization_id=row["organization_id"],
                payload=payload,
                idempotency_key=row.get("idempotency_key"),
                correlation_id=row.get("correlation_id"),
            )
            asyncio.create_task(self._planning_loop(task, skill, row))
            logger.info("[AgentRuntime] resuming task_run id=%s", row.get("id"))
            count += 1
        return count


# ---------------------------------------------------------------------------
# Per-process singleton
# ---------------------------------------------------------------------------

_planning_engine: Optional[AgentPlanningEngine] = None


def get_planning_engine() -> AgentPlanningEngine:
    """Return the process-level AgentPlanningEngine singleton."""
    global _planning_engine
    if _planning_engine is None:
        _planning_engine = AgentPlanningEngine()
    return _planning_engine


def get_agent_runtime() -> AgentPlanningEngine:
    """Backward-compatible alias for the planner singleton."""
    return get_planning_engine()
