"""Tests for the llm_call_log ↔ Box audit trail link.

Reconstructability invariant — given a Box's (box_id, box_type), an
auditor must be able to join audit_events → llm_call_log and see
every Claude call that shaped the Box's state. This test ensures
the link lands correctly on the llm_call_log row when callers pass
box_id + correlation_id through the gateway.
"""
from __future__ import annotations


class TestLLMCallLogLink:
    def test_log_call_persists_box_keys_and_correlation_id(self, tmp_path, monkeypatch):
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

        # Given the Box id, find the LLM calls via box_id + box_type.
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                db._prepare_sql(
                    "SELECT id, action, box_id, box_type, correlation_id, "
                    "       input_tokens, output_tokens, cost_estimate_usd "
                    "FROM llm_call_log "
                    "WHERE box_id = ? AND box_type = 'ap_item'"
                ),
                ("ap-12345",),
            )
            row = cur.fetchone()
        assert row is not None
        row_dict = dict(row) if hasattr(row, "keys") else None
        if row_dict:
            assert row_dict["box_id"] == "ap-12345"
            assert row_dict["box_type"] == "ap_item"
            assert row_dict["correlation_id"] == "corr-abc"
            assert row_dict["action"] == "extract_invoice_fields"
        else:
            assert row[2] == "ap-12345"
            assert row[3] == "ap_item"
            assert row[4] == "corr-abc"

    def test_log_call_accepts_explicit_box_kwargs(self, tmp_path, monkeypatch):
        """Callers can pass (box_id, box_type) directly for non-AP
        Boxes — vendor onboarding, clawback, etc.
        """
        monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "llm_explicit.db"))
        monkeypatch.delenv("DATABASE_URL", raising=False)
        import clearledgr.core.database as db_module
        db_module._DB_INSTANCE = None
        db = db_module.get_db()
        db.initialize()

        from clearledgr.core.llm_gateway import LLMGateway, LLMAction
        gw = LLMGateway.__new__(LLMGateway)
        gw._db = db

        call_id = gw._log_call(
            action=LLMAction.CLASSIFY_VENDOR,
            model="claude-haiku-4-5",
            input_tokens=500,
            output_tokens=100,
            latency_ms=300,
            cost_estimate=0.001,
            truncated=False,
            error=None,
            organization_id="test-org",
            box_id="VO-xyz",
            box_type="vendor_onboarding_session",
            correlation_id="corr-vo-1",
        )
        assert call_id is not None

        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                db._prepare_sql(
                    "SELECT box_id, box_type, correlation_id "
                    "FROM llm_call_log WHERE id = ?"
                ),
                (call_id,),
            )
            row = cur.fetchone()
        assert row is not None
        if hasattr(row, "keys"):
            r = dict(row)
            assert r["box_id"] == "VO-xyz"
            assert r["box_type"] == "vendor_onboarding_session"
            assert r["correlation_id"] == "corr-vo-1"
        else:
            assert row[0] == "VO-xyz"
            assert row[1] == "vendor_onboarding_session"
            assert row[2] == "corr-vo-1"

    def test_log_call_without_link_still_records(self, tmp_path, monkeypatch):
        """Classification calls that run before a Box exists (e.g.
        classify_email for an email that hasn't yet become a Box)
        may pass nothing — the row records with null box keys.
        """
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
        assert row is not None
        box_id = row[0] if not hasattr(row, "keys") else dict(row)["box_id"]
        box_type = row[1] if not hasattr(row, "keys") else dict(row)["box_type"]
        assert box_id is None
        assert box_type is None
