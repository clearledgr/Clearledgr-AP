import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "evaluate_invoice_extraction.py"


def test_invoice_extraction_eval_harness_runs_and_returns_metrics():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=str(ROOT),
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)

    assert report["dataset_size"] >= 65
    assert 0.0 <= report["overall_weighted_score"] <= 1.0
    assert 0.0 <= report["perfect_case_rate"] <= 1.0
    assert report["rating"] in {"needs_work", "good", "great", "perfect"}
    assert len(report.get("dataset_paths") or []) >= 3
    assert report.get("vendor_pack_config_path")

    field_accuracy = report.get("field_accuracy") or {}
    for field in ("amount", "currency", "invoice_number", "vendor", "document_type"):
        assert field in field_accuracy
        assert 0.0 <= field_accuracy[field] <= 1.0

    critical_field_accuracy = report.get("critical_field_accuracy") or {}
    assert report["overall_weighted_score"] >= 0.94
    assert critical_field_accuracy["amount"] >= 0.99
    assert critical_field_accuracy["currency"] >= 0.99
    assert critical_field_accuracy["invoice_number"] >= 0.95
    assert critical_field_accuracy["vendor"] >= 0.90
    assert critical_field_accuracy["document_type"] >= 0.99

    vendor_pack_results = report.get("vendor_pack_results") or []
    assert len(vendor_pack_results) >= 3
    assert all(result.get("passed") for result in vendor_pack_results)
    assert {result.get("id") for result in vendor_pack_results} >= {
        "google_cloud_attachment_invoices",
        "freelance_payment_requests",
        "designco_total_due_selection",
    }

    known_bad_pattern_results = report.get("known_bad_pattern_results") or []
    assert len(known_bad_pattern_results) >= 3
    assert all(result.get("passed") for result in known_bad_pattern_results)

    vendor_scorecards = report.get("vendor_scorecards") or []
    vendor_names = {row.get("vendor_name") for row in vendor_scorecards}
    assert {
        "Google Cloud EMEA Limited",
        "Freelance",
        "Designco",
    }.issubset(vendor_names)
