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

    @staticmethod
    def _task_payload_ap_item(task_payload: Dict[str, Any]) -> Dict[str, Any]:
        payload = task_payload if isinstance(task_payload, dict) else {}
        ap_item = payload.get("ap_item")
        return dict(ap_item) if isinstance(ap_item, dict) else {}

    @classmethod
    def _task_payload_ap_item_ref(cls, task_payload: Dict[str, Any]) -> Optional[str]:
        payload = task_payload if isinstance(task_payload, dict) else {}
        ap_item = cls._task_payload_ap_item(payload)
        candidates = [
            payload.get("ap_item_id"),
            payload.get("entity_id"),
            ap_item.get("id") if ap_item else None,
            (payload.get("invoice") or {}).get("ap_item_id") if isinstance(payload.get("invoice"), dict) else None,
            (payload.get("invoice") or {}).get("gmail_id") if isinstance(payload.get("invoice"), dict) else None,
            (payload.get("invoice") or {}).get("thread_id") if isinstance(payload.get("invoice"), dict) else None,
            (payload.get("invoice") or {}).get("message_id") if isinstance(payload.get("invoice"), dict) else None,
        ]
        for candidate in candidates:
            token = str(candidate or "").strip()
            if token:
                return token
        return None

    def _resolve_task_ap_item(
        self,
        *,
        organization_id: str,
        payload: Dict[str, Any],
        db: Any,
    ) -> Dict[str, Any]:
        ap_item = self._task_payload_ap_item(payload)
        if ap_item:
            return ap_item
        ref = self._task_payload_ap_item_ref(payload)
        if not ref:
            return {}
        lookups = [
            getattr(db, "get_ap_item", None),
            getattr(db, "get_ap_item_by_thread", None),
            getattr(db, "get_ap_item_by_message_id", None),
            getattr(db, "get_ap_item_by_workflow_id", None),
        ]
        for lookup in lookups:
            if not callable(lookup):
                continue
            try:
                if lookup.__name__ == "get_ap_item":
                    item = lookup(ref)
                else:
                    item = lookup(organization_id, ref)
            except Exception:
                item = None
            if isinstance(item, dict) and item:
                return item
        return {}

    def _sync_task_run_memory(
        self,
        *,
        task: AgentTask,
        task_run: Dict[str, Any],
        event_type: str,
        db: Any,
        step_index: Optional[int] = None,
        tool_name: Optional[str] = None,
        output: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        try:
            from clearledgr.services.agent_memory import get_agent_memory_service

            payload = task.payload if isinstance(task.payload, dict) else {}
            ap_item = self._resolve_task_ap_item(
                organization_id=task.organization_id,
                payload=payload,
                db=db,
            )
            ap_item_id = str(
                ap_item.get("id")
                or self._task_payload_ap_item_ref(payload)
                or ""
            ).strip() or None
            memory = get_agent_memory_service(task.organization_id, db=db)
            memory.observe(
                skill_id=str(task.task_type or "planning_task").strip() or "planning_task",
                ap_item_id=ap_item_id,
                thread_id=str(ap_item.get("thread_id") or "").strip() or None,
                event_type=event_type,
                payload={
                    "task_run_id": task_run.get("id"),
                    "task_type": task_run.get("task_type") or task.task_type,
                    "status": task_run.get("status"),
                    "current_step": task_run.get("current_step"),
                    "correlation_id": task_run.get("correlation_id") or task.correlation_id,
                    "step_index": step_index,
                    "tool_name": tool_name,
                    "output": dict(output or {}),
                    "error": error,
                },
                channel="agent_runtime",
                actor_id="agent_runtime",
                correlation_id=task_run.get("correlation_id") or task.correlation_id,
                source="agent_runtime",
                summary=f"{event_type}:{task.task_type}",
            )
        except Exception as exc:
            logger.debug("[AgentRuntime] task-run memory sync skipped for %s: %s", event_type, exc)

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
        self._sync_task_run_memory(
            task=task,
            task_run=task_run,
            event_type="task_run_created",
            db=db,
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
                self._sync_task_run_memory(
                    task=task,
                    task_run=db.get_task_run(task_run_id) or task_run,
                    event_type="task_run_failed",
                    db=db,
                    error="max_execution_time_exceeded",
                )
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
                self._sync_task_run_memory(
                    task=task,
                    task_run=db.get_task_run(task_run_id) or task_run,
                    event_type="task_run_failed",
                    db=db,
                    error=str(exc),
                )
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
                    self._sync_task_run_memory(
                        task=task,
                        task_run=db.get_task_run(task_run_id) or task_run,
                        event_type="task_run_failed",
                        db=db,
                        error=error,
                    )
                    return SkillResult(
                        status="failed",
                        task_run_id=task_run_id,
                        outcome={"response": text, "steps": step},
                        step_count=step,
                        error=error,
                    )
                outcome = {"response": text, "steps": step}
                db.complete_task_run(task_run_id, outcome, status="completed")
                self._sync_task_run_memory(
                    task=task,
                    task_run=db.get_task_run(task_run_id) or task_run,
                    event_type="task_run_completed",
                    db=db,
                    output=outcome,
                )
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

            # §5.1 Rule 1: Pre-execution timeline write — record the action
            # BEFORE it executes. If we crash between here and execution,
            # the timeline shows "executing" which is enough to reconstruct.
            _pre_exec_timeline_id = None
            try:
                _ap_item_id = (
                    input_args.get("ap_item_id")
                    or input_args.get("box_id")
                    or task.metadata.get("ap_item_id")
                    if hasattr(task, "metadata") and isinstance(task.metadata, dict)
                    else None
                )
                if _ap_item_id and hasattr(db, "append_ap_audit_event"):
                    import uuid as _uuid
                    _pre_exec_timeline_id = f"TL-{_uuid.uuid4().hex[:12]}"
                    db.append_ap_audit_event({
                        "id": _pre_exec_timeline_id,
                        "ap_item_id": _ap_item_id,
                        "event_type": f"agent_action:{tool_name}:executing",
                        "actor_type": "agent",
                        "actor_id": "agent_planning_engine",
                        "organization_id": task.organization_id,
                        "payload_json": {
                            "action": tool_name,
                            "parameters": {k: str(v)[:100] for k, v in (input_args or {}).items()},
                            "status": "executing",
                            "step": step,
                        },
                    })
            except Exception:
                pass  # Non-fatal — don't block execution on timeline failure

            # Checkpoint BEFORE executing — if we crash here, we retry this step on resume
            try:
                db.update_task_run_step(
                    task_run_id, step, tool_name, input_args, {}, status="running"
                )
            except Exception as exc:
                logger.error("[AgentRuntime] Pre-exec checkpoint failed (step %d): %s", step, exc)
            self._sync_task_run_memory(
                task=task,
                task_run=db.get_task_run(task_run_id) or task_run,
                event_type="task_run_step_started",
                db=db,
                step_index=step,
                tool_name=tool_name,
            )

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

            # §5.1 Rule 1: Update pre-execution timeline entry with result
            try:
                if _pre_exec_timeline_id and _ap_item_id and hasattr(db, "append_ap_audit_event"):
                    _result_status = "completed" if output.get("ok", True) else "failed"
                    _result_summary = str(output.get("error", ""))[:200] if not output.get("ok", True) else "ok"
                    db.append_ap_audit_event({
                        "id": f"{_pre_exec_timeline_id}-result",
                        "ap_item_id": _ap_item_id,
                        "event_type": f"agent_action:{tool_name}:{_result_status}",
                        "actor_type": "agent",
                        "actor_id": "agent_planning_engine",
                        "organization_id": task.organization_id,
                        "payload_json": {
                            "action": tool_name,
                            "status": _result_status,
                            "result_summary": _result_summary,
                            "step": step,
                            "parent_timeline_id": _pre_exec_timeline_id,
                        },
                    })
            except Exception:
                pass  # Non-fatal

            # Checkpoint result
            try:
                db.update_task_run_step(
                    task_run_id, step + 1, tool_name, input_args, output, status=next_status
                )
            except Exception as exc:
                logger.error("[AgentRuntime] Post-exec checkpoint failed (step %d): %s", step, exc)
            self._sync_task_run_memory(
                task=task,
                task_run=db.get_task_run(task_run_id) or task_run,
                event_type="task_run_step_completed",
                db=db,
                step_index=step + 1,
                tool_name=tool_name,
                output=output,
            )

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
                self._sync_task_run_memory(
                    task=task,
                    task_run=db.get_task_run(task_run_id) or task_run,
                    event_type="task_run_awaiting_human",
                    db=db,
                    output=hitl_ctx,
                )
                return SkillResult(
                    status="awaiting_human",
                    task_run_id=task_run_id,
                    outcome=hitl_ctx,
                    step_count=step,
                    hitl_context=hitl_ctx,
                )

        # Exhausted max steps
        db.complete_task_run(task_run_id, {}, status="max_steps_exceeded")
        self._sync_task_run_memory(
            task=task,
            task_run=db.get_task_run(task_run_id) or task_run,
            event_type="task_run_max_steps_exceeded",
            db=db,
        )
        return SkillResult(
            status="max_steps_exceeded",
            task_run_id=task_run_id,
            outcome={},
            step_count=step,
        )

    # ------------------------------------------------------------------
    # Claude API (via LLM Gateway)
    # ------------------------------------------------------------------

    async def _call_claude_with_tools(
        self,
        system: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Call Claude via the LLM Gateway with tool_use enabled.

        The gateway handles retries, cost tracking, and logging.
        Returns the raw response dict (same shape as the Anthropic API).
        """
        from clearledgr.core.llm_gateway import get_llm_gateway, LLMAction

        gateway = get_llm_gateway()
        llm_resp = await gateway.call(
            LLMAction.AGENT_PLANNING,
            messages=messages,
            system_prompt=system,
            tools=tools,
            model_override=CLAUDE_MODEL,
        )
        return llm_resp.raw_response

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
                except Exception as exc:
                    logger.warning("Could not mark task_run %s as failed: %s", row.get("id", "?"), exc)
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
