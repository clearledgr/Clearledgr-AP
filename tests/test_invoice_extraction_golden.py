from __future__ import annotations

from scripts.evaluate_invoice_extraction import (
    DEFAULT_DATASET_PATHS,
    DEFAULT_VENDOR_PACKS_PATH,
    _load_vendor_pack_config,
    apply_vendor_pack_gates,
    evaluate_cases,
    load_cases,
)


CRITICAL_FIELD_THRESHOLDS = {
    "amount": 0.99,
    "currency": 0.99,
    "invoice_number": 0.95,
    "vendor": 0.90,
    "document_type": 0.99,
}


def test_invoice_extraction_golden_field_accuracy(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    report = evaluate_cases(load_cases(DEFAULT_DATASET_PATHS))
    report = apply_vendor_pack_gates(
        report,
        vendor_pack_config=_load_vendor_pack_config(DEFAULT_VENDOR_PACKS_PATH),
    )
    critical_field_accuracy = report.get("critical_field_accuracy") or {}

    assert report["dataset_size"] >= 65
    assert report["overall_weighted_score"] >= 0.94
    assert report["perfect_case_rate"] >= 0.80

    for field, threshold in CRITICAL_FIELD_THRESHOLDS.items():
        assert critical_field_accuracy.get(field, 0.0) >= threshold

    vendor_pack_results = report.get("vendor_pack_results") or []
    assert len(vendor_pack_results) >= 3
    assert all(result.get("passed") for result in vendor_pack_results)

    known_bad_pattern_results = report.get("known_bad_pattern_results") or []
    assert len(known_bad_pattern_results) >= 3
    assert all(result.get("passed") for result in known_bad_pattern_results)
