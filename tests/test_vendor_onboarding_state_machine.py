"""Tests for Phase 3.1.a — vendor onboarding state machine + DB persistence.

Covers:
  - VendorOnboardingState enum + VALID_TRANSITIONS coverage
  - validate_transition / transition_or_raise / normalize_state semantics
  - is_terminal / is_pre_active predicates
  - Migration v17 creates the vendor_onboarding_sessions table on init
    and is idempotent on re-run
  - VendorStore session accessors:
      * create_vendor_onboarding_session blocks duplicate active sessions
      * get_active_onboarding_session / get_onboarding_session_by_id
      * list_pending_onboarding_sessions filters by state + organization
      * transition_onboarding_session_state validates transitions,
        stamps the canonical timestamp columns, deactivates sessions on
        terminal transitions, emits audit events
      * record_onboarding_chase increments chase_count + last_chase_at
      * attach_erp_vendor_id stores the ERP vendor ID before terminal
  - gmail_extension_support.build_vendor_suggestion_payload uses the
    DB-backed VendorStore and returns matches against vendor_profiles
    instead of crashing on the deleted vendor_management module.
"""
from __future__ import annotations

from typing import Any, Dict

import pytest


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    from clearledgr.core.database import ClearledgrDB, get_db
    from clearledgr.core import database as db_module

    db = get_db()
    db.initialize()
    monkeypatch.setattr(db_module, "_DB_INSTANCE", db)
    return db


def _seed_vendor(db, org="org_t", vendor="Acme Ltd", **kwargs):
    db.create_organization(org, name=org)
    defaults = {"invoice_count": 0}
    defaults.update(kwargs)
    db.upsert_vendor_profile(org, vendor, **defaults)
    return org, vendor


# ===========================================================================
# State machine module
# ===========================================================================


class TestStateMachineEnum:

    def test_all_thesis_stages_present(self):
        from clearledgr.core.vendor_onboarding_states import VendorOnboardingState
        # DESIGN_THESIS.md §9 names four stages — Invited, KYC, Bank
        # Verify, Active. The enum has those plus operational sub-states.
        members = {s.value for s in VendorOnboardingState}
        for required in (
            "invited",
            "kyc",
            "bank_verify",
            "bank_verified",
            "ready_for_erp",
            "active",
            "blocked",
            "closed_unsuccessful",
        ):
            assert required in members
        # Sanity: retired names are gone from the enum.
        assert "rejected" not in members
        assert "abandoned" not in members
        assert "escalated" not in members
        assert "awaiting_kyc" not in members
        assert "awaiting_bank" not in members
        # Sanity: micro-deposit intermediate state was removed — the flow
        # transitions awaiting_bank → bank_verified directly in V1.
        assert "microdeposit_pending" not in members

    def test_terminal_states_have_no_outbound_edges(self):
        from clearledgr.core.vendor_onboarding_states import (
            TERMINAL_STATES,
            VALID_TRANSITIONS,
        )
        for state in TERMINAL_STATES:
            assert VALID_TRANSITIONS[state] == frozenset()

    def test_pre_active_states_excludes_terminal_and_recovery(self):
        from clearledgr.core.vendor_onboarding_states import (
            PRE_ACTIVE_STATES,
            VendorOnboardingState,
        )
        # Pre-active is the chase-eligible band: invited through
        # awaiting_bank. Bank verified, ready_for_erp, escalated and
        # the terminals are NOT in this set.
        assert VendorOnboardingState.INVITED in PRE_ACTIVE_STATES
        assert VendorOnboardingState.KYC in PRE_ACTIVE_STATES
        assert VendorOnboardingState.BANK_VERIFY in PRE_ACTIVE_STATES
        assert VendorOnboardingState.BANK_VERIFIED not in PRE_ACTIVE_STATES
        assert VendorOnboardingState.READY_FOR_ERP not in PRE_ACTIVE_STATES
        assert VendorOnboardingState.BLOCKED not in PRE_ACTIVE_STATES
        assert VendorOnboardingState.ACTIVE not in PRE_ACTIVE_STATES


