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
        self.db.initialize()
        sql = (
            "SELECT * FROM policy_versions "
            "WHERE organization_id = %s AND policy_kind = %s "
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
    ) -> PolicyVersion:
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
        )
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO policy_versions
                  (id, organization_id, policy_kind, version_number,
                   content_json, content_hash, created_at, created_by,
                   description, parent_version_id, is_rollback)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    version.id, version.organization_id, version.policy_kind,
                    version.version_number, json.dumps(version.content),
                    version.content_hash, version.created_at, version.created_by,
                    version.description, version.parent_version_id,
                    1 if version.is_rollback else 0,
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
