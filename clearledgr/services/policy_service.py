"""Versioned policy storage + replay (Gap 2).

Every change to a tenant's coordination policy creates an immutable
``policy_versions`` row. Reads always go through this service so AP
items can be stamped with the version they were evaluated under;
replays can answer "what would have happened if the old policy were
still active?" without manual SQL archaeology.

Supported policy kinds (each is a snapshot of one slice of
``settings_json``):

* ``approval_thresholds`` — per-amount routing rules + approver
  targets. Drives Slack approval card destination + mentions.
* ``gl_account_map`` — semantic GL category → ERP-side account code.
  Drives where Clearledgr-posted bills land in the chart of accounts.
* ``confidence_gate`` — confidence-floor parameters
  (``critical_field_confidence_threshold``,
  ``confidence_gate_threshold``). Drives auto-approve eligibility.
* ``autonomy_policy`` — agent action scope (post / approve / chase /
  reject autonomy windows + thresholds).
* ``vendor_master_gate`` — whether unknown-vendor bills create
  Boxes or get blocked at intake.

Migration: on first read of a kind for an org, if no row exists,
this service snapshots the matching slice of the org's
``settings_json`` and writes it as version 1. Backward-compatible —
existing readers that hit ``settings_json`` directly keep working
because :func:`set_policy` mirrors writes back into
``settings_json`` (and creates a new versioned row).
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from clearledgr.core.database import get_db

logger = logging.getLogger(__name__)


# ─── Public types ──────────────────────────────────────────────────


# Each kind matches a top-level key in ``settings_json``. Adding a
# new kind: append it here + handle the slice extraction in
# :func:`_slice_settings_json`.
POLICY_KINDS: Set[str] = {
    "approval_thresholds",
    "gl_account_map",
    "confidence_gate",
    "autonomy_policy",
    "vendor_master_gate",
    # Gap 3: match-engine tolerances. Per-match-type tolerance config
    # (price variance, quantity variance, amount fuzz, date window).
    # Sub-namespaced under match_type so AP 3-way + bank-recon +
    # AR cash-app + ... can each have independent settings.
    "match_tolerances",
    # Gap 5: annotation targets. Per-tenant on/off + per-target
    # configuration of every external surface that should reflect
    # Box state. Default content has every target disabled — opt-in
    # per customer.
    "annotation_targets",
    # AP matching mode. Selects which match algorithm the
    # coordination engine runs for incoming invoices:
    #   - three_way_required: PO + GRN + invoice; missing GRN blocks
    #   - two_way_fallback:   try 3-way; if only GRN is missing, fall
    #                          back to 2-way (PO + invoice). Default.
    #   - policy_only:        skip matching entirely; route via
    #                          approval_thresholds.
    "match_mode",
    # Sprint 5 Phase A — branchable backoffice config (item #9 in
    # the ModernRelay-inspired roadmap). Four new single-blob kinds
    # that share Sprint 2's branch + replay infrastructure with the
    # existing kinds. Category-2 row-set surfaces (vendor master,
    # GL chart, custom roles, entity restrictions) ride a separate
    # overlay table — see ``services/rowset_branch.py`` (Phase B).
    #
    # ``sanctions_list`` — per-tenant sanctions screening list.
    # Branch-and-replay lets compliance test a list update against
    # historical AP items without affecting live screening.
    "sanctions_list",
    # ``erp_field_mappings`` — per-tenant ERP custom-field mappings
    # (NetSuite custbody_*, SAP Z-fields, etc.). Already a feature
    # surface (workspace_erp_field_mappings); now versioned.
    "erp_field_mappings",
    # ``approval_routing`` — channel routing for approval requests
    # (Slack channel ID, Teams team, email distribution list, fallback).
    # Distinct from ``approval_thresholds`` which determines WHO has
    # to approve; this determines WHERE the request lands.
    "approval_routing",
    # ``org_settings`` — operator-tunable org-level toggles
    # (timezone, fiscal year start, default currency, default
    # payment terms). Branchable for org-wide changes that need
    # ops-side preview before ship.
    "org_settings",
}

VALID_MATCH_MODES: Set[str] = {
    "three_way_required",
    "two_way_fallback",
    "policy_only",
}


@dataclass(frozen=True)
class PolicyVersion:
    """One immutable snapshot of a policy slice."""

    id: str
    organization_id: str
    policy_kind: str
    version_number: int
    content: Dict[str, Any]
    content_hash: str
    created_at: str  # ISO timestamp
    created_by: str
    description: str = ""
    parent_version_id: Optional[str] = None
    is_rollback: bool = False
    # Sprint 2 branchable policy: NULL = main, otherwise the branch
    # this version belongs to. ``get_active`` filters on
    # ``branch_id IS NULL`` so branched experiments don't accidentally
    # become production.
    branch_id: Optional[str] = None


@dataclass(frozen=True)
class PolicyBranch:
    """A named ref pointing at a ``PolicyVersion``.

    Branches are the unit of policy experimentation. Operators
    create a branch off a base version, commit edits (each commit
    appends a new ``PolicyVersion`` with this branch's id), replay
    the head against historical AP items, and either merge (the
    head's content becomes a new version on main) or abandon.
    """

    id: str
    organization_id: str
    policy_kind: str
    name: str
    head_version_id: str
    base_version_id: str
    status: str  # 'open' | 'merged' | 'abandoned'
    description: str
    created_at: str
    created_by: str
    merged_at: Optional[str] = None
    merged_into_version_id: Optional[str] = None
    merged_by: Optional[str] = None
    abandoned_at: Optional[str] = None
    abandoned_by: Optional[str] = None


@dataclass(frozen=True)
class ReplayDelta:
    """Per-AP-item delta from a policy replay run."""

    ap_item_id: str
    field: str  # e.g. 'approval_threshold_band' / 'auto_approve_eligibility' / 'gl_account'
    current_value: Any
    replayed_value: Any


@dataclass(frozen=True)
class ReplayResult:
    target_version_id: str
    target_version_number: int
    target_kind: str
    items_evaluated: int
    deltas: List[ReplayDelta] = field(default_factory=list)
    summary: Dict[str, int] = field(default_factory=dict)


class PolicyKindError(ValueError):
    """Raised when an unknown policy kind is referenced."""


class PolicyVersionNotFound(LookupError):
    """Raised when a version_id doesn't exist for the org."""


class PolicyBranchNotFound(LookupError):
    """Raised when a branch_id (or branch name) doesn't exist for the org."""


# ─── Service ────────────────────────────────────────────────────────


class PolicyService:
    """Read/write/replay versioned policies for one organization.

    Cheap to construct — just holds a DB handle. Created per request.
    """

    def __init__(self, organization_id: str) -> None:
        self.organization_id = str(organization_id or "default").strip() or "default"
        self.db = get_db()

    # ─── Reads ───────────────────────────────────────────────────

    def get_active(self, kind: str) -> PolicyVersion:
        """Return the latest version for this org+kind. If no row
        exists, snapshot the current ``settings_json`` slice as
        version 1 (lazy migration), then return that."""
        _validate_kind(kind)
        latest = self._fetch_latest(kind)
        if latest is not None:
            return latest
        # Lazy migration: snapshot what's in settings_json today.
        snapshot_content = self._slice_from_settings_json(kind)
        return self._insert(
            kind=kind,
            content=snapshot_content,
            created_by="system:lazy_migration_v45",
            description=f"Initial snapshot of {kind} from settings_json",
            parent_version_id=None,
            is_rollback=False,
        )

    def list_versions(self, kind: str, *, limit: int = 50) -> List[PolicyVersion]:
        _validate_kind(kind)
        self.db.initialize()
        sql = (
            "SELECT * FROM policy_versions "
            "WHERE organization_id = %s AND policy_kind = %s "
            "ORDER BY version_number DESC LIMIT %s"
        )
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (self.organization_id, kind, int(limit)))
            rows = cur.fetchall()
        return [_row_to_version(dict(r)) for r in rows]

    def get_version(self, version_id: str) -> PolicyVersion:
        self.db.initialize()
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM policy_versions WHERE id = %s AND organization_id = %s",
                (version_id, self.organization_id),
            )
            row = cur.fetchone()
        if not row:
            raise PolicyVersionNotFound(f"version {version_id!r} not found for org {self.organization_id!r}")
        return _row_to_version(dict(row))

    # ─── Writes (always create new versions) ─────────────────────

    def set_policy(
        self,
        kind: str,
        content: Dict[str, Any],
        *,
        actor: str,
        description: str = "",
        parent_version_id: Optional[str] = None,
        is_rollback: bool = False,
    ) -> PolicyVersion:
        """Create a new version. Idempotent: if the new content's
        hash matches the latest, returns the existing version
        (avoids version inflation from no-op saves).

        Mirrors the new content back into ``settings_json`` so
        existing readers (InvoiceWorkflowService._load_settings,
        erp_connections endpoints, etc.) see the change without
        being refactored to read through PolicyService."""
        _validate_kind(kind)
        latest = self._fetch_latest(kind)
        new_hash = _hash_content(content)
        if latest is not None and latest.content_hash == new_hash:
            return latest

        version = self._insert(
            kind=kind,
            content=content,
            created_by=actor,
            description=description,
            parent_version_id=parent_version_id or (latest.id if latest else None),
            is_rollback=is_rollback,
        )
        # Mirror to settings_json so existing readers stay functional.
        self._mirror_to_settings_json(kind, content)
        return version

    def rollback_to(
        self,
        version_id: str,
        *,
        actor: str,
        description: str = "",
    ) -> PolicyVersion:
        """Roll back to a historical version's content by creating a
        NEW version that copies the old content and links to it via
        ``parent_version_id``. Old versions are never mutated."""
        target = self.get_version(version_id)
        return self.set_policy(
            kind=target.policy_kind,
            content=target.content,
            actor=actor,
            description=description or f"Rollback to v{target.version_number}",
            parent_version_id=target.id,
            is_rollback=True,
        )

    # ─── Branches (Sprint 2 — branchable AP policy with replay) ──

    def create_branch(
        self,
        kind: str,
        *,
        name: str,
        actor: str,
        base_version_id: Optional[str] = None,
        description: str = "",
    ) -> "PolicyBranch":
        """Open a branch off ``base_version_id`` (or the active main
        version, if not supplied).

        The branch has no commits yet — its ``head_version_id`` equals
        ``base_version_id``. The first ``commit_to_branch`` advances
        the head and writes a new ``policy_versions`` row tagged with
        this branch's id.

        Names must be unique across open branches per (org, kind).
        Reusing a name from a merged / abandoned branch is allowed —
        history reads can still find the closed branch by name +
        status.
        """
        _validate_kind(kind)
        normalized_name = (name or "").strip()
        if not normalized_name:
            raise ValueError("branch name is required")
        if normalized_name in {"main", ""}:
            raise ValueError(
                "branch name 'main' is reserved for the active version chain"
            )
        if base_version_id is None:
            active = self.get_active(kind)
            base_version_id = active.id
        else:
            # Validate the base version exists + belongs to this org
            # — passing a foreign org's version id would silently bind
            # the branch to that org's content otherwise.
            base = self.get_version(base_version_id)
            if base.policy_kind != kind:
                raise ValueError(
                    f"base version {base_version_id!r} is for kind "
                    f"{base.policy_kind!r}, not {kind!r}"
                )

        branch = PolicyBranch(
            id=f"PB-{uuid.uuid4().hex}",
            organization_id=self.organization_id,
            policy_kind=kind,
            name=normalized_name,
            head_version_id=base_version_id,
            base_version_id=base_version_id,
            status="open",
            description=description or "",
            created_at=datetime.now(timezone.utc).isoformat(),
            created_by=actor,
        )
        self.db.initialize()
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO policy_branches
                  (id, organization_id, policy_kind, name,
                   head_version_id, base_version_id, status, description,
                   created_at, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    branch.id, branch.organization_id, branch.policy_kind,
                    branch.name, branch.head_version_id, branch.base_version_id,
                    branch.status, branch.description, branch.created_at,
                    branch.created_by,
                ),
            )
            conn.commit()
        return branch

    def commit_to_branch(
        self,
        branch_id: str,
        content: Dict[str, Any],
        *,
        actor: str,
        description: str = "",
    ) -> PolicyVersion:
        """Append a new version to a branch.

        Creates a ``policy_versions`` row with ``branch_id`` set and
        ``parent_version_id`` pointing at the branch's current head,
        then advances the branch's head pointer. Idempotent on
        content hash: re-committing the same content returns the
        existing head without inflating the version chain.

        Branch commits do NOT mirror into ``settings_json`` —
        production routing keeps reading main. The merge step is the
        only path that promotes branch content into runtime.
        """
        branch = self.get_branch(branch_id)
        if branch.status != "open":
            raise ValueError(
                f"cannot commit to branch {branch.name!r}: status={branch.status!r}"
            )

        new_hash = _hash_content(content)
        head = self.get_version(branch.head_version_id)
        if head.content_hash == new_hash:
            # No-op commit; return the existing head.
            return head

        new_version = self._insert(
            kind=branch.policy_kind,
            content=content,
            created_by=actor,
            description=description,
            parent_version_id=branch.head_version_id,
            is_rollback=False,
            branch_id=branch.id,
        )
        # Advance the branch's head pointer.
        self.db.initialize()
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE policy_branches SET head_version_id = %s WHERE id = %s",
                (new_version.id, branch.id),
            )
            conn.commit()
        return new_version

    def list_branches(
        self,
        *,
        kind: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 200,
    ) -> List["PolicyBranch"]:
        """Branches for this org, newest-first. Optional filters by
        kind ('approval_thresholds' / etc.) and status ('open' /
        'merged' / 'abandoned').
        """
        self.db.initialize()
        clauses = ["organization_id = %s"]
        params: List[Any] = [self.organization_id]
        if kind:
            _validate_kind(kind)
            clauses.append("policy_kind = %s")
            params.append(kind)
        if status:
            if status not in {"open", "merged", "abandoned"}:
                raise ValueError(f"invalid status filter {status!r}")
            clauses.append("status = %s")
            params.append(status)
        sql = (
            "SELECT * FROM policy_branches "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY created_at DESC LIMIT %s"
        )
        params.append(int(limit))
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        return [_row_to_branch(dict(r)) for r in rows]

    def get_branch(self, branch_id: str) -> "PolicyBranch":
        self.db.initialize()
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM policy_branches WHERE id = %s AND organization_id = %s",
                (branch_id, self.organization_id),
            )
            row = cur.fetchone()
        if not row:
            raise PolicyBranchNotFound(
                f"branch {branch_id!r} not found for org {self.organization_id!r}"
            )
        return _row_to_branch(dict(row))

    def get_branch_by_name(
        self, kind: str, name: str, *, status: str = "open",
    ) -> "PolicyBranch":
        _validate_kind(kind)
        self.db.initialize()
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM policy_branches "
                "WHERE organization_id = %s AND policy_kind = %s "
                "AND name = %s AND status = %s",
                (self.organization_id, kind, name, status),
            )
            row = cur.fetchone()
        if not row:
            raise PolicyBranchNotFound(
                f"no {status} branch named {name!r} for kind {kind!r}"
            )
        return _row_to_branch(dict(row))

    def diff_branch(self, branch_id: str) -> Dict[str, Any]:
        """Compare a branch's head content against the current main.

        Returns a dict with both contents + a coarse "changed" flag.
        Replay-driven impact analysis (how AP routing would shift)
        is the separate ``replay_against(branch.head_version_id)``
        flow; this method is the cheap content-level diff.
        """
        branch = self.get_branch(branch_id)
        head = self.get_version(branch.head_version_id)
        try:
            main_active = self.get_active(branch.policy_kind)
        except Exception:
            main_active = None
        return {
            "branch_id": branch.id,
            "branch_name": branch.name,
            "branch_head_version_id": head.id,
            "branch_head_content": head.content,
            "branch_head_hash": head.content_hash,
            "main_version_id": main_active.id if main_active else None,
            "main_version_number": main_active.version_number if main_active else None,
            "main_content": main_active.content if main_active else None,
            "main_hash": main_active.content_hash if main_active else None,
            "changed": (main_active is None) or (head.content_hash != main_active.content_hash),
        }

    def merge_branch(
        self,
        branch_id: str,
        *,
        actor: str,
        description: str = "",
    ) -> PolicyVersion:
        """Promote a branch's head content to a new version on main.

        Creates a fresh ``policy_versions`` row with ``branch_id =
        NULL`` and ``parent_version_id`` pointing at both the prior
        main head AND (via the audit trail) the branch's head — so
        history readers can trace the merge back to the source
        branch. Marks the branch as ``merged`` and records the new
        version id on the branch row.

        Mirrors content into ``settings_json`` like ``set_policy``,
        so production routing picks up the merged policy on the
        next read.

        Idempotent on content hash: if the branch's head content
        matches the current main, the branch is closed without a
        new version.
        """
        branch = self.get_branch(branch_id)
        if branch.status != "open":
            raise ValueError(
                f"cannot merge branch {branch.name!r}: status={branch.status!r}"
            )
        head = self.get_version(branch.head_version_id)

        # Pre-merge content hash check: a no-op merge (branch head ==
        # current main content) closes the branch without inflating
        # the version chain.
        try:
            main_active = self.get_active(branch.policy_kind)
        except Exception:
            main_active = None

        if main_active is not None and main_active.content_hash == head.content_hash:
            self._mark_branch_merged(branch.id, main_active.id, actor)
            return main_active

        # Standard merge: create a new version on main with the
        # branch's content. ``set_policy`` handles settings_json
        # mirroring so runtime readers pick up the change.
        merge_description = (
            description
            or f"Merged branch {branch.name!r} (v{head.version_number}) into main"
        )
        new_main_version = self.set_policy(
            kind=branch.policy_kind,
            content=head.content,
            actor=actor,
            description=merge_description,
            parent_version_id=main_active.id if main_active else None,
        )
        self._mark_branch_merged(branch.id, new_main_version.id, actor)
        return new_main_version

    def abandon_branch(
        self,
        branch_id: str,
        *,
        actor: str,
    ) -> "PolicyBranch":
        """Close a branch without merging. Versions on the branch
        stay in ``policy_versions`` (they're audit-trail evidence
        of an experiment that was tried) but the branch is no
        longer ``open``.
        """
        branch = self.get_branch(branch_id)
        if branch.status != "open":
            raise ValueError(
                f"cannot abandon branch {branch.name!r}: status={branch.status!r}"
            )
        ts = datetime.now(timezone.utc).isoformat()
        self.db.initialize()
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE policy_branches SET status = 'abandoned', "
                "abandoned_at = %s, abandoned_by = %s WHERE id = %s",
                (ts, actor, branch.id),
            )
            conn.commit()
        return self.get_branch(branch.id)

    def _mark_branch_merged(self, branch_id: str, target_version_id: str, actor: str) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        self.db.initialize()
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE policy_branches SET status = 'merged', "
                "merged_at = %s, merged_by = %s, merged_into_version_id = %s "
                "WHERE id = %s",
                (ts, actor, target_version_id, branch_id),
            )
            conn.commit()

    # ─── Replay (the novel piece) ────────────────────────────────

    def replay_against(
        self,
        version_id: str,
        *,
        since: Optional[str] = None,
        until: Optional[str] = None,
        limit: int = 500,
    ) -> ReplayResult:
        """Re-evaluate a window of AP items against a historical
        version. Returns deltas vs what those items got under the
        version active at the time.

        Today only ``approval_thresholds`` and ``gl_account_map`` are
        replayable in a meaningful sense — the others
        (confidence_gate, autonomy_policy, vendor_master_gate) drive
        intake-time gating that doesn't replay cleanly because the
        Box may already exist regardless of what the new policy
        says. Future iterations can add their replay strategies.
        """
        target = self.get_version(version_id)
        kind = target.policy_kind
        ap_items = self._fetch_ap_items_for_replay(since=since, until=until, limit=limit)
        deltas: List[ReplayDelta] = []
        summary: Dict[str, int] = {"would_change": 0, "no_change": 0, "skipped": 0}

        if kind == "approval_thresholds":
            deltas, summary = _replay_approval_thresholds(target.content, ap_items)
        elif kind == "gl_account_map":
            deltas, summary = _replay_gl_account_map(target.content, ap_items)
        else:
            summary["skipped"] = len(ap_items)
            logger.info(
                "policy_service: replay for kind=%s is not yet implemented; %d items skipped",
                kind, len(ap_items),
            )

        return ReplayResult(
            target_version_id=target.id,
            target_version_number=target.version_number,
            target_kind=target.policy_kind,
            items_evaluated=len(ap_items),
            deltas=deltas,
            summary=summary,
        )

    # ─── Internals ────────────────────────────────────────────────

    def _fetch_latest(self, kind: str) -> Optional[PolicyVersion]:
        """Latest version on **main** for org+kind.

        Sprint 2 branchable-policy: filters ``branch_id IS NULL`` so
        in-progress branch experiments don't accidentally become the
        active production policy. Branches advance via
        ``commit_to_branch`` (which sets ``branch_id``); merges write
        a fresh row with ``branch_id = NULL`` so the merged content
        flows through ``get_active`` like any normal ``set_policy``.
        """
        self.db.initialize()
        sql = (
            "SELECT * FROM policy_versions "
            "WHERE organization_id = %s AND policy_kind = %s "
            "AND branch_id IS NULL "
            "ORDER BY version_number DESC LIMIT 1"
        )
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (self.organization_id, kind))
            row = cur.fetchone()
        return _row_to_version(dict(row)) if row else None

    def _insert(
        self,
        *,
        kind: str,
        content: Dict[str, Any],
        created_by: str,
        description: str,
        parent_version_id: Optional[str],
        is_rollback: bool,
        branch_id: Optional[str] = None,
    ) -> PolicyVersion:
        """Append a new ``policy_versions`` row.

        ``version_number`` is monotonic per (org, kind) regardless of
        branch — every commit (main or branch) gets a fresh number.
        Keeps audit replay deterministic (one number = one row).
        ``branch_id=None`` means main; ``branch_id=<id>`` means the
        version belongs to a branch and is filtered out of
        ``get_active`` / ``_fetch_latest``.
        """
        self.db.initialize()
        latest_number = 0
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COALESCE(MAX(version_number), 0) FROM policy_versions "
                "WHERE organization_id = %s AND policy_kind = %s",
                (self.organization_id, kind),
            )
            row = cur.fetchone()
            if row:
                # Postgres returns dicts via dict_row factory; the
                # COALESCE column is keyed positionally as 'coalesce'.
                latest_number = int(list(dict(row).values())[0] or 0)

        version = PolicyVersion(
            id=f"PV-{uuid.uuid4().hex}",
            organization_id=self.organization_id,
            policy_kind=kind,
            version_number=latest_number + 1,
            content=content,
            content_hash=_hash_content(content),
            created_at=datetime.now(timezone.utc).isoformat(),
            created_by=created_by,
            description=description,
            parent_version_id=parent_version_id,
            is_rollback=is_rollback,
            branch_id=branch_id,
        )
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO policy_versions
                  (id, organization_id, policy_kind, version_number,
                   content_json, content_hash, created_at, created_by,
                   description, parent_version_id, is_rollback, branch_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    version.id, version.organization_id, version.policy_kind,
                    version.version_number, json.dumps(version.content),
                    version.content_hash, version.created_at, version.created_by,
                    version.description, version.parent_version_id,
                    1 if version.is_rollback else 0, branch_id,
                ),
            )
            conn.commit()
        return version

    def _slice_from_settings_json(self, kind: str) -> Dict[str, Any]:
        """Pull this org's current settings_json slice for a kind."""
        if not hasattr(self.db, "get_organization"):
            return _default_content(kind)
        try:
            org = self.db.get_organization(self.organization_id)
        except Exception:
            return _default_content(kind)
        if not org:
            return _default_content(kind)
        settings = org.get("settings_json") or org.get("settings")
        if isinstance(settings, str):
            try:
                settings = json.loads(settings)
            except Exception:
                settings = {}
        if not isinstance(settings, dict):
            return _default_content(kind)
        return _slice_settings_for_kind(kind, settings)

    def _mirror_to_settings_json(self, kind: str, content: Dict[str, Any]) -> None:
        """Write the new policy content back into the org's
        settings_json so existing readers (which haven't migrated to
        PolicyService yet) see the change."""
        if not hasattr(self.db, "get_organization") or not hasattr(self.db, "update_organization"):
            return
        try:
            org = self.db.get_organization(self.organization_id)
        except Exception:
            return
        if not org:
            return
        settings = org.get("settings_json") or org.get("settings") or {}
        if isinstance(settings, str):
            try:
                settings = json.loads(settings)
            except Exception:
                settings = {}
        if not isinstance(settings, dict):
            settings = {}
        _merge_kind_into_settings(kind, content, settings)
        try:
            self.db.update_organization(self.organization_id, settings=settings)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "policy_service: settings_json mirror failed for org=%s kind=%s — %s",
                self.organization_id, kind, exc,
            )

    def _fetch_ap_items_for_replay(
        self, *, since: Optional[str], until: Optional[str], limit: int,
    ) -> List[Dict[str, Any]]:
        self.db.initialize()
        clauses = ["organization_id = %s"]
        params: List[Any] = [self.organization_id]
        if since:
            clauses.append("created_at >= %s")
            params.append(since)
        if until:
            clauses.append("created_at <= %s")
            params.append(until)
        sql = (
            "SELECT * FROM ap_items WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at DESC LIMIT %s"
        )
        params.append(int(limit))
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        return [dict(r) for r in rows]


