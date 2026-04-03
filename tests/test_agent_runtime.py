"""Tests for the agent planning engine loop, skills abstraction, and task durability.

Follows existing test patterns:
- tmp_path DB via monkeypatch.setenv("CLEARLEDGR_DB_PATH", ...)
- Reset _DB_INSTANCE in teardown (handled by conftest.reset_service_singletons)
- asyncio.run() wrapping (same pattern as test_invoice_workflow_controls.py)
- Mock _call_claude_with_tools to avoid real API calls
"""
import asyncio
import json
import uuid
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest

import clearledgr.core.database as db_module
from clearledgr.core.agent_runtime import AgentPlanningEngine, MAX_PLANNING_STEPS
from clearledgr.core.skills.base import AgentTask, AgentTool, FinanceSkill, SkillResult
from clearledgr.core.skills.ap_skill import APSkill


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_task(task_type: str = "test_skill", org: str = "test-org", key: str = None) -> AgentTask:
    return AgentTask(
        task_type=task_type,
        organization_id=org,
        payload={"foo": "bar"},
        idempotency_key=key,
    )


class _EchoSkill(FinanceSkill):
    """Minimal skill for testing — one tool that echoes its input."""

    @property
    def skill_name(self) -> str:
        return "echo_skill"

    def get_tools(self) -> List[AgentTool]:
        async def _echo(message: str = "", organization_id: str = "default", **_) -> Dict:
            return {"ok": True, "echoed": message}

        return [
            AgentTool(
                name="echo",
                description="Echo the message back.",
                input_schema={
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                    "required": ["message"],
                },
                handler=_echo,
            )
        ]

    def build_system_prompt(self, task: AgentTask) -> str:
        return "Echo skill system prompt."


def _tool_use_response(name: str, input_args: Dict, call_id: str = None) -> Dict:
    """Synthetic Anthropic tool_use response."""
    return {
        "content": [{
            "type": "tool_use",
            "id": call_id or f"tu_{name}",
            "name": name,
            "input": input_args,
        }]
    }


def _text_response(text: str = "Done.") -> Dict:
    """Synthetic Anthropic text response (no tool call = loop terminates)."""
    return {"content": [{"type": "text", "text": text}]}


# ---------------------------------------------------------------------------
# Test 1: skill registration (sync)
# ---------------------------------------------------------------------------

def test_skill_registration():
    runtime = AgentPlanningEngine()
    skill = _EchoSkill()
    runtime.register_skill(skill)
    assert "echo_skill" in runtime._skills
    assert runtime._skills["echo_skill"] is skill


def test_ap_skill_registration():
    runtime = AgentPlanningEngine()
    ap = APSkill("default")
    runtime.register_skill(ap)
    assert "ap_invoice_processing" in runtime._skills
    tools = ap.get_tools()
    tool_names = {t.name for t in tools}
    assert {"enrich_with_context", "run_validation_gate", "get_ap_decision", "execute_routing", "request_vendor_info", "verify_erp_posting", "check_payment_readiness", "resolve_exception"} == tool_names


def test_ap_skill_get_ap_decision_handles_sync_decider_without_fallback():
    ap = APSkill("default")
    decision_tool = next(t for t in ap.get_tools() if t.name == "get_ap_decision")

    class _FakeDB:
        def get_vendor_profile(self, *_args, **_kwargs):
            return {}

        def get_vendor_invoice_history(self, *_args, **_kwargs):
            return []

        def get_vendor_decision_feedback(self, *_args, **_kwargs):
            return {}

    class _FakeDecisionService:
        def decide(self, **_kwargs):
            return SimpleNamespace(
                recommendation="approve",
                reasoning="Looks good.",
                confidence=0.97,
                risk_flags=[],
                info_needed=None,
            )

    invoice_payload = {
        "gmail_id": "thread-agent-1",
        "subject": "Invoice INV-1001",
        "sender": "billing@example.com",
        "vendor_name": "Acme Corp",
        "amount": 100.0,
        "currency": "USD",
        "invoice_number": "INV-1001",
        "due_date": "2026-03-10",
    }

    with patch("clearledgr.core.database.get_db", return_value=_FakeDB()):
        with patch("clearledgr.services.ap_decision.APDecisionService", return_value=_FakeDecisionService()):
            result = asyncio.run(
                decision_tool.handler(
                    invoice_payload=invoice_payload,
                    vendor_context={},
                    organization_id="default",
                )
            )

    assert result["ok"] is True
    assert result["recommendation"] == "approve"
    assert result["confidence"] == pytest.approx(0.97)
    assert result.get("error") is None


# ---------------------------------------------------------------------------
# Test 2: planning loop — single tool then done
# ---------------------------------------------------------------------------

