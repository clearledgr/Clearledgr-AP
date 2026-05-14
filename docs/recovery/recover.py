#!/usr/bin/env python3
"""Replay Claude Code JSONL tool calls onto a target repo.

Reads one or more Claude Code session transcripts and reconstructs the working
tree by replaying every Write / Edit / MultiEdit. Bash tool calls are logged
to a side file for manual review (never auto-executed).

Strategy:
  1. Iterate every JSONL in chronological order, applying a --since cutoff.
  2. For each file, find the last Write in the filtered set; that Write is the
     canonical snapshot. Apply it first, then apply Edits chronologically
     after it. Edits before the last Write are moot.
  3. Files only ever touched by Edit (no Write) start from the target's
     existing on-disk state and accumulate Edits chronologically.

This avoids the naive-chronological-replay failure where an Edit references
text from a Write that happened many sessions earlier.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WRITE, EDIT, MULTI_EDIT, BASH = "Write", "Edit", "MultiEdit", "Bash"


def iter_events(jsonl_path: Path):
    """Yield (timestamp, tool_name, input_dict, line_no) in file order."""
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = rec.get("timestamp") or ""
            msg = rec.get("message") or {}
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_use":
                    continue
                name = block.get("name", "")
                if name in (WRITE, EDIT, MULTI_EDIT, BASH):
                    yield ts, name, (block.get("input") or {}), line_no


def translate_path(file_path: str, original_root: Path, target_root: Path):
    if not file_path:
        return None
    p = Path(file_path)
    if not p.is_absolute():
        out = (target_root / p).resolve()
    else:
        try:
            rel = p.resolve().relative_to(original_root.resolve())
        except ValueError:
            return None
        out = (target_root / rel).resolve()
    try:
        out.relative_to(target_root.resolve())
    except ValueError:
        return None
    return out


def apply_edits_to_text(text: str, edits: list) -> tuple[str, list]:
    """Apply a list of {old_string, new_string, replace_all} ops to text."""
    failed = []
    for i, e in enumerate(edits):
        old = e.get("old_string", "")
        new = e.get("new_string", "")
        replace_all = bool(e.get("replace_all", False))
        if old == "":
            text = new if not text else (new + text)
            continue
        if old not in text:
            failed.append(i)
            continue
        text = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    return text, failed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", required=True, type=Path, action="append",
                    help="Path to a JSONL transcript. Repeat for multiple, oldest first.")
    ap.add_argument("--original-root", required=True, type=Path)
    ap.add_argument("--target", required=True, type=Path)
    ap.add_argument("--since", type=str, default=None,
                    help="ISO-8601 timestamp cutoff; only events at or after this fire.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--bash-log", type=Path, default=Path("bash_commands.log"))
    ap.add_argument("--nomatch-report", type=Path, default=None,
                    help="Write a JSON dump of every Edit/MultiEdit op whose old_string nomatched.")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    for j in args.jsonl:
        if not j.exists():
            print(f"error: --jsonl not found: {j}", file=sys.stderr)
            return 2
    if not args.target.is_dir():
        print(f"error: --target is not a directory", file=sys.stderr)
        return 2

    bash_log = args.bash_log.open("w", encoding="utf-8")
    bash_log.write("# Bash commands from JSONL replay (review + selectively re-run)\n\n")

    # Pass 1: gather all file-mutating events, keyed by target path,
    # plus log Bash. Each event is (ts, line_no, tool, input).
    per_file: dict[Path, list] = {}
    bash_count = 0
    skipped_outside = 0
    skipped_before = 0

    for jsonl_path in args.jsonl:
        for ts, tool, input_data, line_no in iter_events(jsonl_path):
            if args.since and ts and ts < args.since:
                skipped_before += 1
                continue
            if tool == BASH:
                cmd = input_data.get("command", "")
                desc = input_data.get("description", "")
                bash_log.write(f"# {ts} L{line_no} ({jsonl_path.name}): {desc}\n{cmd}\n\n")
                bash_count += 1
                continue
            file_path = input_data.get("file_path", "")
            target_path = translate_path(file_path, args.original_root, args.target)
            if target_path is None:
                skipped_outside += 1
                continue
            per_file.setdefault(target_path, []).append((ts, line_no, tool, input_data))

    bash_log.close()

    # Pass 2: per file, find last Write, then apply Edits/MultiEdits after it.
    counts = {"file_written": 0, "file_edited_only": 0, "miss": 0, "nomatch": 0,
              "edits_applied": 0, "edits_failed": 0}

    files_changed = []
    nomatch_records = []

    for target_path, events in per_file.items():
        # Sort events by (timestamp, line_no) — chronological.
        events.sort(key=lambda e: (e[0], e[1]))

        # Find index of the last Write.
        last_write_idx = None
        for i, (ts, ln, tool, inp) in enumerate(events):
            if tool == WRITE:
                last_write_idx = i

        if last_write_idx is not None:
            # Start from that Write's content.
            base_content = events[last_write_idx][3].get("content", "")
            text = base_content
            mutating = events[last_write_idx + 1:]
            counts["file_written"] += 1
        else:
            # No Write — use existing on-disk file (or empty if missing).
            if target_path.exists():
                try:
                    text = target_path.read_text(encoding="utf-8")
                except Exception:
                    text = ""
            else:
                text = ""
                counts["miss"] += 1
            mutating = events
            counts["file_edited_only"] += 1

        per_file_failures = 0
        for ts, ln, tool, inp in mutating:
            if tool == EDIT:
                old = inp.get("old_string", "")
                new = inp.get("new_string", "")
                replace_all = bool(inp.get("replace_all", False))
                if old == "":
                    # treat as create-on-empty
                    if not text:
                        text = new
                    counts["edits_applied"] += 1
                    continue
                if old not in text:
                    per_file_failures += 1
                    counts["edits_failed"] += 1
                    nomatch_records.append({
                        "file": str(target_path), "ts": ts, "line": ln,
                        "tool": "Edit", "old_string": old, "new_string": new,
                        "replace_all": replace_all,
                    })
                    if args.verbose:
                        print(f"NOMATCH {target_path} L{ln}")
                    continue
                text = text.replace(old, new) if replace_all else text.replace(old, new, 1)
                counts["edits_applied"] += 1
            elif tool == MULTI_EDIT:
                edits = inp.get("edits", []) or []
                # Inline so we can record per-edit nomatches with provenance.
                for ei, e in enumerate(edits):
                    eold = e.get("old_string", "")
                    enew = e.get("new_string", "")
                    era = bool(e.get("replace_all", False))
                    if eold == "":
                        if not text:
                            text = enew
                        counts["edits_applied"] += 1
                        continue
                    if eold not in text:
                        counts["edits_failed"] += 1
                        per_file_failures += 1
                        nomatch_records.append({
                            "file": str(target_path), "ts": ts, "line": ln,
                            "tool": f"MultiEdit[{ei}]", "old_string": eold,
                            "new_string": enew, "replace_all": era,
                        })
                        continue
                    text = text.replace(eold, enew) if era else text.replace(eold, enew, 1)
                    counts["edits_applied"] += 1

        if not args.dry_run:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(text, encoding="utf-8")

        files_changed.append((target_path, len(mutating), per_file_failures))

    print()
    print("=" * 60)
    print(f"JSONLs read:                {len(args.jsonl)}")
    print(f"Bash logged:                {bash_count}  (review {args.bash_log})")
    print(f"Events skipped (--since):   {skipped_before}")
    print(f"Events skipped (outside):   {skipped_outside}")
    print(f"Files touched:              {len(per_file)}")
    print(f"  with at least one Write:  {counts['file_written']}")
    print(f"  edit-only:                {counts['file_edited_only']}")
    print(f"  edit-only & missing on disk: {counts['miss']}")
    print(f"Edit ops applied:           {counts['edits_applied']}")
    print(f"Edit ops failed (nomatch):  {counts['edits_failed']}")
    print(f"\nMode: {'DRY RUN' if args.dry_run else 'APPLIED'}")

    if args.nomatch_report:
        args.nomatch_report.write_text(
            json.dumps(nomatch_records, indent=2), encoding="utf-8"
        )
        print(f"Nomatch report: {args.nomatch_report} ({len(nomatch_records)} records)")

    if args.verbose:
        print("\n-- per-file (top 30 by failures) --")
        files_changed.sort(key=lambda r: -r[2])
        for path, n, fails in files_changed[:30]:
            print(f"  {fails}/{n} failed  {path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
