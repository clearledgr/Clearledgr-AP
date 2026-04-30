"""Tests for Wave 1 / A5 — mandatory GL gate at posting.

Coverage:
  * Default behaviour (no setting): gate is ON. AP items without GL
    fail pre_post_validate with reason ``mandatory_gl``.
  * Single-line invoice with metadata.gl_code: passes.
  * Multi-line invoice with all lines carrying gl_code: passes.
  * Multi-line invoice with one missing gl_code: fails, reports the
    missing line indexes.
  * Multi-line invoice with non-dict line entries: those indexes are
    treated as missing GL.
  * AP item with metadata.suggested_gl_code (from extraction) is
    accepted as a top-level fallback for single-line items.
  * Per-tenant disable via settings_json["mandatory_gl_at_posting"]=
    false: previously-failing items now pass.
  * line_items field accepts ``account_code`` and ``gl_account`` as
    aliases for ``gl_code`` (handles vendor-format variability).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.integrations.erp_router import pre_post_validate  # noqa: E402


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("default", organization_name="default")
    return inst


def _seed_ap_item_ready_to_post(db, *, item_id: str, metadata: dict):
    """Create an AP item in ready_to_post with the metadata blob set."""
    item = db.create_ap_item({
        "id": item_id,
        "organization_id": "default",
        "vendor_name": "Acme",
        "amount": 500.0,
        "currency": "USD",
        "invoice_number": f"INV-{item_id}",
        "state": "ready_to_post",
        "metadata": metadata,
    })
    return item


# ─── Default-on behaviour ───────────────────────────────────────────


def test_no_gl_anywhere_fails_with_mandatory_gl_reason(db):
    item = _seed_ap_item_ready_to_post(
        db, item_id="ap-no-gl-1",
        metadata={"line_items": None},
    )
    out = pre_post_validate(item["id"], "default", db=db)
    assert out["valid"] is False
    assert any(f["check"] == "mandatory_gl" for f in out["failures"]), out["failures"]


def test_top_level_gl_code_passes_single_line_invoice(db):
    item = _seed_ap_item_ready_to_post(
        db, item_id="ap-toplevel-gl-1",
        metadata={"gl_code": "6210"},  # no line_items, single-line case
    )
    out = pre_post_validate(item["id"], "default", db=db)
    # The mandatory_gl check passes; other checks may add their own
    # failures (e.g. duplicate scan), so we only assert the GL gate
    # didn't fire.
    gl_failures = [f for f in out["failures"] if f["check"] == "mandatory_gl"]
    assert gl_failures == []


def test_suggested_gl_code_accepted_as_fallback(db):
    """The LLM's ``suggested_gl_code`` (from extraction) counts as a
    top-level GL for the single-line gate."""
    item = _seed_ap_item_ready_to_post(
        db, item_id="ap-suggested-gl-1",
        metadata={"suggested_gl_code": "6100"},
    )
    out = pre_post_validate(item["id"], "default", db=db)
    gl_failures = [f for f in out["failures"] if f["check"] == "mandatory_gl"]
    assert gl_failures == []


# ─── Multi-line invoices ────────────────────────────────────────────


def test_all_lines_with_gl_code_pass(db):
    item = _seed_ap_item_ready_to_post(
        db, item_id="ap-multi-ok-1",
        metadata={
            "line_items": [
                {"description": "L1", "amount": 100.0, "gl_code": "6210"},
                {"description": "L2", "amount": 400.0, "gl_code": "6500"},
            ],
        },
    )
    out = pre_post_validate(item["id"], "default", db=db)
    gl_failures = [f for f in out["failures"] if f["check"] == "mandatory_gl"]
    assert gl_failures == []


def test_one_missing_line_gl_fails_with_indexes(db):
    item = _seed_ap_item_ready_to_post(
        db, item_id="ap-multi-miss-1",
        metadata={
            "line_items": [
                {"description": "L1", "amount": 100.0, "gl_code": "6210"},
                {"description": "L2", "amount": 200.0},  # missing
                {"description": "L3", "amount": 200.0, "gl_code": "6500"},
            ],
        },
    )
    out = pre_post_validate(item["id"], "default", db=db)
    gl_failures = [f for f in out["failures"] if f["check"] == "mandatory_gl"]
    assert len(gl_failures) == 1
    assert "1" in gl_failures[0]["missing_line_indexes"]


def test_non_dict_line_entries_treated_as_missing(db):
    """A line entry that's a string or int (corrupt extraction) is
    treated as missing GL — fail closed."""
    item = _seed_ap_item_ready_to_post(
        db, item_id="ap-multi-corrupt-1",
        metadata={
            "line_items": [
                {"description": "Good", "amount": 100.0, "gl_code": "6210"},
                "not-a-dict",
                42,
            ],
        },
    )
    out = pre_post_validate(item["id"], "default", db=db)
    gl_failures = [f for f in out["failures"] if f["check"] == "mandatory_gl"]
    assert len(gl_failures) == 1
    assert "1" in gl_failures[0]["missing_line_indexes"]
    assert "2" in gl_failures[0]["missing_line_indexes"]


def test_alias_field_names_accepted(db):
    """Some vendors / extractions output ``account_code`` or
    ``gl_account`` instead of ``gl_code``. The gate accepts all three."""
    item = _seed_ap_item_ready_to_post(
        db, item_id="ap-multi-alias-1",
        metadata={
            "line_items": [
                {"description": "via gl_code", "amount": 100.0, "gl_code": "6210"},
                {"description": "via gl_account", "amount": 100.0, "gl_account": "6500"},
                {"description": "via account_code", "amount": 100.0, "account_code": "6800"},
            ],
        },
    )
    out = pre_post_validate(item["id"], "default", db=db)
    gl_failures = [f for f in out["failures"] if f["check"] == "mandatory_gl"]
    assert gl_failures == []


# ─── Per-tenant disable ─────────────────────────────────────────────


def test_per_tenant_disable_lets_failing_items_pass(db):
    """Tenants that opt out of mandatory_gl get the legacy default-
    account fallback. Setting key: ``mandatory_gl_at_posting=False``."""
    db.update_organization(
        "default",
        settings_json={"mandatory_gl_at_posting": False},
    )
    item = _seed_ap_item_ready_to_post(
        db, item_id="ap-disabled-1",
        metadata={"line_items": None},  # no GL anywhere
    )
    out = pre_post_validate(item["id"], "default", db=db)
    gl_failures = [f for f in out["failures"] if f["check"] == "mandatory_gl"]
    assert gl_failures == []


def test_per_tenant_disable_via_string_value(db):
    """Settings JSON sometimes round-trips booleans as strings ('false',
    '0'). The gate handles both."""
    db.update_organization(
        "default",
        settings_json={"mandatory_gl_at_posting": "false"},
    )
    item = _seed_ap_item_ready_to_post(
        db, item_id="ap-disabled-str-1",
        metadata={"line_items": None},
    )
    out = pre_post_validate(item["id"], "default", db=db)
    gl_failures = [f for f in out["failures"] if f["check"] == "mandatory_gl"]
    assert gl_failures == []


def test_default_when_settings_missing_is_enforced(db):
    """Tenants that have never touched the setting get the safe
    default — gate ON, mandatory GL enforced."""
    # No update_organization call — settings_json stays default
    item = _seed_ap_item_ready_to_post(
        db, item_id="ap-default-on-1",
        metadata={},
    )
    out = pre_post_validate(item["id"], "default", db=db)
    assert any(f["check"] == "mandatory_gl" for f in out["failures"])
