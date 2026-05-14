"""Branchable row-set surfaces — vendor master, GL chart, custom
roles, entity restrictions (Sprint 5 Phase B).

The Sprint 2 branch model treats a branch as a versioned JSON blob:
one row per version in ``policy_versions``. That works for
single-config surfaces (approval thresholds, sanctions lists, etc.)
but not for **row-set** surfaces where the "config" is a table —
vendor_profiles, gl_corrections / chart of accounts, custom_roles,
user_entity_roles. A branch of vendor master isn't a new version
of one blob; it's a set of pending operations on individual rows.

Architecture:

* ``data_branches`` (migration v82) tracks branch lifecycle —
  open / merged / abandoned, creator + merger metadata,
  ``base_snapshot_json`` carrying content hashes of the rows the
  branch is shadowing / tombstoning at branch-creation time
  (for merge-time conflict detection).
* ``vendor_profiles`` (and the other row-set tables) gained
  three columns in v82: ``branch_id`` (NULL = main, else this
  branch), ``branch_op`` ('insert' | 'modify' | 'delete'),
  ``branch_base_id`` (for modify / delete overlays, the id of the
  main row being shadowed).
* Reads on main filter ``branch_id IS NULL``. Branch reads union
  main rows with the branch's overlays (overlays shadow main rows
  by primary key — modify replaces the main row's fields,
  tombstone removes the row from the branch view).
* ``merge`` applies overlay operations to main:
    - 'insert' → flip the overlay row's branch_id to NULL
    - 'modify' → update the main row's fields with the overlay's,
                 then delete the overlay
    - 'delete' → delete the main row + the tombstone overlay
* ``abandon`` deletes every overlay row for the branch, leaves
  main untouched.
* Conflict detection: at branch-creation time, snapshot the
  content_hash of every main row that exists. At merge time,
  re-compute hashes for any main row a branch overlay touches; if
  the hash diverged from the snapshot, another writer modified
  the row. Merge fails with ``DataBranchConflict`` listing
  affected rows so the operator can rebase + reapply.

Tenancy: every overlay row carries the branch's ``organization_id``
(via the underlying table's column). Reads filter on org first.
The CHECK constraint on ``data_branches`` mirrors the M22 tenancy
walls.

This module is the **generic abstraction**. The vendor_profiles
adapter is registered at import time as proof-of-pattern;
gl_corrections / custom_roles / user_entity_roles adapters follow
the same shape and ship in follow-up commits.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple


logger = logging.getLogger(__name__)


class DataBranchNotFound(LookupError):
    """No data_branches row matches (id, org)."""


class DataBranchConflict(RuntimeError):
    """Merge-time conflict: one or more main rows the branch wants
    to modify / delete have diverged since branch creation. Carries
    a list of affected rows so the operator can decide which side
    wins.
    """

    def __init__(
        self,
        message: str,
        *,
        branch_id: str,
        conflicts: Sequence[Dict[str, Any]],
    ) -> None:
        super().__init__(message)
        self.branch_id = branch_id
        self.conflicts = list(conflicts)


@dataclasses.dataclass(frozen=True)
class DataBranch:
    """Lifecycle metadata for a row-set branch (matches the
    ``data_branches`` table shape from migration v82)."""

    id: str
    organization_id: str
    table_name: str
    name: str
    description: str
    status: str  # 'open' | 'merged' | 'abandoned'
    base_snapshot_json: str
    created_at: str
    created_by: str
    merged_at: Optional[str] = None
    merged_by: Optional[str] = None
    abandoned_at: Optional[str] = None
    abandoned_by: Optional[str] = None

    @property
    def base_snapshot(self) -> Dict[str, str]:
        """Decoded snapshot dict: ``{row_id: content_hash}`` of every
        main row the branch is potentially modifying / deleting at
        branch-creation time."""
        if not self.base_snapshot_json:
            return {}
        try:
            decoded = json.loads(self.base_snapshot_json)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}


@dataclasses.dataclass(frozen=True)
class TableAdapter:
    """Per-table configuration for a row-set surface."""
    table_name: str
    primary_key: Tuple[str, ...]
    hashable_columns: Tuple[str, ...]
    insertable_columns: Tuple[str, ...]
    identity_columns: Tuple[str, ...] = ("organization_id",)


VENDOR_PROFILES_ADAPTER = TableAdapter(
    table_name="vendor_profiles",
    primary_key=("id",),
    hashable_columns=(
        "vendor_name", "vendor_aliases", "sender_domains",
        "typical_gl_code", "requires_po", "contract_amount",
        "payment_terms", "always_approved", "approval_override_rate",
        "registration_number", "vat_number", "registered_address",
        "director_names", "primary_contact_email",
        "remittance_email", "remittance_opt_out",
    ),
    insertable_columns=(
        "id", "organization_id", "vendor_name", "vendor_aliases",
        "sender_domains", "typical_gl_code", "requires_po",
        "contract_amount", "payment_terms", "always_approved",
        "approval_override_rate", "registration_number", "vat_number",
        "registered_address", "director_names", "primary_contact_email",
        "remittance_email", "remittance_opt_out",
    ),
    identity_columns=("id", "organization_id"),
)


_REGISTERED_ADAPTERS: Dict[str, TableAdapter] = {
    VENDOR_PROFILES_ADAPTER.table_name: VENDOR_PROFILES_ADAPTER,
}


def register_adapter(adapter: TableAdapter) -> None:
    _REGISTERED_ADAPTERS[adapter.table_name] = adapter


def get_adapter(table_name: str) -> TableAdapter:
    if table_name not in _REGISTERED_ADAPTERS:
        raise ValueError(
            f"no adapter registered for table {table_name!r}; "
            f"register one via ``register_adapter`` first"
        )
    return _REGISTERED_ADAPTERS[table_name]


def hash_row(row: Dict[str, Any], adapter: TableAdapter) -> str:
    """Deterministic content hash over the adapter's hashable_columns."""
    payload = {
        col: _normalize_value(row.get(col))
        for col in adapter.hashable_columns
    }
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_value(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, list):
        try:
            return sorted(value)
        except TypeError:
            return sorted(value, key=lambda v: json.dumps(v, default=str, sort_keys=True))
    return value