class TestValidTransitions:

    def test_happy_path_invited_to_active(self):
        from clearledgr.core.vendor_onboarding_states import validate_transition
        path = [
            ("invited", "kyc"),
            ("kyc", "bank_verify"),
            ("bank_verify", "bank_verified"),
            ("bank_verified", "ready_for_erp"),
            ("ready_for_erp", "active"),
        ]
        for current, target in path:
            assert validate_transition(current, target), f"{current}->{target}"

    def test_skip_stages_blocked(self):
        from clearledgr.core.vendor_onboarding_states import validate_transition
        # Cannot leap from invited straight to active
        assert not validate_transition("invited", "active")
        # Cannot skip bank verification
        assert not validate_transition("bank_verify", "ready_for_erp")
        assert not validate_transition("kyc", "bank_verified")

    def test_terminal_states_have_no_forward_edges(self):
        from clearledgr.core.vendor_onboarding_states import validate_transition
        # active is terminal — no escape
        assert not validate_transition("active", "invited")
        assert not validate_transition("active", "blocked")
        # closed_unsuccessful is terminal
        assert not validate_transition("closed_unsuccessful", "invited")
        # Legacy alias: the rename left normalize_state mapping
        # rejected/abandoned → closed_unsuccessful, which is still
        # terminal, so validate_transition stays False for those.
        assert not validate_transition("rejected", "invited")
        assert not validate_transition("abandoned", "invited")

    def test_escalated_can_recover_to_any_pre_active(self):
        from clearledgr.core.vendor_onboarding_states import validate_transition
        for target in (
            "invited",
            "kyc",
            "bank_verify",
            "bank_verified",
            "ready_for_erp",
        ):
            assert validate_transition("blocked", target), f"escalated->{target}"

    def test_unknown_states_rejected(self):
        from clearledgr.core.vendor_onboarding_states import validate_transition
        assert not validate_transition("unknown", "active")
        assert not validate_transition("invited", "made_up_state")
        assert not validate_transition("", "invited")

    def test_normalize_state_lowercases_and_strips(self):
        from clearledgr.core.vendor_onboarding_states import normalize_state
        assert normalize_state("  INVITED ") == "invited"
        assert normalize_state("Awaiting_KYC") == "kyc"
        # Unknown values pass through unchanged for downstream rejection
        assert normalize_state("garbage") == "garbage"

    def test_transition_or_raise_includes_session_id(self):
        from clearledgr.core.vendor_onboarding_states import (
            IllegalVendorOnboardingTransitionError,
            transition_or_raise,
        )
        with pytest.raises(IllegalVendorOnboardingTransitionError) as ei:
            transition_or_raise("invited", "active", session_id="sess_123")
        assert ei.value.session_id == "sess_123"
        assert "sess_123" in str(ei.value)
        assert "invited" in str(ei.value)
        assert "active" in str(ei.value)

    def test_predicates(self):
        from clearledgr.core.vendor_onboarding_states import is_pre_active, is_terminal
        assert is_terminal("active") is True
        assert is_terminal("closed_unsuccessful") is True
        # Legacy aliases — normalize to closed_unsuccessful which is terminal.
        assert is_terminal("rejected") is True
        assert is_terminal("abandoned") is True
        assert is_terminal("invited") is False
        assert is_terminal("blocked") is False
        assert is_pre_active("invited") is True
        assert is_pre_active("kyc") is True
        assert is_pre_active("bank_verified") is False
        assert is_pre_active("active") is False


# ===========================================================================
# Migration v17
# ===========================================================================


