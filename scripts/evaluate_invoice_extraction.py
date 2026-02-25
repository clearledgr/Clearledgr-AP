#!/usr/bin/env python3
"""Lightweight invoice/AP email extraction scorecard for local evaluation.

This evaluates the deterministic local parser (`clearledgr.services.email_parser.EmailParser`)
against a labeled JSON dataset. It is intentionally lightweight so teams can
measure extraction quality on a growing golden set without external services.

Notes:
- This measures the local parser baseline, not the full Gmail triage path
  (which may include agent reasoning/reflection and multimodal extraction).
- It is still useful as a regression scorecard and a reality check when users
  ask, "How good is parsing right now?"
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Tuple

from clearledgr.services.email_parser import EmailParser


FIELD_WEIGHTS: Dict[str, float] = {
    "email_type": 0.05,
    "vendor": 0.20,
    "primary_amount": 0.40,
    "primary_invoice": 0.25,
    "currency": 0.10,
}


def _norm_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower()
    # collapse punctuation/spacing for vendor-like comparisons
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
    if field == "primary_amount":
        return _eq_amount(expected, observed)
    if field == "primary_invoice":
        return _norm_invoice(expected) == _norm_invoice(observed)
    if field == "vendor":
        return _norm_text(expected) == _norm_text(observed)
    if field == "currency":
        return _norm_currency(expected) == _norm_currency(observed)
    if field == "email_type":
        return _norm_text(expected) == _norm_text(observed)
    return expected == observed


def _extract_observed_fields(parsed: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "email_type": parsed.get("email_type"),
        "vendor": parsed.get("vendor"),
        "primary_amount": parsed.get("primary_amount"),
        "primary_invoice": parsed.get("primary_invoice"),
        "currency": parsed.get("currency"),
    }


def evaluate_cases(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    parser = EmailParser()
    per_case: List[Dict[str, Any]] = []
    field_totals: Dict[str, int] = {}
    field_hits: Dict[str, int] = {}
    weighted_scores: List[float] = []
    perfect_cases = 0

    for case in cases:
        case_id = str(case.get("id") or f"case_{len(per_case)+1}")
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

        for field, exp_value in expected.items():
            obs_value = observed.get(field)
            matched = _compare_field(field, exp_value, obs_value)
            comparisons[field] = {
                "expected": exp_value,
                "observed": obs_value,
                "matched": matched,
            }
            field_totals[field] = field_totals.get(field, 0) + 1
            field_hits[field] = field_hits.get(field, 0) + (1 if matched else 0)

            w = float(FIELD_WEIGHTS.get(field, 0.0))
            weight_sum += w
            weighted_hit_sum += (w if matched else 0.0)

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
    overall_weighted = round(sum(weighted_scores) / len(weighted_scores), 4) if weighted_scores else 0.0
    perfect_case_rate = round(perfect_cases / len(per_case), 4) if per_case else 0.0

    amount_acc = field_accuracy.get("primary_amount", 0.0)
    invoice_acc = field_accuracy.get("primary_invoice", 0.0)
    vendor_acc = field_accuracy.get("vendor", 0.0)

    if overall_weighted == 1.0 and perfect_case_rate == 1.0:
        rating = "perfect"
    elif overall_weighted >= 0.9 and amount_acc >= 0.9 and invoice_acc >= 0.85 and vendor_acc >= 0.8:
        rating = "great"
    elif overall_weighted >= 0.75 and amount_acc >= 0.8:
        rating = "good"
    else:
        rating = "needs_work"

    return {
        "dataset_size": len(per_case),
        "overall_weighted_score": overall_weighted,
        "perfect_case_rate": perfect_case_rate,
        "field_accuracy": field_accuracy,
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


def _print_human_report(report: Dict[str, Any], dataset_path: Path) -> None:
    print(f"Invoice Extraction Evaluation")
    print(f"Dataset: {dataset_path}")
    print(f"Cases: {report['dataset_size']}")
    print(f"Overall weighted score: {report['overall_weighted_score']:.2%}")
    print(f"Perfect-case rate: {report['perfect_case_rate']:.2%}")
    print(f"Rating: {report['rating']}")
    print("")
    print("Field accuracy:")
    for field, acc in report.get("field_accuracy", {}).items():
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
    parser = argparse.ArgumentParser(description="Evaluate invoice/AP email extraction quality on a labeled dataset")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("tests/test_data/invoice_extraction_eval_cases.json"),
        help="Path to labeled JSON dataset",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    args = parser.parse_args()

    dataset_path = args.dataset
    cases = _load_cases(dataset_path)
    report = evaluate_cases(cases)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_human_report(report, dataset_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

