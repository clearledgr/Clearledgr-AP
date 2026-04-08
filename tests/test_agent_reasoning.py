from __future__ import annotations

import clearledgr.core.database as db_module
from clearledgr.core.database import get_db
from clearledgr.services.agent_memory import AgentMemoryService
from clearledgr.services.agent_reasoning import AgentReasoningService, ReasoningFactor


def test_agent_reasoning_uses_persisted_profile_thresholds(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "agent-reasoning.db"))
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    db_module._DB_INSTANCE = None

    db = get_db()
    memory = AgentMemoryService("test-org", db=db)
    memory.ensure_profile(
        skill_id="ap_v1",
        profile_overrides={
            "risk_posture": "bounded_autonomy",
            "autonomy_level": "assisted",
        },
    )

    agent = AgentReasoningService("test-org")

    decision, summary = agent._make_decision(
        0.97,
        [
            ReasoningFactor(
                factor="extraction_confidence",
                score=0.97,
                detail="High-confidence extraction",
            )
        ],
        [],
        profile=agent.profile,
    )

    assert agent.profile["risk_posture"] == "bounded_autonomy"
    assert decision == "send_for_approval"
    assert "Needs approval" in summary