class TestMigrationV17:

    def test_table_present_after_init(self, tmp_db):
        # PRAGMA is SQLite-only; under PG use information_schema. Both
        # dialects end at the same shape: a set of column names.
        with tmp_db.connect() as conn:
            cur = conn.cursor()
            if tmp_db.use_postgres:
                cur.execute(
                    tmp_db._prepare_sql(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = ?"
                    ),
                    ("vendor_onboarding_sessions",),
                )
                columns = {row[0] for row in cur.fetchall()}
            else:
                cur.execute("PRAGMA table_info(vendor_onboarding_sessions)")
                columns = {row[1] for row in cur.fetchall()}
        for col in (
            "id",
            "organization_id",
            "vendor_name",
            "state",
            "is_active",
            "invited_at",
            "invited_by",
            "last_activity_at",
            "last_chase_at",
            "chase_count",
            "kyc_submitted_at",
            "bank_submitted_at",
            "microdeposit_initiated_at",
            "microdeposit_initiated_by",
            "bank_verified_at",
            "erp_activated_at",
            "erp_vendor_id",
            "completed_at",
            "escalated_at",
            "escalated_reason",
            "rejected_at",
            "rejected_by",
            "rejection_reason",
            "abandoned_at",
            "metadata",
            "created_at",
            "updated_at",
        ):
            assert col in columns, f"missing column {col}"

    def test_migration_v17_idempotent(self, tmp_db):
        from clearledgr.core.migrations import _MIGRATIONS
        m17 = next(m for m in _MIGRATIONS if m[0] == 17)
        with tmp_db.connect() as conn:
            if tmp_db.use_postgres:
                conn.autocommit = True
            cur = conn.cursor()
            m17[2](cur, tmp_db)  # already-applied table; should not raise
            if not tmp_db.use_postgres:
                conn.commit()


# ===========================================================================
# VendorStore session accessors
# ===========================================================================


class TestSessionLifecycle:

    def test_create_session_starts_in_invited(self, tmp_db):
        org, vendor = _seed_vendor(tmp_db)
        session = tmp_db.create_vendor_onboarding_session(
            org, vendor, invited_by="cfo@customer.com"
        )
        assert session is not None
        assert session["state"] == "invited"
        assert session["is_active"] is True
        assert session["invited_by"] == "cfo@customer.com"
        assert session["chase_count"] == 0
        assert session["last_chase_at"] is None
        assert session["last_activity_at"] is not None

    def test_duplicate_active_session_rejected(self, tmp_db):
        org, vendor = _seed_vendor(tmp_db)
        first = tmp_db.create_vendor_onboarding_session(
            org, vendor, invited_by="cfo@customer.com"
        )
        assert first is not None
        # Second open against the same vendor while first is active
        # must return None.
        second = tmp_db.create_vendor_onboarding_session(
            org, vendor, invited_by="cfo@customer.com"
        )
        assert second is None

    def test_get_active_returns_only_active(self, tmp_db):
        org, vendor = _seed_vendor(tmp_db)
        session = tmp_db.create_vendor_onboarding_session(
            org, vendor, invited_by="cfo@customer.com"
        )
        active = tmp_db.get_active_onboarding_session(org, vendor)
        assert active is not None
        assert active["id"] == session["id"]

    def test_get_by_id(self, tmp_db):
        org, vendor = _seed_vendor(tmp_db)
        session = tmp_db.create_vendor_onboarding_session(
            org, vendor, invited_by="cfo@customer.com"
        )
        fetched = tmp_db.get_onboarding_session_by_id(session["id"])
        assert fetched is not None
        assert fetched["id"] == session["id"]

    def test_get_by_id_returns_none_for_unknown(self, tmp_db):
        assert tmp_db.get_onboarding_session_by_id("does-not-exist") is None

    def test_create_with_unknown_initial_state_rejected(self, tmp_db):
        org, vendor = _seed_vendor(tmp_db)
        result = tmp_db.create_vendor_onboarding_session(
            org, vendor, invited_by="cfo@customer.com",
            initial_state="not_a_real_state",
        )
        assert result is None


