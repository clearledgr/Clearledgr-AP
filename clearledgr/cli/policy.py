"""``solden policy`` — show / list / replay AP policy versions.

Subcommands:

* ``show <org_id> [--kind ap]`` — current active policy version.
* ``versions <org_id> [--kind ap] [--limit N]`` — version history.
* ``replay <version_id> --org <org_id> [--since DATE] [--until DATE]``
  — re-evaluate a past window's AP items against ``version_id`` and
  return per-item deltas. Same surface as the ``/api/policies/replay``
  HTTP endpoint, but invokable from cron / CI without an HTTP layer.
* ``rollback <version_id> --org <org_id> [--description TEXT]`` —
  create a new version that copies the historical content. Append-
  only; never overwrites history.

Talks through ``PolicyService`` so the audit trail + content hashing
match what the API emits.
"""
from __future__ import annotations

import argparse
from typing import Any, Dict

from . import _common


_VERSION_COLUMNS = (
    "id", "policy_kind", "version_number", "is_rollback",
    "created_at", "created_by", "description",
)


def add_subparsers(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "policy",
        help="Inspect / list / replay / rollback AP policy versions",
    )
    group = parser.add_subparsers(dest="policy_cmd", required=True)

    p_show = group.add_parser("show", help="Show the active policy for a kind")
    p_show.add_argument("org_id")
    p_show.add_argument("--kind", default="approval_thresholds",
                        help="Policy kind (default: approval_thresholds; see services/policy_service.py for the registry)")
    p_show.set_defaults(func=_cmd_show)

    p_versions = group.add_parser("versions", help="List version history for a kind")
    p_versions.add_argument("org_id")
    p_versions.add_argument("--kind", default="approval_thresholds")
    p_versions.add_argument("--limit", type=int, default=50)
    p_versions.set_defaults(func=_cmd_versions)

    p_replay = group.add_parser("replay", help="Replay a window of AP items against a historical version")
    p_replay.add_argument("version_id")
    p_replay.add_argument("--org", required=True, dest="org_id")
    p_replay.add_argument("--since", help="ISO timestamp lower bound (inclusive)")
    p_replay.add_argument("--until", help="ISO timestamp upper bound (inclusive)")
    p_replay.add_argument("--limit", type=int, default=500)
    p_replay.set_defaults(func=_cmd_replay)

    p_rollback = group.add_parser("rollback", help="Create a new version copying a historical version's content")
    p_rollback.add_argument("version_id")
    p_rollback.add_argument("--org", required=True, dest="org_id")
    p_rollback.add_argument("--description", default="rolled back via solden CLI")
    p_rollback.add_argument("--actor", default="solden-cli",
                            help="Actor recorded on the new version (default: solden-cli)")
    p_rollback.set_defaults(func=_cmd_rollback)

    p_lint = group.add_parser(
        "lint",
        help="Static-analyze the active policy for an org (dead bands, coverage gaps, risky thresholds)",
    )
    p_lint.add_argument("org_id")
    p_lint.add_argument("--kind", default="approval_thresholds",
                        help="Policy kind to lint (only approval_thresholds is supported in v0.1)")
    p_lint.add_argument("--version-id", default=None,
                        help="Lint a specific version_id instead of the active one")
    p_lint.add_argument("--auto-approve-ceiling", type=float, default=None,
                        help="Override the auto-approve safety ceiling (default: 5000)")
    p_lint.set_defaults(func=_cmd_lint)

    # ─── Sprint 2: branch ops ──────────────────────────────────────
    p_branch = group.add_parser(
        "branch",
        help="Branchable AP policy: open / commit / list / diff / merge / abandon",
    )
    bgrp = p_branch.add_subparsers(dest="branch_cmd", required=True)

    b_create = bgrp.add_parser("create", help="Open a new branch off the active main version")
    b_create.add_argument("org_id")
    b_create.add_argument("name")
    b_create.add_argument("--kind", default="approval_thresholds")
    b_create.add_argument("--base-version-id", default=None,
                          help="Fork from this version id instead of the active main version")
    b_create.add_argument("--description", default="")
    b_create.add_argument("--actor", default="solden-cli")
    b_create.set_defaults(func=_cmd_branch_create)

    b_list = bgrp.add_parser("list", help="List branches for an org (filter by kind / status)")
    b_list.add_argument("org_id")
    b_list.add_argument("--kind", default=None)
    b_list.add_argument("--status", default=None,
                        choices=["open", "merged", "abandoned"])
    b_list.add_argument("--limit", type=int, default=50)
    b_list.set_defaults(func=_cmd_branch_list)

    b_show = bgrp.add_parser("show", help="Show a branch's metadata + head version pointer")
    b_show.add_argument("org_id")
    b_show.add_argument("branch_id")
    b_show.set_defaults(func=_cmd_branch_show)

    b_commit = bgrp.add_parser(
        "commit",
        help="Append a new version to a branch from a JSON content file (or '-' for stdin)",
    )
    b_commit.add_argument("org_id")
    b_commit.add_argument("branch_id")
    b_commit.add_argument("--content-file", required=True,
                          help="Path to JSON file with the new policy content (or '-' for stdin)")
    b_commit.add_argument("--description", default="")
    b_commit.add_argument("--actor", default="solden-cli")
    b_commit.set_defaults(func=_cmd_branch_commit)

    b_diff = bgrp.add_parser("diff", help="Diff a branch's head against current main")
    b_diff.add_argument("org_id")
    b_diff.add_argument("branch_id")
    b_diff.set_defaults(func=_cmd_branch_diff)

    b_merge = bgrp.add_parser(
        "merge",
        help="Merge a branch's head content into main (creates a new active version)",
    )
    b_merge.add_argument("org_id")
    b_merge.add_argument("branch_id")
    b_merge.add_argument("--description", default="")
    b_merge.add_argument("--actor", default="solden-cli")
    b_merge.set_defaults(func=_cmd_branch_merge)

    b_abandon = bgrp.add_parser(
        "abandon",
        help="Close a branch without merging (versions stay in audit trail)",
    )
    b_abandon.add_argument("org_id")
    b_abandon.add_argument("branch_id")
    b_abandon.add_argument("--actor", default="solden-cli")
    b_abandon.set_defaults(func=_cmd_branch_abandon)


