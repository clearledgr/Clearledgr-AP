from __future__ import annotations

from scripts.evaluate_invoice_extraction import DEFAULT_DATASET_PATHS, evaluate_cases, load_cases


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
    critical_field_accuracy = report.get("critical_field_accuracy") or {}

    assert report["dataset_size"] >= 65
    assert report["overall_weighted_score"] >= 0.94
    assert report["perfect_case_rate"] >= 0.80

    for field, threshold in CRITICAL_FIELD_THRESHOLDS.items():
        assert critical_field_accuracy.get(field, 0.0) >= threshold