# ─── Module helpers ────────────────────────────────────────────────


def _validate_kind(kind: str) -> None:
    if kind not in POLICY_KINDS:
        raise PolicyKindError(
            f"unknown policy kind {kind!r}; valid: {sorted(POLICY_KINDS)}"
        )


def _hash_content(content: Dict[str, Any]) -> str:
    """Stable hash so idempotent re-saves are detected as no-ops.

    JSON-serialise with sorted keys + no whitespace so logical
    equality is hash equality.
    """
    payload = json.dumps(content or {}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _default_content(kind: str) -> Dict[str, Any]:
    """When an org has no setting yet, what does v1 look like?"""
    if kind == "approval_thresholds":
        return {"thresholds": []}
    if kind == "gl_account_map":
        return {"map": {}}
    if kind == "confidence_gate":
        return {"critical_field_confidence_threshold": 0.95}
    if kind == "autonomy_policy":
        return {"autonomy_actions": {}}
    if kind == "vendor_master_gate":
        return {"vendor_master_gate": False}
    if kind == "match_tolerances":
        return {
            "ap_three_way": {
                "price_tolerance_percent": 2.0,
                "quantity_tolerance_percent": 5.0,
                "amount_tolerance": 10.0,
            },
            "bank_reconciliation": {
                "amount_tolerance": 0.01,
                "date_window_days": 3,
            },
        }
    if kind == "match_mode":
        # Default: 2-way fallback. Most permissive sensible default —
        # runs 3-way when GRN is present, falls back to 2-way (PO
        # only) when GRN is missing, and degrades to approval-policy
        # routing when no PO at all. Existing orgs that haven't
        # opted in get this on first lazy-migration read.
        return {"mode": "two_way_fallback"}
    if kind == "annotation_targets":
        # All targets disabled by default — customers opt in per
        # surface. Activating a target is a policy edit (creates a
        # new version row, mirrors back to settings_json).
        return {
            "gmail_label": {"enabled": False},
            "netsuite_custom_field": {
                "enabled": False,
                "field_id": "custbody_clearledgr_state",
            },
            "sap_z_field": {
                "enabled": False,
                "field_id": "YY1_CLEARLEDGR_STATE",
            },
            "customer_webhook": {
                "enabled": False,
                "filter_event_types": [],
                "include_metadata": True,
            },
            "slack_card_update": {
                "enabled": False,
                "show_actor_attribution": True,
            },
        }
    # ─── Sprint 5 Phase A defaults ──────────────────────────────
    if kind == "sanctions_list":
        # Empty list by default. Each ``entries`` row carries the
        # sanctioned name + reason + source (OFAC / EU / UN / custom)
        # so an audit explaining why a screening fired can render
        # back to the originating source.
        return {"entries": [], "default_action": "block"}
    if kind == "erp_field_mappings":
        # One namespace per supported ERP. Keys inside each
        # namespace are Solden field names → ERP custom field ids
        # (custbody_*, YY1_*, etc.). Defaults are intentionally
        # empty so an org that hasn't configured mappings produces
        # no spurious ERP custom-field writes.
        return {
            "netsuite": {},
            "sap": {},
            "quickbooks": {},
            "xero": {},
        }
    if kind == "approval_routing":
        # Where approval requests land. Empty defaults mean "use
        # the existing tenant integrations" — Slack channel
        # resolves to the org's default channel, Teams to the
        # default team. Operators override here when they want a
        # specific channel for AP approvals (vs e.g. mention
        # threads).
        return {
            "slack_channel": "",
            "teams_team_id": "",
            "email_distribution": [],
            "fallback_channel": "slack",
        }
    if kind == "org_settings":
        # Org-level toggles that don't fit any other kind. Kept
        # narrow on purpose — adding a new toggle here is a small
        # decision; adding a whole new kind is a bigger one.
        return {
            "timezone": "UTC",
            "fiscal_year_start": "01-01",
            "default_currency": "USD",
            "default_payment_terms_days": 30,
        }
    return {}


def _slice_settings_for_kind(kind: str, settings: Dict[str, Any]) -> Dict[str, Any]:
    """Pull the right slice of settings_json for a kind."""
    if kind == "approval_thresholds":
        return {"thresholds": list(settings.get("approval_thresholds") or [])}
    if kind == "gl_account_map":
        return {"map": dict(settings.get("gl_account_map") or {})}
    if kind == "confidence_gate":
        out: Dict[str, Any] = {}
        for key in ("critical_field_confidence_threshold", "confidence_gate_threshold"):
            if key in settings:
                out[key] = settings[key]
        return out or {"critical_field_confidence_threshold": 0.95}
    if kind == "autonomy_policy":
        return {"autonomy_actions": dict(settings.get("autonomy_actions") or {})}
    if kind == "vendor_master_gate":
        return {"vendor_master_gate": bool(settings.get("vendor_master_gate") or False)}
    if kind == "match_tolerances":
        existing = settings.get("match_tolerances") or {}
        if isinstance(existing, dict) and existing:
            return existing
        return _default_content("match_tolerances")
    if kind == "match_mode":
        raw = settings.get("match_mode")
        if isinstance(raw, dict) and raw.get("mode") in VALID_MATCH_MODES:
            return {"mode": raw["mode"]}
        if isinstance(raw, str) and raw in VALID_MATCH_MODES:
            return {"mode": raw}
        return _default_content("match_mode")
    if kind == "annotation_targets":
        existing = settings.get("annotation_targets") or {}
        if isinstance(existing, dict) and existing:
            return existing
        return _default_content("annotation_targets")
    # ─── Sprint 5 Phase A slices ────────────────────────────────
    if kind == "sanctions_list":
        existing = settings.get("sanctions_list") or {}
        if isinstance(existing, dict) and "entries" in existing:
            return {
                "entries": list(existing.get("entries") or []),
                "default_action": str(existing.get("default_action") or "block"),
            }
        return _default_content("sanctions_list")
    if kind == "erp_field_mappings":
        existing = settings.get("erp_field_mappings") or {}
        if isinstance(existing, dict) and existing:
            # Make sure every supported ERP namespace is present
            # (operators sometimes ship with only one configured;
            # normalize on read so the version content has a stable
            # shape for diffing / replaying).
            normalized = dict(_default_content("erp_field_mappings"))
            for key, value in existing.items():
                if isinstance(value, dict):
                    normalized[key] = value
            return normalized
        return _default_content("erp_field_mappings")
    if kind == "approval_routing":
        existing = settings.get("approval_routing") or {}
        if isinstance(existing, dict) and existing:
            base = dict(_default_content("approval_routing"))
            base.update({k: v for k, v in existing.items()
                          if k in base})
            return base
        return _default_content("approval_routing")
    if kind == "org_settings":
        existing = settings.get("org_settings") or {}
        if isinstance(existing, dict) and existing:
            base = dict(_default_content("org_settings"))
            base.update({k: v for k, v in existing.items()
                          if k in base})
            return base
        return _default_content("org_settings")
    return {}


def _merge_kind_into_settings(
    kind: str, content: Dict[str, Any], settings: Dict[str, Any],
) -> None:
    """In-place merge of a policy slice back into settings_json."""
    if kind == "approval_thresholds":
        settings["approval_thresholds"] = list(content.get("thresholds") or [])
    elif kind == "gl_account_map":
        settings["gl_account_map"] = dict(content.get("map") or {})
    elif kind == "confidence_gate":
        for key in ("critical_field_confidence_threshold", "confidence_gate_threshold"):
            if key in content:
                settings[key] = content[key]
    elif kind == "autonomy_policy":
        settings["autonomy_actions"] = dict(content.get("autonomy_actions") or {})
    elif kind == "vendor_master_gate":
        settings["vendor_master_gate"] = bool(content.get("vendor_master_gate") or False)
    elif kind == "match_tolerances":
        settings["match_tolerances"] = dict(content or {})
    elif kind == "match_mode":
        mode = (content or {}).get("mode") if isinstance(content, dict) else None
        if mode in VALID_MATCH_MODES:
            settings["match_mode"] = {"mode": mode}
    elif kind == "annotation_targets":
        settings["annotation_targets"] = dict(content or {})
    # ─── Sprint 5 Phase A mirror ────────────────────────────────
    elif kind == "sanctions_list":
        # Normalize to the canonical shape so settings_json stays
        # stable regardless of how the operator authored the JSON.
        settings["sanctions_list"] = {
            "entries": list((content or {}).get("entries") or []),
            "default_action": str(
                (content or {}).get("default_action") or "block"
            ),
        }
    elif kind == "erp_field_mappings":
        settings["erp_field_mappings"] = dict(content or {})
    elif kind == "approval_routing":
        settings["approval_routing"] = dict(content or {})
    elif kind == "org_settings":
        settings["org_settings"] = dict(content or {})


def _row_to_version(row: Dict[str, Any]) -> PolicyVersion:
    raw_content = row.get("content_json") or "{}"
    if isinstance(raw_content, dict):
        content = raw_content
    else:
        try:
            content = json.loads(raw_content)
        except Exception:
            content = {}
    return PolicyVersion(
        id=str(row.get("id") or ""),
        organization_id=str(row.get("organization_id") or ""),
        policy_kind=str(row.get("policy_kind") or ""),
        version_number=int(row.get("version_number") or 0),
        content=content if isinstance(content, dict) else {},
        content_hash=str(row.get("content_hash") or ""),
        created_at=str(row.get("created_at") or ""),
        created_by=str(row.get("created_by") or ""),
        description=str(row.get("description") or ""),
        parent_version_id=str(row.get("parent_version_id")) if row.get("parent_version_id") else None,
        is_rollback=bool(row.get("is_rollback") or 0),
        branch_id=str(row.get("branch_id")) if row.get("branch_id") else None,
    )


def _row_to_branch(row: Dict[str, Any]) -> "PolicyBranch":
    return PolicyBranch(
        id=str(row.get("id") or ""),
        organization_id=str(row.get("organization_id") or ""),
        policy_kind=str(row.get("policy_kind") or ""),
        name=str(row.get("name") or ""),
        head_version_id=str(row.get("head_version_id") or ""),
        base_version_id=str(row.get("base_version_id") or ""),
        status=str(row.get("status") or "open"),
        description=str(row.get("description") or ""),
        created_at=str(row.get("created_at") or ""),
        created_by=str(row.get("created_by") or ""),
        merged_at=str(row.get("merged_at")) if row.get("merged_at") else None,
        merged_into_version_id=str(row.get("merged_into_version_id")) if row.get("merged_into_version_id") else None,
        merged_by=str(row.get("merged_by")) if row.get("merged_by") else None,
        abandoned_at=str(row.get("abandoned_at")) if row.get("abandoned_at") else None,
        abandoned_by=str(row.get("abandoned_by")) if row.get("abandoned_by") else None,
    )


# ─── Replay strategies ─────────────────────────────────────────────


def _replay_approval_thresholds(
    content: Dict[str, Any], ap_items: List[Dict[str, Any]],
) -> tuple[List[ReplayDelta], Dict[str, int]]:
    """For each AP item, recompute which threshold band would have
    matched under the target version vs what's recorded today.

    Today's recorded routing isn't stored on AP items directly — we
    infer it from ``approval_policy_version`` (the version active at
    intake) by reading that version's content. This is a simplified
    replay: we compute the band an item *would* hit under the
    target version and compare its stored channel against ours.
    """
    target_thresholds = list(content.get("thresholds") or [])
    deltas: List[ReplayDelta] = []
    summary = {"would_change": 0, "no_change": 0, "skipped": 0}
    for item in ap_items:
        amount = _safe_float(item.get("amount"))
        if amount is None:
            summary["skipped"] += 1
            continue
        replayed_band = _match_threshold_band(target_thresholds, amount, item)
        # Today's actual band: read from the AP item's recorded
        # routing metadata, which lives in ap_items.metadata under
        # "approval_target.threshold_label" (not always populated
        # — fall back to the threshold the item's stored channel
        # implies via parsing the original policy).
        current_band = _extract_current_band(item)
        if current_band == replayed_band:
            summary["no_change"] += 1
            continue
        summary["would_change"] += 1
        deltas.append(ReplayDelta(
            ap_item_id=str(item.get("id") or ""),
            field="approval_threshold_band",
            current_value=current_band,
            replayed_value=replayed_band,
        ))
    return deltas, summary


def _replay_gl_account_map(
    content: Dict[str, Any], ap_items: List[Dict[str, Any]],
) -> tuple[List[ReplayDelta], Dict[str, int]]:
    """For each AP item, see whether the GL account it was posted
    under (stored on the row's metadata via the post-result) would
    have differed under the target map. Only meaningful for items
    actually posted to ERP."""
    target_map = dict(content.get("map") or {})
    deltas: List[ReplayDelta] = []
    summary = {"would_change": 0, "no_change": 0, "skipped": 0}
    for item in ap_items:
        if str(item.get("state") or "").lower() not in {"posted_to_erp", "closed"}:
            summary["skipped"] += 1
            continue
        # Today's GL: read off metadata.posting_metadata.gl_account
        meta = item.get("metadata") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        current_gl = ((meta or {}).get("posting_metadata") or {}).get("gl_account")
        if not current_gl:
            summary["skipped"] += 1
            continue
        # The target map keys are semantic categories; for replay we
        # compare what "expenses" would resolve to under the target
        # map vs what was used. Coarse, but enough to flag items
        # that would have moved.
        target_default = target_map.get("expenses")
        if target_default is None or target_default == current_gl:
            summary["no_change"] += 1
            continue
        summary["would_change"] += 1
        deltas.append(ReplayDelta(
            ap_item_id=str(item.get("id") or ""),
            field="gl_account",
            current_value=current_gl,
            replayed_value=target_default,
        ))
    return deltas, summary


def _match_threshold_band(thresholds: List[Dict[str, Any]], amount: float, item: Dict[str, Any]) -> Optional[str]:
    """Return the threshold's label/name (or stringified band) that
    would match. Mirrors :meth:`_resolve_approval_target` semantics."""
    vendor_lower = str(item.get("vendor_name") or "").strip().lower()
    for rule in thresholds:
        if not isinstance(rule, dict):
            continue
        try:
            min_amt = float(rule.get("min_amount") or 0)
        except (TypeError, ValueError):
            min_amt = 0.0
        max_amt_raw = rule.get("max_amount")
        try:
            max_amt = float(max_amt_raw) if max_amt_raw not in (None, "") else None
        except (TypeError, ValueError):
            max_amt = None
        if amount < min_amt:
            continue
        if max_amt is not None and amount >= max_amt:
            continue
        rule_vendors = [str(v).strip().lower() for v in (rule.get("vendors") or []) if v]
        if rule_vendors and vendor_lower and vendor_lower not in rule_vendors:
            continue
        label = rule.get("label") or rule.get("name") or rule.get("channel")
        if label:
            return str(label)
        return f"{min_amt}-{max_amt}" if max_amt is not None else f">={min_amt}"
    return None


def _extract_current_band(item: Dict[str, Any]) -> Optional[str]:
    meta = item.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    target = (meta or {}).get("approval_target") or {}
    return target.get("threshold_label") or target.get("channel")


def _safe_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