def _service(org_id: str):
    """Lazy import to keep CLI startup snappy."""
    from clearledgr.services.policy_service import PolicyService
    return PolicyService(organization_id=org_id)


def _to_dict(version) -> Dict[str, Any]:
    return {
        "id": version.id,
        "organization_id": version.organization_id,
        "policy_kind": version.policy_kind,
        "version_number": version.version_number,
        "content": version.content,
        "content_hash": version.content_hash,
        "created_at": version.created_at,
        "created_by": version.created_by,
        "description": version.description,
        "parent_version_id": version.parent_version_id,
        "is_rollback": version.is_rollback,
    }


def _cmd_show(args: argparse.Namespace) -> int:
    svc = _service(args.org_id)
    try:
        active = svc.get_active(args.kind)
    except Exception as exc:
        print(f"error: {exc}")
        return 1
    _common.emit(_to_dict(active),
                 ("id", "policy_kind", "version_number", "is_rollback",
                  "created_at", "created_by", "description", "content"),
                 as_json=args.json)
    return 0


def _cmd_versions(args: argparse.Namespace) -> int:
    svc = _service(args.org_id)
    try:
        versions = svc.list_versions(args.kind, limit=args.limit)
    except Exception as exc:
        print(f"error: {exc}")
        return 1
    rows = [_to_dict(v) for v in versions]
    _common.emit(rows, _VERSION_COLUMNS, as_json=args.json,
                 empty=f"(no versions for kind={args.kind!r})")
    return 0


def _cmd_replay(args: argparse.Namespace) -> int:
    svc = _service(args.org_id)
    try:
        result = svc.replay_against(
            version_id=args.version_id,
            since=args.since,
            until=args.until,
            limit=int(args.limit or 500),
        )
    except Exception as exc:
        print(f"error: {exc}")
        return 1
    payload = {
        "target_version_id": result.target_version_id,
        "target_version_number": result.target_version_number,
        "target_kind": result.target_kind,
        "items_evaluated": result.items_evaluated,
        "summary": result.summary,
        "deltas": [
            {
                "ap_item_id": d.ap_item_id,
                "field": d.field,
                "current_value": d.current_value,
                "replayed_value": d.replayed_value,
            }
            for d in result.deltas
        ],
    }
    _common.print_json(payload)
    return 0


def _cmd_rollback(args: argparse.Namespace) -> int:
    svc = _service(args.org_id)
    try:
        new_version = svc.rollback_to(
            version_id=args.version_id,
            actor=args.actor,
            description=args.description,
        )
    except Exception as exc:
        print(f"error: {exc}")
        return 1
    _common.print_json(_to_dict(new_version))
    return 0


def _cmd_lint(args: argparse.Namespace) -> int:
    """Static-analyze a policy version. Exits 1 if any finding is
    at error severity, 0 otherwise. Designed for CI use.
    """
    from clearledgr.services import policy_linter

    if args.kind != "approval_thresholds":
        print(
            f"error: only approval_thresholds is lintable in v0.1; got {args.kind!r}",
            file=__import__("sys").stderr,
        )
        return 2

    svc = _service(args.org_id)
    try:
        if args.version_id:
            version = svc.get_version(args.version_id)
        else:
            version = svc.get_active(args.kind)
    except Exception as exc:
        print(f"error: {exc}")
        return 1

    ceiling = (
        args.auto_approve_ceiling
        if args.auto_approve_ceiling is not None
        else policy_linter.DEFAULT_AUTO_APPROVE_CEILING
    )
    findings = policy_linter.lint_approval_thresholds(
        version.content,
        auto_approve_ceiling=ceiling,
    )

    if args.json:
        _common.print_json({
            "org_id": args.org_id,
            "kind": args.kind,
            "version_id": version.id,
            "version_number": version.version_number,
            "findings": [f.to_dict() for f in findings],
            "error_count": sum(1 for f in findings if f.severity == policy_linter.SEVERITY_ERROR),
            "warning_count": sum(1 for f in findings if f.severity == policy_linter.SEVERITY_WARNING),
        })
    else:
        if not findings:
            print(f"clean — {args.kind} v{version.version_number} for org={args.org_id}")
        else:
            print(f"linting {args.kind} v{version.version_number} for org={args.org_id}\n")
            for f in findings:
                print(f"[{f.severity.upper():7s}] {f.rule}  {f.location}")
                print(f"          {f.message}")
                if f.suggestion:
                    print(f"          → {f.suggestion}")
                print()
            errs = sum(1 for f in findings if f.severity == policy_linter.SEVERITY_ERROR)
            warns = sum(1 for f in findings if f.severity == policy_linter.SEVERITY_WARNING)
            print(f"summary: {errs} error(s), {warns} warning(s)")

    return 1 if policy_linter.has_errors(findings) else 0