def test_planning_loop_single_tool_then_done(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "test.db"))
    db_module._DB_INSTANCE = None

    runtime = AgentPlanningEngine()
    skill = _EchoSkill()
    runtime.register_skill(skill)

    # Mock: first call → tool_use, second call → text (done)
    call_responses = [
        _tool_use_response("echo", {"message": "hello"}),
        _text_response("Task complete."),
    ]

    async def fake_claude(system, messages, tools):
        return call_responses.pop(0)

    with patch.object(runtime, "_call_claude_with_tools", side_effect=fake_claude):
        task = _make_task("echo_skill", key="test-single-tool")
        result = asyncio.run(runtime.run_task(task))

    assert result.status == "completed"
    assert result.step_count == 1
    assert "response" in result.outcome

    # Verify DB row
    from clearledgr.core.database import get_db
    row = get_db().get_task_run(result.task_run_id)
    assert row is not None
    assert row["status"] == "completed"


# ---------------------------------------------------------------------------
# Test 3: planning loop — multiple tool calls
# ---------------------------------------------------------------------------

def test_planning_loop_three_steps(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "test.db"))
    db_module._DB_INSTANCE = None

    runtime = AgentPlanningEngine()
    skill = _EchoSkill()
    runtime.register_skill(skill)

    responses = [
        _tool_use_response("echo", {"message": "step1"}, "id_1"),
        _tool_use_response("echo", {"message": "step2"}, "id_2"),
        _text_response("All done."),
    ]

    async def fake_claude(system, messages, tools):
        return responses.pop(0)

    with patch.object(runtime, "_call_claude_with_tools", side_effect=fake_claude):
        result = asyncio.run(runtime.run_task(_make_task("echo_skill", key="test-multi-step")))

    assert result.status == "completed"
    assert result.step_count == 2


# ---------------------------------------------------------------------------
# Test 4: planning loop rejects no-tool completion on first step
# ---------------------------------------------------------------------------

def test_planning_loop_rejects_no_tool_completion(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "test.db"))
    db_module._DB_INSTANCE = None

    runtime = AgentPlanningEngine()
    skill = _EchoSkill()
    runtime.register_skill(skill)

    async def fake_claude(system, messages, tools):
        return _text_response("done without tools")

    with patch.object(runtime, "_call_claude_with_tools", side_effect=fake_claude):
        result = asyncio.run(runtime.run_task(_make_task("echo_skill", key="no-tool-key")))

    assert result.status == "failed"
    assert result.error == "planning_returned_no_tool_use"


# ---------------------------------------------------------------------------
# Test 4: task run idempotency
# ---------------------------------------------------------------------------

def test_task_run_idempotency(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "test.db"))
    db_module._DB_INSTANCE = None

    runtime = AgentPlanningEngine()
    skill = _EchoSkill()
    runtime.register_skill(skill)

    call_responses = [
        _tool_use_response("echo", {"message": "idempotent"}),
        _text_response("done"),
    ]

    async def fake_claude(system, messages, tools):
        return call_responses.pop(0)

    with patch.object(runtime, "_call_claude_with_tools", side_effect=fake_claude):
        r1 = asyncio.run(runtime.run_task(_make_task("echo_skill", key="idempotent-key")))

    async def fake_claude2(system, messages, tools):
        return _text_response("done again")

    with patch.object(runtime, "_call_claude_with_tools", side_effect=fake_claude2):
        r2 = asyncio.run(runtime.run_task(_make_task("echo_skill", key="idempotent-key")))

    # Same task_run_id means the same DB row was returned (idempotent)
    assert r1.task_run_id == r2.task_run_id

    # Only one row in DB for this idempotency key
    from clearledgr.core.database import get_db
    rows = get_db().list_pending_task_runs(statuses=("completed",))
    assert len([r for r in rows if r.get("idempotency_key") == "idempotent-key"]) == 1


# ---------------------------------------------------------------------------
# Test 5: resume from checkpoint
# ---------------------------------------------------------------------------

def test_resume_from_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "test.db"))
    db_module._DB_INSTANCE = None

    from clearledgr.core.database import get_db
    db = get_db()
    db.initialize()

    # Manually insert a task_run that was interrupted at step 2
    task_run_id = str(uuid.uuid4())
    step_results = {
        "0": {"tool": "echo", "input": {"message": "step0"}, "output": {"ok": True, "echoed": "step0"}, "at": "2026-01-01"},
        "1": {"tool": "echo", "input": {"message": "step1"}, "output": {"ok": True, "echoed": "step1"}, "at": "2026-01-01"},
    }
    db.create_task_run(
        id=task_run_id,
        org_id="test-org",
        task_type="echo_skill",
        input_payload=json.dumps({"foo": "bar"}),
        idempotency_key="resume-test-key",
    )
    # Simulate interrupted state at step 2
    with db.connect() as conn:
        cur = conn.cursor()
        sql = db._prepare_sql(
            "UPDATE task_runs SET current_step=2, step_results=?, status='running' WHERE id=?"
        )
        cur.execute(sql, (json.dumps(step_results), task_run_id))
        conn.commit()

    runtime = AgentPlanningEngine()
    skill = _EchoSkill()
    runtime.register_skill(skill)

    captured_messages: List[List] = []

    async def fake_claude(system, messages, tools):
        captured_messages.append(list(messages))
        return _text_response("resumed and done")

    task_run = db.get_task_run(task_run_id)
    task = AgentTask(
        task_type="echo_skill",
        organization_id="test-org",
        payload={"foo": "bar"},
        idempotency_key="resume-test-key",
    )

    with patch.object(runtime, "_call_claude_with_tools", side_effect=fake_claude):
        result = asyncio.run(runtime._planning_loop(task, skill, task_run))

    assert result.status == "completed"
    # Claude was called once (step 2 → text done)
    assert len(captured_messages) == 1
    msgs = captured_messages[0]
    # 1 opening user msg + 2 tool_use/tool_result pairs replayed from checkpoint = 5 msgs
    assert len(msgs) == 1 + 2 * 2


