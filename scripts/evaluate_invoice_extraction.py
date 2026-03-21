#!/usr/bin/env python3
"""Invoice/AP extraction scorecard for regression gating and local evaluation."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

# Ensure project root is on sys.path when script is run directly.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from clearledgr.services.email_parser import EmailParser


DEFAULT_DATASET_PATHS: tuple[Path, ...] = (
    Path("tests/test_data/invoice_extraction_eval_cases.json"),
    Path("tests/fixtures/invoice_extraction_golden.json"),
    Path("tests/fixtures/reviewed_production_invoice_truth.json"),
)
DEFAULT_VENDOR_PACKS_PATH = Path("tests/test_data/invoice_extraction_vendor_packs.json")

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


def _case_identifier(case: Mapping[str, Any], index: int) -> str:
    return str(case.get("id") or case.get("name") or f"case_{index}")


def _sender_domain(value: Any) -> str:
    sender = str(value or "").strip().lower()
    if "@" not in sender:
        return ""
    return sender.rsplit("@", 1)[-1]


def _subject_pattern(raw: Any) -> str:
    subject = str(raw or "").strip().lower()
    if not subject:
        return ""
    subject = re.sub(r"\d+", "#", subject)
    subject = re.sub(r"[^a-z0-9# ]+", " ", subject)
    return " ".join(subject.split())[:120]


def _case_layout_key(case: Mapping[str, Any]) -> str:
    input_payload = case.get("input") or {}
    metadata = case.get("metadata") or {}
    existing = str(metadata.get("layout_key") or "").strip()
    if existing:
        return existing
    sender_domain = _sender_domain(input_payload.get("sender"))
    document_type = str(
        (case.get("expected") or {}).get("email_type")
        or (case.get("expected") or {}).get("document_type")
        or metadata.get("document_type")
        or "invoice"
    ).strip().lower()
    attachments = input_payload.get("attachments") or []
    attachment_names = []
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        filename = str(attachment.get("filename") or "").strip()
        if filename:
            attachment_names.append(re.sub(r"\d+", "#", Path(filename).stem.lower())[:24])
    attachment_basis = "|".join(attachment_names[:3])
    subject_basis = _subject_pattern(input_payload.get("subject"))
    basis = attachment_basis or subject_basis or "generic"
    return "::".join(part for part in (sender_domain or "unknown", document_type, basis) if part)


def _case_vendor(case: Mapping[str, Any], observed: Mapping[str, Any]) -> str:
    expected = case.get("expected") or {}
    metadata = case.get("metadata") or {}
    return str(
        expected.get("vendor")
        or metadata.get("vendor_name")
        or observed.get("vendor")
        or "UNKNOWN"
    ).strip()


def _case_document_type(case: Mapping[str, Any], observed: Mapping[str, Any]) -> str:
    expected = case.get("expected") or {}
    metadata = case.get("metadata") or {}
    return str(
        expected.get("email_type")
        or expected.get("document_type")
        or metadata.get("document_type")
        or observed.get("document_type")
        or "unknown"
    ).strip()


def _determine_rating(
    *,
    overall_weighted: float,
    perfect_case_rate: float,
    critical_field_accuracy: Mapping[str, float],
) -> str:
    amount_acc = critical_field_accuracy.get("amount", 0.0)
    invoice_acc = critical_field_accuracy.get("invoice_number", 0.0)
    vendor_acc = critical_field_accuracy.get("vendor", 0.0)
    document_type_acc = critical_field_accuracy.get("document_type", 0.0)

    if overall_weighted == 1.0 and perfect_case_rate == 1.0:
        return "perfect"
    if (
        overall_weighted >= 0.95
        and amount_acc >= 0.95
        and invoice_acc >= 0.95
        and vendor_acc >= 0.90
        and document_type_acc >= 0.98
    ):
        return "great"
    if overall_weighted >= 0.80 and amount_acc >= 0.85 and invoice_acc >= 0.85:
        return "good"
    return "needs_work"


def _summarize_case_reports(case_reports: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    field_totals: Dict[str, int] = {}
    field_hits: Dict[str, int] = {}
    weighted_scores: List[float] = []
    perfect_cases = 0

    for case in case_reports:
        comparisons = case.get("comparisons") or {}
        weighted_scores.append(float(case.get("score") or 0.0))
        if case.get("all_expected_fields_matched"):
            perfect_cases += 1
        for field, detail in comparisons.items():
            field_totals[field] = field_totals.get(field, 0) + 1
            if detail.get("matched"):
                field_hits[field] = field_hits.get(field, 0) + 1

    field_accuracy = {
        field: round(field_hits.get(field, 0) / total, 4) if total else 0.0
        for field, total in sorted(field_totals.items())
    }
    critical_field_accuracy = {
        field: field_accuracy.get(field, 0.0)
        for field in CRITICAL_FIELDS
    }
    overall_weighted = round(sum(weighted_scores) / len(weighted_scores), 4) if weighted_scores else 0.0
    perfect_case_rate = round(perfect_cases / len(case_reports), 4) if case_reports else 0.0
    rating = _determine_rating(
        overall_weighted=overall_weighted,
        perfect_case_rate=perfect_case_rate,
        critical_field_accuracy=critical_field_accuracy,
    )
    return {
        "dataset_size": len(case_reports),
        "overall_weighted_score": overall_weighted,
        "perfect_case_rate": perfect_case_rate,
        "field_accuracy": field_accuracy,
        "critical_field_accuracy": critical_field_accuracy,
        "rating": rating,
    }


def _empty_gate_result(
    *,
    gate_id: str,
    gate_type: str,
    vendor_name: str | None = None,
) -> Dict[str, Any]:
    return {
        "id": gate_id,
        "gate_type": gate_type,
        "vendor_name": vendor_name,
        "dataset_size": 0,
        "overall_weighted_score": 0.0,
        "perfect_case_rate": 0.0,
        "field_accuracy": {},
        "critical_field_accuracy": {field: 0.0 for field in CRITICAL_FIELDS},
        "rating": "needs_work",
        "passed": False,
        "missing_case_ids": [],
        "case_ids": [],
        "field_failures": {},
        "minimum_overall_score": 0.0,
        "critical_field_thresholds": {},
    }


def evaluate_cases(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    parser = EmailParser()
    per_case: List[Dict[str, Any]] = []

    for index, case in enumerate(cases, start=1):
        case_id = _case_identifier(case, index)
        input_payload = case.get("input") or {}
        expected = case.get("expected") or {}
        metadata = case.get("metadata") or {}

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
            weight = float(FIELD_SPECS.get(field, {}).get("weight") or 0.0)
            if weight > 0:
                weight_sum += weight
                weighted_hit_sum += (weight if matched else 0.0)

        case_score = round((weighted_hit_sum / weight_sum) if weight_sum > 0 else 0.0, 4)
        all_match = all(item["matched"] for item in comparisons.values()) if comparisons else False
        per_case.append(
            {
                "id": case_id,
                "score": case_score,
                "all_expected_fields_matched": all_match,
                "comparisons": comparisons,
                "vendor_name": _case_vendor(case, observed),
                "document_type": _case_document_type(case, observed),
                "sender_domain": _sender_domain(input_payload.get("sender")),
                "subject": str(input_payload.get("subject") or ""),
                "layout_key": _case_layout_key(case),
                "metadata": metadata,
            }
        )

    base_report = _summarize_case_reports(per_case)
    base_report["cases"] = per_case

    vendor_groups: Dict[str, List[Dict[str, Any]]] = {}
    for case in per_case:
        vendor_groups.setdefault(str(case.get("vendor_name") or "UNKNOWN"), []).append(dict(case))

    vendor_scorecards = []
    for vendor_name, vendor_cases in sorted(
        vendor_groups.items(),
        key=lambda item: (-len(item[1]), item[0].lower()),
    ):
        summary = _summarize_case_reports(vendor_cases)
        summary.update(
            {
                "vendor_name": vendor_name,
                "case_ids": [str(case.get("id")) for case in vendor_cases],
                "document_types": sorted(
                    {
                        str(case.get("document_type") or "").strip()
                        for case in vendor_cases
                        if str(case.get("document_type") or "").strip()
                    }
                ),
                "layout_keys": sorted(
                    {
                        str(case.get("layout_key") or "").strip()
                        for case in vendor_cases
                        if str(case.get("layout_key") or "").strip()
                    }
                )[:10],
            }
        )
        vendor_scorecards.append(summary)

    base_report["vendor_scorecards"] = vendor_scorecards
    return base_report


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
        if not path.exists():
            continue
        loaded.extend(_load_cases(path))
    return loaded


def _load_vendor_pack_config(path: Path | None) -> Dict[str, List[Dict[str, Any]]]:
    if path is None or not path.exists():
        return {"vendor_packs": [], "known_bad_patterns": []}
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError("Vendor pack config must be a JSON object")
    vendor_packs = payload.get("vendor_packs") or []
    known_bad_patterns = payload.get("known_bad_patterns") or []
    if not isinstance(vendor_packs, list) or not isinstance(known_bad_patterns, list):
        raise ValueError("Vendor pack config must contain list-valued vendor_packs and known_bad_patterns")
    return {
        "vendor_packs": vendor_packs,
        "known_bad_patterns": known_bad_patterns,
    }


def _case_matches_gate(case: Mapping[str, Any], spec: Mapping[str, Any]) -> bool:
    case_ids = [str(value) for value in (spec.get("case_ids") or []) if str(value).strip()]
    if case_ids:
        return str(case.get("id") or "") in set(case_ids)
    vendor_name = str(spec.get("vendor") or spec.get("vendor_name") or "").strip()
    if vendor_name and _norm_text(case.get("vendor_name")) != _norm_text(vendor_name):
        return False
    document_type = str(spec.get("document_type") or "").strip()
    if document_type and _norm_text(case.get("document_type")) != _norm_text(document_type):
        return False
    sender_domain = str(spec.get("sender_domain") or "").strip().lower()
    if sender_domain and str(case.get("sender_domain") or "").strip().lower() != sender_domain:
        return False
    subject_contains = str(spec.get("subject_contains") or "").strip().lower()
    if subject_contains and subject_contains not in str(case.get("subject") or "").strip().lower():
        return False
    layout_contains = str(spec.get("layout_key_contains") or "").strip().lower()
    if layout_contains and layout_contains not in str(case.get("layout_key") or "").strip().lower():
        return False
    return bool(vendor_name or document_type or sender_domain or subject_contains or layout_contains)


def _evaluate_gate(
    *,
    case_reports: Sequence[Mapping[str, Any]],
    spec: Mapping[str, Any],
    gate_type: str,
) -> Dict[str, Any]:
    gate_id = str(spec.get("id") or spec.get("name") or f"{gate_type}_{len(case_reports)}").strip()
    vendor_name = str(spec.get("vendor") or spec.get("vendor_name") or "").strip() or None
    explicit_case_ids = [str(value) for value in (spec.get("case_ids") or []) if str(value).strip()]
    selected = [dict(case) for case in case_reports if _case_matches_gate(case, spec)]
    missing_case_ids = [case_id for case_id in explicit_case_ids if case_id not in {str(case.get("id")) for case in selected}]
    if not selected:
        result = _empty_gate_result(gate_id=gate_id, gate_type=gate_type, vendor_name=vendor_name)
    else:
        result = _summarize_case_reports(selected)
        result.update(
            {
                "id": gate_id,
                "gate_type": gate_type,
                "vendor_name": vendor_name,
                "case_ids": [str(case.get("id")) for case in selected],
                "missing_case_ids": missing_case_ids,
            }
        )

    thresholds = dict(spec.get("critical_field_thresholds") or {})
    minimum_overall_score = float(spec.get("minimum_overall_score") or 0.0)
    field_failures = {}
    for field, threshold in thresholds.items():
        actual = float((result.get("critical_field_accuracy") or {}).get(field, 0.0))
        threshold_value = float(threshold)
        if actual < threshold_value:
            field_failures[field] = {
                "actual": round(actual, 4),
                "minimum": threshold_value,
            }

    passed = (
        bool(result.get("dataset_size"))
        and not missing_case_ids
        and float(result.get("overall_weighted_score") or 0.0) >= minimum_overall_score
        and not field_failures
    )
    result.update(
        {
            "description": spec.get("description"),
            "minimum_overall_score": minimum_overall_score,
            "critical_field_thresholds": thresholds,
            "field_failures": field_failures,
            "passed": passed,
            "known_bad_patterns": list(spec.get("known_bad_patterns") or []),
        }
    )
    return result


def apply_vendor_pack_gates(
    report: Dict[str, Any],
    *,
    vendor_pack_config: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    case_reports = list(report.get("cases") or [])
    vendor_pack_results = [
        _evaluate_gate(case_reports=case_reports, spec=spec, gate_type="vendor_pack")
        for spec in (vendor_pack_config.get("vendor_packs") or [])
    ]
    known_bad_pattern_results = [
        _evaluate_gate(case_reports=case_reports, spec=spec, gate_type="known_bad_pattern")
        for spec in (vendor_pack_config.get("known_bad_patterns") or [])
    ]
    report["vendor_pack_results"] = vendor_pack_results
    report["known_bad_pattern_results"] = known_bad_pattern_results
    report["vendor_pack_failures"] = [row for row in vendor_pack_results if not row.get("passed")]
    report["known_bad_pattern_failures"] = [
        row for row in known_bad_pattern_results if not row.get("passed")
    ]
    return report


def _print_gate_section(title: str, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        return
    print("")
    print(title)
    for row in rows:
        status = "PASS" if row.get("passed") else "FAIL"
        print(
            f"  - [{status}] {row.get('id')} "
            f"(cases={row.get('dataset_size')}, overall={float(row.get('overall_weighted_score') or 0.0):.2%})"
        )
        for field, failure in (row.get("field_failures") or {}).items():
            print(
                f"      {field}: {float(failure.get('actual') or 0.0):.2%} "
                f"< {float(failure.get('minimum') or 0.0):.2%}"
            )
        missing_case_ids = row.get("missing_case_ids") or []
        if missing_case_ids:
            print(f"      missing cases: {', '.join(str(value) for value in missing_case_ids)}")


def _print_human_report(
    report: Dict[str, Any],
    dataset_paths: Iterable[Path],
    *,
    vendor_pack_config_path: Path | None,
) -> None:
    print("Invoice Extraction Evaluation")
    print("Datasets:")
    for path in dataset_paths:
        if path.exists():
            print(f"  - {path}")
    if vendor_pack_config_path and vendor_pack_config_path.exists():
        print(f"Vendor pack config: {vendor_pack_config_path}")
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

    vendor_scorecards = report.get("vendor_scorecards") or []
    if vendor_scorecards:
        print("")
        print("Vendor scorecards:")
        for row in vendor_scorecards[:10]:
            print(
                f"  - {row.get('vendor_name')}: "
                f"cases={row.get('dataset_size')} overall={float(row.get('overall_weighted_score') or 0.0):.2%}"
            )

    _print_gate_section("Vendor pack gates:", report.get("vendor_pack_results") or [])
    _print_gate_section("Known bad patterns:", report.get("known_bad_pattern_results") or [])

    failed_cases = [c for c in report.get("cases", []) if not c.get("all_expected_fields_matched")]
    if failed_cases:
        print("")
        print("Cases with mismatches:")
        for case in failed_cases:
            print(f"  - {case['id']} (score={float(case['score']):.2f})")
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
    parser.add_argument(
        "--vendor-pack-config",
        type=Path,
        default=DEFAULT_VENDOR_PACKS_PATH,
        help="Path to vendor-specific regression gate configuration.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    args = parser.parse_args()

    dataset_paths = tuple(args.dataset or DEFAULT_DATASET_PATHS)
    report = evaluate_cases(load_cases(dataset_paths))
    vendor_pack_config = _load_vendor_pack_config(args.vendor_pack_config)
    report = apply_vendor_pack_gates(report, vendor_pack_config=vendor_pack_config)
    report["dataset_paths"] = [str(path) for path in dataset_paths if path.exists()]
    report["vendor_pack_config_path"] = str(args.vendor_pack_config) if args.vendor_pack_config and args.vendor_pack_config.exists() else None
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_human_report(report, dataset_paths, vendor_pack_config_path=args.vendor_pack_config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