class TestStateTransitions:

    def test_transition_stamps_kyc_timestamp(self, tmp_db):
        org, vendor = _seed_vendor(tmp_db)
        session = tmp_db.create_vendor_onboarding_session(
            org, vendor, invited_by="cfo@customer.com"
        )
        sid = session["id"]
        tmp_db.transition_onboarding_session_state(
            sid, "kyc", actor_id="vendor"
        )
        updated = tmp_db.transition_onboarding_session_state(
            sid, "bank_verify", actor_id="vendor"
        )
        assert updated["state"] == "bank_verify"
        assert updated["kyc_submitted_at"] is not None

    def test_transition_to_bank_verified_stamps_submitted_and_verified(self, tmp_db):
        """With micro-deposit removed, awaiting_bank → bank_verified stamps
        both bank_submitted_at (the vendor just submitted) and
        bank_verified_at (V1 direct edge — provider adapters will gate
        this in a future phase)."""
        org, vendor = _seed_vendor(tmp_db)
        session = tmp_db.create_vendor_onboarding_session(
            org, vendor, invited_by="cfo@customer.com"
        )
        sid = session["id"]
        tmp_db.transition_onboarding_session_state(sid, "kyc", actor_id="vendor")
        tmp_db.transition_onboarding_session_state(sid, "bank_verify", actor_id="vendor")
        updated = tmp_db.transition_onboarding_session_state(
            sid, "bank_verified", actor_id="vendor"
        )
        assert updated["state"] == "bank_verified"
        assert updated["bank_submitted_at"] is not None
        assert updated["bank_verified_at"] is not None

    def test_transition_to_active_stamps_completion_and_deactivates(self, tmp_db):
        org, vendor = _seed_vendor(tmp_db)
        session = tmp_db.create_vendor_onboarding_session(
            org, vendor, invited_by="cfo@customer.com"
        )
        sid = session["id"]
        for nxt in ("kyc", "bank_verify",
                    "bank_verified", "ready_for_erp", "active"):
            tmp_db.transition_onboarding_session_state(
                sid, nxt, actor_id="agent"
            )
        final = tmp_db.get_onboarding_session_by_id(sid)
        assert final["state"] == "active"
        assert final["completed_at"] is not None
        assert final["erp_activated_at"] is not None
        assert final["is_active"] is False  # deactivated on terminal

    def test_terminal_deactivation_unblocks_re_onboarding(self, tmp_db):
        org, vendor = _seed_vendor(tmp_db)
        first = tmp_db.create_vendor_onboarding_session(
            org, vendor, invited_by="cfo@customer.com"
        )
        # Abandon it
        tmp_db.transition_onboarding_session_state(
            first["id"], "abandoned", actor_id="agent"
        )
        # Now a fresh session should be allowed
        second = tmp_db.create_vendor_onboarding_session(
            org, vendor, invited_by="cfo@customer.com"
        )
        assert second is not None
        assert second["id"] != first["id"]
        assert second["state"] == "invited"

    def test_illegal_transition_raises(self, tmp_db):
        from clearledgr.core.vendor_onboarding_states import (
            IllegalVendorOnboardingTransitionError,
        )
        org, vendor = _seed_vendor(tmp_db)
        session = tmp_db.create_vendor_onboarding_session(
            org, vendor, invited_by="cfo@customer.com"
        )
        with pytest.raises(IllegalVendorOnboardingTransitionError):
            tmp_db.transition_onboarding_session_state(
                session["id"], "active", actor_id="agent"
            )

    def test_rejection_records_reason_and_actor(self, tmp_db):
        org, vendor = _seed_vendor(tmp_db)
        session = tmp_db.create_vendor_onboarding_session(
            org, vendor, invited_by="cfo@customer.com"
        )
        # "rejected" is a legacy alias for closed_unsuccessful — this is
        # the spec-level "onboarding ended without activation" terminal.
        # normalize_state() translates the legacy input before the
        # transition machinery checks it.
        tmp_db.transition_onboarding_session_state(
            session["id"],
            "rejected",
            actor_id="cfo@customer.com",
            reason="Failed sanctions screen",
        )
        updated = tmp_db.get_onboarding_session_by_id(session["id"])
        assert updated["state"] == "closed_unsuccessful"
        assert updated["rejected_by"] == "cfo@customer.com"
        assert updated["rejection_reason"] == "Failed sanctions screen"
        assert updated["is_active"] is False

    def test_escalation_records_reason(self, tmp_db):
        org, vendor = _seed_vendor(tmp_db)
        session = tmp_db.create_vendor_onboarding_session(
            org, vendor, invited_by="cfo@customer.com"
        )
        tmp_db.transition_onboarding_session_state(
            session["id"],
            "blocked",
            actor_id="agent",
            reason="No vendor response after 72h",
        )
        updated = tmp_db.get_onboarding_session_by_id(session["id"])
        assert updated["state"] == "blocked"
        assert updated["escalated_reason"] == "No vendor response after 72h"
        # Escalation is NOT terminal — session remains active so AP
        # Manager intervention can recover it.
        assert updated["is_active"] is True

    def test_escalation_recovery_to_any_pre_active(self, tmp_db):
        org, vendor = _seed_vendor(tmp_db)
        session = tmp_db.create_vendor_onboarding_session(
            org, vendor, invited_by="cfo@customer.com"
        )
        sid = session["id"]
        tmp_db.transition_onboarding_session_state(sid, "kyc", actor_id="vendor")
        tmp_db.transition_onboarding_session_state(
            sid, "blocked", actor_id="agent", reason="stalled"
        )
        # Recovery: AP Manager re-engages and restarts at awaiting_bank
        recovered = tmp_db.transition_onboarding_session_state(
            sid, "bank_verify", actor_id="ap_manager@customer.com"
        )
        assert recovered["state"] == "bank_verify"
        assert recovered["is_active"] is True

    def test_metadata_patch_merges(self, tmp_db):
        org, vendor = _seed_vendor(tmp_db)
        session = tmp_db.create_vendor_onboarding_session(
            org, vendor, invited_by="cfo@customer.com"
        )
        tmp_db.transition_onboarding_session_state(
            session["id"],
            "kyc",
            actor_id="vendor",
            metadata_patch={"opened_link_at": "2026-04-10T10:00:00+00:00"},
        )
        updated = tmp_db.get_onboarding_session_by_id(session["id"])
        assert updated["metadata"]["opened_link_at"] == "2026-04-10T10:00:00+00:00"


