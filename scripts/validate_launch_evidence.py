#!/usr/bin/env python3
"""Validate launch-evidence tracker completeness for a release id.

This script is intentionally lightweight so readiness owners can run one command
and see whether required tracker items are fully evidenced.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


REPO_ROOT = Path(__file__).resolve().parent.parent
TRACKER_PATH = REPO_ROOT / "docs" / "GA_LAUNCH_READINESS_TRACKER.md"
RELEASES_ROOT = REPO_ROOT / "docs" / "ga-evidence" / "releases"

PILOT_REQUIRED_ITEMS = ("L01", "L02", "L03", "L04", "L11")
GA_REQUIRED_ITEMS = ("L01", "L02", "L03", "L04", "L05", "L06", "L07", "L09", "L10", "L11", "L12")


@dataclass
class LaunchItem:
    item_id: str
    status: str = ""
    owner: str = ""
    artifact_links: List[str] = field(default_factory=list)


def _extract_backtick_value(line: str) -> str:
    match = re.search(r"`([^`]+)`", line)
    return match.group(1).strip() if match else ""


def _normalize_artifact_path(raw: str, release_id: str) -> Optional[Path]:
    value = str(raw or "").strip()
    if value.startswith("`") and value.endswith("`"):
        value = value[1:-1].strip()
    if not value or value.upper() == "TBD":
        return None
    value = value.replace("<release_id>", release_id)
    if value.startswith("http://") or value.startswith("https://"):
        return None
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    return candidate


def parse_target_release_id(tracker_text: str) -> str:
    match = re.search(r"Target release id:\s*`([^`]+)`", tracker_text)
    return match.group(1).strip() if match else ""


def parse_launch_tracker_items(tracker_text: str) -> Dict[str, LaunchItem]:
    items: Dict[str, LaunchItem] = {}
    current: Optional[LaunchItem] = None
    in_artifacts = False

    for raw_line in tracker_text.splitlines():
        line = raw_line.rstrip("\n")
        header = re.match(r"^###\s+(L\d+)\s*$", line.strip())
        if header:
            current = LaunchItem(item_id=header.group(1))
            items[current.item_id] = current
            in_artifacts = False
            continue
        if current is None:
            continue

        stripped = line.strip()
        if stripped.startswith("- Status:"):
            current.status = _extract_backtick_value(stripped)
            in_artifacts = False
            continue
        if stripped.startswith("- Owner:"):
            current.owner = _extract_backtick_value(stripped)
            in_artifacts = False
            continue
        if stripped.startswith("- Artifact links:"):
            in_artifacts = True
            continue
        if in_artifacts:
            if line.startswith("  - "):
                _, _, value = line.partition(":")
                normalized_value = value.strip()
                if normalized_value.startswith("`") and normalized_value.endswith("`"):
                    normalized_value = normalized_value[1:-1].strip()
                current.artifact_links.append(normalized_value)
                continue
            if stripped and not line.startswith("  - "):
                in_artifacts = False

    return items


def _count_tbd(text: str) -> int:
    return len(re.findall(r"\bTBD\b", text))


def _read_working_status(path: Path) -> str:
    if not path.exists():
        return "missing"
    match = re.search(r"^Status:\s*`([^`]+)`", path.read_text(), flags=re.MULTILINE)
    return (match.group(1).strip() if match else "unknown").lower()


def validate_release(
    *,
    tracker_path: Path,
    release_id: str,
    mode: str,
    repo_root: Path,
) -> Dict[str, object]:
    tracker_text = tracker_path.read_text()
    items = parse_launch_tracker_items(tracker_text)
    required = PILOT_REQUIRED_ITEMS if mode == "pilot" else GA_REQUIRED_ITEMS

    errors: List[str] = []
    warnings: List[str] = []
    item_results: List[Dict[str, object]] = []

    for item_id in required:
        item = items.get(item_id)
        if item is None:
            errors.append(f"{item_id}:missing_tracker_item")
            item_results.append({"id": item_id, "ok": False, "reason": "missing_tracker_item"})
            continue

        item_errors: List[str] = []
        if item.status.upper() != "DONE":
            item_errors.append(f"status_not_done:{item.status or 'unset'}")
        if not item.owner or item.owner.upper() == "TBD":
            item_errors.append("owner_not_assigned")
        if not item.artifact_links:
            item_errors.append("artifact_links_missing")
        else:
            for link in item.artifact_links:
                if not link or link.upper() == "TBD":
                    item_errors.append("artifact_link_tbd")
                    continue
                normalized = _normalize_artifact_path(link, release_id)
                if normalized is not None and not normalized.exists():
                    item_errors.append(f"artifact_not_found:{normalized}")

        if item_errors:
            errors.append(f"{item_id}:{'|'.join(item_errors)}")
            item_results.append({"id": item_id, "ok": False, "reason": item_errors})
        else:
            item_results.append({"id": item_id, "ok": True})

    release_dir = repo_root / "docs" / "ga-evidence" / "releases" / release_id
    manifest_path = release_dir / "MANIFEST.md"
    if not manifest_path.exists():
        errors.append(f"manifest_missing:{manifest_path}")
        manifest_tbd_count = -1
    else:
        manifest_text = manifest_path.read_text()
        manifest_tbd_count = _count_tbd(manifest_text)
        if manifest_tbd_count > 0:
            warnings.append(f"manifest_contains_tbd:{manifest_tbd_count}")

    working_status_paths = {
        "erp_parity": release_dir / "ERP_PARITY_MATRIX.md",
        "failure_mode": release_dir / "FAILURE_MODE_MATRIX.md",
        "runbooks": release_dir / "RUNBOOK_VALIDATIONS.md",
        "rollback": release_dir / "ROLLBACK_CONTROLS_VERIFICATION.md",
        "signoffs": release_dir / "SIGNOFFS.md",
    }
    working_statuses: Dict[str, str] = {}
    for name, path in working_status_paths.items():
        status = _read_working_status(path)
        working_statuses[name] = status
        if status in {"missing", "not started", "unknown"}:
            warnings.append(f"{name}_status:{status}")

    return {
        "release_id": release_id,
        "mode": mode,
        "required_items": list(required),
        "items": item_results,
        "manifest_path": str(manifest_path),
        "manifest_tbd_count": manifest_tbd_count,
        "working_statuses": working_statuses,
        "errors": errors,
        "warnings": warnings,
        "passed": len(errors) == 0,
    }


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate GA/Pilot launch evidence completeness")
    parser.add_argument(
        "--release-id",
        default="",
        help="Release id (defaults to target release id declared in GA_LAUNCH_READINESS_TRACKER.md)",
    )
    parser.add_argument(
        "--mode",
        choices=("pilot", "ga"),
        default="pilot",
        help="Readiness mode to validate required launch items",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if not TRACKER_PATH.exists():
        sys.stderr.write(f"Missing tracker file: {TRACKER_PATH}\n")
        return 2

    tracker_text = TRACKER_PATH.read_text()
    release_id = str(args.release_id or "").strip() or parse_target_release_id(tracker_text)
    if not release_id:
        sys.stderr.write("Missing release id. Pass --release-id or set target release id in launch tracker.\n")
        return 2

    report = validate_release(
        tracker_path=TRACKER_PATH,
        release_id=release_id,
        mode=args.mode,
        repo_root=REPO_ROOT,
    )

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"Launch evidence validation: release={release_id} mode={args.mode}")
        print(f"Passed: {report['passed']}")
        if report["errors"]:
            print("Errors:")
            for entry in report["errors"]:
                print(f"  - {entry}")
        if report["warnings"]:
            print("Warnings:")
            for entry in report["warnings"]:
                print(f"  - {entry}")

    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
