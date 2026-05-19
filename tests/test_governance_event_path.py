"""Tests for Group 1 of the agent-runtime audit: governance wiring.

Two fixes covered:

1. ``CoordinationEngine.execute()`` now runs the doctrine + autonomy
   gate before risky financial writes (post_bill, schedule_payment,
   reverse_erp_post, freeze_vendor_payments) on the event-driven
   path. Previously only the synchronous skill path
   (``FinanceAgentLoopService.run_skill_request``) called
   ``build_deliberation``.

2. ``evaluate_doctrine`` now records gate statuses truthfully when
   ``autonomous_requested=False`` — failed gates show ``observe``
   instead of silently recording as ``pass``. Block logic is
   unchanged (still requires autonomous_requested + risky_action).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from solden.core import database as db_module  # noqa: E402
from solden.core.coordination_engine import CoordinationEngine  # noqa: E402
from solden.core.plan import Action, Plan  # noqa: E402
from solden.services import finance_agent_runtime as far  # noqa: E402
from solden.services.finance_agent_governance import (  # noqa: E402
    _RISKY_ACTIONS,
    evaluate_doctrine,
)


# ─── Group 1c: evaluate_doctrine audit recording ───────────────────


class TestEvaluateDoctrineAuditRecording:
    """Truthful gate status recording on non-autonomous calls.

    Previously: ``autonomous_requested=False`` made every gate but
    forbidden_actions silently record "pass" even when the gate was
    actually failing. Now they record "observe" so the audit row
    reflects reality. Block behavior is unchanged.
    """

    def _quality_snapshot(self, *, autonomous_allowed: bool, gates: Dict[str, str]) -> Dict[str, Any]:
        return {
            "autonomous_allowed": autonomous_allowed,
            "calibration": {"success_rate": 0.7},
            "gate_statuses": gates,
            "proof_status": "observe",
        }

    def _profile_with_required_gates(self, gates: list) -> Dict[str, Any]:
        return {
            "promotion_gate_status": {"required_gates": gates},
        }

    def test_promotion_gate_records_observe_when_non_autonomous_with_failures(self):
        """Failed promotion gates on a risky action with
        autonomous_requested=False should NOT silently record
        ``pass`` — they should record ``observe`` so the audit row
        reflects the actual gate state."""
        verdict = evaluate_doctrine(
            profile=self._profile_with_required_gates(["calibration"]),
            requested_action="post_to_erp",
            quality_snapshot=self._quality_snapshot(
                autonomous_allowed=True,
                gates={"calibration": "fail"},
            ),
            autonomous_requested=False,
        )
        promotion_check = next(
            c for c in verdict["checks"] if c["check"] == "promotion_gates"
        )
        assert promotion_check["status"] == "observe"
        # Block behavior unchanged: not autonomous → not blocked
        assert verdict["blocked"] is False

    def test_promotion_gate_blocks_on_autonomous_with_failures(self):
        verdict = evaluate_doctrine(
            profile=self._profile_with_required_gates(["calibration"]),
            requested_action="post_to_erp",
            quality_snapshot=self._quality_snapshot(
                autonomous_allowed=True,
                gates={"calibration": "fail"},
            ),
            autonomous_requested=True,
        )
        promotion_check = next(
            c for c in verdict["checks"] if c["check"] == "promotion_gates"
        )
        assert promotion_check["status"] == "fail"
        assert verdict["blocked"] is True
        assert any(c.startswith("missing_gate:") for c in verdict["reason_codes"])

    def test_autonomy_policy_records_observe_when_non_autonomous_with_failure(self):
        verdict = evaluate_doctrine(
            profile={},
            requested_action="post_to_erp",
            quality_snapshot=self._quality_snapshot(
                autonomous_allowed=False,
                gates={},
            ),
            autonomous_requested=False,
        )
        autonomy_check = next(
            c for c in verdict["checks"] if c["check"] == "autonomy_policy"
        )
        assert autonomy_check["status"] == "observe"
        assert verdict["blocked"] is False

    def test_autonomy_policy_blocks_on_autonomous_unmet(self):
        verdict = evaluate_doctrine(
            profile={},
            requested_action="post_to_erp",
            quality_snapshot=self._quality_snapshot(
                autonomous_allowed=False,
                gates={},
            ),
            autonomous_requested=True,
        )
        assert verdict["blocked"] is True
        assert "autonomy_not_earned" in verdict["reason_codes"]

    def test_belief_alignment_blocks_unconditionally_on_risky_misaligned(self):
        """belief_alignment is a STATE gate, not a risk gate. The
        belief surfaces unverified fields / open vendor questions /
        open recovery work; an operator's approval click doesn't
        clear that state, so authority can't bypass. Same shape as
        forbidden_actions: unconditional on risky actions."""
        verdict = evaluate_doctrine(
            profile={},
            requested_action="post_to_erp",
            quality_snapshot=self._quality_snapshot(
                autonomous_allowed=True,
                gates={},
            ),
            belief={"next_action": {"type": "human_field_review"}},
            autonomous_requested=False,
        )
        belief_check = next(
            c for c in verdict["checks"] if c["check"] == "belief_alignment"
        )
        assert belief_check["status"] == "fail"
        assert verdict["blocked"] is True
        assert "belief_requires:human_field_review" in verdict["reason_codes"]

    def test_belief_alignment_blocks_on_autonomous_misaligned(self):
        """Autonomous + risky + misaligned belief: same block as
        non-autonomous (state gate)."""
        verdict = evaluate_doctrine(
            profile={},
            requested_action="post_to_erp",
            quality_snapshot=self._quality_snapshot(
                autonomous_allowed=True,
                gates={},
            ),
            belief={"next_action": {"type": "await_vendor_info"}},
            autonomous_requested=True,
        )
        assert verdict["blocked"] is True
        assert "belief_requires:await_vendor_info" in verdict["reason_codes"]

    def test_belief_alignment_does_not_block_non_risky_actions(self):
        """The state gate only fires for risky actions. A non-risky
        action with a misaligned belief still passes."""
        verdict = evaluate_doctrine(
            profile={},
            requested_action="route_low_risk_for_approval",
            quality_snapshot=self._quality_snapshot(
                autonomous_allowed=True,
                gates={},
            ),
            belief={"next_action": {"type": "human_field_review"}},
            autonomous_requested=False,
        )
        belief_check = next(
            c for c in verdict["checks"] if c["check"] == "belief_alignment"
        )
        assert belief_check["status"] == "pass"
        assert verdict["blocked"] is False

    def test_forbidden_actions_blocks_unconditionally(self):
        verdict = evaluate_doctrine(
            profile={"forbidden_actions": ["post_to_erp"]},
            requested_action="post_to_erp",
            quality_snapshot=self._quality_snapshot(
                autonomous_allowed=True,
                gates={},
            ),
            autonomous_requested=False,
        )
        # forbidden_actions doesn't depend on autonomous_requested
        assert verdict["blocked"] is True
        assert "forbidden_action:post_to_erp" in verdict["reason_codes"]

    def test_clean_path_passes(self):
        verdict = evaluate_doctrine(
            profile={"promotion_gate_status": {"required_gates": ["calibration"]}},
            requested_action="post_to_erp",
            quality_snapshot=self._quality_snapshot(
                autonomous_allowed=True,
                gates={"calibration": "pass"},
            ),
            autonomous_requested=True,
        )
        assert verdict["blocked"] is False
        for check in verdict["checks"]:
            assert check["status"] in ("pass", "observe")


# ─── Group 1b: engine governance gate ──────────────────────────────


class TestEngineGovernanceGate:
    """The event-driven path runs governance for risky actions."""

    @pytest.fixture(autouse=True)
    def _reset_runtime_cache(self):
        far._reset_platform_finance_runtime_cache()
        yield
        far._reset_platform_finance_runtime_cache()

    @pytest.fixture()
    def db(self):
        inst = db_module.get_db()
        inst.initialize()
        inst.ensure_organization("orgGov", organization_name="Gov Test")
        return inst

    def _make_engine(self, db) -> CoordinationEngine:
        return CoordinationEngine(db=db, organization_id="orgGov")

    def _make_box(self, db, item_id: str = "AP-gov-1") -> dict:
        item = db.create_ap_item({
            "id": item_id,
            "organization_id": "orgGov",
            "vendor_name": "Vendor Z",
            "amount": 100.0,
            "currency": "USD",
            "invoice_number": f"INV-{item_id}",
            "state": "received",
        })
        return db.get_ap_item(item["id"])

    def test_non_risky_action_skips_governance(self, db):
        """Actions outside the gated set return None — no DB hits,
        no runtime build, no deliberation."""
        engine = self._make_engine(db)
        box = self._make_box(db)
        plan = Plan(event_type="email_received", actions=[], box_id=box["id"])
        action = Action("apply_label", "DET", {"label": "x"}, "test")

        verdict = engine._evaluate_governance_for_action(action, plan)
        assert verdict is None

    def test_risky_action_with_no_box_id_skips(self, db):
        engine = self._make_engine(db)
        plan = Plan(event_type="email_received", actions=[], box_id=None)
        action = Action("post_bill", "DET", {}, "test")

        verdict = engine._evaluate_governance_for_action(action, plan)
        assert verdict is None

    def test_risky_action_invokes_build_deliberation(self, db):
        engine = self._make_engine(db)
        box = self._make_box(db, "AP-gov-2")
        plan = Plan(event_type="email_received", actions=[], box_id=box["id"])
        action = Action("post_bill", "DET", {}, "test")

        called = {}

        def fake_build_deliberation(*, runtime, request, action, ap_item, belief, recall, profile):
            called["runtime_org"] = runtime.organization_id
            called["request_task_type"] = request.task_type
            called["action_token"] = action.action
            called["ap_item_id"] = ap_item.get("id")
            return {
                "should_execute": True,
                "doctrine": {"checks": [], "reason_codes": []},
            }

        with patch(
            "solden.services.finance_agent_governance.build_deliberation",
            side_effect=fake_build_deliberation,
        ):
            verdict = engine._evaluate_governance_for_action(action, plan)

        assert called["runtime_org"] == "orgGov"
        assert called["request_task_type"] == "post_bill"
        assert called["action_token"] == "post_to_erp"
        assert called["ap_item_id"] == box["id"]
        assert verdict["should_execute"] is True

    def test_engine_executes_when_governance_allows(self, db):
        """A post_bill plan whose deliberation returns should_execute=True
        proceeds to the actual handler."""
        engine = self._make_engine(db)
        box = self._make_box(db, "AP-gov-3")
        plan = Plan(
            event_type="email_received",
            actions=[Action("post_bill", "DET", {}, "test")],
            box_id=box["id"],
        )

        # Stub the actual post handler so we don't need ERP creds.
        async def fake_post_bill(action, plan):
            return {"ok": True, "erp_reference": "EXT-1"}

        engine._handlers["post_bill"] = fake_post_bill

        with patch.object(
            engine, "_evaluate_governance_for_action",
            return_value={"should_execute": True, "doctrine": {}},
        ):
            result = asyncio.run(engine.execute(plan))

        assert result.status == "completed"

    def test_engine_blocks_and_records_audit_when_governance_vetoes(self, db):
        """A post_bill plan whose deliberation returns should_execute=False
        does not run the handler, records governance audit row, and
        returns failed."""
        engine = self._make_engine(db)
        box = self._make_box(db, "AP-gov-4")
        plan = Plan(
            event_type="email_received",
            actions=[Action("post_bill", "DET", {}, "test")],
            box_id=box["id"],
        )

        post_bill_called = {"hit": False}

        async def fake_post_bill(action, plan):
            post_bill_called["hit"] = True
            return {"ok": True}

        engine._handlers["post_bill"] = fake_post_bill

        # Avoid the cascading exception flow in the test (it touches
        # Slack / LLM / labels which need real adapters).
        async def fake_exception_flow(plan, ctx, match_result):
            return None

        engine._run_exception_flow = fake_exception_flow  # type: ignore[method-assign]

        veto_verdict = {
            "should_execute": False,
            "stop_reason": "Doctrine blocked execution until: autonomy_not_earned",
            "doctrine": {
                "checks": [{"check": "autonomy_policy", "status": "fail"}],
                "reason_codes": ["autonomy_not_earned"],
            },
            "confidence": 0.4,
        }

        with patch.object(
            engine, "_evaluate_governance_for_action",
            return_value=veto_verdict,
        ):
            result = asyncio.run(engine.execute(plan))

        assert result.status == "failed"
        assert "governance_blocked" in (result.error or "")
        assert post_bill_called["hit"] is False, (
            "post_bill handler must not run when governance vetoes"
        )

        # Check the governance-block audit row landed. The metadata
        # I passed to append_audit_event lands inside payload_json
        # on the persisted row (see ap_store.append_audit_event:1946-1954).
        events = db.list_ap_audit_events(box["id"], limit=20)
        block_rows = [
            e for e in events
            if e.get("event_type") == "agent_action_blocked_by_governance"
        ]
        assert len(block_rows) >= 1
        payload = block_rows[0].get("payload_json") or block_rows[0].get("metadata") or {}
        assert payload.get("action") == "post_bill"
        assert payload.get("stop_reason")
        assert "autonomy_not_earned" in (payload.get("reason_codes") or [])
        # Structured governance_verdict column captures the veto.
        assert block_rows[0].get("governance_verdict") == "vetoed"

    def test_all_listed_risky_actions_are_in_governance_risky_set(self):
        """Drift fence: the engine's gated-action map must use action
        tokens the governance module actually treats as risky."""
        from solden.core.coordination_engine import _GOVERNANCE_GATED_ACTIONS

        for engine_action, governance_token in _GOVERNANCE_GATED_ACTIONS.items():
            assert governance_token in _RISKY_ACTIONS, (
                f"{engine_action} -> {governance_token} not in _RISKY_ACTIONS; "
                f"governance gate would never fire"
            )