class TestListPendingSessions:

    def test_default_filter_is_pre_active(self, tmp_db):
        org_a = "org_a"
        org_b = "org_b"
        tmp_db.create_organization(org_a, name="A")
        tmp_db.create_organization(org_b, name="B")
        for org, vendor in [(org_a, "Acme"), (org_a, "Beta"), (org_b, "Gamma")]:
            tmp_db.upsert_vendor_profile(org, vendor)
            tmp_db.create_vendor_onboarding_session(org, vendor, invited_by="cfo")

        # Move Beta into bank_verified — should drop out of default filter
        beta = tmp_db.get_active_onboarding_session(org_a, "Beta")
        for s in ("kyc", "bank_verify", "bank_verified"):
            tmp_db.transition_onboarding_session_state(beta["id"], s, actor_id="agent")

        all_pending = tmp_db.list_pending_onboarding_sessions()
        names = {s["vendor_name"] for s in all_pending}
        assert "Acme" in names
        assert "Gamma" in names
        assert "Beta" not in names

    def test_org_filter_scoped(self, tmp_db):
        org_a = "org_a"
        org_b = "org_b"
        tmp_db.create_organization(org_a, name="A")
        tmp_db.create_organization(org_b, name="B")
        tmp_db.upsert_vendor_profile(org_a, "Acme")
        tmp_db.upsert_vendor_profile(org_b, "Gamma")
        tmp_db.create_vendor_onboarding_session(org_a, "Acme", invited_by="cfo")
        tmp_db.create_vendor_onboarding_session(org_b, "Gamma", invited_by="cfo")

        org_a_only = tmp_db.list_pending_onboarding_sessions(organization_id=org_a)
        names = {s["vendor_name"] for s in org_a_only}
        assert names == {"Acme"}

    def test_explicit_state_filter(self, tmp_db):
        org, vendor = _seed_vendor(tmp_db)
        session = tmp_db.create_vendor_onboarding_session(
            org, vendor, invited_by="cfo@customer.com"
        )
        sid = session["id"]
        tmp_db.transition_onboarding_session_state(
            sid, "blocked", actor_id="agent", reason="x"
        )
        escalated = tmp_db.list_pending_onboarding_sessions(states=["blocked"])
        assert len(escalated) == 1
        assert escalated[0]["state"] == "blocked"


