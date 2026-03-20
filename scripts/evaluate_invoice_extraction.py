#!/usr/bin/env python3
"""Invoice/AP extraction scorecard for regression gating and local evaluation."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

# Ensure project root is on sys.path when script is run directly.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from clearledgr.services.email_parser import EmailParser


DEFAULT_DATASET_PATHS: tuple[Path, ...] = (
    Path("tests/test_data/invoice_extraction_eval_cases.json"),
    Path("tests/fixtures/invoice_extraction_golden.json"),
)

CRITICAL_FIELDS: tuple[str, ...] = (
    "amount",
    "currency",
    "invoice_number",
    "vendor",
    "document_type",
)

FIELD_SPECS: Dict[str, Dict[str, Any]] = {
    "document_type": {
        "expected_keys": ("document_type", "email_type"),
        "observed_key": "email_type",
        "weight": 0.10,
    },
    "vendor": {
        "expected_keys": ("vendor",),
        "observed_key": "vendor",
        "weight": 0.20,
    },
    "amount": {
        "expected_keys": ("amount", "primary_amount"),
        "observed_key": "primary_amount",
        "weight": 0.35,
    },
    "invoice_number": {
        "expected_keys": ("invoice_number", "primary_invoice"),
        "observed_key": "primary_invoice",
        "weight": 0.25,
    },
    "currency": {
        "expected_keys": ("currency",),
        "observed_key": "currency",
        "weight": 0.10,
    },
    "source": {
        "expected_keys": ("source", "primary_source"),
        "observed_key": "primary_source",
        "weight": 0.05,
    },
}


def _norm_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower()
    chars = []
    for ch in text:
        if ch.isalnum():
            chars.append(ch)
        elif ch.isspace():
            chars.append(" ")
    return " ".join("".join(chars).split())


def _norm_invoice(value: Any) -> str:
    if value is None:
        return ""
    return "".join(ch for ch in str(value).upper() if ch.isalnum())


def _norm_currency(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().upper()


def _eq_amount(expected: Any, observed: Any) -> bool:
    try:
        return math.isclose(float(expected), float(observed), abs_tol=0.01)
    except (TypeError, ValueError):
        return False


def _compare_field(field: str, expected: Any, observed: Any) -> bool:
    if field == "amount":
        return _eq_amount(expected, observed)
    if field == "invoice_number":
        return _norm_invoice(expected) == _norm_invoice(observed)
    if field in {"vendor", "document_type", "source"}:
        return _norm_text(expected) == _norm_text(observed)
    if field == "currency":
        return _norm_currency(expected) == _norm_currency(observed)
    return expected == observed


def _extract_observed_fields(parsed: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "document_type": parsed.get("email_type"),
        "vendor": parsed.get("vendor"),
        "amount": parsed.get("primary_amount"),
        "invoice_number": parsed.get("primary_invoice"),
        "currency": parsed.get("currency"),
        "source": parsed.get("primary_source"),
    }


def _iter_expected_fields(expected: Mapping[str, Any]) -> Iterable[tuple[str, Any]]:
    for field, spec in FIELD_SPECS.items():
        for key in spec["expected_keys"]:
            if key in expected:
                yield field, expected.get(key)
                break


def evaluate_cases(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    parser = EmailParser()
    per_case: List[Dict[str, Any]] = []
    field_totals: Dict[str, int] = {}
    field_hits: Dict[str, int] = {}
    weighted_scores: List[float] = []
    perfect_cases = 0

    for index, case in enumerate(cases, start=1):
        case_id = str(case.get("id") or case.get("name") or f"case_{index}")
        input_payload = case.get("input") or {}
        expected = case.get("expected") or {}

        parsed = parser.parse_email(
            subject=str(input_payload.get("subject") or ""),
            body=str(input_payload.get("body") or ""),
            sender=str(input_payload.get("sender") or ""),
            attachments=list(input_payload.get("attachments") or []),
        )
        observed = _extract_observed_fields(parsed)

        comparisons: Dict[str, Dict[str, Any]] = {}
        weight_sum = 0.0
        weighted_hit_sum = 0.0

        for field, exp_value in _iter_expected_fields(expected):
            obs_value = observed.get(field)
            matched = _compare_field(field, exp_value, obs_value)
            comparisons[field] = {
                "expected": exp_value,
                "observed": obs_value,
                "matched": matched,
            }
            field_totals[field] = field_totals.get(field, 0) + 1
            field_hits[field] = field_hits.get(field, 0) + (1 if matched else 0)

            weight = float(FIELD_SPECS.get(field, {}).get("weight") or 0.0)
            if weight > 0:
                weight_sum += weight
                weighted_hit_sum += (weight if matched else 0.0)

        case_score = (weighted_hit_sum / weight_sum) if weight_sum > 0 else 0.0
        all_match = all(item["matched"] for item in comparisons.values()) if comparisons else False
        if all_match:
            perfect_cases += 1
        weighted_scores.append(case_score)
        per_case.append(
            {
                "id": case_id,
                "score": round(case_score, 4),
                "all_expected_fields_matched": all_match,
                "comparisons": comparisons,
            }
        )

    field_accuracy = {
        field: round(field_hits.get(field, 0) / total, 4) if total else 0.0
        for field, total in sorted(field_totals.items())
    }
    critical_field_accuracy = {
        field: field_accuracy.get(field, 0.0)
        for field in CRITICAL_FIELDS
    }
    overall_weighted = round(sum(weighted_scores) / len(weighted_scores), 4) if weighted_scores else 0.0
    perfect_case_rate = round(perfect_cases / len(per_case), 4) if per_case else 0.0

    amount_acc = critical_field_accuracy.get("amount", 0.0)
    invoice_acc = critical_field_accuracy.get("invoice_number", 0.0)
    vendor_acc = critical_field_accuracy.get("vendor", 0.0)
    document_type_acc = critical_field_accuracy.get("document_type", 0.0)

    if overall_weighted == 1.0 and perfect_case_rate == 1.0:
        rating = "perfect"
    elif (
        overall_weighted >= 0.95
        and amount_acc >= 0.95
        and invoice_acc >= 0.95
        and vendor_acc >= 0.90
        and document_type_acc >= 0.98
    ):
        rating = "great"
    elif overall_weighted >= 0.80 and amount_acc >= 0.85 and invoice_acc >= 0.85:
        rating = "good"
    else:
        rating = "needs_work"

    return {
        "dataset_size": len(per_case),
        "overall_weighted_score": overall_weighted,
        "perfect_case_rate": perfect_case_rate,
        "field_accuracy": field_accuracy,
        "critical_field_accuracy": critical_field_accuracy,
        "rating": rating,
        "cases": per_case,
    }


def _load_cases(path: Path) -> List[Dict[str, Any]]:
    payload = json.loads(path.read_text())
    if isinstance(payload, dict):
        cases = payload.get("cases") or []
    else:
        cases = payload
    if not isinstance(cases, list):
        raise ValueError("Dataset must be a list or an object with a 'cases' list")
    return cases


def load_cases(paths: Iterable[Path]) -> List[Dict[str, Any]]:
    loaded: List[Dict[str, Any]] = []
    for path in paths:
        loaded.extend(_load_cases(path))
    return loaded


def _print_human_report(report: Dict[str, Any], dataset_paths: Iterable[Path]) -> None:
    print("Invoice Extraction Evaluation")
    print("Datasets:")
    for path in dataset_paths:
        print(f"  - {path}")
    print(f"Cases: {report['dataset_size']}")
    print(f"Overall weighted score: {report['overall_weighted_score']:.2%}")
    print(f"Perfect-case rate: {report['perfect_case_rate']:.2%}")
    print(f"Rating: {report['rating']}")
    print("")
    print("Critical field accuracy:")
    for field in CRITICAL_FIELDS:
        print(f"  - {field}: {float(report['critical_field_accuracy'].get(field, 0.0)):.2%}")

    supporting_fields = {
        field: acc
        for field, acc in (report.get("field_accuracy") or {}).items()
        if field not in CRITICAL_FIELDS
    }
    if supporting_fields:
        print("")
        print("Supporting field accuracy:")
        for field, acc in supporting_fields.items():
            print(f"  - {field}: {acc:.2%}")

    failed_cases = [c for c in report.get("cases", []) if not c.get("all_expected_fields_matched")]
    if failed_cases:
        print("")
        print("Cases with mismatches:")
        for case in failed_cases:
            print(f"  - {case['id']} (score={case['score']:.2f})")
            for field, detail in (case.get("comparisons") or {}).items():
                if detail.get("matched"):
                    continue
                print(
                    f"      {field}: expected={detail.get('expected')!r} observed={detail.get('observed')!r}"
                )


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate invoice/AP email extraction quality on labeled datasets")
    parser.add_argument(
        "--dataset",
        type=Path,
        action="append",
        default=None,
        help="Path to labeled JSON dataset. Pass multiple times to combine corpora.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    args = parser.parse_args()

    dataset_paths = tuple(args.dataset or DEFAULT_DATASET_PATHS)
    report = evaluate_cases(load_cases(dataset_paths))
    report["dataset_paths"] = [str(path) for path in dataset_paths]
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_human_report(report, dataset_paths)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
