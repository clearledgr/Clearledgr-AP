"""PLAN.md Section 7.1 — Acceptance tests for storage/contract guardrails.

These tests intentionally exercise DB/state-machine primitives directly
(`ClearledgrDB.update_ap_item`, audit writes, transition validation) so plan
requirements can be validated without external services.

They are *not* the proof of runtime orchestration alignment. Runtime-path
evidence lives in:
- ``tests/test_invoice_workflow_runtime_state_transitions.py`` (service paths)
- ``tests/test_channel_approval_contract.py`` (Slack/Teams handler paths)
- ``tests/test_ap_workflow_runtime.py`` (declarative workflow adapter/runtime)
"""

import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core.ap_states import (
    APState,
    IllegalTransitionError,
    VALID_TRANSITIONS,
    classify_post_failure_recoverability,
    normalize_state,
    transition_or_raise,
    validate_transition,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_db():
    """Return a fresh, isolated ClearledgrDB for testing.

    Uses a temp file instead of :memory: because ClearledgrDB opens a new
    connection per ``connect()`` call, and :memory: databases are not shared
    across connections.
    """
    from clearledgr.core.database import ClearledgrDB

    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
    tmp.close()
    db = ClearledgrDB(db_path=tmp.name)
    db.initialize()
    db.ensure_organization(
        organization_id="test-org",
        organization_name="Test Org",
        domain="test.com",
    )
    return db


def _create_item(db, state="received", **extra):
    """Create a test AP item in the given state."""
    payload = {
        "organization_id": "test-org",
        "vendor_name": "Acme Corp",
        "amount": 100.00,
        "currency": "USD",
        "invoice_number": f"INV-{uuid.uuid4().hex[:6]}",
        "state": state,
        "thread_id": f"gmail-{uuid.uuid4().hex[:8]}",
    }
    payload.update(extra)
    item = db.create_ap_item(payload)
    return item["id"] if isinstance(item, dict) else item


# ===========================================================================
# 1. Intake creates AP item in ``received``
# ===========================================================================

class TestIntake:
    def test_intake_creates_received_state(self):
        """PLAN.md 7.1.1: Intake creates AP item in 'received' state."""
        db = _get_db()
        ap_id = _create_item(db)
        item = db.get_ap_item(ap_id)
        assert item is not None
        assert item["state"] == "received"


# ===========================================================================
# 2. Validation routes to ``validated`` or ``needs_info``
# ===========================================================================

class TestValidation:
    def test_route_to_validated(self):
        """PLAN.md 7.1.2a: Item can transition from received to validated."""
        assert validate_transition("received", "validated") is True

    def test_route_to_needs_info(self):
        """PLAN.md 7.1.2b: Item can route from validated to needs_info."""
        assert validate_transition("validated", "needs_info") is True

    def test_needs_info_back_to_validated(self):
        """PLAN.md 7.1.2c: needs_info can resubmit back to validated."""
        assert validate_transition("needs_info", "validated") is True

    def test_needs_approval_can_route_to_needs_info(self):
        """Request-info actions can route from needs_approval to needs_info."""
        assert validate_transition("needs_approval", "needs_info") is True


# ===========================================================================
# 3. Illegal transitions rejected server-side
# ===========================================================================

class TestIllegalTransitions:
    def test_received_cannot_skip_to_approved(self):
        """PLAN.md 7.1.3: Illegal transition raises error."""
        with pytest.raises(IllegalTransitionError):
            transition_or_raise("received", "approved", "test-item")

    def test_closed_cannot_transition(self):
        """Terminal states cannot transition anywhere."""
        with pytest.raises(IllegalTransitionError):
            transition_or_raise("closed", "received", "test-item")

    def test_rejected_cannot_transition(self):
        """Rejected is terminal."""
        with pytest.raises(IllegalTransitionError):
            transition_or_raise("rejected", "approved", "test-item")

    def test_posted_cannot_go_to_approved(self):
        """Cannot go backward from posted_to_erp."""
        with pytest.raises(IllegalTransitionError):
            transition_or_raise("posted_to_erp", "approved", "test-item")

    def test_all_valid_transitions_accepted(self):
        """Every declared valid transition should pass validation."""
        for src, targets in VALID_TRANSITIONS.items():
            for tgt in targets:
                assert validate_transition(src.value, tgt.value) is True, (
                    f"{src.value} -> {tgt.value} should be valid"
                )


# ===========================================================================
# 4. Reject writes actor, reason, timestamp, audit
# ===========================================================================

class TestRejection:
    def test_rejection_records_metadata(self):
        """PLAN.md 7.1.4: Rejection records actor, reason, timestamp."""
        db = _get_db()
        ap_id = _create_item(db, state="needs_approval")
        db.update_ap_item(
            ap_id,
            state="rejected",
            rejected_by="finance-lead",
            rejection_reason="Duplicate invoice",
            rejected_at=datetime.now(timezone.utc).isoformat(),
            _actor_type="user",
            _actor_id="finance-lead",
        )
        item = db.get_ap_item(ap_id)
        assert item["state"] == "rejected"
        assert item["rejected_by"] == "finance-lead"
        assert item["rejection_reason"] == "Duplicate invoice"
        assert item["rejected_at"] is not None


# ===========================================================================
# 5. Legacy state normalization
# ===========================================================================

class TestLegacyStates:
    def test_legacy_new_maps_to_received(self):
        """Legacy 'new' status maps to 'received'."""
        assert normalize_state("new") == "received"

    def test_legacy_pending_maps_to_needs_approval(self):
        """Legacy 'pending' status maps to 'needs_approval'."""
        assert normalize_state("pending") == "needs_approval"

    def test_legacy_pending_approval_maps_to_needs_approval(self):
        """Legacy 'pending_approval' maps to 'needs_approval'."""
        assert normalize_state("pending_approval") == "needs_approval"

    def test_legacy_posted_maps_to_posted_to_erp(self):
        """Legacy 'posted' maps to 'posted_to_erp'."""
        assert normalize_state("posted") == "posted_to_erp"


# ===========================================================================
# 6. Slack signature verification
# ===========================================================================

class TestSlackVerification:
    def test_valid_signature_accepted(self):
        """PLAN.md 7.1.6a: Valid Slack signature accepted."""
        from clearledgr.core.slack_verify import verify_slack_signature
        import hashlib
        import hmac
        import time

        secret = "test-signing-secret"
        timestamp = str(int(time.time()))
        body = b"token=test&team_id=T12345"
        basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
        expected_sig = "v0=" + hmac.new(
            secret.encode("utf-8"),
            basestring.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        assert verify_slack_signature(secret, timestamp, body, expected_sig) is True

    def test_invalid_signature_rejected(self):
        """PLAN.md 7.1.6b: Invalid Slack signature rejected."""
        from clearledgr.core.slack_verify import verify_slack_signature
        import time

        assert verify_slack_signature(
            "secret", str(int(time.time())), b"body", "v0=bad"
        ) is False

    def test_replay_rejected(self):
        """PLAN.md 7.1.6c: Stale timestamp rejected (replay protection)."""
        from clearledgr.core.slack_verify import verify_slack_signature

        old_ts = str(int(1000000))  # very old timestamp
        assert verify_slack_signature("secret", old_ts, b"body", "v0=anything") is False


# ===========================================================================
# 7. Approval idempotency
# ===========================================================================

class TestApprovalIdempotency:
    def test_double_approval_does_not_duplicate(self):
        """PLAN.md 7.1.8: Repeated approval callback doesn't duplicate state."""
        db = _get_db()
        ap_id = _create_item(db, state="needs_approval")

        # First approval — succeeds
        db.update_ap_item(
            ap_id, state="approved",
            _actor_type="user", _actor_id="approver1",
        )
        item = db.get_ap_item(ap_id)
        assert item["state"] == "approved"

        # Second identical approval — should raise (already approved)
        with pytest.raises(IllegalTransitionError):
            db.update_ap_item(
                ap_id, state="approved",
                _actor_type="user", _actor_id="approver1",
            )


# ===========================================================================
# 8. ERP post success -> erp_reference persisted
# ===========================================================================

class TestERPPost:
    def test_erp_success_persists_reference(self):
        """PLAN.md 7.1.9: ERP post success persists erp_reference."""
        db = _get_db()
        ap_id = _create_item(db, state="ready_to_post")

        db.update_ap_item(
            ap_id,
            state="posted_to_erp",
            erp_reference="NS-BILL-12345",
            erp_posted_at=datetime.now(timezone.utc).isoformat(),
            _actor_type="system",
            _actor_id="erp_adapter",
        )
        item = db.get_ap_item(ap_id)
        assert item["state"] == "posted_to_erp"
        assert item["erp_reference"] == "NS-BILL-12345"
        assert item["erp_posted_at"] is not None


# ===========================================================================
# 9. ERP post failure -> failed_post + audit
# ===========================================================================

class TestERPFailure:
    def test_erp_failure_records_error(self):
        """PLAN.md 7.1.10: ERP failure -> failed_post state with error."""
        db = _get_db()
        ap_id = _create_item(db, state="ready_to_post")

        db.update_ap_item(
            ap_id,
            state="failed_post",
            last_error="NetSuite connection timeout",
            _actor_type="system",
            _actor_id="erp_adapter",
        )
        item = db.get_ap_item(ap_id)
        assert item["state"] == "failed_post"
        assert "timeout" in item["last_error"].lower()

    def test_failed_post_can_retry(self):
        """Failed post can transition back to ready_to_post."""
        db = _get_db()
        ap_id = _create_item(db, state="failed_post")

        db.update_ap_item(
            ap_id,
            state="ready_to_post",
            last_error=None,
            _actor_type="user",
            _actor_id="ops",
        )
        item = db.get_ap_item(ap_id)
        assert item["state"] == "ready_to_post"


# ===========================================================================
# 10. SQL injection prevention
# ===========================================================================

class TestSQLInjection:
    def test_disallowed_column_rejected(self):
        """SQL injection via column name is blocked by whitelist."""
        db = _get_db()
        ap_id = _create_item(db)

        with pytest.raises(ValueError, match="Disallowed columns"):
            db.update_ap_item(ap_id, **{"malicious_col; DROP TABLE ap_items--": "pwned"})


# ===========================================================================
# 11. Cross-tenant isolation
# ===========================================================================

class TestCrossTenant:
    def test_list_requires_org_id(self):
        """list_ap_items_all requires organization_id."""
        db = _get_db()
        _create_item(db, organization_id="test-org")

        # Empty org_id should raise
        with pytest.raises(ValueError):
            db.list_ap_items_all(organization_id="")

    def test_list_filters_by_org(self):
        """Items from another org are not returned."""
        db = _get_db()
        db.ensure_organization("org-a", "Org A")
        db.ensure_organization("org-b", "Org B")
        _create_item(db, organization_id="org-a")
        _create_item(db, organization_id="org-b")

        items_a = db.list_ap_items_all(organization_id="org-a")
        for item in items_a:
            assert item.get("organization_id") == "org-a"


# ===========================================================================
# 12. Full pipeline happy path (state machine walk)
# ===========================================================================

class TestFullPipeline:
    def test_happy_path_state_machine(self):
        """PLAN.md 7.1: Full pipeline from received -> closed."""
        db = _get_db()
        ap_id = _create_item(db, state="received")

        # received -> validated
        db.update_ap_item(ap_id, state="validated", _actor_type="system", _actor_id="validator")
        assert db.get_ap_item(ap_id)["state"] == "validated"

        # validated -> needs_approval
        db.update_ap_item(ap_id, state="needs_approval", _actor_type="system", _actor_id="router")
        assert db.get_ap_item(ap_id)["state"] == "needs_approval"

        # needs_approval -> approved
        db.update_ap_item(ap_id, state="approved", _actor_type="user", _actor_id="cfo")
        assert db.get_ap_item(ap_id)["state"] == "approved"

        # approved -> ready_to_post
        db.update_ap_item(ap_id, state="ready_to_post", _actor_type="system", _actor_id="workflow")
        assert db.get_ap_item(ap_id)["state"] == "ready_to_post"

        # ready_to_post -> posted_to_erp
        db.update_ap_item(
            ap_id,
            state="posted_to_erp",
            erp_reference="NS-123",
            erp_posted_at=datetime.now(timezone.utc).isoformat(),
            _actor_type="system",
            _actor_id="erp",
        )
        assert db.get_ap_item(ap_id)["state"] == "posted_to_erp"
        assert db.get_ap_item(ap_id)["erp_reference"] == "NS-123"

        # posted_to_erp -> closed
        db.update_ap_item(ap_id, state="closed", _actor_type="system", _actor_id="closer")
        assert db.get_ap_item(ap_id)["state"] == "closed"

        # closed is terminal — cannot transition
        with pytest.raises(IllegalTransitionError):
            db.update_ap_item(ap_id, state="received", _actor_type="system", _actor_id="x")

    def test_exception_path_with_retry(self):
        """Exception path: ready_to_post -> failed_post -> ready_to_post -> posted."""
        db = _get_db()
        ap_id = _create_item(db, state="ready_to_post")

        # Fail
        db.update_ap_item(ap_id, state="failed_post", last_error="timeout",
                          _actor_type="system", _actor_id="erp")
        assert db.get_ap_item(ap_id)["state"] == "failed_post"

        # Retry
        db.update_ap_item(ap_id, state="ready_to_post", last_error=None,
                          _actor_type="user", _actor_id="ops")
        assert db.get_ap_item(ap_id)["state"] == "ready_to_post"

        # Success
        db.update_ap_item(ap_id, state="posted_to_erp", erp_reference="NS-456",
                          _actor_type="system", _actor_id="erp")
        assert db.get_ap_item(ap_id)["state"] == "posted_to_erp"


# ===========================================================================
# 13. Security: no hardcoded secrets
# ===========================================================================

class TestSecurityHardening:
    def test_no_hardcoded_secrets(self):
        """Grep-equivalent: no hardcoded 'clearledgr-dev-secret' in AP path."""
        import re
        ap_path_files = [
            ROOT / "clearledgr" / "core" / "database.py",
            ROOT / "clearledgr" / "core" / "auth.py",
            ROOT / "clearledgr" / "api" / "auth.py",
            ROOT / "clearledgr" / "api" / "workspace_shell.py",
        ]
        for fpath in ap_path_files:
            if fpath.exists():
                content = fpath.read_text()
                assert "clearledgr-dev-secret" not in content, (
                    f"Hardcoded secret found in {fpath.name}"
                )

    def test_require_secret_crashes_in_prod(self):
        """require_secret raises RuntimeError in production-like envs if not set."""
        from clearledgr.core.secrets import require_secret, _generated_cache

        for env_name in ("production", "staging"):
            with patch.dict(os.environ, {"ENV": env_name}, clear=False):
                _generated_cache.pop("__TEST_SECRET__", None)
                os.environ.pop("__TEST_SECRET__", None)
                with pytest.raises(RuntimeError, match="not set"):
                    require_secret("__TEST_SECRET__")


class TestBatchRetryRecoverability:
    def test_retry_recoverability_allows_transient_errors(self):
        verdict = classify_post_failure_recoverability(
            last_error="connector timeout while posting",
            exception_code="erp_post_failed",
        )
        assert verdict["recoverable"] is True

    def test_retry_recoverability_blocks_non_recoverable_errors(self):
        verdict = classify_post_failure_recoverability(
            last_error="duplicate invoice already posted in ERP",
            exception_code="duplicate",
        )
        assert verdict["recoverable"] is False
