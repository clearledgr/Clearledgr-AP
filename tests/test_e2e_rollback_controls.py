"""Rollback control enforcement tests.

Proves that ``clearledgr/core/launch_controls.py`` correctly gates ERP posting
and channel actions — the production code paths that check these controls before
executing side-effects.

Wiring verified in production code:
  - clearledgr/services/erp_api_first.py       — get_erp_posting_block_reason()
  - clearledgr/api/slack_invoices.py            — get_channel_action_block_reason("slack")
  - clearledgr/api/teams_invoices.py            — get_channel_action_block_reason("teams")

No HTTP, no external calls — all tests use a temp-file SQLite DB.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core.database import ClearledgrDB, get_db
from clearledgr.core.launch_controls import (
    default_rollback_controls,
    get_channel_action_block_reason,
    get_erp_posting_block_reason,
    get_rollback_controls,
    set_rollback_controls,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    """Fresh isolated temp-file ClearledgrDB with a test org.

    Uses a file-based DB (not :memory:) so that set_rollback_controls()
    persists across calls within the same test.
    """
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    _db = get_db()
    _db.initialize()
    _db.ensure_organization(
        organization_id="rc-org",
        organization_name="Rollback Control Test Org",
        domain="rc.test",
    )
    yield _db
    os.unlink(path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRollbackControlEnforcement:
    """Verifies the rollback control functions work correctly end-to-end."""

    def test_erp_posting_block_clears_and_reinstates(self, db):
        """Enabling erp_posting_disabled returns a block reason;
        disabling it clears the block."""

        # Default state — no block
        assert get_erp_posting_block_reason("rc-org", db=db) is None

        # Enable block with reason
        set_rollback_controls(
            "rc-org",
            {"erp_posting_disabled": True, "reason": "incident_drill_2026"},
            updated_by="ops@rc.test",
            db=db,
        )
        reason = get_erp_posting_block_reason("rc-org", db=db)
        assert reason is not None
        assert "incident_drill_2026" in reason

        # Verify get_rollback_controls reflects the state
        controls = get_rollback_controls("rc-org", db=db)
        assert controls["erp_posting_disabled"] is True
        assert controls["updated_by"] == "ops@rc.test"
        assert controls["active"] is True

        # Clear block
        set_rollback_controls(
            "rc-org",
            {"erp_posting_disabled": False},
            updated_by="ops@rc.test",
            db=db,
        )
        assert get_erp_posting_block_reason("rc-org", db=db) is None

    def test_per_connector_block(self, db):
        """erp_connectors_disabled blocks named connectors but leaves others clear."""

        # Block Xero only
        set_rollback_controls(
            "rc-org",
            {
                "erp_connectors_disabled": ["xero"],
                "reason": "xero_api_outage",
            },
            db=db,
        )

        # Xero is blocked
        xero_reason = get_erp_posting_block_reason("rc-org", erp_type="XERO", db=db)
        assert xero_reason is not None
        assert "xero" in xero_reason.lower()

        # NetSuite is not blocked
        assert get_erp_posting_block_reason("rc-org", erp_type="netsuite", db=db) is None
        # Global flag is still clear — no blanket block
        assert get_erp_posting_block_reason("rc-org", db=db) is None

        # Expand to QuickBooks and SAP
        set_rollback_controls(
            "rc-org",
            {"erp_connectors_disabled": ["xero", "quickbooks", "sap"]},
            db=db,
        )
        for connector in ("xero", "QUICKBOOKS", "SAP"):
            blocked = get_erp_posting_block_reason("rc-org", erp_type=connector, db=db)
            assert blocked is not None, f"Expected block for connector {connector}"

        # NetSuite still clear
        assert get_erp_posting_block_reason("rc-org", erp_type="netsuite", db=db) is None

    def test_channel_action_block_per_channel(self, db):
        """Channel action blocks honour per-channel flags: slack=True, teams=False."""

        # Default — no blocks
        assert get_channel_action_block_reason("rc-org", "slack", db=db) is None
        assert get_channel_action_block_reason("rc-org", "teams", db=db) is None

        # Disable only Slack
        set_rollback_controls(
            "rc-org",
            {
                "channel_actions_disabled": {"slack": True, "teams": False},
                "reason": "slack_outage",
            },
            db=db,
        )

        slack_reason = get_channel_action_block_reason("rc-org", "slack", db=db)
        assert slack_reason is not None
        assert "slack_outage" in slack_reason

        # Teams is NOT blocked
        assert get_channel_action_block_reason("rc-org", "teams", db=db) is None

        # Disable all channels
        set_rollback_controls(
            "rc-org",
            {"channel_actions_disabled": {"all": True}, "reason": "all_channels_drill"},
            db=db,
        )
        for ch in ("slack", "teams"):
            reason = get_channel_action_block_reason("rc-org", ch, db=db)
            assert reason is not None, f"Expected all-channel block for {ch}"

    def test_expired_control_is_inactive(self, db):
        """A control with expires_at in the past is treated as inactive
        — all block functions return None even if flags are set."""

        past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

        set_rollback_controls(
            "rc-org",
            {
                "erp_posting_disabled": True,
                "channel_actions_disabled": {"slack": True},
                "erp_connectors_disabled": ["xero"],
                "reason": "temporary_drill",
                "expires_at": past,
            },
            db=db,
        )

        # Control is stored but inactive due to expiry
        controls = get_rollback_controls("rc-org", db=db)
        assert controls["erp_posting_disabled"] is True   # flag is set
        assert controls["active"] is False                  # but expired

        # All block functions treat expired control as no-op
        assert get_erp_posting_block_reason("rc-org", db=db) is None
        assert get_erp_posting_block_reason("rc-org", erp_type="xero", db=db) is None
        assert get_channel_action_block_reason("rc-org", "slack", db=db) is None
