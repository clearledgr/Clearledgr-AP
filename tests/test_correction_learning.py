from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest

import clearledgr.services.correction_learning as correction_learning_module
from clearledgr.services.correction_learning import CorrectionLearningService


class _SQLiteBackedDB:
    """In-test DB stub that writes to a local SQLite file. Production
    correction_learning.py no longer branches on engine — both
    Postgres and SQLite 3.24+ support ``ON CONFLICT (id) DO UPDATE
    SET``, so the SQL issued by the service works here unchanged as
    long as we expose a ``_prepare_sql`` passthrough matching
    ClearledgrDB's protocol.
    """

    def __init__(self, db_path: Path):
        self.db_path = str(db_path)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _prepare_sql(self, sql: str) -> str:
        # SQLite uses ? natively; strip the INSERT OR IGNORE rewrite
        # (ClearledgrDB rewrites to ON CONFLICT DO NOTHING for PG).
        return sql


@pytest.fixture
def learning_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CorrectionLearningService:
    fake_db = _SQLiteBackedDB(tmp_path / "correction-learning.db")
    monkeypatch.setattr(correction_learning_module, "get_db", lambda: fake_db)
    return CorrectionLearningService("org-test")


def test_record_correction_persists_normalized_event_stats_and_reviewed_case(
    learning_service: CorrectionLearningService,
):
    result = learning_service.record_correction(
        correction_type="invoice_number",
        original_value="INV-OLD",
        corrected_value="INV-NEW",
        context={
            "ap_item_id": "ap-123",
            "vendor": "Google Cloud EMEA Limited",
            "sender": "payments-noreply@google.com",
            "subject": "Google Workspace: Your invoice is available",
            "snippet": "Invoice number INV-OLD is attached.",
            "body_excerpt": "Invoice Number: INV-OLD",
            "attachment_names": ["5449235811.pdf"],
            "document_type": "invoice",
            "selected_source": "attachment",
            "expected_fields": {
                "vendor": "Google Cloud EMEA Limited",
                "primary_amount": 40.23,
                "currency": "EUR",
                "primary_invoice": "INV-NEW",
                "email_type": "invoice",
            },
        },
        user_id="mo@clearledgr.com",
        invoice_id="thread-123",
        feedback="Attachment has the correct invoice number.",
    )

    assert result["normalized_event_id"]
    assert result["vendor_layout_stat_id"]
    assert result["reviewed_case_id"] == "reviewed_ap-123"

    with learning_service.db.connect() as conn:
        event_row = conn.execute("SELECT * FROM agent_correction_events").fetchone()
        stat_row = conn.execute("SELECT * FROM vendor_layout_error_stats").fetchone()
        reviewed_row = conn.execute("SELECT * FROM reviewed_extraction_cases").fetchone()

    assert event_row["field_name"] == "invoice_number"
    assert event_row["sender_domain"] == "google.com"
    assert json.loads(event_row["expected_fields_json"])["primary_invoice"] == "INV-NEW"

    assert stat_row["vendor_name"] == "Google Cloud EMEA Limited"
    assert stat_row["field_name"] == "invoice_number"
    assert stat_row["correction_count"] == 1

    reviewed_payload = json.loads(reviewed_row["expected_fields_json"])
    assert reviewed_row["ap_item_id"] == "ap-123"
    assert reviewed_payload["primary_invoice"] == "INV-NEW"
    assert reviewed_payload["vendor"] == "Google Cloud EMEA Limited"


