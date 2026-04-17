"""§13 agent-action credit pool tests.

Covers:
  - Monthly auto-grant (idempotent within a billing period)
  - Balance computation from ledger (grants - consumes + refunds)
  - Consume with sufficient balance writes a consume entry
  - Consume with insufficient balance short-circuits and writes
    nothing
  - Refund via entry id reverses a consume entry
  - Purchase records a purchase entry and bumps the balance
  - Preview flags the confirmation prompt above the threshold
  - Enterprise unlimited path bypasses the ledger entirely
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def db_starter(tmp_path, monkeypatch):
    """Fresh DB with a Starter subscription seeded for test-org."""
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "credits.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    import clearledgr.core.database as db_module
    db_module._DB_INSTANCE = None

    import clearledgr.services.subscription as sub_mod
    sub_mod._subscription_service = None

    db = db_module.get_db()
    db.initialize()

    from clearledgr.services.subscription import get_subscription_service, PlanTier
    get_subscription_service().upgrade_plan("test-org", tier=PlanTier.STARTER)
    return db


@pytest.fixture()
def db_enterprise(tmp_path, monkeypatch):
    """Fresh DB with an Enterprise subscription (unlimited pool)."""
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "ent.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    import clearledgr.core.database as db_module
    db_module._DB_INSTANCE = None

    import clearledgr.services.subscription as sub_mod
    sub_mod._subscription_service = None

    db = db_module.get_db()
    db.initialize()

    from clearledgr.services.subscription import get_subscription_service, PlanTier
    get_subscription_service().upgrade_plan("test-org", tier=PlanTier.ENTERPRISE)
    return db


class TestMonthlyGrant:
    def test_first_call_writes_grant_at_tier_allowance(self, db_starter):
        from clearledgr.services.agent_credit_pool import (
            ensure_monthly_grant, get_balance,
        )
        entry_id = ensure_monthly_grant("test-org", db=db_starter)
        assert entry_id is not None
        # Starter tier allowance is 500 credits.
        assert get_balance("test-org", db=db_starter) == 500

    def test_second_call_same_period_is_idempotent(self, db_starter):
        from clearledgr.services.agent_credit_pool import ensure_monthly_grant
        first = ensure_monthly_grant("test-org", db=db_starter)
        second = ensure_monthly_grant("test-org", db=db_starter)
        assert first is not None
        assert second is None  # Already granted; no-op.

    def test_enterprise_skips_grant(self, db_enterprise):
        from clearledgr.services.agent_credit_pool import ensure_monthly_grant
        # Unlimited tier doesn't use the ledger — returns None.
        assert ensure_monthly_grant("test-org", db=db_enterprise) is None


class TestBalance:
    def test_fresh_org_starts_at_zero(self, db_starter):
        # ensure_monthly_grant hasn't fired yet; balance is 0.
        from clearledgr.services.agent_credit_pool import get_balance
        assert get_balance("test-org", db=db_starter) == 0

    def test_balance_is_grants_minus_consumes_plus_refunds(self, db_starter):
        from clearledgr.services.agent_credit_pool import (
            consume_credit, get_balance, refund_credit,
        )
        # Grant of 500 via the monthly path.
        consume1 = consume_credit(
            "test-org", credits=50, action_type="extraction",
            db=db_starter,
        )
        assert consume1["ok"] is True
        assert consume1["balance_after"] == 450

        consume2 = consume_credit(
            "test-org", credits=25, action_type="adverse_media",
            db=db_starter,
        )
        assert consume2["balance_after"] == 425

        # Refund the first consume.
        refund_credit(
            "test-org",
            original_entry_id=consume1["entry_id"],
            reason="action_failed",
            db=db_starter,
        )
        assert get_balance("test-org", db=db_starter) == 475  # 500 - 25

    def test_enterprise_balance_is_unlimited_sentinel(self, db_enterprise):
        from clearledgr.services.agent_credit_pool import get_balance
        assert get_balance("test-org", db=db_enterprise) == -1


class TestConsume:
    def test_sufficient_balance_writes_consume(self, db_starter):
        from clearledgr.services.agent_credit_pool import consume_credit
        result = consume_credit(
            "test-org", credits=10, action_type="extraction",
            ap_item_id="ap-123",
            db=db_starter,
        )
        assert result["ok"] is True
        assert result["entry_id"] is not None
        assert result["balance_after"] == 490

    def test_insufficient_balance_is_blocked(self, db_starter):
        from clearledgr.services.agent_credit_pool import consume_credit
        # Starter pool = 500. Consume 600 should fail.
        result = consume_credit(
            "test-org", credits=600, action_type="extraction",
            db=db_starter,
        )
        assert result["ok"] is False
        assert result["reason"] == "insufficient_credits"
        assert result["balance"] == 500
        assert result["requested"] == 600

    def test_negative_credits_rejected(self, db_starter):
        from clearledgr.services.agent_credit_pool import consume_credit
        result = consume_credit(
            "test-org", credits=-5, action_type="extraction",
            db=db_starter,
        )
        assert result["ok"] is False
        assert result["reason"] == "negative_credits_not_allowed"

    def test_enterprise_consume_returns_unlimited(self, db_enterprise):
        from clearledgr.services.agent_credit_pool import consume_credit
        result = consume_credit(
            "test-org", credits=1000, action_type="extraction",
            db=db_enterprise,
        )
        assert result["ok"] is True
        assert result["unlimited"] is True
        assert result["balance_after"] == -1


class TestRefund:
    def test_refund_reverses_consume(self, db_starter):
        from clearledgr.services.agent_credit_pool import (
            consume_credit, get_balance, refund_credit,
        )
        consumed = consume_credit(
            "test-org", credits=30, action_type="extraction",
            db=db_starter,
        )
        assert get_balance("test-org", db=db_starter) == 470

        refund = refund_credit(
            "test-org",
            original_entry_id=consumed["entry_id"],
            reason="action_failed",
            db=db_starter,
        )
        assert refund["ok"] is True
        assert refund["refunded_credits"] == 30
        assert get_balance("test-org", db=db_starter) == 500

    def test_refund_missing_entry_fails(self, db_starter):
        from clearledgr.services.agent_credit_pool import refund_credit
        result = refund_credit(
            "test-org",
            original_entry_id="nonexistent-id",
            reason="test",
            db=db_starter,
        )
        assert result["ok"] is False
        assert result["reason"] == "original_entry_not_found"

    def test_cannot_refund_a_refund_entry(self, db_starter):
        from clearledgr.services.agent_credit_pool import (
            consume_credit, refund_credit,
        )
        consumed = consume_credit(
            "test-org", credits=10, action_type="x", db=db_starter,
        )
        first = refund_credit(
            "test-org", original_entry_id=consumed["entry_id"],
            reason="failure", db=db_starter,
        )
        assert first["ok"] is True
        # Attempt to refund the refund entry itself — should fail
        # because it's not a consume.
        second = refund_credit(
            "test-org", original_entry_id=first["refund_entry_id"],
            reason="double_refund", db=db_starter,
        )
        assert second["ok"] is False
        assert second["reason"] == "original_not_consume_entry"


class TestPurchase:
    def test_purchase_records_entry_and_bumps_balance(self, db_starter):
        from clearledgr.services.agent_credit_pool import (
            get_balance, purchase_credits,
        )
        # Monthly grant = 500; top up with 1000 more.
        result = purchase_credits(
            "test-org", credits=1000, actor_id="admin@acme",
            stripe_charge_id="ch_test_123",
            price_usd_cents=9900,
            db=db_starter,
        )
        assert result["ok"] is True
        assert result["credits_added"] == 1000
        assert get_balance("test-org", db=db_starter) == 1500

    def test_purchase_zero_or_negative_rejected(self, db_starter):
        from clearledgr.services.agent_credit_pool import purchase_credits
        assert purchase_credits(
            "test-org", credits=0, actor_id="admin", db=db_starter,
        )["ok"] is False
        assert purchase_credits(
            "test-org", credits=-100, actor_id="admin", db=db_starter,
        )["ok"] is False

    def test_enterprise_purchase_rejected_with_reason(self, db_enterprise):
        from clearledgr.services.agent_credit_pool import purchase_credits
        result = purchase_credits(
            "test-org", credits=1000, actor_id="admin", db=db_enterprise,
        )
        assert result["ok"] is False
        assert result["reason"] == "tier_is_unlimited"


class TestPreviewConsume:
    def test_below_threshold_no_confirmation(self, db_starter):
        from clearledgr.services.agent_credit_pool import preview_consume
        preview = preview_consume(
            "test-org", credits=5,
            confirmation_threshold=10,
            db=db_starter,
        )
        assert preview.allowed is True
        assert preview.requires_confirmation is False
        assert preview.balance_after == 495

    def test_at_or_above_threshold_requires_confirmation(self, db_starter):
        from clearledgr.services.agent_credit_pool import preview_consume
        preview = preview_consume(
            "test-org", credits=20,
            confirmation_threshold=10,
            db=db_starter,
        )
        assert preview.allowed is True
        assert preview.requires_confirmation is True

    def test_insufficient_balance_not_allowed(self, db_starter):
        from clearledgr.services.agent_credit_pool import preview_consume
        preview = preview_consume(
            "test-org", credits=1000,
            confirmation_threshold=10,
            db=db_starter,
        )
        assert preview.allowed is False
        assert preview.requires_confirmation is False
        assert preview.reason == "insufficient_credits"

    def test_enterprise_never_requires_confirmation(self, db_enterprise):
        from clearledgr.services.agent_credit_pool import preview_consume
        preview = preview_consume(
            "test-org", credits=100_000,
            confirmation_threshold=10,
            db=db_enterprise,
        )
        assert preview.allowed is True
        assert preview.unlimited is True
        assert preview.requires_confirmation is False


class TestRecentEntries:
    def test_returns_newest_first(self, db_starter):
        from clearledgr.services.agent_credit_pool import (
            consume_credit, list_recent_entries,
        )
        consume_credit("test-org", credits=5, action_type="a", db=db_starter)
        consume_credit("test-org", credits=7, action_type="b", db=db_starter)
        entries = list_recent_entries("test-org", limit=10, db=db_starter)
        # At minimum: auto_grant + two consumes = 3 entries.
        assert len(entries) >= 3
        # Newest first: most recent entry_type is 'consume' (the b=7 one).
        assert entries[0]["entry_type"] == "consume"
