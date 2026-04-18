"""Box invariant tests — the state+audit atomicity and Rule 1
fail-closed behaviour the business rests on.

DESIGN_THESIS.md §7.6: "The audit trail is not a compliance
feature — it is the evidence that the system is trustworthy."
A Box that reaches a new state with no matching audit row, or
an agent action that runs with no pre-write timeline entry, is
the failure mode the thesis says must not happen. These tests
encode that contract so a future refactor can't silently remove
the guarantee.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestVendorOnboardingAtomicTransition:
    """§7.6 — vendor onboarding state UPDATE and audit INSERT
    share one transaction. If either fails, neither commits.
    """

    def _fresh_db_with_session(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "vo.db"))
        monkeypatch.delenv("DATABASE_URL", raising=False)
        import clearledgr.core.database as db_module
        db_module._DB_INSTANCE = None
        db = db_module.get_db()
        db.initialize()

        # Seed one vendor onboarding session
        session = db.create_vendor_onboarding_session(
            organization_id="test-org",
            vendor_name="Acme Inc",
            invited_by="ap@test-org",
        )
        return db, session

    def test_happy_path_writes_both_state_and_audit(self, tmp_path, monkeypatch):
        db, session = self._fresh_db_with_session(tmp_path, monkeypatch)
        session_id = session["id"]

        updated = db.transition_onboarding_session_state(
            session_id, target_state="kyc", actor_id="agent",
        )
        assert updated is not None
        assert updated["state"] == "kyc"

        # Exactly one transition audit event should exist for this
        # session — written from inside the same transaction as the
        # state UPDATE.
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                db._prepare_sql(
                    "SELECT COUNT(*) FROM audit_events "
                    "WHERE event_type = ? "
                    "  AND payload_json LIKE ?"
                ),
                ("vendor_onboarding_state_transition", f'%"session_id": "{session_id}"%'),
            )
            row = cur.fetchone()
            audit_count = row[0] if not hasattr(row, "__getitem__") else row[0]
        assert audit_count == 1

    def test_audit_insert_failure_rolls_back_state_update(self, tmp_path, monkeypatch):
        db, session = self._fresh_db_with_session(tmp_path, monkeypatch)
        session_id = session["id"]
        original_state = session["state"]

        # Force the audit payload serialisation inside the
        # transition to raise. That runs AFTER the state UPDATE
        # statement has been dispatched on the cursor but BEFORE
        # conn.commit() — so the transaction is rolled back by the
        # `with self.connect() as conn` exit. Whatever the state
        # UPDATE did must vanish.
        import clearledgr.core.stores.vendor_store as vs_mod
        real_json_dumps = vs_mod.json.dumps
        call_count = {"n": 0}

        def _flaky_dumps(obj, *args, **kwargs):
            call_count["n"] += 1
            # The transition calls json.dumps at least once for the
            # metadata patch (if any) and once for the audit
            # payload. The audit payload carries session_id + from/
            # to state — target that specific call shape so we only
            # fail the audit INSERT's serialisation, not the state
            # UPDATE's metadata dump.
            if isinstance(obj, dict) and obj.get("session_id") == session_id:
                raise RuntimeError("simulated audit serialisation failure")
            return real_json_dumps(obj, *args, **kwargs)

        monkeypatch.setattr(vs_mod.json, "dumps", _flaky_dumps)

        result = db.transition_onboarding_session_state(
            session_id, target_state="kyc", actor_id="agent",
        )
        # Transition returns None on the rollback path.
        assert result is None

        # Verify state did NOT change.
        current = db.get_onboarding_session_by_id(session_id)
        assert current["state"] == original_state, (
            f"Box state changed despite audit-insert failure — "
            f"expected rollback to {original_state}, got {current['state']}"
        )

        # And no audit row exists for the attempted transition.
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                db._prepare_sql(
                    "SELECT COUNT(*) FROM audit_events "
                    "WHERE event_type = ? "
                    "  AND payload_json LIKE ?"
                ),
                ("vendor_onboarding_state_transition", f'%"session_id": "{session_id}"%'),
            )
            row = cur.fetchone()
            audit_count = row[0] if not hasattr(row, "__getitem__") else row[0]
        assert audit_count == 0


class TestRule1PreWriteFailsClosed:
    """§7.6 — an agent action does NOT run if the Rule 1 pre-write
    audit INSERT cannot land. Previously this failed open (log
    warning, execute anyway) which meant an ERP post could happen
    without a corresponding timeline entry. Now it fails closed —
    retries three times, then aborts the plan.
    """

    def _make_engine(self, append_side_effect):
        from clearledgr.core.coordination_engine import CoordinationEngine
        engine = CoordinationEngine.__new__(CoordinationEngine)
        engine.organization_id = "test-org"
        engine.db = MagicMock()
        engine.db.append_audit_event = MagicMock(side_effect=append_side_effect)
        return engine

    def test_pre_write_success_returns_timeline_id_on_first_try(self):
        engine = self._make_engine(append_side_effect=None)
        from clearledgr.core.plan import Action
        action = Action(name="classify_email", layer="LLM", params={}, description="test")

        timeline_id = engine._pre_write("ap-123", action, step=0)
        assert timeline_id.startswith("TL-")
        assert engine.db.append_audit_event.call_count == 1

    def test_pre_write_retries_then_succeeds(self):
        # Fail twice, succeed on third attempt.
        calls = {"n": 0}

        def _flaky(_payload):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("transient DB blip")
            return None

        engine = self._make_engine(append_side_effect=_flaky)
        from clearledgr.core.plan import Action
        action = Action(name="classify_email", layer="LLM", params={}, description="test")

        timeline_id = engine._pre_write("ap-123", action, step=0)
        assert timeline_id.startswith("TL-")
        assert calls["n"] == 3

    def test_pre_write_raises_after_three_failures(self):
        from clearledgr.core.coordination_engine import _Rule1PreWriteFailed
        engine = self._make_engine(append_side_effect=RuntimeError("persistent DB down"))
        from clearledgr.core.plan import Action
        action = Action(name="post_bill", layer="DET", params={}, description="test")

        with pytest.raises(_Rule1PreWriteFailed) as exc_info:
            engine._pre_write("ap-123", action, step=0)

        assert exc_info.value.action_name == "post_bill"
        assert exc_info.value.box_id == "ap-123"
        assert isinstance(exc_info.value.original, RuntimeError)
        assert engine.db.append_audit_event.call_count == 3

    def test_pre_write_with_no_box_id_is_noop(self):
        # Free-floating actions without a Box target don't write
        # timeline entries. Still returns a timeline id so post_write
        # calls stay well-formed.
        engine = self._make_engine(append_side_effect=None)
        from clearledgr.core.plan import Action
        action = Action(name="fetch_attachment", layer="DET", params={}, description="test")

        timeline_id = engine._pre_write(None, action, step=0)
        assert timeline_id.startswith("TL-")
        assert engine.db.append_audit_event.call_count == 0


class TestExecutionLoopAbortsOnRule1Failure:
    """End-to-end: execution engine aborts the plan when Rule 1
    pre-write fails, moves the Box to exception, and returns a
    failed CoordinationResult carrying the rule1 error code. The side-
    effect action does NOT run.
    """

    @pytest.mark.asyncio
    async def test_plan_aborts_without_running_action_body(self):
        from clearledgr.core.coordination_engine import CoordinationEngine
        from clearledgr.core.plan import Action, Plan

        engine = CoordinationEngine.__new__(CoordinationEngine)
        engine.organization_id = "test-org"
        engine.db = MagicMock()
        engine.db.append_audit_event = MagicMock(
            side_effect=RuntimeError("audit unavailable"),
        )
        # update_ap_item is the path _move_to_exception calls; stub
        # it so the rollback-to-exception step doesn't blow up on a
        # missing row. State write failure here is not the subject
        # of this test; the subject is: the action body did not run.
        engine.db.update_ap_item = MagicMock(return_value=True)
        engine.db.get_ap_item = MagicMock(return_value={"state": "received"})
        engine._workflow = None
        engine._ctx = {}

        # Register a handler that would throw if called — proving
        # the abort short-circuits before the action body runs.
        handler_called = {"n": 0}

        async def _handler(action, plan):
            handler_called["n"] += 1
            return {"ok": True}

        engine._handlers = {"post_bill": _handler}

        plan = Plan(
            event_type="approval_received",
            actions=[Action(name="post_bill", layer="DET", params={}, description="test")],
            box_id="ap-456",
        )

        result = await engine.execute(plan)
        assert result.status == "failed"
        assert "rule1_pre_write_failed" in (result.error or "")
        # Action body never ran — the pre-write abort short-
        # circuited before _execute_action was called.
        assert handler_called["n"] == 0


class TestBoxKeyedAuditWrites:
    """Phase 3a — every audit INSERT populates (box_id, box_type).
    Before the refactor, vendor_onboarding rows carried ap_item_id=''
    and nothing else identifying the Box. Post-refactor, both AP and
    vendor-onboarding rows carry a non-null box_id + correct box_type.
    """

    def _fresh_db(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "boxkeys.db"))
        monkeypatch.delenv("DATABASE_URL", raising=False)
        import clearledgr.core.database as db_module
        db_module._DB_INSTANCE = None
        db = db_module.get_db()
        db.initialize()
        return db

    def test_ap_state_transition_writes_box_keys(self, tmp_path, monkeypatch):
        db = self._fresh_db(tmp_path, monkeypatch)
        db.create_ap_item({
            "id": "ap-box-1",
            "invoice_key": "inv-ap-box-1",
            "thread_id": "t-box-1",
            "state": "received",
            "organization_id": "test-org",
            "vendor_name": "Acme",
            "amount": 100.0,
        })
        # Valid transition: received → validated.
        db.update_ap_item("ap-box-1", state="validated")

        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                db._prepare_sql(
                    "SELECT box_id, box_type FROM audit_events "
                    "WHERE box_id = ? AND box_type = 'ap_item' "
                    "  AND event_type = 'state_transition'"
                ),
                ("ap-box-1",),
            )
            row = cur.fetchone()
        assert row is not None
        box_id = row[0] if not hasattr(row, "keys") else dict(row)["box_id"]
        box_type = row[1] if not hasattr(row, "keys") else dict(row)["box_type"]
        assert box_id == "ap-box-1"
        assert box_type == "ap_item"

    def test_vendor_transition_writes_session_id_as_box_id(self, tmp_path, monkeypatch):
        db = self._fresh_db(tmp_path, monkeypatch)
        session = db.create_vendor_onboarding_session(
            organization_id="test-org",
            vendor_name="Acme",
            invited_by="ap@test-org",
        )
        session_id = session["id"]

        db.transition_onboarding_session_state(
            session_id, target_state="kyc", actor_id="agent",
        )

        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                db._prepare_sql(
                    "SELECT box_id, box_type FROM audit_events "
                    "WHERE event_type = ? AND new_state = 'kyc'"
                ),
                ("vendor_onboarding_state_transition",),
            )
            row = cur.fetchone()
        assert row is not None
        if hasattr(row, "keys"):
            r = dict(row)
            box_id = r["box_id"]
            box_type = r["box_type"]
        else:
            box_id, box_type = row[0], row[1]
        assert box_id == session_id
        assert box_type == "vendor_onboarding_session"

    def test_funnel_ap_shortcut_populates_box_fields(self, tmp_path, monkeypatch):
        """``append_audit_event`` accepts the AP-convenience
        ``ap_item_id`` kwarg: it's treated as the box_id for type
        ``ap_item``. Both columns land on the row.
        """
        db = self._fresh_db(tmp_path, monkeypatch)
        evt = db.append_audit_event({
            "ap_item_id": "ap-funnel-99",
            "event_type": "test_event",
            "actor_type": "agent",
            "actor_id": "test",
            "organization_id": "test-org",
        })
        assert evt is not None

        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                db._prepare_sql(
                    "SELECT box_id, box_type FROM audit_events "
                    "WHERE box_id = ? AND box_type = 'ap_item'"
                ),
                ("ap-funnel-99",),
            )
            row = cur.fetchone()
        assert row is not None
        box_id = row[0] if not hasattr(row, "keys") else dict(row)["box_id"]
        box_type = row[1] if not hasattr(row, "keys") else dict(row)["box_type"]
        assert box_id == "ap-funnel-99"
        assert box_type == "ap_item"

    def test_funnel_requires_box_id_or_ap_item_id(self, tmp_path, monkeypatch):
        """Writing an audit event without any Box identifier is a
        hard error — the ledger needs to know which Box an event
        belongs to.
        """
        db = self._fresh_db(tmp_path, monkeypatch)
        with pytest.raises(ValueError):
            db.append_audit_event({
                "event_type": "test_event",
                "actor_type": "agent",
                "actor_id": "test",
                "organization_id": "test-org",
            })

    def test_llm_gateway_log_call_writes_box_keys(self, tmp_path, monkeypatch):
        db = self._fresh_db(tmp_path, monkeypatch)
        from clearledgr.core.llm_gateway import LLMGateway, LLMAction
        gw = LLMGateway.__new__(LLMGateway)
        gw._db = db

        call_id = gw._log_call(
            action=LLMAction.EXTRACT_INVOICE_FIELDS,
            model="claude-haiku-4-5",
            input_tokens=100,
            output_tokens=50,
            latency_ms=200,
            cost_estimate=0.001,
            truncated=False,
            error=None,
            organization_id="test-org",
            ap_item_id="ap-llm-1",
        )
        assert call_id is not None

        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                db._prepare_sql(
                    "SELECT box_id, box_type FROM llm_call_log WHERE id = ?"
                ),
                (call_id,),
            )
            row = cur.fetchone()
        box_id = row[0] if not hasattr(row, "keys") else dict(row)["box_id"]
        box_type = row[1] if not hasattr(row, "keys") else dict(row)["box_type"]
        assert box_id == "ap-llm-1"
        assert box_type == "ap_item"
