import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "evaluate_invoice_extraction.py"
DATASET = ROOT / "tests" / "test_data" / "invoice_extraction_eval_cases.json"


def test_invoice_extraction_eval_harness_runs_and_returns_metrics():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dataset", str(DATASET), "--json"],
        cwd=str(ROOT),
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)

    assert 50 <= report["dataset_size"] <= 100
    assert 0.0 <= report["overall_weighted_score"] <= 1.0
    assert 0.0 <= report["perfect_case_rate"] <= 1.0
    assert report["rating"] in {"needs_work", "good", "great", "perfect"}

    field_accuracy = report.get("field_accuracy") or {}
    for field in ("vendor", "primary_amount", "primary_invoice"):
        assert field in field_accuracy
        assert 0.0 <= field_accuracy[field] <= 1.0

    # Guard against major regressions on this baseline dataset.
    assert report["overall_weighted_score"] >= 0.75
    assert field_accuracy["primary_amount"] >= 0.70
    assert field_accuracy["primary_invoice"] >= 0.80
