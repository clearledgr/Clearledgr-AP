"""SAPAdapter park methods must fail closed.

The adapter has no live SAP write path. A non-dry-run park must never report
``status="parked"`` for a document that was never sent to SAP — it fails
closed (``status="failed"``, gated by FEATURE_SAP_LIVE_WRITE, off by default).
Dry-run requests return an honest preview.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from solden.models.erp import (  # noqa: E402
    ERPMetadata,
    ParkedAPInvoiceRequest,
    SAPDocumentConfig,
)
from solden.services.erp.sap import SAPAdapter  # noqa: E402


def _req(dry_run: bool) -> ParkedAPInvoiceRequest:
    return ParkedAPInvoiceRequest(
        metadata=ERPMetadata(vendor_id="100001", amount=125.0, currency="EUR", invoice_date="2026-05-24"),
        config=SAPDocumentConfig(company_code="1000", currency="EUR", dry_run=dry_run),
    )


def test_dry_run_returns_honest_preview():
    result = SAPAdapter().park_ap_invoice(_req(dry_run=True))
    assert result.mode == "dry_run"
    assert result.status == "parked"
    assert "would call" in (result.message or "").lower()


def test_live_park_fails_closed_when_flag_off(monkeypatch):
    monkeypatch.setattr(
        "solden.services.erp.sap.is_sap_live_write_enabled", lambda: False
    )
    result = SAPAdapter().park_ap_invoice(_req(dry_run=False))
    # Never "parked" for a document that was never sent.
    assert result.status == "failed"
    assert result.mode == "live"
    assert "FEATURE_SAP_LIVE_WRITE" in (result.message or "")


def test_live_park_raises_when_flag_on_but_unimplemented(monkeypatch):
    monkeypatch.setattr(
        "solden.services.erp.sap.is_sap_live_write_enabled", lambda: True
    )
    with pytest.raises(NotImplementedError):
        SAPAdapter().park_ap_invoice(_req(dry_run=False))
