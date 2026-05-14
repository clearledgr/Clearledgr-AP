#!/usr/bin/env python3
"""Extract Edit/MultiEdit calls that nomatch under recover.py's last-write-wins
strategy. Mirror semantics exactly: per file, find the last Write in the
filtered window; replay that Write; apply Edits chronologically after it; log
nomatches.

For files with no Write in the window, start from the on-disk content.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

WRITE, EDIT, MULTI_EDIT = "Write", "Edit", "MultiEdit"


def iter_events(jsonl_path: Path):
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
                if name in (WRITE, EDIT, MULTI_EDIT):
                    yield ts, name, (block.get("input") or {}), line_no


def main():
    if len(sys.argv) < 5:
        print("usage: extract_nomatch.py SINCE ORIGINAL_ROOT TARGET JSONL [JSONL...]")
        sys.exit(2)
    since = sys.argv[1]
    original_root = Path(sys.argv[2]).resolve()
    target_root = Path(sys.argv[3]).resolve()
    jsonls = [Path(p) for p in sys.argv[4:]]

    def translate(file_path: str):
        if not file_path:
            return None
        p = Path(file_path)
        if not p.is_absolute():
            return None
        try:
            rel = p.relative_to(original_root)
        except ValueError:
            return None
        out = (target_root / rel).resolve()
        try:
            out.relative_to(target_root)
        except ValueError:
            return None
        return out

    # Per-file event lists.
    per_file: dict[Path, list] = {}
    for jp in jsonls:
        for ts, tool, inp, ln in iter_events(jp):
            if since and ts and ts < since:
                continue
            fp = inp.get("file_path", "")
            target = translate(fp)
            if target is None:
                continue
            per_file.setdefault(target, []).append((ts, ln, tool, inp, jp.name))

    nomatches = []

    for target, events in per_file.items():
        events.sort(key=lambda e: (e[0], e[1]))
        last_write_idx = None
        for i, (ts, ln, tool, inp, fname) in enumerate(events):
            if tool == WRITE:
                last_write_idx = i
        if last_write_idx is not None:
            text = events[last_write_idx][3].get("content", "")
            mutating = events[last_write_idx + 1:]
        else:
            if target.exists():
                try:
                    text = target.read_text(encoding="utf-8")
                except Exception:
                    text = ""
            else:
                text = ""
            mutating = events

        for ts, ln, tool, inp, fname in mutating:
            if tool == EDIT:
                old = inp.get("old_string", "")
                new = inp.get("new_string", "")
                ra = bool(inp.get("replace_all", False))
                if old == "":
                    if not text:
                        text = new
                    continue
                if old not in text:
                    nomatches.append((ts, ln, fname, target, old, new, ra))
                    continue
                text = text.replace(old, new) if ra else text.replace(old, new, 1)
            elif tool == MULTI_EDIT:
                edits = inp.get("edits", []) or []
                for e in edits:
                    old = e.get("old_string", "")
                    new = e.get("new_string", "")
                    ra = bool(e.get("replace_all", False))
                    if old == "":
                        if not text:
                            text = new
                        continue
                    if old not in text:
                        nomatches.append((ts, ln, fname, target, old, new, ra))
                        continue
                    text = text.replace(old, new) if ra else text.replace(old, new, 1)

    by_file: dict[Path, list] = {}
    for entry in nomatches:
        by_file.setdefault(entry[3], []).append(entry)

    print(f"# {len(nomatches)} nomatches across {len(by_file)} files\n")
    for target, entries in sorted(by_file.items(), key=lambda kv: -len(kv[1])):
        print("=" * 80)
        print(f"FILE: {target}")
        print(f"  {len(entries)} failures")
        print("=" * 80)
        for i, (ts, ln, fname, _, old, new, ra) in enumerate(entries, 1):
            print(f"\n--- nomatch {i}/{len(entries)}  ({fname} L{ln}, {ts}, replace_all={ra}) ---")
            print("OLD ---")
            print(old)
            print("NEW +++")
            print(new)


if __name__ == "__main__":
    main()
