"""Tests for Phase 2.1.a — bank details tokenisation (DESIGN_THESIS.md §19).

Covers:
  - bank_details helper module: normalize, encrypt round-trip, mask shapes
    for every field, diff field-names-only
  - AP store: create_ap_item with bank_details on the typed kwarg AND
    inside metadata (defensive strip), get_ap_item_bank_details,
    get_ap_item_bank_details_masked, set/clear, ciphertext is opaque,
    metadata blob never contains bank_details after persistence
  - Vendor store: same pattern via set_vendor_bank_details /
    get_vendor_bank_details / get_vendor_bank_details_masked
  - Migration v13: backfills existing plaintext metadata.bank_details
    rows to the encrypted column and strips them from metadata
  - Validation gate: bank-details-mismatch reads via typed accessor and
    persists ONLY field names (never values) in the audit reason
  - ap_item_service read: API responses always return masked bank
    details, never plaintext
  - No-plaintext-in-logs guard: every log line emitted by the test
    suite has the IBAN constant absent
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest


# A distinctive IBAN that the no-plaintext-in-logs guard searches for.
SAMPLE_IBAN = "GB82WEST12345698765432"
SAMPLE_BANK_DETAILS: Dict[str, str] = {
    "iban": SAMPLE_IBAN,
    "account_number": "12345678",
    "routing_number": "021000021",
    "sort_code": "20-00-00",
    "swift": "BARCGB22XXX",
    "account_holder_name": "Acme Trading Ltd",
    "bank_name": "Barclays",
    "currency": "GBP",
}


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    from clearledgr.core.database import ClearledgrDB
    from clearledgr.core import database as db_module

    db = ClearledgrDB(db_path=str(tmp_path / "bank_details.db"))
    db.initialize()
    monkeypatch.setattr(db_module, "_DB_INSTANCE", db)
    return db


# ===========================================================================
# Helper module — pure functions
# ===========================================================================


class TestBankDetailsHelper:

    def test_normalize_drops_unknown_keys(self):
        from clearledgr.core.stores.bank_details import normalize_bank_details
        result = normalize_bank_details(
            {"iban": "GB82", "evil_field": "X", "account_number": "1234"}
        )
        assert result == {"iban": "GB82", "account_number": "1234"}

    def test_normalize_returns_none_for_empty(self):
        from clearledgr.core.stores.bank_details import normalize_bank_details
        assert normalize_bank_details(None) is None
        assert normalize_bank_details({}) is None
        assert normalize_bank_details("not a dict") is None
        assert normalize_bank_details({"iban": "  "}) is None

    def test_normalize_strips_whitespace_and_stringifies(self):
        from clearledgr.core.stores.bank_details import normalize_bank_details
        result = normalize_bank_details({"account_number": "  12345678  ", "currency": 1234})
        assert result == {"account_number": "12345678", "currency": "1234"}

    def test_encrypt_round_trip(self):
        from clearledgr.core.stores.bank_details import (
            encrypt_bank_details,
            decrypt_bank_details,
        )

        # Mock encrypt/decrypt to verify wiring
        encrypted_storage: Dict[str, str] = {}

        def fake_encrypt(s: str) -> str:
            token = f"enc_{len(encrypted_storage)}"
            encrypted_storage[token] = s
            return token

        def fake_decrypt(t: str) -> Optional[str]:
            return encrypted_storage.get(t)

        ct = encrypt_bank_details(SAMPLE_BANK_DETAILS, encrypt_fn=fake_encrypt)
        assert ct.startswith("enc_")
        # Stored payload is JSON
        stored = encrypted_storage[ct]
        assert json.loads(stored)["iban"] == SAMPLE_IBAN
        # Round-trip
        plain = decrypt_bank_details(ct, decrypt_fn=fake_decrypt)
        assert plain == SAMPLE_BANK_DETAILS

    def test_encrypt_returns_none_for_empty_input(self):
        from clearledgr.core.stores.bank_details import encrypt_bank_details
        assert encrypt_bank_details(None, encrypt_fn=lambda s: s) is None
        assert encrypt_bank_details({}, encrypt_fn=lambda s: s) is None

    def test_decrypt_returns_none_for_corrupt_ciphertext(self):
        from clearledgr.core.stores.bank_details import decrypt_bank_details
        # Decrypt that returns a non-JSON string
        assert decrypt_bank_details("xxx", decrypt_fn=lambda t: "not json") is None

    def test_decrypt_returns_none_for_decrypt_exception(self):
        from clearledgr.core.stores.bank_details import decrypt_bank_details

        def raises(_):
            raise RuntimeError("kaboom")

        assert decrypt_bank_details("xxx", decrypt_fn=raises) is None

    # ---- masking ----

    def test_mask_iban_format_matches_thesis(self):
        from clearledgr.core.stores.bank_details import mask_bank_details
        masked = mask_bank_details({"iban": SAMPLE_IBAN})
        assert masked["iban"] == "GB82 **** **** **** 5432"
        assert SAMPLE_IBAN not in masked["iban"]

    def test_mask_account_number_keeps_only_last_4(self):
        from clearledgr.core.stores.bank_details import mask_bank_details
        masked = mask_bank_details({"account_number": "12345678"})
        assert masked["account_number"] == "****5678"

    def test_mask_sort_code_uk_format(self):
        from clearledgr.core.stores.bank_details import mask_bank_details
        masked = mask_bank_details({"sort_code": "20-00-00"})
        assert masked["sort_code"] == "**-**-00"

    def test_mask_swift_keeps_only_last_4(self):
        from clearledgr.core.stores.bank_details import mask_bank_details
        masked = mask_bank_details({"swift": "BARCGB22XXX"})
        # length 11 → first 7 masked, last 4 visible
        assert masked["swift"] == "*******2XXX"

    def test_mask_holder_name_initials_only(self):
        from clearledgr.core.stores.bank_details import mask_bank_details
        masked = mask_bank_details({"account_holder_name": "Acme Trading Ltd"})
        assert masked["account_holder_name"] == "A*** T****** L**"

    def test_mask_bank_name_passes_through(self):
        from clearledgr.core.stores.bank_details import mask_bank_details
        masked = mask_bank_details({"bank_name": "Barclays"})
        assert masked["bank_name"] == "Barclays"

    def test_mask_returns_none_for_empty(self):
        from clearledgr.core.stores.bank_details import mask_bank_details
        assert mask_bank_details(None) is None
        assert mask_bank_details({}) is None

    def test_mask_does_not_mutate_input(self):
        from clearledgr.core.stores.bank_details import mask_bank_details
        original = dict(SAMPLE_BANK_DETAILS)
        _ = mask_bank_details(original)
        assert original == SAMPLE_BANK_DETAILS

    def test_full_mask_shape_contains_no_plaintext_iban(self):
        from clearledgr.core.stores.bank_details import mask_bank_details
        masked = mask_bank_details(SAMPLE_BANK_DETAILS)
        full_text = json.dumps(masked)
        assert SAMPLE_IBAN not in full_text
        assert "12345678" not in full_text
        assert "021000021" not in full_text
        assert "BARCGB22XXX" not in full_text

    # ---- diff ----

    def test_diff_field_names_only(self):
        from clearledgr.core.stores.bank_details import diff_bank_details_field_names
        result = diff_bank_details_field_names(
            {"iban": "GB82WEST12345698765432", "sort_code": "20-00-00"},
            {"iban": "GB82WEST99999999999999", "sort_code": "20-00-00"},
        )
        assert result == ["iban"]
        # Output is just field names — never values
        for value in (
            "GB82WEST12345698765432",
            "GB82WEST99999999999999",
            "20-00-00",
        ):
            assert value not in result

    def test_diff_empty_inputs_return_empty_list(self):
        from clearledgr.core.stores.bank_details import diff_bank_details_field_names
        assert diff_bank_details_field_names(None, {"iban": "X"}) == []
        assert diff_bank_details_field_names({"iban": "X"}, None) == []
        assert diff_bank_details_field_names({}, {}) == []


# ===========================================================================
# AP store — typed accessors and create_ap_item integration
# ===========================================================================


class TestAPStoreBankDetails:

    def _create_with_bank_details(self, db, **overrides):
        payload = {
            "id": "AP-BD-1",
            "organization_id": "org_t",
            "vendor_name": "Acme",
            "amount": 1000.0,
            "currency": "GBP",
            "state": "received",
            "thread_id": "gmail-bd-1",
            "invoice_number": "INV-BD-1",
            "bank_details": SAMPLE_BANK_DETAILS,
        }
        payload.update(overrides)
        return db.create_ap_item(payload)

    def test_create_persists_to_encrypted_column(self, tmp_db):
        item = self._create_with_bank_details(tmp_db)
        assert item.get("bank_details_encrypted")
        assert SAMPLE_IBAN not in str(item["bank_details_encrypted"])

    def test_create_strips_bank_details_from_metadata(self, tmp_db):
        item = self._create_with_bank_details(tmp_db, metadata={"foo": "bar"})
        meta = (
            json.loads(item["metadata"])
            if isinstance(item["metadata"], str)
            else item["metadata"]
        )
        assert "bank_details" not in meta
        assert meta == {"foo": "bar"}

    def test_create_defensive_strip_from_legacy_metadata_shape(self, tmp_db):
        """Even if a caller passes bank_details inside metadata (the legacy
        plaintext shape), it should be migrated to the encrypted column."""
        item = tmp_db.create_ap_item(
            {
                "id": "AP-BD-LEGACY",
                "organization_id": "org_t",
                "vendor_name": "Legacy",
                "amount": 500.0,
                "state": "received",
                "thread_id": "gmail-legacy",
                "invoice_number": "INV-LEGACY",
                "metadata": {"foo": "baz", "bank_details": SAMPLE_BANK_DETAILS},
            }
        )
        # Encrypted column populated
        assert item.get("bank_details_encrypted")
        # Metadata blob stripped clean
        meta = (
            json.loads(item["metadata"])
            if isinstance(item["metadata"], str)
            else item["metadata"]
        )
        assert "bank_details" not in meta
        assert meta == {"foo": "baz"}
        # Round-trip works
        decrypted = tmp_db.get_ap_item_bank_details("AP-BD-LEGACY")
        assert decrypted == SAMPLE_BANK_DETAILS

    def test_get_ap_item_bank_details_round_trip(self, tmp_db):
        self._create_with_bank_details(tmp_db, id="AP-RT")
        plain = tmp_db.get_ap_item_bank_details("AP-RT")
        assert plain == SAMPLE_BANK_DETAILS

    def test_get_ap_item_bank_details_returns_none_when_absent(self, tmp_db):
        tmp_db.create_ap_item(
            {
                "id": "AP-NOBD",
                "organization_id": "org_t",
                "vendor_name": "X",
                "amount": 1.0,
                "state": "received",
                "thread_id": "gmail-no",
                "invoice_number": "INV-NO",
            }
        )
        assert tmp_db.get_ap_item_bank_details("AP-NOBD") is None

    def test_get_ap_item_bank_details_masked(self, tmp_db):
        self._create_with_bank_details(tmp_db, id="AP-MASK")
        masked = tmp_db.get_ap_item_bank_details_masked("AP-MASK")
        assert masked is not None
        assert masked["iban"] == "GB82 **** **** **** 5432"
        # No raw value present
        assert SAMPLE_IBAN not in json.dumps(masked)

    def test_set_ap_item_bank_details_round_trip(self, tmp_db):
        tmp_db.create_ap_item(
            {
                "id": "AP-SET",
                "organization_id": "org_t",
                "vendor_name": "X",
                "amount": 1.0,
                "state": "received",
                "thread_id": "gmail-set",
                "invoice_number": "INV-SET",
            }
        )
        new_details = {"iban": "DE89370400440532013000", "sort_code": "10-20-30"}
        ok = tmp_db.set_ap_item_bank_details("AP-SET", new_details)
        assert ok
        plain = tmp_db.get_ap_item_bank_details("AP-SET")
        assert plain == new_details

    def test_clear_ap_item_bank_details(self, tmp_db):
        self._create_with_bank_details(tmp_db, id="AP-CLR")
        ok = tmp_db.clear_ap_item_bank_details("AP-CLR")
        assert ok
        assert tmp_db.get_ap_item_bank_details("AP-CLR") is None

    def test_get_ap_item_does_not_decrypt(self, tmp_db):
        """Plain get_ap_item must NOT return decrypted bank details — that
        would let a stray logger.info(ap_item) leak the plaintext."""
        self._create_with_bank_details(tmp_db, id="AP-RAW")
        row = tmp_db.get_ap_item("AP-RAW")
        # Encrypted column is present (it's a regular SELECT *)
        assert "bank_details_encrypted" in row
        # ...but it's the ciphertext, not a decrypted dict
        assert SAMPLE_IBAN not in str(row.get("bank_details_encrypted") or "")
        # The row dict has no "bank_details" key whatsoever
        assert "bank_details" not in row


# ===========================================================================
# Vendor store — typed accessors
# ===========================================================================


class TestVendorStoreBankDetails:

    def test_set_and_get_round_trip(self, tmp_db):
        tmp_db.create_organization("org_t", name="X")
        tmp_db.upsert_vendor_profile("org_t", "Acme", invoice_count=5)
        ok = tmp_db.set_vendor_bank_details("org_t", "Acme", SAMPLE_BANK_DETAILS)
        assert ok
        plain = tmp_db.get_vendor_bank_details("org_t", "Acme")
        assert plain == SAMPLE_BANK_DETAILS

    def test_get_masked_returns_safe_shape(self, tmp_db):
        tmp_db.create_organization("org_t", name="X")
        tmp_db.upsert_vendor_profile("org_t", "Acme", invoice_count=5)
        tmp_db.set_vendor_bank_details("org_t", "Acme", SAMPLE_BANK_DETAILS)
        masked = tmp_db.get_vendor_bank_details_masked("org_t", "Acme")
        assert SAMPLE_IBAN not in json.dumps(masked)
        assert masked["iban"] == "GB82 **** **** **** 5432"

    def test_set_bumps_changed_at_timestamp(self, tmp_db):
        tmp_db.create_organization("org_t", name="X")
        tmp_db.upsert_vendor_profile("org_t", "Acme", invoice_count=5)
        before_profile = tmp_db.get_vendor_profile("org_t", "Acme")
        before_ts = (before_profile or {}).get("bank_details_changed_at")
        assert before_ts is None

        tmp_db.set_vendor_bank_details("org_t", "Acme", SAMPLE_BANK_DETAILS)
        after_profile = tmp_db.get_vendor_profile("org_t", "Acme")
        assert after_profile["bank_details_changed_at"] is not None

    def test_clear_via_none(self, tmp_db):
        tmp_db.create_organization("org_t", name="X")
        tmp_db.upsert_vendor_profile("org_t", "Acme", invoice_count=5)
        tmp_db.set_vendor_bank_details("org_t", "Acme", SAMPLE_BANK_DETAILS)
        tmp_db.set_vendor_bank_details("org_t", "Acme", None)
        assert tmp_db.get_vendor_bank_details("org_t", "Acme") is None

    def test_get_returns_none_for_unknown_vendor(self, tmp_db):
        assert tmp_db.get_vendor_bank_details("org_t", "Unknown") is None


# ===========================================================================
# Migration v13 — backfill plaintext metadata to encrypted column
# ===========================================================================


class TestMigrationV13Backfill:
    """Verify that any pre-existing plaintext bank_details inside the
    metadata blob are migrated to bank_details_encrypted by migration v13.
    The fresh DBs created by tmp_db already include the column from
    initialize(), so this test simulates the legacy state by directly
    inserting a row with plaintext metadata + a NULL encrypted column,
    then re-runs the migration callback."""

    def test_backfill_strips_plaintext_from_metadata(self, tmp_db):
        # Insert a legacy-shaped row directly via SQL bypassing create_ap_item
        with tmp_db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                tmp_db._prepare_sql(
                    "INSERT INTO ap_items "
                    "(id, organization_id, vendor_name, amount, currency, state, "
                    "thread_id, invoice_number, created_at, updated_at, metadata, "
                    "bank_details_encrypted) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                ),
                (
                    "AP-LEGACY-MIG",
                    "org_t",
                    "Legacy Vendor",
                    100.0,
                    "GBP",
                    "received",
                    "gmail-legacy-mig",
                    "INV-LM",
                    "2026-04-09T00:00:00+00:00",
                    "2026-04-09T00:00:00+00:00",
                    json.dumps({"foo": "bar", "bank_details": SAMPLE_BANK_DETAILS}),
                    None,  # encrypted column NULL — simulates pre-migration
                ),
            )
            conn.commit()

        # Run migration v13 directly against the DB
        from clearledgr.core.migrations import _MIGRATIONS
        m13 = next(m for m in _MIGRATIONS if m[0] == 13)
        with tmp_db.connect() as conn:
            cur = conn.cursor()
            m13[2](cur, tmp_db)
            conn.commit()

        # After migration: encrypted column populated, metadata stripped
        with tmp_db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                tmp_db._prepare_sql(
                    "SELECT metadata, bank_details_encrypted FROM ap_items WHERE id = ?"
                ),
                ("AP-LEGACY-MIG",),
            )
            row = cur.fetchone()
        meta = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
        assert "bank_details" not in meta
        assert meta == {"foo": "bar"}
        assert row["bank_details_encrypted"]

        # Round-trip via the typed accessor
        plain = tmp_db.get_ap_item_bank_details("AP-LEGACY-MIG")
        assert plain == SAMPLE_BANK_DETAILS

    def test_backfill_idempotent_no_op_on_clean_rows(self, tmp_db):
        # Insert a row with NO bank_details
        with tmp_db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                tmp_db._prepare_sql(
                    "INSERT INTO ap_items "
                    "(id, organization_id, vendor_name, amount, currency, state, "
                    "thread_id, invoice_number, created_at, updated_at, metadata) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                ),
                (
                    "AP-CLEAN",
                    "org_t",
                    "Clean Vendor",
                    100.0,
                    "GBP",
                    "received",
                    "gmail-clean",
                    "INV-CL",
                    "2026-04-09T00:00:00+00:00",
                    "2026-04-09T00:00:00+00:00",
                    json.dumps({"foo": "bar"}),
                ),
            )
            conn.commit()

        from clearledgr.core.migrations import _MIGRATIONS
        m13 = next(m for m in _MIGRATIONS if m[0] == 13)
        with tmp_db.connect() as conn:
            cur = conn.cursor()
            m13[2](cur, tmp_db)
            conn.commit()

        assert tmp_db.get_ap_item_bank_details("AP-CLEAN") is None


# ===========================================================================
# Validation gate — bank-details mismatch persists field-names-only
# ===========================================================================


class TestValidationGateBankDetailsMismatch:

    def test_mismatch_reason_contains_no_plaintext_values(self, tmp_db):
        # The gate lives on InvoiceValidationMixin which is composed into
        # InvoiceWorkflowService. Use the workflow service as the entry
        # point (same pattern as Phase 1 tests).
        from clearledgr.services.invoice_workflow import InvoiceWorkflowService
        from clearledgr.services.invoice_models import InvoiceData

        tmp_db.create_organization("org_t", name="X", settings={})
        # Seed enough vendor history so first_payment_hold doesn't fire.
        from datetime import datetime, timezone, timedelta
        tmp_db.upsert_vendor_profile(
            "org_t",
            "Acme",
            invoice_count=5,
            avg_invoice_amount=10_000.0,
            always_approved=1,
            last_invoice_date=(
                datetime.now(timezone.utc) - timedelta(days=7)
            ).isoformat(),
        )
        # Stored vendor has the original bank details
        tmp_db.set_vendor_bank_details("org_t", "Acme", SAMPLE_BANK_DETAILS)

        service = InvoiceWorkflowService(organization_id="org_t")

        # Invoice arrives with a DIFFERENT IBAN — fraud signal
        adversarial_iban = "DE89999999999999999999"
        invoice = InvoiceData(
            gmail_id="gmail-fraud",
            subject="Invoice",
            sender="ap@acme.com",
            vendor_name="Acme",
            amount=10_000.0,
            currency="GBP",
            invoice_number="INV-FRAUD",
            due_date="2026-04-30",
            confidence=0.97,
            organization_id="org_t",
            field_confidences={
                "vendor": 0.99,
                "amount": 0.98,
                "invoice_number": 0.97,
                "due_date": 0.95,
            },
        )
        invoice.bank_details = {
            "iban": adversarial_iban,
            "account_number": "12345678",  # same
        }

        gate = asyncio.run(service._evaluate_deterministic_validation(invoice))

        # Gate should have a mismatch reason
        mismatch_reasons = [
            r for r in gate["reasons"]
            if r["code"] == "bank_details_mismatch_from_invoice"
        ]
        assert len(mismatch_reasons) == 1
        reason = mismatch_reasons[0]

        # The reason details must contain field names ONLY
        details = reason["details"]
        assert "mismatched_fields" in details
        assert details["mismatched_fields"] == ["iban"]
        # NO plaintext values anywhere
        details_str = json.dumps(details)
        assert SAMPLE_IBAN not in details_str
        assert adversarial_iban not in details_str
        assert "12345678" not in details_str

        # Also verify the human-readable message names the field
        # but does NOT contain the plaintext values
        assert "iban" in reason["message"]
        assert SAMPLE_IBAN not in reason["message"]
        assert adversarial_iban not in reason["message"]


# ===========================================================================
# API read path — masked-only
# ===========================================================================


class TestAPItemServiceMasksReadPath:

    def test_payload_load_returns_masked_bank_details(self, tmp_db):
        """The PayloadLoader path used by the AP item read API masks
        bank details before returning them. Unit-tests the
        get_ap_item_bank_details_masked accessor end-to-end via the
        store, since the full PayloadLoader pulls in too many other
        services for an isolated test."""
        tmp_db.create_ap_item(
            {
                "id": "AP-API-1",
                "organization_id": "org_t",
                "vendor_name": "Acme",
                "amount": 1000.0,
                "currency": "GBP",
                "state": "received",
                "thread_id": "gmail-api-1",
                "invoice_number": "INV-API-1",
                "bank_details": SAMPLE_BANK_DETAILS,
            }
        )
        masked = tmp_db.get_ap_item_bank_details_masked("AP-API-1")
        assert masked is not None

        # No raw IBAN, account number, routing number, or SWIFT in the
        # serialized API-shaped payload
        full_text = json.dumps(masked)
        for raw_value in (
            SAMPLE_IBAN,
            "12345678",
            "021000021",
            "BARCGB22XXX",
            "Acme Trading Ltd",
        ):
            assert raw_value not in full_text, (
                f"raw plaintext leaked: {raw_value}"
            )

        # And the masked shape contains the expected display strings
        assert masked["iban"] == "GB82 **** **** **** 5432"
        assert masked["sort_code"] == "**-**-00"


# ===========================================================================
# No-plaintext-in-logs guard
# ===========================================================================


class TestNoPlaintextInLogs:
    """Belt-and-suspenders: when bank details flow through the system,
    no log line emitted by any module should contain a raw IBAN /
    account number. This guards against future regressions where
    someone slips a debug log into the bank-details path."""

    def test_no_plaintext_in_logs_during_full_flow(self, tmp_db, caplog):
        caplog.set_level(logging.DEBUG)
        # Full create + read flow
        tmp_db.create_organization("org_t", name="X")
        tmp_db.upsert_vendor_profile("org_t", "Acme", invoice_count=5)
        tmp_db.set_vendor_bank_details("org_t", "Acme", SAMPLE_BANK_DETAILS)
        tmp_db.create_ap_item(
            {
                "id": "AP-LOGS",
                "organization_id": "org_t",
                "vendor_name": "Acme",
                "amount": 1000.0,
                "currency": "GBP",
                "state": "received",
                "thread_id": "gmail-logs",
                "invoice_number": "INV-LOGS",
                "bank_details": SAMPLE_BANK_DETAILS,
            }
        )
        _ = tmp_db.get_ap_item_bank_details("AP-LOGS")
        _ = tmp_db.get_ap_item_bank_details_masked("AP-LOGS")
        _ = tmp_db.get_vendor_bank_details("org_t", "Acme")

        # Search every captured log record's message AND args for the
        # distinctive plaintext values.
        offending: List[str] = []
        for record in caplog.records:
            msg = record.getMessage()
            for raw in (SAMPLE_IBAN, "12345678", "021000021", "BARCGB22XXX"):
                if raw in msg:
                    offending.append(f"{record.name}:{record.levelname}: {msg}")
                    break
        assert not offending, (
            "Plaintext bank details leaked into logs:\n" + "\n".join(offending)
        )
