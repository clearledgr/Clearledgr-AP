"""Pipeline object model store — DESIGN_THESIS.md §5.1.

First-class Pipeline, Stage, Column, SavedView, and BoxLink objects.
These layer on top of existing ap_items and vendor_onboarding_sessions
tables without modifying them.

``PipelineStore`` is a mixin — no ``__init__``, expects:
  self.connect()
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class PipelineStore:
    """Mixin for Pipeline/Stage/Column/SavedView/BoxLink CRUD."""

    # ------------------------------------------------------------------
    # Pipeline
    # ------------------------------------------------------------------

    def get_pipeline(self, organization_id: str, slug: str) -> Optional[Dict[str, Any]]:
        """Get a pipeline by org + slug. Falls back to __default__ org."""
        self.initialize()
        sql = (
            "SELECT * FROM pipelines WHERE slug = %s AND (organization_id = %s OR organization_id = '__default__') "
            "AND is_active = 1 ORDER BY CASE WHEN organization_id = %s THEN 0 ELSE 1 END LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (slug, organization_id, organization_id))
            row = cur.fetchone()
        if not row:
            return None
        pipeline = dict(row)
        pipeline["stages"] = self.get_pipeline_stages(pipeline["id"])
        pipeline["columns"] = self.get_pipeline_columns(pipeline["id"])
        return pipeline

    def list_pipelines(self, organization_id: str) -> List[Dict[str, Any]]:
        """List all active pipelines for an org (including defaults)."""
        self.initialize()
        sql = (
            "SELECT * FROM pipelines WHERE (organization_id = %s OR organization_id = '__default__') "
            "AND is_active = 1 ORDER BY name"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id,))
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Pipeline Stages
    # ------------------------------------------------------------------

    def get_pipeline_stages(self, pipeline_id: str) -> List[Dict[str, Any]]:
        """Get ordered stages for a pipeline."""
        self.initialize()
        sql = (
            "SELECT * FROM pipeline_stages WHERE pipeline_id = %s ORDER BY stage_order"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (pipeline_id,))
            rows = cur.fetchall()
        result = []
        for row in rows:
            stage = dict(row)
            try:
                stage["source_states"] = json.loads(stage.get("source_states") or "[]")
            except (json.JSONDecodeError, TypeError):
                stage["source_states"] = []
            result.append(stage)
        return result

    def get_box_stage(self, pipeline_id: str, state: str) -> Optional[Dict[str, Any]]:
        """Reverse lookup: given a DB state, return the thesis stage it belongs to."""
        stages = self.get_pipeline_stages(pipeline_id)
        for stage in stages:
            if state in stage.get("source_states", []):
                return stage
        return None

    # ------------------------------------------------------------------
    # Pipeline Columns
    # ------------------------------------------------------------------

    def get_pipeline_columns(self, pipeline_id: str) -> List[Dict[str, Any]]:
        """Get ordered columns for a pipeline."""
        self.initialize()
        sql = (
            "SELECT * FROM pipeline_columns WHERE pipeline_id = %s ORDER BY display_order"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (pipeline_id,))
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Boxes in Stage (queries source table via stage config)
    # ------------------------------------------------------------------

    def list_boxes_in_stage(
        self,
        pipeline_id: str,
        stage_slug: str,
        organization_id: str,
        limit: int = 200,
        entity_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List items (Boxes) that belong to a specific thesis stage.

        Reads the stage's source_states, then queries the pipeline's
        source_table for items in those states.
        """
        self.initialize()

        # Get pipeline + stage config
        pipeline_sql = "SELECT source_table FROM pipelines WHERE id = %s"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(pipeline_sql, (pipeline_id,))
            pl_row = cur.fetchone()
        if not pl_row:
            return []
        source_table = dict(pl_row).get("source_table", "ap_items")

        # Whitelist source_table to prevent SQL injection via crafted pipeline config
        _ALLOWED_SOURCE_TABLES = {"ap_items", "vendor_onboarding_sessions"}
        if source_table not in _ALLOWED_SOURCE_TABLES:
            logger.warning("[PipelineStore] Rejected source_table %r — not in whitelist", source_table)
            return []

        # source_filter_json was added in migration v37. On older schemas
        # the column may not exist yet — SELECT *-less so we can default.
        try:
            stage_sql = (
                "SELECT source_states, source_filter_json FROM pipeline_stages "
                "WHERE pipeline_id = %s AND slug = %s"
            )
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(stage_sql, (pipeline_id, stage_slug))
                st_row = cur.fetchone()
        except Exception:
            # Fallback for pre-v37 schemas (tests / legacy DBs).
            stage_sql = (
                "SELECT source_states FROM pipeline_stages WHERE pipeline_id = %s AND slug = %s"
            )
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(stage_sql, (pipeline_id, stage_slug))
                st_row = cur.fetchone()
        if not st_row:
            return []

        st_dict = dict(st_row)
        try:
            source_states = json.loads(st_dict.get("source_states") or "[]")
        except (json.JSONDecodeError, TypeError):
            return []
        if not source_states:
            return []

        try:
            source_filter = json.loads(st_dict.get("source_filter_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            source_filter = {}
        if not isinstance(source_filter, dict):
            source_filter = {}

        # Query the source table for items in the mapped states
        placeholders = ", ".join("%s" for _ in source_states)
        state_col = "state"

        where_parts = [f"organization_id = %s", f"{state_col} IN ({placeholders})"]
        params: list = [organization_id, *source_states]

        if entity_id and source_table == "ap_items":
            where_parts.append("entity_id = %s")
            params.append(entity_id)

        # Apply source_filter_json predicates (from v37). Keys must be
        # real columns on the source table; values can be a scalar
        # (equality) or a list (IN). Anything that doesn't match the
        # whitelist is silently dropped so a crafted pipeline config
        # can never inject SQL — invalid filters degrade to "no extra
        # filter" rather than a 500.
        _STAGE_FILTER_WHITELIST: Dict[str, set] = {
            "ap_items": {"payment_status", "match_status", "exception_code",
                         "entity_id", "is_resubmission", "has_resubmission"},
            "vendor_onboarding_sessions": {"chase_count"},
        }
        allowed_keys = _STAGE_FILTER_WHITELIST.get(source_table, set())
        for key, value in source_filter.items():
            if key not in allowed_keys:
                continue
            if isinstance(value, (list, tuple)):
                if not value:
                    continue
                fph = ", ".join("%s" for _ in value)
                where_parts.append(f"{key} IN ({fph})")
                params.extend(value)
            elif value is None:
                where_parts.append(f"{key} IS NULL")
            else:
                where_parts.append(f"{key} = %s")
                params.append(value)

        where_clause = " AND ".join(where_parts)
        query_sql = (
            f"SELECT * FROM {source_table} WHERE {where_clause} ORDER BY created_at DESC LIMIT %s"
        )
        params.append(limit)

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(query_sql, tuple(params))
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Saved Views
    # ------------------------------------------------------------------

    def create_saved_view(
        self,
        organization_id: str,
        pipeline_id: str,
        name: str,
        filter_json: Optional[Dict] = None,
        sort_json: Optional[Dict] = None,
        show_in_inbox: bool = False,
        created_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a saved view for a pipeline."""
        self.initialize()
        view_id = f"SV-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        sql = (
            "INSERT INTO saved_views (id, organization_id, pipeline_id, name, filter_json, sort_json, show_in_inbox, created_by, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
        )
        with self.connect() as conn:
            conn.execute(sql, (
                view_id, organization_id, pipeline_id, name,
                json.dumps(filter_json or {}), json.dumps(sort_json or {}),
                1 if show_in_inbox else 0, created_by, now,
            ))
            conn.commit()
        return self.get_saved_view(view_id) or {}

    def list_saved_views(
        self, organization_id: str, pipeline_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List saved views for an org, optionally filtered by pipeline."""
        self.initialize()
        if pipeline_id:
            sql = (
                "SELECT * FROM saved_views WHERE (organization_id = %s OR organization_id = '__default__') "
                "AND pipeline_id = %s ORDER BY is_default DESC, name"
            )
            params = (organization_id, pipeline_id)
        else:
            sql = (
                "SELECT * FROM saved_views WHERE (organization_id = %s OR organization_id = '__default__') "
                "ORDER BY is_default DESC, name"
            )
            params = (organization_id,)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        result = []
        for row in rows:
            view = dict(row)
            for json_field in ("filter_json", "sort_json"):
                try:
                    view[json_field] = json.loads(view.get(json_field) or "{}")
                except (json.JSONDecodeError, TypeError):
                    view[json_field] = {}
            result.append(view)
        return result

    def get_saved_view(self, view_id: str) -> Optional[Dict[str, Any]]:
        """Get a single saved view by ID."""
        self.initialize()
        sql = "SELECT * FROM saved_views WHERE id = %s"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (view_id,))
            row = cur.fetchone()
        if not row:
            return None
        view = dict(row)
        for json_field in ("filter_json", "sort_json"):
            try:
                view[json_field] = json.loads(view.get(json_field) or "{}")
            except (json.JSONDecodeError, TypeError):
                view[json_field] = {}
        return view

    def delete_saved_view(self, view_id: str) -> bool:
        """Delete a saved view. Default views cannot be deleted."""
        self.initialize()
        sql = "DELETE FROM saved_views WHERE id = %s AND is_default = 0"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (view_id,))
            conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Box Links
    # ------------------------------------------------------------------

    def link_boxes(
        self,
        source_box_id: str,
        source_box_type: str,
        target_box_id: str,
        target_box_type: str,
        link_type: str = "related",
    ) -> Dict[str, Any]:
        """Create a link between two Boxes (e.g. invoice ↔ vendor onboarding)."""
        self.initialize()
        link_id = f"BL-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        sql = (
            "INSERT INTO box_links (id, source_box_id, source_box_type, target_box_id, target_box_type, link_type, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT DO NOTHING"
        )
        with self.connect() as conn:
            conn.execute(sql, (link_id, source_box_id, source_box_type, target_box_id, target_box_type, link_type, now))
            conn.commit()
        return {"id": link_id, "source_box_id": source_box_id, "target_box_id": target_box_id, "link_type": link_type}

    def get_box_links(self, box_id: str, box_type: str) -> List[Dict[str, Any]]:
        """Get all links for a Box (both directions)."""
        self.initialize()
        sql = (
            "SELECT * FROM box_links WHERE (source_box_id = %s AND source_box_type = %s) "
            "OR (target_box_id = %s AND target_box_type = %s) ORDER BY created_at DESC"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (box_id, box_type, box_id, box_type))
            rows = cur.fetchall()
        return [dict(row) for row in rows]
