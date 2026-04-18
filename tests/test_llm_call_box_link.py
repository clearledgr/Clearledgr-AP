"""Tests for the llm_call_log ↔ Box audit trail link.

Reconstructability invariant — given a Box's ap_item_id, an auditor
must be able to join audit_events → llm_call_log and see every
Claude call that shaped the Box's state. This test ensures the
link lands correctly on the llm_call_log row when callers pass
ap_item_id + correlation_id through the gateway.
"""
from __future__ import annotations

import pytest


class TestLLMCallLogLink:
    def test_log_call_persists_ap_item_id_and_correlation_id(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "llm.db"))
        monkeypatch.delenv("DATABASE_URL", raising=False)
        import clearledgr.core.database as db_module
        db_module._DB_INSTANCE = None
        db = db_module.get_db()
        db.initialize()

        from clearledgr.core.llm_gateway import LLMGateway, LLMAction
        gw = LLMGateway.__new__(LLMGateway)
        gw._db = db

        call_id = gw._log_call(
            action=LLMAction.EXTRACT_INVOICE_FIELDS,
            model="claude-haiku-4-5",
            input_tokens=1200,
            output_tokens=350,
            latency_ms=820,
            cost_estimate=0.002,
            truncated=False,
            error=None,
            organization_id="test-org",
            ap_item_id="ap-12345",
            correlation_id="corr-abc",
        )
        assert call_id is not None
        assert call_id.startswith("LLM-")

        # Cross-reference: given the ap_item_id, find the LLM calls.
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                db._prepare_sql(
                    "SELECT id, action, ap_item_id, correlation_id, input_tokens, "
                    "       output_tokens, cost_estimate_usd "
                    "FROM llm_call_log WHERE ap_item_id = ?"
                ),
                ("ap-12345",),
            )
            row = cur.fetchone()
        assert row is not None
        # Tuple or Row indexing works here — we only need to confirm
        # the two link columns land.
        row_dict = dict(row) if hasattr(row, "keys") else None
        if row_dict:
            assert row_dict["ap_item_id"] == "ap-12345"
            assert row_dict["correlation_id"] == "corr-abc"
            assert row_dict["action"] == "extract_invoice_fields"
        else:
            # Positional fallback
            assert row[2] == "ap-12345"
            assert row[3] == "corr-abc"

    def test_log_call_without_link_still_records(self, tmp_path, monkeypatch):
        # Legacy caller that hasn't been updated yet — call should
        # still record, just without the link columns populated.
        monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "llm_nolink.db"))
        monkeypatch.delenv("DATABASE_URL", raising=False)
        import clearledgr.core.database as db_module
        db_module._DB_INSTANCE = None
        db = db_module.get_db()
        db.initialize()

        from clearledgr.core.llm_gateway import LLMGateway, LLMAction
        gw = LLMGateway.__new__(LLMGateway)
        gw._db = db

        call_id = gw._log_call(
            action=LLMAction.CLASSIFY_EMAIL,
            model="claude-haiku-4-5",
            input_tokens=500,
            output_tokens=100,
            latency_ms=300,
            cost_estimate=0.001,
            truncated=False,
            error=None,
            organization_id="test-org",
        )
        assert call_id is not None  # Still records

        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                db._prepare_sql("SELECT ap_item_id, correlation_id FROM llm_call_log WHERE id = ?"),
                (call_id,),
            )
            row = cur.fetchone()
        assert row is not None
        ap_id = row[0] if not hasattr(row, "keys") else dict(row)["ap_item_id"]
        corr = row[1] if not hasattr(row, "keys") else dict(row)["correlation_id"]
        assert ap_id is None
        assert corr is None