def test_record_correction_auto_exports_reviewed_cases(
    learning_service: CorrectionLearningService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    export_path = tmp_path / "reviewed-production.json"
    monkeypatch.setenv("CLEARLEDGR_REVIEWED_EXTRACTION_EXPORT_PATH", str(export_path))

    result = learning_service.record_correction(
        correction_type="due_date",
        original_value="2026-02-01",
        corrected_value="2026-02-15",
        context={
            "ap_item_id": "ap-456",
            "vendor": "Designco",
            "sender": "billing@designco.com",
            "subject": "Invoice INV-7788 for February",
            "snippet": "Due date updated to 2026-02-15.",
            "attachment_names": ["invoice-inv-7788.pdf"],
            "document_type": "invoice",
            "selected_source": "email",
            "expected_fields": {
                "vendor": "Designco",
                "primary_amount": 280.0,
                "currency": "USD",
                "primary_invoice": "INV-7788",
                "due_date": "2026-02-15",
                "email_type": "invoice",
            },
        },
        user_id="mo@clearledgr.com",
        invoice_id="thread-456",
    )

    export_result = result["reviewed_case_export"]
    assert export_result
    assert export_result["path"] == str(export_path)
    assert export_path.exists()

    payload = json.loads(export_path.read_text())
    assert payload["organization_id"] == "org-test"
    assert len(payload["cases"]) == 1
    assert payload["cases"][0]["id"] == "reviewed_ap-456"
    assert payload["cases"][0]["expected"]["due_date"] == "2026-02-15"
    assert payload["cases"][0]["metadata"]["correction_fields"] == ["due_date"]


def test_review_history_tightening_returns_threshold_overrides(
    learning_service: CorrectionLearningService,
):
    for idx in range(3):
        learning_service.record_correction(
            correction_type="invoice_number",
            original_value=f"INV-OLD-{idx}",
            corrected_value=f"INV-NEW-{idx}",
            context={
                "ap_item_id": f"ap-tighten-{idx}",
                "vendor": "Google Cloud EMEA Limited",
                "sender": "payments-noreply@google.com",
                "subject": "Google Workspace: Your invoice is available",
                "snippet": "Invoice number needs correction.",
                "attachment_names": ["5449235811.pdf"],
                "document_type": "invoice",
                "selected_source": "attachment",
                "expected_fields": {
                    "vendor": "Google Cloud EMEA Limited",
                    "primary_amount": 40.23,
                    "currency": "EUR",
                    "primary_invoice": f"INV-NEW-{idx}",
                    "email_type": "invoice",
                },
            },
            user_id="mo@clearledgr.com",
            invoice_id=f"thread-tighten-{idx}",
        )

    adjustments = learning_service.get_extraction_confidence_adjustments(
        vendor_name="Google Cloud EMEA Limited",
        sender_domain="google.com",
        document_type="invoice",
    )

    assert adjustments["profile_id"] == "learned_review_history_tightening"
    assert adjustments["threshold_overrides"]["invoice_number"] == 0.96
    assert adjustments["signal_count"] == 3


def test_record_review_outcome_builds_confirmation_snapshot(
    learning_service: CorrectionLearningService,
):
    result = learning_service.record_review_outcome(
        field_name="amount",
        outcome_type="confirmed_correct",
        context={
            "ap_item_id": "ap-review-1",
            "vendor": "Google Cloud EMEA Limited",
            "sender": "payments-noreply@google.com",
            "subject": "Google Workspace: Your invoice is available",
            "document_type": "invoice",
            "selected_source": "attachment",
            "confidence_profile_id": "known_billing_attachment_invoice",
            "attachment_names": ["5449235811.pdf"],
        },
        user_id="mo@clearledgr.com",
        selected_source="attachment",
        outcome_tags=["confirmed_correct", "resolved_with_attachment"],
    )

    assert result["review_outcome_event_id"]
    assert result["review_stat_id"]

    snapshot = learning_service.get_extraction_review_calibration_snapshot(
        vendor_name="Google Cloud EMEA Limited",
        sender_domain="google.com",
        document_type="invoice",
        confidence_profile_id="known_billing_attachment_invoice",
    )

    assert snapshot["status"] == "available"
    assert snapshot["summary"]["total_reviews"] == 1
    assert snapshot["fields"]["amount"]["review_count"] == 1
    assert snapshot["fields"]["amount"]["confirmed_count"] == 1
    assert snapshot["fields"]["amount"]["correction_rate"] == 0.0
    assert snapshot["fields"]["amount"]["source_win_rates"]["attachment"] == 1.0