# ---------------------------------------------------------------------------
# Test 6: HITL pause
# ---------------------------------------------------------------------------

def test_hitl_pause(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "test.db"))
    db_module._DB_INSTANCE = None

    runtime = AgentPlanningEngine()

    # Skill whose one tool triggers HITL
    async def _hitl_tool(organization_id="default", **_) -> Dict:
        return {
            "ok": True,
            "is_awaiting_human": True,
            "hitl_context": {"question": "Please approve this invoice", "invoice_id": "inv-123"},
        }

    class _HitlSkill(FinanceSkill):
        @property
        def skill_name(self):
            return "hitl_skill"

        def get_tools(self):
            return [AgentTool("pause", "Trigger HITL.", {"type": "object", "properties": {}}, _hitl_tool)]

        def build_system_prompt(self, task):
            return "HITL skill prompt."

    runtime.register_skill(_HitlSkill())

    call_count = 0

    async def fake_claude(system, messages, tools):
        nonlocal call_count
        call_count += 1
        return _tool_use_response("pause", {})

    with patch.object(runtime, "_call_claude_with_tools", side_effect=fake_claude):
        result = asyncio.run(runtime.run_task(_make_task("hitl_skill", key="hitl-test")))

    assert result.status == "awaiting_human"
    assert result.hitl_context is not None
    assert result.hitl_context.get("question") == "Please approve this invoice"
    assert call_count == 1  # loop stopped after HITL

    from clearledgr.core.database import get_db
    row = get_db().get_task_run(result.task_run_id)
    assert row["status"] == "awaiting_human"


# ---------------------------------------------------------------------------
# Test 7: max steps exceeded
# ---------------------------------------------------------------------------

def test_max_steps_exceeded(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "test.db"))
    db_module._DB_INSTANCE = None

    runtime = AgentPlanningEngine()
    skill = _EchoSkill()
    runtime.register_skill(skill)

    call_count = 0

    async def always_tool_use(system, messages, tools):
        nonlocal call_count
        call_count += 1
        return _tool_use_response("echo", {"message": f"step {call_count}"}, f"id_{call_count}")

    with patch.object(runtime, "_call_claude_with_tools", side_effect=always_tool_use):
        result = asyncio.run(runtime.run_task(_make_task("echo_skill", key="max-steps-test")))

    assert result.status == "max_steps_exceeded"
    assert call_count == MAX_PLANNING_STEPS

    from clearledgr.core.database import get_db
    row = get_db().get_task_run(result.task_run_id)
    assert row["status"] == "max_steps_exceeded"


# ---------------------------------------------------------------------------
# Test 8: planner requires Anthropic API key (no fake completion fallback)
# ---------------------------------------------------------------------------

def test_call_claude_requires_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    runtime = AgentPlanningEngine()

    with pytest.raises(RuntimeError, match="anthropic_api_key_missing"):
        asyncio.run(
            runtime._call_claude_with_tools(
                system="test",
                messages=[],
                tools=[],
            )
        )


# ---------------------------------------------------------------------------
# Test 9: TaskStore — basic CRUD (sync)
# ---------------------------------------------------------------------------

def test_task_store_crud(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "test.db"))
    db_module._DB_INSTANCE = None

    from clearledgr.core.database import get_db
    db = get_db()
    db.initialize()

    run_id = str(uuid.uuid4())
    row = db.create_task_run(
        id=run_id,
        org_id="org-a",
        task_type="test",
        input_payload='{"x": 1}',
        idempotency_key="crud-test",
    )
    assert row["id"] == run_id
    assert row["status"] == "pending"

    # Update step
    db.update_task_run_step(run_id, 1, "my_tool", {"arg": "val"}, {"result": "ok"}, "running")
    updated = db.get_task_run(run_id)
    assert updated["current_step"] == 1
    assert updated["status"] == "running"

    step_results = json.loads(updated["step_results"])
    assert "1" in step_results
    assert step_results["1"]["tool"] == "my_tool"

    # Complete
    db.complete_task_run(run_id, {"final_answer": 42}, status="completed")
    final = db.get_task_run(run_id)
    assert final["status"] == "completed"
    final_steps = json.loads(final["step_results"])
    assert final_steps["final"]["final_answer"] == 42

    # list_pending returns nothing for completed task
    pending = db.list_pending_task_runs(statuses=("pending", "running"))
    assert all(r["id"] != run_id for r in pending)