class TestChaseTracking:

    def test_record_chase_increments_count(self, tmp_db):
        org, vendor = _seed_vendor(tmp_db)
        session = tmp_db.create_vendor_onboarding_session(
            org, vendor, invited_by="cfo@customer.com"
        )
        sid = session["id"]
        tmp_db.record_onboarding_chase(sid, "chase_24h")
        tmp_db.record_onboarding_chase(sid, "chase_48h")
        updated = tmp_db.get_onboarding_session_by_id(sid)
        assert updated["chase_count"] == 2
        assert updated["last_chase_at"] is not None
        # State is unchanged — chases happen in place
        assert updated["state"] == "invited"


class TestErpAttachment:

    def test_attach_erp_vendor_id_persists(self, tmp_db):
        org, vendor = _seed_vendor(tmp_db)
        session = tmp_db.create_vendor_onboarding_session(
            org, vendor, invited_by="cfo@customer.com"
        )
        sid = session["id"]
        # Drive to ready_for_erp
        for nxt in ("kyc", "bank_verify",
                    "bank_verified", "ready_for_erp"):
            tmp_db.transition_onboarding_session_state(sid, nxt, actor_id="agent")
        updated = tmp_db.attach_erp_vendor_id(sid, "QB-VND-12345")
        assert updated["erp_vendor_id"] == "QB-VND-12345"

    def test_attach_erp_vendor_id_rejects_empty(self, tmp_db):
        org, vendor = _seed_vendor(tmp_db)
        session = tmp_db.create_vendor_onboarding_session(
            org, vendor, invited_by="cfo@customer.com"
        )
        assert tmp_db.attach_erp_vendor_id(session["id"], "") is None


# ===========================================================================
# Audit event emission
# ===========================================================================


class TestAuditEmission:

    def _audit_events(self, db, event_type=None):
        # psycopg uses dict_row (HybridRow) so no row_factory manipulation
        # is needed; sqlite3.Row was SQLite-only and setting it on a
        # psycopg connection errors.
        try:
            with db.connect() as conn:
                if not db.use_postgres:
                    import sqlite3
                    conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                if event_type:
                    cur.execute(
                        db._prepare_sql(
                            "SELECT * FROM audit_events WHERE event_type = ? "
                            "ORDER BY ts ASC"
                        ),
                        (event_type,),
                    )
                else:
                    cur.execute("SELECT * FROM audit_events ORDER BY ts ASC")
                return [dict(r) for r in cur.fetchall()]
        except Exception:
            return []

    def test_state_transition_emits_audit_event(self, tmp_db):
        org, vendor = _seed_vendor(tmp_db)
        session = tmp_db.create_vendor_onboarding_session(
            org, vendor, invited_by="cfo@customer.com"
        )
        tmp_db.transition_onboarding_session_state(
            session["id"], "kyc", actor_id="vendor@acme.com"
        )
        events = self._audit_events(tmp_db, "vendor_onboarding_state_transition")
        assert len(events) == 1
        decision_reason = events[0].get("decision_reason") or ""
        assert "invited" in decision_reason
        assert "kyc" in decision_reason

    def test_chase_emits_audit_event(self, tmp_db):
        org, vendor = _seed_vendor(tmp_db)
        session = tmp_db.create_vendor_onboarding_session(
            org, vendor, invited_by="cfo@customer.com"
        )
        tmp_db.record_onboarding_chase(session["id"], "chase_24h")
        events = self._audit_events(tmp_db, "vendor_onboarding_chase_sent")
        assert len(events) == 1
        decision_reason = events[0].get("decision_reason") or ""
        assert "chase_24h" in decision_reason