# ─── Branch subcommands (Sprint 2) ─────────────────────────────────


_BRANCH_LIST_COLUMNS = (
    "id", "policy_kind", "name", "status",
    "head_version_id", "base_version_id",
    "created_at", "created_by",
)


def _branch_to_dict(branch) -> Dict[str, Any]:
    return {
        "id": branch.id,
        "organization_id": branch.organization_id,
        "policy_kind": branch.policy_kind,
        "name": branch.name,
        "head_version_id": branch.head_version_id,
        "base_version_id": branch.base_version_id,
        "status": branch.status,
        "description": branch.description,
        "created_at": branch.created_at,
        "created_by": branch.created_by,
        "merged_at": branch.merged_at,
        "merged_into_version_id": branch.merged_into_version_id,
        "merged_by": branch.merged_by,
        "abandoned_at": branch.abandoned_at,
        "abandoned_by": branch.abandoned_by,
    }


def _cmd_branch_create(args: argparse.Namespace) -> int:
    svc = _service(args.org_id)
    try:
        branch = svc.create_branch(
            args.kind,
            name=args.name,
            actor=args.actor,
            base_version_id=args.base_version_id,
            description=args.description,
        )
    except Exception as exc:
        print(f"error: {exc}")
        return 1
    _common.emit(_branch_to_dict(branch), _BRANCH_LIST_COLUMNS, as_json=args.json)
    return 0


def _cmd_branch_list(args: argparse.Namespace) -> int:
    svc = _service(args.org_id)
    branches = svc.list_branches(kind=args.kind, status=args.status, limit=args.limit)
    rows = [_branch_to_dict(b) for b in branches]
    _common.emit(rows, _BRANCH_LIST_COLUMNS, as_json=args.json,
                 empty="(no branches)")
    return 0


def _cmd_branch_show(args: argparse.Namespace) -> int:
    svc = _service(args.org_id)
    try:
        branch = svc.get_branch(args.branch_id)
    except Exception as exc:
        print(f"error: {exc}")
        return 1
    _common.emit(_branch_to_dict(branch), _BRANCH_LIST_COLUMNS + (
        "description", "merged_at", "merged_into_version_id", "merged_by",
        "abandoned_at", "abandoned_by",
    ), as_json=args.json)
    return 0


def _cmd_branch_commit(args: argparse.Namespace) -> int:
    """Read content from --content-file (path or '-' for stdin),
    parse as JSON, commit to the branch.
    """
    import sys
    if args.content_file == "-":
        raw = sys.stdin.read()
    else:
        with open(args.content_file, "r") as f:
            raw = f.read()
    try:
        import json as _json
        content = _json.loads(raw)
    except Exception as exc:
        print(f"error: --content-file did not parse as JSON: {exc}")
        return 1
    if not isinstance(content, dict):
        print("error: --content-file JSON must be an object")
        return 1

    svc = _service(args.org_id)
    try:
        version = svc.commit_to_branch(
            args.branch_id,
            content,
            actor=args.actor,
            description=args.description,
        )
    except Exception as exc:
        print(f"error: {exc}")
        return 1
    _common.print_json(_to_dict(version))
    return 0


def _cmd_branch_diff(args: argparse.Namespace) -> int:
    svc = _service(args.org_id)
    try:
        diff = svc.diff_branch(args.branch_id)
    except Exception as exc:
        print(f"error: {exc}")
        return 1
    _common.print_json(diff)
    return 0


def _cmd_branch_merge(args: argparse.Namespace) -> int:
    svc = _service(args.org_id)
    try:
        new_main_version = svc.merge_branch(
            args.branch_id,
            actor=args.actor,
            description=args.description,
        )
    except Exception as exc:
        print(f"error: {exc}")
        return 1
    _common.print_json(_to_dict(new_main_version))
    return 0


def _cmd_branch_abandon(args: argparse.Namespace) -> int:
    svc = _service(args.org_id)
    try:
        branch = svc.abandon_branch(args.branch_id, actor=args.actor)
    except Exception as exc:
        print(f"error: {exc}")
        return 1
    _common.emit(_branch_to_dict(branch), _BRANCH_LIST_COLUMNS + (
        "abandoned_at", "abandoned_by",
    ), as_json=args.json)
    return 0
