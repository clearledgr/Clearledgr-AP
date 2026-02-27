"""Core contracts for the Clearledgr finance agent skills layer.

AgentTool    — a single callable tool Claude can select during a planning loop.
AgentTask    — the input to the runtime: what to do and with what payload.
SkillResult  — the output: final outcome + structured artifact.
FinanceSkill — ABC for a finance domain module (AP, AR, Close, etc.).

No I/O in this module — pure Python dataclasses + ABC.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional


@dataclass
class AgentTool:
    """A single tool Claude can choose to call during a planning loop.

    Attributes:
        name: Snake-case identifier sent to Claude (e.g. "validate_invoice").
        description: One-sentence description Claude sees in the tool catalogue.
        input_schema: Anthropic-format JSON Schema dict for the tool parameters.
        handler: Async callable that NEVER raises — returns {"ok": False, "error": "..."}
            on failure. Signature: async (**kwargs) -> Dict[str, Any].
    """

    name: str
    description: str
    input_schema: Dict[str, Any]
    handler: Callable[..., Coroutine[Any, Any, Dict[str, Any]]]

    def to_claude_spec(self) -> Dict[str, Any]:
        """Return the dict Anthropic's tool-use API expects."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


@dataclass
class AgentTask:
    """The runtime's input: a unit of work to be done by a registered skill.

    Attributes:
        task_type: Maps to a registered FinanceSkill.skill_name.
        organization_id: Tenant scope.
        payload: Arbitrary input data for the skill (serialised to JSON in DB).
        idempotency_key: Optional stable key — prevents duplicate task creation.
        correlation_id: Optional upstream correlation ID for audit linkage.
    """

    task_type: str
    organization_id: str
    payload: Dict[str, Any]
    idempotency_key: Optional[str] = None
    correlation_id: Optional[str] = None


@dataclass
class SkillResult:
    """The runtime's final output after the planning loop completes.

    Attributes:
        status: "completed" | "hitl_pause" | "failed" | "max_steps_exceeded"
        task_run_id: Primary key of the task_runs DB row.
        outcome: Skill-specific result dict.
        step_count: How many tool calls were made.
        hitl_context: Set when status=="hitl_pause" — describes what human action is needed.
        error: Set when status=="failed".
    """

    status: str
    task_run_id: str
    outcome: Dict[str, Any]
    step_count: int = 0
    hitl_context: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class FinanceSkill(abc.ABC):
    """Abstract base class for a finance domain skill module.

    A skill knows:
    1. What tools Claude can call (get_tools).
    2. How to build the system prompt that opens the planning loop.
    3. What task_type name it handles (skill_name property).

    Tools call existing service methods — they do not re-implement logic.
    """

    @property
    @abc.abstractmethod
    def skill_name(self) -> str:
        """Unique identifier matched against AgentTask.task_type."""

    @abc.abstractmethod
    def get_tools(self) -> List[AgentTool]:
        """Return all tools available during the planning loop for this skill."""

    @abc.abstractmethod
    def build_system_prompt(self, task: AgentTask) -> str:
        """Return the system-level instruction that opens the planning loop."""