# ===========================================================================
# build_vendor_suggestion_payload — DB-backed rewrite (Phase 3.1.a)
# ===========================================================================


class TestVendorSuggestionPayload:

    def test_empty_when_no_vendors(self, tmp_db):
        from clearledgr.services.gmail_extension_support import (
            build_vendor_suggestion_payload,
        )
        tmp_db.create_organization("org_t", name="X")
        result = build_vendor_suggestion_payload(
            organization_id="org_t",
            extracted_vendor="Acme Limited",
        )
        assert result["primary"] is None
        assert result["has_suggestion"] is False
        assert result["is_new_vendor"] is True

    def test_extraction_match_above_threshold(self, tmp_db):
        from clearledgr.services.gmail_extension_support import (
            build_vendor_suggestion_payload,
        )
        tmp_db.create_organization("org_t", name="X")
        tmp_db.upsert_vendor_profile("org_t", "Acme Ltd")
        result = build_vendor_suggestion_payload(
            organization_id="org_t",
            extracted_vendor="ACME LIMITED",
        )
        assert result["primary"] is not None
        assert result["primary"]["vendor_name"] == "Acme Ltd"
        assert result["primary"]["source"] == "extraction"
        assert result["has_suggestion"] is True

    def test_email_domain_match(self, tmp_db):
        from clearledgr.services.gmail_extension_support import (
            build_vendor_suggestion_payload,
        )
        tmp_db.create_organization("org_t", name="X")
        tmp_db.upsert_vendor_profile(
            "org_t", "Acme Ltd", sender_domains=["acme.com"]
        )
        result = build_vendor_suggestion_payload(
            organization_id="org_t",
            sender_email="billing@acme.com",
            extracted_vendor=None,
        )
        assert result["primary"] is not None
        assert result["primary"]["vendor_name"] == "Acme Ltd"
        assert result["primary"]["source"] == "email_domain"

    def test_no_double_count_same_vendor(self, tmp_db):
        from clearledgr.services.gmail_extension_support import (
            build_vendor_suggestion_payload,
        )
        tmp_db.create_organization("org_t", name="X")
        tmp_db.upsert_vendor_profile(
            "org_t", "Acme Ltd", sender_domains=["acme.com"]
        )
        result = build_vendor_suggestion_payload(
            organization_id="org_t",
            sender_email="billing@acme.com",
            extracted_vendor="Acme Limited",
        )
        # Both extraction and domain hit Acme Ltd — but the result
        # should list it once.
        all_names = [result["primary"]["vendor_name"]] + [
            a["vendor_name"] for a in result["alternatives"]
        ]
        assert all_names.count("Acme Ltd") == 1

    def test_unrelated_vendor_not_returned(self, tmp_db):
        from clearledgr.services.gmail_extension_support import (
            build_vendor_suggestion_payload,
        )
        tmp_db.create_organization("org_t", name="X")
        tmp_db.upsert_vendor_profile("org_t", "Globex Industries")
        result = build_vendor_suggestion_payload(
            organization_id="org_t",
            extracted_vendor="Acme Ltd",
        )
        # Globex shouldn't match Acme — confidence floor is 0.6
        assert result["primary"] is None
        assert result["is_new_vendor"] is True

    def test_vendor_management_module_is_gone(self):
        """The deleted in-memory module must not be importable."""
        with pytest.raises(ImportError):
            import clearledgr.services.vendor_management  # noqa: F401
