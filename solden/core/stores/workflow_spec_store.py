"""WorkflowSpecStore — tenant-authored, versioned declarative Box types.

Level 2: customers define Box types at runtime (no deploy). A spec is stored
per-tenant and versioned in the ``workflow_specs`` table; versions are
immutable once activated, and exactly one version per ``(org, box_type)`` is
active at a time. Importing this module installs two resolvers:

  * the WorkflowSpec resolver (``workflow_spec.set_spec_resolver``) — so the
    generic store resolves a tenant's active (or version-pinned) DB spec,
    falling back to code-registered built-ins;
  * the box_registry dynamic resolver — so per-type policy (e.g.
    ``exception_state``) is available for tenant types that aren't in the
    global registry.

Both are pure registrations at import; they are only *called* at runtime.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from solden.core import box_registry, workflow_spec
from solden.core.workflow_spec import (
    WorkflowSpec,
    boxtype_from_spec,
    from_json,
    to_json,
    validate_spec,
)

logger = logging.getLogger(__name__)

_VALID_STATUSES = ("draft", "active", "archived")


class WorkflowSpecStore:
    """Mixin: per-tenant versioned WorkflowSpec CRUD + resolution."""

    def create_workflow_spec_draft(
        self,
        organization_id: str,
        spec_dict: Dict[str, Any],
        *,
        created_by: str = "",
    ) -> Dict[str, Any]:
        """Validate a spec and store it as the next draft version.

        Raises ``ValueError`` (with the validation errors) if the spec is
        invalid. Does not activate it — call :meth:`activate_workflow_spec`.
        """
        if not organization_id:
            raise ValueError("create_workflow_spec_draft requires organization_id")
        spec = from_json(spec_dict)
        errors = validate_spec(spec)
        if errors:
            raise ValueError("invalid_spec: " + "; ".join(errors))

        self.initialize()
        box_type = spec.box_type
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COALESCE(MAX(version), 0) AS v FROM workflow_specs "
                "WHERE organization_id = %s AND box_type = %s",
                (organization_id, box_type),
            )
            row = cur.fetchone()
            next_version = int((dict(row).get("v") if row else 0) or 0) + 1
            cur.execute(
                """
                INSERT INTO workflow_specs
                (organization_id, box_type, version, spec_json, status,
                 created_by, created_at)
                VALUES (%s, %s, %s, %s::jsonb, 'draft', %s, %s)
                """,
                (
                    organization_id, box_type, next_version,
                    json.dumps(to_json(spec)), created_by, now,
                ),
            )
            conn.commit()
        return self.get_workflow_spec_row(organization_id, box_type, next_version)  # type: ignore[return-value]

    def get_workflow_spec_row(
        self,
        organization_id: str,
        box_type: str,
        version: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return one spec row (spec_json parsed). version=None → active."""
        self.initialize()
        with self.connect() as conn:
            cur = conn.cursor()
            if version is not None:
                cur.execute(
                    "SELECT * FROM workflow_specs WHERE organization_id = %s "
                    "AND box_type = %s AND version = %s",
                    (organization_id, box_type, int(version)),
                )
            else:
                cur.execute(
                    "SELECT * FROM workflow_specs WHERE organization_id = %s "
                    "AND box_type = %s AND status = 'active'",
                    (organization_id, box_type),
                )
            row = cur.fetchone()
        return self._deserialize_spec_row(dict(row)) if row else None

    def list_workflow_specs(self, organization_id: str) -> List[Dict[str, Any]]:
        """All spec versions for an org, newest first."""
        self.initialize()
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM workflow_specs WHERE organization_id = %s "
                "ORDER BY box_type ASC, version DESC",
                (organization_id,),
            )
            rows = [dict(r) for r in cur.fetchall()]
        return [self._deserialize_spec_row(r) for r in rows]

    def activate_workflow_spec(
        self,
        organization_id: str,
        box_type: str,
        version: int,
        *,
        actor: str = "",
    ) -> Dict[str, Any]:
        """Make *version* the single active spec, archiving any prior active.

        Atomic: archive-then-activate in one transaction so the partial-unique
        ``one active per (org, box_type)`` index never trips.
        """
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT version FROM workflow_specs WHERE organization_id = %s "
                "AND box_type = %s AND version = %s",
                (organization_id, box_type, int(version)),
            )
            if cur.fetchone() is None:
                raise ValueError(
                    f"workflow_spec {box_type!r} v{version} not found for org"
                )
            cur.execute(
                "UPDATE workflow_specs SET status = 'archived', archived_at = %s "
                "WHERE organization_id = %s AND box_type = %s AND status = 'active'",
                (now, organization_id, box_type),
            )
            cur.execute(
                "UPDATE workflow_specs SET status = 'active', activated_at = %s "
                "WHERE organization_id = %s AND box_type = %s AND version = %s",
                (now, organization_id, box_type, int(version)),
            )
            conn.commit()
        logger.info(
            "[workflow_specs] activated %s v%s for org=%s by=%s",
            box_type, version, organization_id, actor or "?",
        )
        return self.get_workflow_spec_row(organization_id, box_type, version)  # type: ignore[return-value]

    def archive_workflow_spec(
        self,
        organization_id: str,
        box_type: str,
        version: int,
        *,
        actor: str = "",
    ) -> Dict[str, Any]:
        """Archive a specific spec version (no active spec remains if it was active)."""
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE workflow_specs SET status = 'archived', archived_at = %s "
                "WHERE organization_id = %s AND box_type = %s AND version = %s",
                (now, organization_id, box_type, int(version)),
            )
            conn.commit()
        return self.get_workflow_spec_row(organization_id, box_type, version)  # type: ignore[return-value]

    def resolve_workflow_spec(
        self,
        organization_id: str,
        box_type: str,
        version: Optional[int] = None,
    ) -> Optional[WorkflowSpec]:
        """Return the governing :class:`WorkflowSpec` for a tenant Box, or None.

        version given → that exact version (Box pinning); else the active
        version. Returns None when the org has no DB spec for this type, so the
        caller falls back to the code registry.
        """
        row = self.get_workflow_spec_row(organization_id, box_type, version)
        if not row:
            return None
        spec = from_json(row["spec_json"])
        spec.version = int(row.get("version") or 1)
        return spec

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _deserialize_spec_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        raw = row.get("spec_json")
        if isinstance(raw, str):
            try:
                row["spec_json"] = json.loads(raw)
            except json.JSONDecodeError:
                row["spec_json"] = {}
        return row


# ---------------------------------------------------------------------------
# Resolver installation — registered at import, called only at runtime.
# ---------------------------------------------------------------------------

def _db_spec_resolver(
    box_type: str,
    organization_id: Optional[str],
    version: Optional[int],
) -> Optional[WorkflowSpec]:
    if not organization_id:
        return None
    from solden.core.database import get_db
    db = get_db()
    if not hasattr(db, "resolve_workflow_spec"):
        return None
    try:
        return db.resolve_workflow_spec(organization_id, box_type, version)
    except Exception:  # pragma: no cover - resolver must never crash the path
        logger.exception("[workflow_specs] resolver failed for %s", box_type)
        return None


def _db_boxtype_resolver(
    box_type: str,
    organization_id: Optional[str],
) -> Optional[Any]:
    spec = workflow_spec.resolve_spec(box_type, organization_id)
    if spec is None:
        return None
    return boxtype_from_spec(spec)


workflow_spec.set_spec_resolver(_db_spec_resolver)
box_registry.set_dynamic_resolver(_db_boxtype_resolver)
