from __future__ import annotations

from pathlib import Path

from scripts.validate_launch_evidence import (
    parse_launch_tracker_items,
    parse_target_release_id,
    validate_release,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_parse_launch_tracker_items_extracts_status_owner_and_artifacts() -> None:
    text = """
Target release id: `ap-v1-2026-03-01-pilot-rc1`

### L01
- Status: `DONE`
- Owner: `eng-a`
- Artifact links:
  - report: `/tmp/report.md`

### L02
- Status: `OPEN`
- Owner: `TBD`
- Artifact links:
  - report: `TBD`
"""
    items = parse_launch_tracker_items(text)
    assert items["L01"].status == "DONE"
    assert items["L01"].owner == "eng-a"
    assert items["L01"].artifact_links == ["/tmp/report.md"]
    assert items["L02"].status == "OPEN"
    assert parse_target_release_id(text) == "ap-v1-2026-03-01-pilot-rc1"


def test_validate_release_fails_when_required_items_not_done(tmp_path: Path) -> None:
    repo = tmp_path
    tracker = repo / "docs" / "GA_LAUNCH_READINESS_TRACKER.md"
    _write(
        tracker,
        """
Target release id: `ap-v1-2026-03-01-pilot-rc1`

### L01
- Status: `IN_PROGRESS`
- Owner: `eng-a`
- Artifact links:
  - report: `TBD`
""",
    )
    _write(
        repo / "docs" / "ga-evidence" / "releases" / "ap-v1-2026-03-01-pilot-rc1" / "MANIFEST.md",
        "Status: `draft`\n",
    )

    report = validate_release(
        tracker_path=tracker,
        release_id="ap-v1-2026-03-01-pilot-rc1",
        mode="pilot",
        repo_root=repo,
    )
    assert report["passed"] is False
    errors = "\n".join(report["errors"])
    assert "L01:status_not_done" in errors
    assert "L02:missing_tracker_item" in errors
    assert "L03:missing_tracker_item" in errors


def test_validate_release_passes_for_pilot_required_items(tmp_path: Path) -> None:
    repo = tmp_path
    release_id = "ap-v1-2026-03-01-pilot-rc1"
    tracker = repo / "docs" / "GA_LAUNCH_READINESS_TRACKER.md"
    evidence = repo / "docs" / "ga-evidence" / "releases" / release_id

    _write(
        tracker,
        f"""
Target release id: `{release_id}`

### L01
- Status: `DONE`
- Owner: `eng-a`
- Artifact links:
  - report: `{evidence / "GMAIL_RUNTIME_E2E.md"}`

### L02
- Status: `DONE`
- Owner: `eng-b`
- Artifact links:
  - report: `{evidence / "ROLLBACK_CONTROLS_VERIFICATION.md"}`

### L03
- Status: `DONE`
- Owner: `eng-c`
- Artifact links:
  - report: `{evidence / "FAILURE_MODE_MATRIX.md"}`

### L04
- Status: `DONE`
- Owner: `eng-d`
- Artifact links:
  - report: `{evidence / "RUNBOOK_VALIDATIONS.md"}`

### L11
- Status: `DONE`
- Owner: `eng-e`
- Artifact links:
  - report: `{evidence / "SIGNOFFS.md"}`
""",
    )
    _write(evidence / "MANIFEST.md", "Status: `ready`\n")
    _write(evidence / "GMAIL_RUNTIME_E2E.md", "Status: `done`\n")
    _write(evidence / "FAILURE_MODE_MATRIX.md", "Status: `done`\n")
    _write(evidence / "RUNBOOK_VALIDATIONS.md", "Status: `done`\n")
    _write(evidence / "ROLLBACK_CONTROLS_VERIFICATION.md", "Status: `done`\n")
    _write(evidence / "ERP_PARITY_MATRIX.md", "Status: `done`\n")
    _write(evidence / "SIGNOFFS.md", "Status: `done`\n")

    report = validate_release(
        tracker_path=tracker,
        release_id=release_id,
        mode="pilot",
        repo_root=repo,
    )
    assert report["passed"] is True
    assert report["errors"] == []

