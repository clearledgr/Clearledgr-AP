# Recovery report — May 10, 2026 disk-corruption event

## What happened
APFS partial-write failure at ~96 % disk capacity zeroed several
files (`.git/refs/heads/main`, `clearledgr/services/rowset_branch.py`)
and bus-errored on memory-mapped pack files. Effect: ~18 unpushed
local commits (M15 → M19f → Sprints 1 – 4 Phase 2 → partial Sprint 5)
were unrecoverable from the working repository.

GitHub Actions free-tier minutes were exhausted, so those 18 commits
had been queued locally awaiting quota refresh on the 1st. None of
them ever shipped to `origin/main`.

## What was recovered
The complete Claude Code JSONL transcripts for the affected sessions
were intact:

* `5bfe0590-2fbc-4c33-bc96-0841ba520654.jsonl` — Apr 9 → Apr 21 23:01:35
* `066fff3a-8ce3-4b1d-957c-dced2c657657.jsonl` — Apr 21 23:02:36 → May 10 18:14:33

Every `Write` / `Edit` / `MultiEdit` tool-call from those sessions was
replayed onto a fresh clone of `origin/main` (HEAD `9b9043b9`,
committed 2026-05-09 10:11:48 UTC) by `recover.py`.

Cutoff filter: only events at or after `2026-05-09T10:11:48Z` ran,
which excludes work that already shipped to `origin/main` and prevents
the replay from overwriting baseline files with stale snapshots.

## Replay statistics

| Metric | Count |
|---|---|
| JSONL transcripts replayed | 2 |
| Total events read | ~30 k + 54 k |
| Events skipped (--since cutoff) | 15 707 |
| `Bash` commands logged for review | 452 |
| Files touched by replay | 74 |
| Files with at least one Write (full snapshot) | 26 |
| Files reconstructed from Edits only | 48 |
| Edit ops applied successfully | 139 |
| Edit ops nomatched | 35 |
| New modules created | 18 |

`python -m compileall clearledgr/ tests/ scripts/` returns clean — no
syntax damage from the replay.

## What is *not* recovered: 35 nomatch sites

The 35 failures (in 23 files, see `nomatches.json`) are Edits whose
`old_string` could not be located in the post-replay file content.
Cause: the JSONL transcript references intermediate file states from
sessions whose Writes were not preserved (sessions whose files were
compacted or deleted before this incident). Without a Write snapshot
we cannot reconstruct that intermediate state from JSONL alone — Reads
in the transcript are partial, not full-file.

Failure profile (per file):

```
4 fails  tests/test_webhook_auth_hardening.py
4 fails  clearledgr/api/workspace_shell.py
4 fails  clearledgr/core/migrations.py
2 fails  tests/test_channel_approval_contract.py
2 fails  clearledgr/services/finance_runtime_actions.py
2 fails  tests/test_runtime_tenant_isolation.py
1 fail   17 other files (1-edit touch-ups each)
```

The biggest gap is `clearledgr/core/migrations.py`: four Edits adding
roughly 365 lines of migration code (likely v80 + v82 CHECK constraints
and policy_branches/data_branches tables from Sprints 1 + 4) did not
apply.

## What's in this branch

* `docs/recovery/recover.py` — the replay script
* `docs/recovery/extract_nomatch.py` — the nomatch diagnostic tool
* `docs/recovery/nomatches.json` — full per-Edit dump of every gap
  (file path, JSONL line, timestamp, full old_string, full new_string)
* `docs/recovery/bash_commands.log` — every `Bash` command run during
  the affected sessions, in chronological order, with timestamps
* `docs/recovery/RECOVERY_REPORT.md` — this file

The 37 modified files + 18 new modules from the replay are also
committed on this branch, as a single bundled commit.

## Follow-up: 35 gaps closed by hand (commit 2)

A follow-up commit walks `nomatches.json` and patches all 35 sites:

* **12 sites** were no-ops — the JSONL `new_string` was already
  present in the file (older Edit chains the replay had already
  applied via a later Write or a more recent Edit). Marked resolved.
* **4 sites** were superseded by Mo's "no AI tells" rule — JSONL
  used em-dashes, the canonical form is plain commas. Already in
  desired form.
* **19 sites** were genuine missing content, applied by hand:
  * Tenant-rename imports + helpers wired into `ap_policies`,
    `policies`, `gmail_extension`, `workspace_shell`,
    `agent_retry_jobs`, `finance_runtime_actions`.
  * `_resolve_org_id` rewritten on `workspace_shell` with the legacy
    `org_access_denied` back-compat shim.
  * Slack OAuth state-payload now raises 400 on missing org instead
    of 500.
  * `ui_perf` beacon drops unscoped requests silently (telemetry
    contract preserves 200).
  * `noqa: org-default` markers added to platform-mode sentinels in
    `finance_learning`, `correction_learning`, `agent_memory`.
  * `slack_notifications` log uses `<unscoped>` instead of `default`
    coerce.
  * `tests/test_runtime_tenant_isolation` doc explains in-memory
    sentinel vs v79 CHECK.
  * `tests/test_channel_approval_contract` seeds Slack install for
    rollout-control test.
  * Migration **v80** (per-table CHECK constraints, M21), **v81**
    (policy branches table, Sprint 2), and **v82** (data_branches
    + overlay columns on row-set tables, Sprint 5 Phase B) appended
    to `migrations.py` (~293 new lines).

Tree compile-check stays clean after follow-up.

## Next steps

1. Run the test suite against Postgres locally to confirm no
   regressions from the hand-patches.
2. Spot-check `bash_commands.log` for any `mv`, `rm`, or `perl -i`
   operations that affected files outside the 74 touched here.
3. Cherry-pick or merge this branch back into `main` (or split it
   into the original M15 → M19f → Sprint-1/2/3/4 commit groups for
   cleaner history).