class RowSetBranchService:
    """Generic branch operations over a row-set surface."""

    def __init__(self, organization_id: str, *, db: Any = None) -> None:
        self.organization_id = organization_id
        self.db = db
        if self.db is None:
            from clearledgr.core.database import get_db
            self.db = get_db()

    # ─── Branch lifecycle ──────────────────────────────────────────

    def create_branch(
        self,
        table_name: str,
        *,
        name: str,
        actor: str,
        description: str = "",
    ) -> DataBranch:
        adapter = get_adapter(table_name)
        normalized_name = (name or "").strip()
        if not normalized_name:
            raise ValueError("branch name is required")

        main_rows = self._fetch_main_rows(adapter)
        base_snapshot = {
            self._row_key(row, adapter): hash_row(row, adapter)
            for row in main_rows
        }

        branch_id = f"DB-{uuid.uuid4().hex}"
        now = datetime.now(timezone.utc).isoformat()

        self.db.initialize()
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO data_branches
                  (id, organization_id, table_name, name, description, status,
                   base_snapshot_json, created_at, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    branch_id, self.organization_id, table_name,
                    normalized_name, description, "open",
                    json.dumps(base_snapshot, sort_keys=True),
                    now, actor,
                ),
            )
            conn.commit()

        return self.get_branch(branch_id)

    def get_branch(self, branch_id: str) -> DataBranch:
        self.db.initialize()
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM data_branches "
                "WHERE id = %s AND organization_id = %s",
                (branch_id, self.organization_id),
            )
            row = cur.fetchone()
        if not row:
            raise DataBranchNotFound(
                f"data branch {branch_id!r} not found for org "
                f"{self.organization_id!r}"
            )
        return _row_to_branch(dict(row))

    def list_branches(
        self,
        *,
        table_name: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 200,
    ) -> List[DataBranch]:
        clauses = ["organization_id = %s"]
        params: List[Any] = [self.organization_id]
        if table_name:
            clauses.append("table_name = %s")
            params.append(table_name)
        if status:
            if status not in {"open", "merged", "abandoned"}:
                raise ValueError(f"invalid status filter {status!r}")
            clauses.append("status = %s")
            params.append(status)
        sql = (
            "SELECT * FROM data_branches "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY created_at DESC LIMIT %s"
        )
        params.append(int(limit))
        self.db.initialize()
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        return [_row_to_branch(dict(r)) for r in rows]

    # ─── Overlay operations ────────────────────────────────────────

    def commit_insert(
        self,
        branch_id: str,
        row: Dict[str, Any],
    ) -> Dict[str, Any]:
        branch = self._require_open_branch(branch_id)
        adapter = get_adapter(branch.table_name)
        for col in adapter.identity_columns:
            if not row.get(col):
                raise ValueError(
                    f"insert overlay requires identity column {col!r}"
                )
        if str(row.get("organization_id")) != self.organization_id:
            raise ValueError(
                "overlay row organization_id must match the service org"
            )

        overlay = dict(row)
        overlay["branch_id"] = branch.id
        overlay["branch_op"] = "insert"
        overlay["branch_base_id"] = None
        self._insert_row(adapter, overlay)
        return overlay

    def commit_modify(
        self,
        branch_id: str,
        base_row_id: str,
        fields: Dict[str, Any],
    ) -> Dict[str, Any]:
        branch = self._require_open_branch(branch_id)
        adapter = get_adapter(branch.table_name)
        main = self._fetch_main_row_by_id(adapter, base_row_id)
        if main is None:
            raise LookupError(
                f"main row {base_row_id!r} not found in "
                f"{adapter.table_name} for org {self.organization_id!r}"
            )
        for key in fields:
            if key not in adapter.insertable_columns:
                raise ValueError(
                    f"column {key!r} not in {adapter.table_name} "
                    f"insertable_columns"
                )

        overlay = dict(main)
        overlay.update(fields)
        if adapter.primary_key == ("id",):
            overlay["id"] = f"OVL-{uuid.uuid4().hex}"
        overlay["branch_id"] = branch.id
        overlay["branch_op"] = "modify"
        overlay["branch_base_id"] = base_row_id
        self._insert_row(adapter, overlay)
        return overlay

    def commit_delete(
        self,
        branch_id: str,
        base_row_id: str,
    ) -> Dict[str, Any]:
        branch = self._require_open_branch(branch_id)
        adapter = get_adapter(branch.table_name)
        main = self._fetch_main_row_by_id(adapter, base_row_id)
        if main is None:
            raise LookupError(
                f"main row {base_row_id!r} not found in "
                f"{adapter.table_name} for org {self.organization_id!r}"
            )
        overlay = dict(main)
        if adapter.primary_key == ("id",):
            overlay["id"] = f"OVL-{uuid.uuid4().hex}"
        overlay["branch_id"] = branch.id
        overlay["branch_op"] = "delete"
        overlay["branch_base_id"] = base_row_id
        self._insert_row(adapter, overlay)
        return overlay

    def list_overlays(self, branch_id: str) -> List[Dict[str, Any]]:
        branch = self.get_branch(branch_id)
        adapter = get_adapter(branch.table_name)
        self.db.initialize()
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f'SELECT * FROM "{adapter.table_name}" '
                f"WHERE organization_id = %s AND branch_id = %s "
                f"ORDER BY branch_op, branch_base_id",
                (self.organization_id, branch.id),
            )
            rows = cur.fetchall()
        return [dict(r) for r in rows]

    def materialize_branch_view(self, branch_id: str) -> List[Dict[str, Any]]:
        """Render the row set as the branch sees it."""
        branch = self.get_branch(branch_id)
        adapter = get_adapter(branch.table_name)
        main_rows = self._fetch_main_rows(adapter)
        overlays = self.list_overlays(branch.id)

        modifies: Dict[str, Dict[str, Any]] = {}
        deletes: set = set()
        inserts: List[Dict[str, Any]] = []
        for ov in overlays:
            op = str(ov.get("branch_op") or "")
            base_id = ov.get("branch_base_id")
            if op == "modify" and base_id:
                modifies[base_id] = ov
            elif op == "delete" and base_id:
                deletes.add(base_id)
            elif op == "insert":
                inserts.append(ov)

        view: List[Dict[str, Any]] = []
        for main in main_rows:
            main_id = self._row_key(main, adapter)
            if main_id in deletes:
                continue
            if main_id in modifies:
                shadow = dict(modifies[main_id])
                shadow.pop("branch_id", None)
                shadow.pop("branch_op", None)
                shadow.pop("branch_base_id", None)
                if adapter.primary_key == ("id",):
                    shadow["id"] = main["id"]
                view.append(shadow)
            else:
                view.append(dict(main))

        for ins in inserts:
            row = dict(ins)
            row.pop("branch_id", None)
            row.pop("branch_op", None)
            row.pop("branch_base_id", None)
            view.append(row)

        return view

    # ─── Merge / abandon ───────────────────────────────────────────

    def merge_branch(
        self,
        branch_id: str,
        *,
        actor: str,
    ) -> Dict[str, Any]:
        branch = self._require_open_branch(branch_id)
        adapter = get_adapter(branch.table_name)
        overlays = self.list_overlays(branch.id)

        snapshot = branch.base_snapshot
        conflicts: List[Dict[str, Any]] = []
        for ov in overlays:
            op = str(ov.get("branch_op") or "")
            base_id = ov.get("branch_base_id")
            if op not in ("modify", "delete") or not base_id:
                continue
            current = self._fetch_main_row_by_id(adapter, base_id)
            if current is None:
                conflicts.append({
                    "row_id": base_id, "op": op,
                    "reason": "main_row_deleted",
                })
                continue
            current_hash = hash_row(current, adapter)
            base_hash = snapshot.get(base_id)
            if base_hash and current_hash != base_hash:
                conflicts.append({
                    "row_id": base_id, "op": op,
                    "reason": "main_row_changed",
                    "base_hash": base_hash,
                    "current_hash": current_hash,
                })
        if conflicts:
            raise DataBranchConflict(
                f"branch {branch.name!r} cannot merge: "
                f"{len(conflicts)} row conflict(s) on main",
                branch_id=branch.id,
                conflicts=conflicts,
            )

        applied = {"insert": 0, "modify": 0, "delete": 0}
        self.db.initialize()
        with self.db.connect() as conn:
            cur = conn.cursor()
            for ov in overlays:
                op = str(ov.get("branch_op") or "")
                if op == "insert":
                    self._apply_insert(cur, ov, adapter)
                    applied["insert"] += 1
                elif op == "modify":
                    self._apply_modify(cur, ov, adapter)
                    applied["modify"] += 1
                elif op == "delete":
                    self._apply_delete(cur, ov, adapter)
                    applied["delete"] += 1

            now = datetime.now(timezone.utc).isoformat()
            cur.execute(
                "UPDATE data_branches "
                "SET status = 'merged', merged_at = %s, merged_by = %s "
                "WHERE id = %s",
                (now, actor, branch.id),
            )
            conn.commit()

        return {
            "branch_id": branch.id,
            "merged_at": now,
            "applied": applied,
        }

    def abandon_branch(
        self,
        branch_id: str,
        *,
        actor: str,
    ) -> Dict[str, Any]:
        branch = self._require_open_branch(branch_id)
        adapter = get_adapter(branch.table_name)
        now = datetime.now(timezone.utc).isoformat()

        self.db.initialize()
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f'DELETE FROM "{adapter.table_name}" '
                f"WHERE organization_id = %s AND branch_id = %s",
                (self.organization_id, branch.id),
            )
            cur.execute(
                "UPDATE data_branches "
                "SET status = 'abandoned', abandoned_at = %s, "
                "abandoned_by = %s WHERE id = %s",
                (now, actor, branch.id),
            )
            conn.commit()
        return {
            "branch_id": branch.id,
            "abandoned_at": now,
        }

    # ─── Internal: apply ops to main ──────────────────────────────

    def _apply_insert(self, cur, overlay, adapter) -> None:
        cur.execute(
            f'UPDATE "{adapter.table_name}" '
            f"SET branch_id = NULL, branch_op = NULL, branch_base_id = NULL "
            f"WHERE organization_id = %s AND branch_id = %s "
            f"AND id = %s",
            (self.organization_id, overlay.get("branch_id"), overlay.get("id")),
        )

    def _apply_modify(self, cur, overlay, adapter) -> None:
        main = self._fetch_main_row_by_id(adapter, overlay["branch_base_id"])
        if main is None:
            return
        diff: Dict[str, Any] = {}
        for col in adapter.insertable_columns:
            if col in adapter.identity_columns:
                continue
            new = overlay.get(col)
            old = main.get(col)
            if new != old:
                diff[col] = new
        if diff:
            set_clause = ", ".join(f'"{c}" = %s' for c in diff.keys())
            params = list(diff.values()) + [
                self.organization_id, main["id"],
            ]
            cur.execute(
                f'UPDATE "{adapter.table_name}" SET {set_clause} '
                f'WHERE organization_id = %s AND id = %s '
                f'AND branch_id IS NULL',
                tuple(params),
            )
        cur.execute(
            f'DELETE FROM "{adapter.table_name}" '
            f"WHERE organization_id = %s AND id = %s "
            f"AND branch_id = %s",
            (self.organization_id, overlay["id"], overlay["branch_id"]),
        )

    def _apply_delete(self, cur, overlay, adapter) -> None:
        cur.execute(
            f'DELETE FROM "{adapter.table_name}" '
            f"WHERE organization_id = %s AND id = %s "
            f"AND branch_id IS NULL",
            (self.organization_id, overlay["branch_base_id"]),
        )
        cur.execute(
            f'DELETE FROM "{adapter.table_name}" '
            f"WHERE organization_id = %s AND id = %s "
            f"AND branch_id = %s",
            (self.organization_id, overlay["id"], overlay["branch_id"]),
        )

    # ─── Internal: read helpers ───────────────────────────────────

    def _fetch_main_rows(self, adapter):
        self.db.initialize()
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f'SELECT * FROM "{adapter.table_name}" '
                f"WHERE organization_id = %s AND branch_id IS NULL "
                f"ORDER BY {adapter.primary_key[0]}",
                (self.organization_id,),
            )
            rows = cur.fetchall()
        return [dict(r) for r in rows]

    def _fetch_main_row_by_id(self, adapter, row_id):
        if adapter.primary_key != ("id",):
            raise NotImplementedError(
                "single-column PKs only in this phase"
            )
        self.db.initialize()
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f'SELECT * FROM "{adapter.table_name}" '
                f"WHERE organization_id = %s AND id = %s "
                f"AND branch_id IS NULL",
                (self.organization_id, row_id),
            )
            row = cur.fetchone()
        return dict(row) if row else None

    def _row_key(self, row, adapter) -> str:
        if len(adapter.primary_key) == 1:
            return str(row.get(adapter.primary_key[0]) or "")
        return "::".join(str(row.get(col) or "") for col in adapter.primary_key)

    def _insert_row(self, adapter, row) -> None:
        allowed_cols = list(adapter.insertable_columns) + [
            "branch_id", "branch_op", "branch_base_id",
            "created_at", "updated_at",
        ]
        if "created_at" not in row:
            row["created_at"] = datetime.now(timezone.utc).isoformat()
        if "updated_at" not in row:
            row["updated_at"] = row["created_at"]
        cols = [c for c in allowed_cols if c in row]
        placeholders = ", ".join(["%s"] * len(cols))
        col_list = ", ".join(f'"{c}"' for c in cols)
        params = tuple(row.get(c) for c in cols)
        self.db.initialize()
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f'INSERT INTO "{adapter.table_name}" ({col_list}) '
                f"VALUES ({placeholders})",
                params,
            )
            conn.commit()

    def _require_open_branch(self, branch_id: str) -> DataBranch:
        branch = self.get_branch(branch_id)
        if branch.status != "open":
            raise ValueError(
                f"branch {branch.name!r} is {branch.status!r}, not open"
            )
        return branch


def _row_to_branch(row: Dict[str, Any]) -> DataBranch:
    return DataBranch(
        id=str(row.get("id") or ""),
        organization_id=str(row.get("organization_id") or ""),
        table_name=str(row.get("table_name") or ""),
        name=str(row.get("name") or ""),
        description=str(row.get("description") or ""),
        status=str(row.get("status") or "open"),
        base_snapshot_json=str(row.get("base_snapshot_json") or "{}"),
        created_at=str(row.get("created_at") or ""),
        created_by=str(row.get("created_by") or ""),
        merged_at=str(row.get("merged_at")) if row.get("merged_at") else None,
        merged_by=str(row.get("merged_by")) if row.get("merged_by") else None,
        abandoned_at=str(row.get("abandoned_at")) if row.get("abandoned_at") else None,
        abandoned_by=str(row.get("abandoned_by")) if row.get("abandoned_by") else None,
    )
