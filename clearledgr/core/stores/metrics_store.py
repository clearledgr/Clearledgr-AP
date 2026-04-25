"""Metrics and KPI data-access mixin for ClearledgrDB.

``MetricsStore`` is a **mixin class** -- it has no ``__init__`` of its own and
expects the concrete class that inherits it to provide:

* ``self.connect()``                       -- returns a DB connection (context manager)
* ``self._prepare_sql()``                  -- adapts ``?`` placeholders for the active engine
* ``self.initialize()``                    -- ensures tables exist
* ``self._decode_json()``                  -- safely parses a JSON string or returns ``{}``
* ``self._deserialize_audit_event()``      -- deserializes an audit event row
* ``self._deserialize_browser_action_event()`` -- deserializes a browser action event row
* ``self.list_ap_items()``                 -- lists AP items for an organization
* ``self.list_approvals()``                -- lists approvals for an organization
* ``self.list_audit_events()``             -- lists audit events for an organization
* ``self.use_postgres``                    -- bool flag for Postgres vs SQLite dialect

All methods are copied verbatim from ``clearledgr/core/database.py`` so that
``ClearledgrDB(MetricsStore, ...)`` inherits them without any behavioural change.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from clearledgr.core.ap_entity_routing import resolve_entity_routing
from clearledgr.core.utils import safe_float

logger = logging.getLogger(__name__)


def _state_transition_event_types(box_type: str) -> List[str]:
    """Map a Box type to the audit event_type(s) that record its
    state transitions. Vendor onboarding emits its own prefixed
    event_type; AP uses the generic ``state_transition``.
    """
    if box_type == "vendor_onboarding_session":
        return ["vendor_onboarding_state_transition"]
    return ["state_transition"]


class MetricsStore:
    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    def _parse_iso(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                # Treat legacy naive timestamps as UTC for consistent comparisons.
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _percentile(values: List[float], percentile: float) -> Optional[float]:
        if not values:
            return None
        ordered = sorted(values)
        safe = max(0.0, min(1.0, float(percentile)))
        idx = max(0, min(len(ordered) - 1, int(round(safe * (len(ordered) - 1)))))
        return ordered[idx]

    @staticmethod
    def _p95(values: List[float]) -> Optional[float]:
        return MetricsStore._percentile(values, 0.95)

    def _decode_json_any(self, value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, str) and value.strip():
            try:
                return json.loads(value)
            except Exception:
                return value
        return value

    @staticmethod
    def _coerce_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @staticmethod
    def _safe_rate(numerator: int, denominator: int) -> float:
        return round((float(numerator) / float(denominator)) if denominator else 0.0, 4)

    def _blocking_source_conflicts(self, raw_conflicts: Any) -> List[Dict[str, Any]]:
        blockers: List[Dict[str, Any]] = []
        if not isinstance(raw_conflicts, list):
            return blockers
        for conflict in raw_conflicts:
            if not isinstance(conflict, dict):
                continue
            field = str(conflict.get("field") or "").strip().lower()
            if not field:
                continue
            if not bool(conflict.get("blocking")):
                continue
            blockers.append(conflict)
        return blockers

    @staticmethod
    def _dominant_count_key(counts: Dict[str, int]) -> str:
        if not counts:
            return ""
        ordered = sorted(
            ((str(key or ""), int(value or 0)) for key, value in counts.items()),
            key=lambda pair: (-pair[1], pair[0]),
        )
        if not ordered or ordered[0][1] <= 0:
            return ""
        return ordered[0][0]

    def _build_extraction_drift_metrics(
        self,
        items: List[Dict[str, Any]],
        *,
        now: datetime,
        recent_window_days: int = 7,
        baseline_window_days: int = 30,
        scorecard_limit: int = 20,
        sample_limit: int = 20,
    ) -> Dict[str, Any]:
        if not items:
            return {
                "summary": {
                    "window_days": int(baseline_window_days),
                    "recent_window_days": int(recent_window_days),
                    "vendors_monitored": 0,
                    "vendors_at_risk": 0,
                    "high_risk_vendors": 0,
                    "sampled_review_count": 0,
                    "recent_open_blocked_items": 0,
                },
                "vendor_scorecards": [],
                "sampled_review_queue": [],
            }

        window_start = now - timedelta(days=max(1, int(baseline_window_days)))
        recent_start = now - timedelta(days=max(1, int(recent_window_days)))
        open_states = {
            "received",
            "validated",
            "needs_info",
            "needs_approval",
            "pending_approval",
            "approved",
            "ready_to_post",
            "failed_post",
        }
        drift_fields = ("amount", "currency", "invoice_number", "vendor")
        vendor_buckets: Dict[str, Dict[str, Any]] = {}

        def _new_slice() -> Dict[str, Any]:
            return {
                "count": 0,
                "requires_field_review_count": 0,
                "blocking_conflict_count": 0,
                "open_blocked_count": 0,
                "conflict_fields": {},
                "field_sources": {field: {} for field in drift_fields},
            }

        def _bump(mapping: Dict[str, int], key: str, amount: int = 1) -> None:
            safe_key = str(key or "").strip().lower()
            if not safe_key:
                return
            mapping[safe_key] = mapping.get(safe_key, 0) + int(amount)

        for item in items:
            item_ts = self._parse_iso(item.get("created_at")) or self._parse_iso(item.get("updated_at")) or now
            if item_ts < window_start:
                continue

            vendor = str(item.get("vendor_name") or item.get("vendor") or "Unknown").strip() or "Unknown"
            metadata = self._decode_json_any(item.get("metadata"))
            metadata_dict = metadata if isinstance(metadata, dict) else {}
            confidence_gate = metadata_dict.get("confidence_gate")
            confidence_gate_dict = confidence_gate if isinstance(confidence_gate, dict) else {}
            requires_field_review = self._coerce_bool(
                item.get("requires_field_review")
                if item.get("requires_field_review") is not None
                else (
                    metadata_dict.get("requires_field_review")
                    if metadata_dict.get("requires_field_review") is not None
                    else confidence_gate_dict.get("requires_field_review")
                )
            )
            raw_conflicts = metadata_dict.get("source_conflicts")
            source_conflicts = raw_conflicts if isinstance(raw_conflicts, list) else []
            blocking_conflicts = self._blocking_source_conflicts(source_conflicts)
            blocking_fields = sorted(
                {
                    str(conflict.get("field") or "").strip().lower()
                    for conflict in blocking_conflicts
                    if str(conflict.get("field") or "").strip()
                }
            )

            field_provenance = metadata_dict.get("field_provenance")
            field_provenance_dict = field_provenance if isinstance(field_provenance, dict) else {}
            state = str(item.get("state") or "").strip().lower()
            open_blocked = state in open_states and (requires_field_review or bool(blocking_fields))

            bucket = vendor_buckets.setdefault(
                vendor,
                {
                    "vendor_name": vendor,
                    "recent": _new_slice(),
                    "baseline": _new_slice(),
                    "recent_items": [],
                },
            )
            window_bucket = bucket["recent"] if item_ts >= recent_start else bucket["baseline"]
            window_bucket["count"] += 1
            if requires_field_review:
                window_bucket["requires_field_review_count"] += 1
            if blocking_fields:
                window_bucket["blocking_conflict_count"] += 1
            if open_blocked:
                window_bucket["open_blocked_count"] += 1
            for field in blocking_fields:
                _bump(window_bucket["conflict_fields"], field)
            for field in drift_fields:
                provenance_entry = field_provenance_dict.get(field)
                if not isinstance(provenance_entry, dict):
                    continue
                source = str(provenance_entry.get("source") or "").strip().lower()
                if not source:
                    continue
                _bump(window_bucket["field_sources"].setdefault(field, {}), source)

            if item_ts >= recent_start:
                bucket["recent_items"].append(
                    {
                        "ap_item_id": str(item.get("id") or "").strip(),
                        "vendor_name": vendor,
                        "invoice_number": str(item.get("invoice_number") or "").strip() or None,
                        "amount": round(safe_float(item.get("amount"), 0.0), 2)
                        if item.get("amount") is not None
                        else None,
                        "currency": str(item.get("currency") or "USD").strip().upper(),
                        "state": state or "received",
                        "created_at": item_ts.isoformat(),
                        "sort_ts": float(item_ts.timestamp()),
                        "requires_field_review": requires_field_review,
                        "open_blocked": open_blocked,
                        "blocking_fields": blocking_fields,
                    }
                )

        def _priority_rank(entry: Dict[str, Any]) -> int:
            if bool(entry.get("open_blocked")):
                return 0
            if entry.get("blocking_fields"):
                return 1
            if bool(entry.get("requires_field_review")):
                return 2
            if str(entry.get("state") or "").strip().lower() in open_states:
                return 3
            return 4

        state_priority = {
            "received": 0,
            "validated": 1,
            "needs_info": 2,
            "needs_approval": 3,
            "pending_approval": 4,
            "approved": 5,
            "ready_to_post": 6,
            "failed_post": 7,
        }

        def _state_rank(entry: Dict[str, Any]) -> int:
            return state_priority.get(str(entry.get("state") or "").strip().lower(), 99)

        risk_rank = {"high": 0, "medium": 1, "stable": 2}
        scorecards: List[Dict[str, Any]] = []
        sampled_review_queue: List[Dict[str, Any]] = []

        for bucket in vendor_buckets.values():
            recent = bucket["recent"]
            baseline = bucket["baseline"]
            recent_count = int(recent.get("count") or 0)
            baseline_count = int(baseline.get("count") or 0)
            recent_review_rate = self._safe_rate(
                int(recent.get("requires_field_review_count") or 0),
                recent_count,
            )
            baseline_review_rate = self._safe_rate(
                int(baseline.get("requires_field_review_count") or 0),
                baseline_count,
            )
            recent_blocking_rate = self._safe_rate(
                int(recent.get("blocking_conflict_count") or 0),
                recent_count,
            )
            baseline_blocking_rate = self._safe_rate(
                int(baseline.get("blocking_conflict_count") or 0),
                baseline_count,
            )

            source_shift_fields: List[Dict[str, Any]] = []
            for field in drift_fields:
                recent_sources = recent.get("field_sources", {}).get(field) or {}
                baseline_sources = baseline.get("field_sources", {}).get(field) or {}
                recent_total = sum(int(value or 0) for value in recent_sources.values())
                baseline_total = sum(int(value or 0) for value in baseline_sources.values())
                recent_dominant = self._dominant_count_key(recent_sources)
                baseline_dominant = self._dominant_count_key(baseline_sources)
                if (
                    recent_total >= 2
                    and baseline_total >= 2
                    and recent_dominant
                    and baseline_dominant
                    and recent_dominant != baseline_dominant
                ):
                    source_shift_fields.append(
                        {
                            "field": field,
                            "from_source": baseline_dominant,
                            "to_source": recent_dominant,
                        }
                    )

            recent_conflict_fields = recent.get("conflict_fields") or {}
            top_conflict_fields = [
                field
                for field, _count in sorted(
                    recent_conflict_fields.items(),
                    key=lambda pair: (-int(pair[1] or 0), pair[0]),
                )[:3]
            ]

            risk_signals: List[str] = []
            risk_score = 0
            if recent_count >= 2 and recent_review_rate >= 0.25:
                risk_signals.append("field_review_rate_high")
                risk_score += 2
            if (
                recent_count >= 2
                and int(recent.get("requires_field_review_count") or 0) > 0
                and recent_review_rate >= (baseline_review_rate + 0.15)
            ):
                risk_signals.append("field_review_rate_spike")
                risk_score += 2
            if recent_count >= 2 and recent_blocking_rate >= 0.2:
                risk_signals.append("blocking_conflict_rate_high")
                risk_score += 2
            if (
                recent_count >= 2
                and int(recent.get("blocking_conflict_count") or 0) > 0
                and recent_blocking_rate >= (baseline_blocking_rate + 0.10)
            ):
                risk_signals.append("blocking_conflict_rate_spike")
                risk_score += 2
            if int(recent_conflict_fields.get("amount") or 0) > 0:
                risk_signals.append("amount_conflict_present")
                risk_score += 2
            if int(recent_conflict_fields.get("invoice_number") or 0) > 0:
                risk_signals.append("invoice_number_conflict_present")
                risk_score += 2
            if int(recent.get("open_blocked_count") or 0) > 0:
                risk_signals.append("open_blocked_items_present")
                risk_score += 1
            for shift in source_shift_fields:
                risk_signals.append(
                    f"source_shift:{shift['field']}:{shift['from_source']}->{shift['to_source']}"
                )
                risk_score += 1

            if (
                int(recent.get("open_blocked_count") or 0) >= 2
                or risk_score >= 5
                or (recent_count >= 3 and recent_blocking_rate >= 0.34)
            ):
                drift_risk = "high"
            elif int(recent.get("open_blocked_count") or 0) >= 1 or risk_score >= 2:
                drift_risk = "medium"
            else:
                drift_risk = "stable"

            sample_recommended_count = 0
            if drift_risk == "high":
                sample_recommended_count = 3 if recent_count >= 5 else 2 if recent_count >= 2 else 1
            elif drift_risk == "medium":
                sample_recommended_count = 1

            scorecard = {
                "vendor_name": bucket["vendor_name"],
                "window_invoice_count": int(recent_count + baseline_count),
                "recent_invoice_count": recent_count,
                "baseline_invoice_count": baseline_count,
                "recent_requires_field_review_count": int(recent.get("requires_field_review_count") or 0),
                "recent_requires_field_review_rate": recent_review_rate,
                "baseline_requires_field_review_rate": baseline_review_rate,
                "recent_blocking_conflict_count": int(recent.get("blocking_conflict_count") or 0),
                "recent_blocking_conflict_rate": recent_blocking_rate,
                "baseline_blocking_conflict_rate": baseline_blocking_rate,
                "recent_open_blocked_count": int(recent.get("open_blocked_count") or 0),
                "top_conflict_fields": top_conflict_fields,
                "source_shift_fields": source_shift_fields,
                "drift_risk": drift_risk,
                "risk_score": int(risk_score),
                "risk_signals": risk_signals,
                "sample_recommended_count": int(sample_recommended_count),
            }
            scorecards.append(scorecard)

            if sample_recommended_count <= 0:
                continue

            recent_items = sorted(
                bucket["recent_items"],
                key=lambda entry: (
                    _priority_rank(entry),
                    _state_rank(entry),
                    -float(entry.get("sort_ts") or 0.0),
                    str(entry.get("ap_item_id") or ""),
                ),
            )
            blocked_candidates = [entry for entry in recent_items if _priority_rank(entry) <= 2]
            clean_candidates = [entry for entry in recent_items if _priority_rank(entry) >= 3]
            chosen: List[Dict[str, Any]] = []
            chosen_ids: set[str] = set()

            def _add_candidate(entry: Dict[str, Any]) -> None:
                ap_item_id = str(entry.get("ap_item_id") or "").strip()
                if not ap_item_id or ap_item_id in chosen_ids or len(chosen) >= sample_recommended_count:
                    return
                chosen.append(entry)
                chosen_ids.add(ap_item_id)

            if blocked_candidates:
                _add_candidate(blocked_candidates[0])
            if drift_risk == "high" and clean_candidates:
                _add_candidate(clean_candidates[0])
            for entry in recent_items:
                _add_candidate(entry)
                if len(chosen) >= sample_recommended_count:
                    break

            for entry in chosen:
                item_signals: List[str] = []
                if bool(entry.get("requires_field_review")):
                    item_signals.append("requires_field_review")
                for field in list(entry.get("blocking_fields") or [])[:3]:
                    item_signals.append(f"blocking_conflict:{field}")
                for signal in risk_signals[:4]:
                    if signal not in item_signals:
                        item_signals.append(signal)

                if entry.get("blocking_fields"):
                    sample_reason = "blocking_conflict_present"
                elif bool(entry.get("requires_field_review")):
                    sample_reason = "field_review_required"
                elif source_shift_fields:
                    sample_reason = "vendor_layout_shift_check"
                else:
                    sample_reason = "vendor_drift_review"

                sampled_review_queue.append(
                    {
                        "ap_item_id": entry.get("ap_item_id"),
                        "vendor_name": entry.get("vendor_name"),
                        "invoice_number": entry.get("invoice_number"),
                        "amount": entry.get("amount"),
                        "currency": entry.get("currency"),
                        "state": entry.get("state"),
                        "created_at": entry.get("created_at"),
                        "sample_reason": sample_reason,
                        "risk_signals": item_signals,
                        "requires_field_review": bool(entry.get("requires_field_review")),
                        "blocking_fields": list(entry.get("blocking_fields") or []),
                    }
                )

        scorecards.sort(
            key=lambda row: (
                risk_rank.get(str(row.get("drift_risk") or "stable"), 3),
                -int(row.get("risk_score") or 0),
                -int(row.get("recent_invoice_count") or 0),
                str(row.get("vendor_name") or ""),
            )
        )
        sampled_review_queue.sort(
            key=lambda row: (
                0 if str(row.get("sample_reason") or "").startswith("blocking") else 1,
                -int(bool(row.get("requires_field_review"))),
                str(row.get("created_at") or ""),
                str(row.get("ap_item_id") or ""),
            ),
            reverse=False,
        )
        sampled_review_queue = sorted(
            sampled_review_queue,
            key=lambda row: (
                0 if str(row.get("sample_reason") or "").startswith("blocking") else 1,
                -int(bool(row.get("requires_field_review"))),
                -float(self._parse_iso(row.get("created_at")).timestamp()) if self._parse_iso(row.get("created_at")) else 0.0,
                str(row.get("ap_item_id") or ""),
            ),
        )[: max(1, int(scorecard_limit if sample_limit <= 0 else sample_limit))]

        at_risk_count = sum(1 for row in scorecards if str(row.get("drift_risk")) in {"high", "medium"})
        high_risk_count = sum(1 for row in scorecards if str(row.get("drift_risk")) == "high")
        recent_open_blocked_items = sum(int((row.get("recent_open_blocked_count") or 0)) for row in scorecards)

        return {
            "summary": {
                "window_days": int(baseline_window_days),
                "recent_window_days": int(recent_window_days),
                "vendors_monitored": len(scorecards),
                "vendors_at_risk": int(at_risk_count),
                "high_risk_vendors": int(high_risk_count),
                "sampled_review_count": len(sampled_review_queue),
                "recent_open_blocked_items": int(recent_open_blocked_items),
            },
            "vendor_scorecards": scorecards[: max(1, int(scorecard_limit))],
            "sampled_review_queue": sampled_review_queue,
        }

    @staticmethod
    def _normalize_shadow_field_value(field: str, value: Any) -> Any:
        token = str(field or "").strip().lower()
        if value is None:
            return None
        if token == "amount":
            try:
                return round(float(value), 2)
            except (TypeError, ValueError):
                return None
        if token == "currency":
            return str(value or "").strip().upper() or None
        if token in {"vendor", "invoice_number", "document_type", "due_date"}:
            normalized = str(value or "").strip()
            return normalized.casefold() if normalized else None
        return str(value or "").strip() or None

    def _actual_shadow_field_value(
        self,
        *,
        field: str,
        item: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> Any:
        token = str(field or "").strip().lower()
        if token == "vendor":
            return item.get("vendor_name") or item.get("vendor")
        if token == "document_type":
            return metadata.get("document_type") or metadata.get("email_type") or "invoice"
        return item.get(token)

    def _actual_shadow_action(
        self,
        *,
        item: Dict[str, Any],
        metadata: Dict[str, Any],
        approvals: List[Dict[str, Any]],
        audit_events: List[Dict[str, Any]],
    ) -> Optional[str]:
        document_type = str(
            metadata.get("document_type") or metadata.get("email_type") or "invoice"
        ).strip().lower() or "invoice"
        if document_type != "invoice":
            return "non_invoice_finance_doc"

        resolutions = metadata.get("field_review_resolutions")
        resolutions = resolutions if isinstance(resolutions, dict) else {}
        confidence_blockers = metadata.get("confidence_blockers")
        confidence_blockers = confidence_blockers if isinstance(confidence_blockers, list) else []
        source_conflicts = metadata.get("source_conflicts")
        blocking_conflicts = self._blocking_source_conflicts(
            source_conflicts if isinstance(source_conflicts, list) else []
        )
        if (
            self._coerce_bool(metadata.get("requires_field_review"))
            or resolutions
            or confidence_blockers
            or blocking_conflicts
        ):
            return "field_review"

        if approvals:
            return "route_for_approval"

        state = str(item.get("state") or "").strip().lower()
        event_types = {
            str((event or {}).get("event_type") or "").strip().lower()
            for event in audit_events
            if isinstance(event, dict)
        }
        if state in {"closed", "posted_to_erp", "approved", "ready_to_post", "failed_post"}:
            return "auto_approve_post"
        if event_types & {"erp_post_attempted", "erp_post_succeeded", "erp_post_failed"}:
            return "auto_approve_post"
        if state in {"received", "validated"}:
            return None
        return "route_for_approval"

    def _build_shadow_decision_metrics(
        self,
        items: List[Dict[str, Any]],
        *,
        approvals_by_item: Dict[str, List[Dict[str, Any]]],
        audit_events_by_item: Dict[str, List[Dict[str, Any]]],
        scorecard_limit: int = 20,
        sample_limit: int = 20,
    ) -> Dict[str, Any]:
        summary = {
            "scored_item_count": 0,
            "action_population": 0,
            "action_match_count": 0,
            "action_match_rate": 0.0,
            "critical_field_population": 0,
            "critical_field_match_count": 0,
            "critical_field_match_rate": 0.0,
            "corrected_item_count": 0,
            "disagreement_count": 0,
        }
        if not items:
            return {
                "summary": summary,
                "vendor_scorecards": [],
                "sampled_disagreements": [],
            }

        critical_fields = ("amount", "currency", "invoice_number", "vendor", "document_type")
        vendor_buckets: Dict[str, Dict[str, Any]] = {}
        sampled_disagreements: List[Dict[str, Any]] = []

        def _vendor_bucket(vendor_name: str) -> Dict[str, Any]:
            return vendor_buckets.setdefault(
                vendor_name,
                {
                    "vendor_name": vendor_name,
                    "scored_item_count": 0,
                    "action_population": 0,
                    "action_match_count": 0,
                    "critical_field_population": 0,
                    "critical_field_match_count": 0,
                    "corrected_item_count": 0,
                    "disagreement_count": 0,
                    "disagreement_fields": {},
                },
            )

        for item in items:
            metadata = self._decode_json_any(item.get("metadata"))
            metadata_dict = metadata if isinstance(metadata, dict) else {}
            shadow = metadata_dict.get("shadow_decision")
            shadow = shadow if isinstance(shadow, dict) else {}
            proposed_fields = shadow.get("proposed_fields") if isinstance(shadow.get("proposed_fields"), dict) else {}
            proposed_action = str(shadow.get("proposed_action") or "").strip().lower()
            if not shadow or (not proposed_fields and not proposed_action):
                continue

            item_id = str(item.get("id") or "").strip()
            vendor_name = str(item.get("vendor_name") or item.get("vendor") or "Unknown").strip() or "Unknown"
            approvals = approvals_by_item.get(item_id, [])
            audit_events = audit_events_by_item.get(item_id, [])
            actual_action = self._actual_shadow_action(
                item=item,
                metadata=metadata_dict,
                approvals=approvals,
                audit_events=audit_events,
            )
            corrected = bool(
                metadata_dict.get("field_review_resolutions")
                or any(
                    str((event or {}).get("event_type") or "").strip().lower() == "field_correction"
                    for event in audit_events
                )
            )

            bucket = _vendor_bucket(vendor_name)
            summary["scored_item_count"] += 1
            bucket["scored_item_count"] += 1
            if corrected:
                summary["corrected_item_count"] += 1
                bucket["corrected_item_count"] += 1

            disagreement_fields: List[str] = []
            for field in critical_fields:
                proposed_value = self._normalize_shadow_field_value(field, proposed_fields.get(field))
                actual_value = self._normalize_shadow_field_value(
                    field,
                    self._actual_shadow_field_value(field=field, item=item, metadata=metadata_dict),
                )
                if proposed_value is None and actual_value is None:
                    continue
                summary["critical_field_population"] += 1
                bucket["critical_field_population"] += 1
                if proposed_value == actual_value:
                    summary["critical_field_match_count"] += 1
                    bucket["critical_field_match_count"] += 1
                else:
                    disagreement_fields.append(field)
                    field_counts = bucket["disagreement_fields"]
                    field_counts[field] = int(field_counts.get(field) or 0) + 1

            action_match = True
            if actual_action:
                summary["action_population"] += 1
                bucket["action_population"] += 1
                action_match = proposed_action == actual_action
                if action_match:
                    summary["action_match_count"] += 1
                    bucket["action_match_count"] += 1

            if disagreement_fields or (actual_action and not action_match):
                summary["disagreement_count"] += 1
                bucket["disagreement_count"] += 1
                sampled_disagreements.append(
                    {
                        "ap_item_id": item_id,
                        "vendor_name": vendor_name,
                        "invoice_number": str(item.get("invoice_number") or "").strip() or None,
                        "proposed_action": proposed_action or None,
                        "actual_action": actual_action,
                        "disagreement_fields": disagreement_fields,
                        "corrected": corrected,
                        "created_at": str(item.get("created_at") or item.get("updated_at") or ""),
                    }
                )

        summary["action_match_rate"] = self._safe_rate(
            int(summary["action_match_count"]),
            int(summary["action_population"]),
        )
        summary["critical_field_match_rate"] = self._safe_rate(
            int(summary["critical_field_match_count"]),
            int(summary["critical_field_population"]),
        )

        vendor_scorecards: List[Dict[str, Any]] = []
        for bucket in vendor_buckets.values():
            disagreement_fields = [
                field
                for field, _count in sorted(
                    (bucket.get("disagreement_fields") or {}).items(),
                    key=lambda pair: (-int(pair[1] or 0), pair[0]),
                )[:3]
            ]
            scored_count = int(bucket.get("scored_item_count") or 0)
            action_rate = self._safe_rate(
                int(bucket.get("action_match_count") or 0),
                int(bucket.get("action_population") or 0),
            )
            critical_rate = self._safe_rate(
                int(bucket.get("critical_field_match_count") or 0),
                int(bucket.get("critical_field_population") or 0),
            )
            if scored_count < 2:
                trust = "unproven"
            elif action_rate < 0.75 or critical_rate < 0.85:
                trust = "weak"
            elif action_rate < 0.9 or critical_rate < 0.95 or int(bucket.get("disagreement_count") or 0) > 0:
                trust = "watch"
            else:
                trust = "strong"
            vendor_scorecards.append(
                {
                    "vendor_name": bucket["vendor_name"],
                    "scored_item_count": scored_count,
                    "action_population": int(bucket.get("action_population") or 0),
                    "action_match_rate": action_rate,
                    "critical_field_population": int(bucket.get("critical_field_population") or 0),
                    "critical_field_match_rate": critical_rate,
                    "corrected_item_count": int(bucket.get("corrected_item_count") or 0),
                    "disagreement_count": int(bucket.get("disagreement_count") or 0),
                    "top_disagreement_fields": disagreement_fields,
                    "trust_mode": trust,
                }
            )

        vendor_scorecards.sort(
            key=lambda row: (
                {"weak": 0, "watch": 1, "unproven": 2, "strong": 3}.get(str(row.get("trust_mode") or "strong"), 9),
                -int(row.get("disagreement_count") or 0),
                -int(row.get("scored_item_count") or 0),
                str(row.get("vendor_name") or ""),
            )
        )
        sampled_disagreements = sorted(
            sampled_disagreements,
            key=lambda row: (
                0 if row.get("actual_action") and row.get("proposed_action") != row.get("actual_action") else 1,
                -len(row.get("disagreement_fields") or []),
                str(row.get("created_at") or ""),
                str(row.get("ap_item_id") or ""),
            ),
        )[: max(1, int(sample_limit))]

        return {
            "summary": summary,
            "vendor_scorecards": vendor_scorecards[: max(1, int(scorecard_limit))],
            "sampled_disagreements": sampled_disagreements,
        }

    def _build_post_action_verification_metrics(
        self,
        items: List[Dict[str, Any]],
        *,
        audit_events_by_item: Dict[str, List[Dict[str, Any]]],
        scorecard_limit: int = 20,
        sample_limit: int = 20,
    ) -> Dict[str, Any]:
        summary = {
            "attempted_count": 0,
            "verified_count": 0,
            "mismatch_count": 0,
            "verification_rate": 0.0,
            "success_event_count": 0,
            "failed_event_count": 0,
            "success_rate": 0.0,
            "recovery_attempt_count": 0,
            "recovered_count": 0,
            "recovery_rate": 0.0,
        }
        if not items:
            return {
                "summary": summary,
                "vendor_scorecards": [],
                "sampled_mismatches": [],
            }

        vendor_buckets: Dict[str, Dict[str, Any]] = {}
        sampled_mismatches: List[Dict[str, Any]] = []

        def _vendor_bucket(vendor_name: str) -> Dict[str, Any]:
            return vendor_buckets.setdefault(
                vendor_name,
                {
                    "vendor_name": vendor_name,
                    "attempted_count": 0,
                    "verified_count": 0,
                    "mismatch_count": 0,
                },
            )

        for item in items:
            item_id = str(item.get("id") or "").strip()
            audit_events = audit_events_by_item.get(item_id, [])
            metadata = self._decode_json_any(item.get("metadata"))
            metadata_dict = metadata if isinstance(metadata, dict) else {}
            post_verification = metadata_dict.get("post_action_verification")
            post_verification = post_verification if isinstance(post_verification, dict) else {}
            event_types = {
                str((event or {}).get("event_type") or "").strip().lower()
                for event in audit_events
                if isinstance(event, dict)
            }
            attempted = bool(post_verification.get("attempted")) or bool(
                event_types & {"erp_post_attempted", "erp_post_succeeded", "erp_post_failed"}
            )
            if not attempted:
                continue

            summary["attempted_count"] += 1
            vendor_name = str(item.get("vendor_name") or item.get("vendor") or "Unknown").strip() or "Unknown"
            bucket = _vendor_bucket(vendor_name)
            bucket["attempted_count"] += 1

            success_event = "erp_post_succeeded" in event_types
            failed_event = "erp_post_failed" in event_types
            if success_event:
                summary["success_event_count"] += 1
            if failed_event:
                summary["failed_event_count"] += 1
                summary["recovery_attempt_count"] += 1
            if failed_event and success_event:
                summary["recovered_count"] += 1

            state = str(item.get("state") or "").strip().lower()
            erp_reference = str(
                item.get("erp_reference")
                or post_verification.get("erp_reference")
                or ""
            ).strip()
            exception_code = str(
                item.get("exception_code")
                or metadata_dict.get("exception_code")
                or ""
            ).strip().lower()
            last_error = str(
                item.get("last_error")
                or metadata_dict.get("last_error")
                or ""
            ).strip().lower()

            mismatch_reasons: List[str] = []
            verified = False
            if success_event:
                if state in {"closed", "posted_to_erp"} and erp_reference:
                    verified = True
                else:
                    if state not in {"closed", "posted_to_erp"}:
                        mismatch_reasons.append("posted_success_state_mismatch")
                    if not erp_reference:
                        mismatch_reasons.append("posted_success_missing_reference")
            elif failed_event:
                if state == "failed_post" or "erp_post_failed" in exception_code or "erp" in last_error:
                    verified = True
                else:
                    mismatch_reasons.append("post_failure_state_mismatch")
            else:
                mismatch_reasons.append("post_attempt_missing_terminal_event")

            if verified:
                summary["verified_count"] += 1
                bucket["verified_count"] += 1
            else:
                summary["mismatch_count"] += 1
                bucket["mismatch_count"] += 1
                sampled_mismatches.append(
                    {
                        "ap_item_id": item_id,
                        "vendor_name": vendor_name,
                        "invoice_number": str(item.get("invoice_number") or "").strip() or None,
                        "state": state or None,
                        "erp_reference": erp_reference or None,
                        "mismatch_reasons": mismatch_reasons,
                        "created_at": str(item.get("updated_at") or item.get("created_at") or ""),
                    }
                )

        summary["verification_rate"] = self._safe_rate(
            int(summary["verified_count"]),
            int(summary["attempted_count"]),
        )
        summary["success_rate"] = self._safe_rate(
            int(summary["success_event_count"]),
            int(summary["attempted_count"]),
        )
        summary["recovery_rate"] = self._safe_rate(
            int(summary["recovered_count"]),
            int(summary["recovery_attempt_count"]),
        )

        vendor_scorecards = [
            {
                "vendor_name": bucket["vendor_name"],
                "attempted_count": int(bucket.get("attempted_count") or 0),
                "verified_count": int(bucket.get("verified_count") or 0),
                "mismatch_count": int(bucket.get("mismatch_count") or 0),
                "verification_rate": self._safe_rate(
                    int(bucket.get("verified_count") or 0),
                    int(bucket.get("attempted_count") or 0),
                ),
            }
            for bucket in vendor_buckets.values()
        ]
        vendor_scorecards.sort(
            key=lambda row: (
                float(row.get("verification_rate") or 0.0),
                -int(row.get("mismatch_count") or 0),
                -int(row.get("attempted_count") or 0),
                str(row.get("vendor_name") or ""),
            )
        )
        sampled_mismatches = sorted(
            sampled_mismatches,
            key=lambda row: (
                -len(row.get("mismatch_reasons") or []),
                str(row.get("created_at") or ""),
                str(row.get("ap_item_id") or ""),
            ),
        )[: max(1, int(sample_limit))]

        return {
            "summary": summary,
            "vendor_scorecards": vendor_scorecards[: max(1, int(scorecard_limit))],
            "sampled_mismatches": sampled_mismatches,
        }

    # ------------------------------------------------------------------
    # Audit events (query)
    # ------------------------------------------------------------------

    def list_audit_events(
        self,
        organization_id: str,
        event_types: Optional[List[str]] = None,
        limit: int = 10000,
    ) -> List[Dict[str, Any]]:
        self.initialize()
        params: List[Any] = [organization_id]
        sql = "SELECT * FROM audit_events WHERE organization_id = %s"
        if event_types:
            placeholders = ",".join("%s" for _ in event_types)
            sql += f" AND event_type IN ({placeholders})"
            params.extend(event_types)
        sql += " ORDER BY ts DESC LIMIT %s"
        params.append(limit)
        sql = self._prepare_sql(sql)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        return [self._deserialize_audit_event(dict(row)) for row in rows]

    # ------------------------------------------------------------------
    # Operational metrics
    # ------------------------------------------------------------------

    def get_operational_metrics(
        self,
        organization_id: str,
        approval_sla_minutes: int = 240,
        workflow_stuck_minutes: int = 120,
    ) -> Dict[str, Any]:
        self.initialize()
        now = datetime.now(timezone.utc)
        items = self.list_ap_items(organization_id, limit=5000)
        approvals = self.list_approvals(organization_id, status="approved", limit=5000)
        post_events = self.list_audit_events(
            organization_id,
            event_types=["erp_post_attempted", "erp_post_failed"],
            limit=10000,
        )
        callback_events = self.list_audit_events(
            organization_id,
            event_types=["approval_callback_rejected"],
            limit=10000,
        )

        state_counts: Dict[str, int] = {}
        open_states = {"received", "validated", "needs_info", "needs_approval", "approved", "ready_to_post", "failed_post"}
        queue_lags: List[float] = []
        sla_breached_open = 0
        workflow_stuck_count = 0

        for item in items:
            state = str(item.get("state") or "received")
            state_counts[state] = state_counts.get(state, 0) + 1
            if state not in open_states:
                continue
            created_at = self._parse_iso(item.get("created_at")) or self._parse_iso(item.get("updated_at"))
            if not created_at:
                continue
            lag_min = max(0.0, (now - created_at).total_seconds() / 60.0)
            queue_lags.append(lag_min)
            if state == "needs_approval" and lag_min >= approval_sla_minutes:
                sla_breached_open += 1
            if lag_min >= workflow_stuck_minutes:
                workflow_stuck_count += 1

        approval_latencies: List[float] = []
        for approval in approvals:
            created_at = self._parse_iso(approval.get("created_at"))
            approved_at = self._parse_iso(approval.get("approved_at"))
            if not created_at or not approved_at:
                continue
            latency_min = (approved_at - created_at).total_seconds() / 60.0
            if latency_min >= 0:
                approval_latencies.append(latency_min)

        # H13/H14: Also fetch retry and per-ERP breakdown events
        retry_events = self.list_audit_events(
            organization_id,
            event_types=["erp_post_resumed", "erp_post_retry_enqueued"],
            limit=10000,
        )
        succeeded_events = self.list_audit_events(
            organization_id,
            event_types=["erp_post_succeeded"],
            limit=10000,
        )

        cutoff = now - timedelta(hours=24)
        attempted_24h = 0
        failed_24h = 0
        retry_24h = 0
        retry_success_24h = 0
        per_erp_attempted: Dict[str, int] = {}
        per_erp_failed: Dict[str, int] = {}
        for event in post_events:
            ts = self._parse_iso(event.get("ts"))
            if not ts or ts < cutoff:
                continue
            meta = event.get("metadata") or {}
            if isinstance(meta, str):
                try:
                    import json as _json
                    meta = _json.loads(meta)
                except Exception:
                    meta = {}
            erp_type = str(meta.get("erp_type") or meta.get("erp") or "unknown")
            if event.get("event_type") == "erp_post_attempted":
                attempted_24h += 1
                per_erp_attempted[erp_type] = per_erp_attempted.get(erp_type, 0) + 1
            elif event.get("event_type") == "erp_post_failed":
                failed_24h += 1
                per_erp_failed[erp_type] = per_erp_failed.get(erp_type, 0) + 1
        for event in retry_events:
            ts = self._parse_iso(event.get("ts"))
            if ts and ts >= cutoff:
                retry_24h += 1
        for event in succeeded_events:
            ts = self._parse_iso(event.get("ts"))
            meta = event.get("metadata") or {}
            if isinstance(meta, str):
                try:
                    import json as _json
                    meta = _json.loads(meta)
                except Exception:
                    meta = {}
            if ts and ts >= cutoff and meta.get("is_retry"):
                retry_success_24h += 1

        failure_rate_24h = (failed_24h / attempted_24h) if attempted_24h else 0.0
        callback_verification_failures_24h = 0
        for event in callback_events:
            ts = self._parse_iso(event.get("ts"))
            if not ts or ts < cutoff:
                continue
            callback_verification_failures_24h += 1

        return {
            "organization_id": organization_id,
            "generated_at": now.isoformat(),
            "states": state_counts,
            "queue_lag": {
                "open_items": len(queue_lags),
                "avg_minutes": round(sum(queue_lags) / len(queue_lags), 2) if queue_lags else 0.0,
                "max_minutes": round(max(queue_lags), 2) if queue_lags else 0.0,
                "p95_minutes": round(self._p95(queue_lags) or 0.0, 2),
            },
            "approval_latency": {
                "approved_count": len(approval_latencies),
                "avg_minutes": round(sum(approval_latencies) / len(approval_latencies), 2) if approval_latencies else 0.0,
                "p95_minutes": round(self._p95(approval_latencies) or 0.0, 2),
                "sla_minutes": int(approval_sla_minutes),
                "sla_breached_open_count": int(sla_breached_open),
            },
            "posting": {
                "attempted_24h": attempted_24h,
                "failed_24h": failed_24h,
                "failure_rate_24h": round(failure_rate_24h, 4),
                "per_erp": {
                    erp: {
                        "attempted": per_erp_attempted.get(erp, 0),
                        "failed": per_erp_failed.get(erp, 0),
                        "failure_rate": round(
                            per_erp_failed.get(erp, 0) / per_erp_attempted[erp], 4
                        ) if per_erp_attempted.get(erp) else 0.0,
                    }
                    for erp in sorted(set(list(per_erp_attempted.keys()) + list(per_erp_failed.keys())))
                },
            },
            "retry": {
                "retry_count_24h": retry_24h,
                "retry_success_24h": retry_success_24h,
                "retry_success_rate": round(
                    retry_success_24h / retry_24h, 4
                ) if retry_24h else 0.0,
            },
            "post_failure_rate": {
                "attempted_24h": attempted_24h,
                "failed_24h": failed_24h,
                "rate_24h": round(failure_rate_24h, 4),
            },
            "callback_verification_failures": {
                "window_hours": 24,
                "count": callback_verification_failures_24h,
            },
            "workflow_stuck_count": {
                "threshold_minutes": int(workflow_stuck_minutes),
                "count": int(workflow_stuck_count),
            },
        }

    # ------------------------------------------------------------------
    # Box health (drill-down — complements get_operational_metrics)
    # ------------------------------------------------------------------

    def get_box_health(
        self,
        organization_id: str,
        stuck_threshold_minutes: int = 120,
        approval_sla_minutes: int = 240,
        limit: int = 500,
        box_type: str = "ap_item",
    ) -> Dict[str, Any]:
        """Drill-down view of Box health for the ops surface.

        Complements ``get_operational_metrics``: that returns aggregates
        ("4 stuck"), this returns the specific Boxes — which ones, in
        what stage, for how long, and what the exception signal is.
        Lets the team see the product breathing.

        The open/terminal/exception state sets come from the Box type
        registry so this view works for any registered Box type. AP is
        the default for backward compatibility.
        """
        from clearledgr.core import box_registry

        self.initialize()
        now = datetime.now(timezone.utc)

        bt = box_registry.get(box_type)
        open_states = set(bt.open_states)
        exception_states = set(bt.exception_states)

        items = self._list_boxes_for_health(box_type, organization_id, limit)

        # Map (box_id, state) → latest state-transition ts. Events are ts DESC
        # so the first hit per key is the most recent entry into that state.
        transitions = self.list_audit_events(
            organization_id,
            event_types=_state_transition_event_types(box_type),
            limit=20000,
        )
        latest_entry: Dict[tuple, datetime] = {}
        for evt in transitions:
            # Prefer the new generic column; fall back to the legacy one
            # for rows written before migration v42.
            bx_id = evt.get("box_id")
            new_state = evt.get("new_state")
            ts = self._parse_iso(evt.get("ts"))
            if not bx_id or not new_state or not ts:
                continue
            key = (bx_id, new_state)
            if key not in latest_entry:
                latest_entry[key] = ts

        stuck_boxes: List[Dict[str, Any]] = []
        time_in_stage_by_state: Dict[str, List[float]] = {}
        exception_by_state: Dict[str, Dict[str, Any]] = {}

        for item in items:
            state = str(item.get("state") or "")
            if state not in open_states:
                continue
            bx_id = item.get("id") or item.get("ap_item_id")
            entry_ts = (
                latest_entry.get((bx_id, state))
                or self._parse_iso(item.get("updated_at"))
                or self._parse_iso(item.get("created_at"))
            )
            if not entry_ts:
                continue
            tis_min = max(0.0, (now - entry_ts).total_seconds() / 60.0)
            time_in_stage_by_state.setdefault(state, []).append(tis_min)

            is_stuck = False
            stuck_reason: Optional[str] = None
            # AP-specific approval SLA override. For other box types,
            # the generic stuck threshold applies uniformly.
            if (
                box_type == "ap_item"
                and state == "needs_approval"
                and tis_min >= approval_sla_minutes
            ):
                is_stuck = True
                stuck_reason = "awaiting_approval_over_sla"
            elif tis_min >= stuck_threshold_minutes:
                is_stuck = True
                stuck_reason = f"stalled_in_{state}"

            if state in exception_states:
                bucket = exception_by_state.setdefault(state, {
                    "count": 0,
                    "sample_box_ids": [],
                    "sample_errors": [],
                })
                bucket["count"] += 1
                if len(bucket["sample_box_ids"]) < 5:
                    bucket["sample_box_ids"].append(str(bx_id))
                err = item.get("last_error")
                if err and len(bucket["sample_errors"]) < 5:
                    bucket["sample_errors"].append(str(err)[:120])

            if is_stuck:
                stuck_boxes.append({
                    "box_id": bx_id,
                    "box_type": box_type,
                    "vendor_name": item.get("vendor_name") or item.get("vendor"),
                    "amount": safe_float(item.get("amount"), 0.0),
                    "currency": item.get("currency") or "USD",
                    "state": state,
                    "time_in_stage_minutes": round(tis_min, 2),
                    "entered_stage_at": entry_ts.isoformat(),
                    "last_error": item.get("last_error"),
                    "stuck_reason": stuck_reason,
                })

        stuck_boxes.sort(key=lambda b: b["time_in_stage_minutes"], reverse=True)
        stuck_boxes = stuck_boxes[:limit]

        time_in_stage_summary: Dict[str, Dict[str, Any]] = {}
        for state, vals in time_in_stage_by_state.items():
            if not vals:
                continue
            time_in_stage_summary[state] = {
                "count": len(vals),
                "avg_minutes": round(sum(vals) / len(vals), 2),
                "max_minutes": round(max(vals), 2),
                "p95_minutes": round(self._p95(vals) or 0.0, 2),
            }

        exception_clusters = [
            {"state": state, **bucket}
            for state, bucket in sorted(
                exception_by_state.items(),
                key=lambda kv: kv[1]["count"],
                reverse=True,
            )
        ]

        return {
            "organization_id": organization_id,
            "box_type": box_type,
            "generated_at": now.isoformat(),
            "stuck_count": len(stuck_boxes),
            "stuck_boxes": stuck_boxes,
            "time_in_stage": time_in_stage_summary,
            "exception_clusters": exception_clusters,
            "thresholds": {
                "stuck_minutes": int(stuck_threshold_minutes),
                "approval_sla_minutes": int(approval_sla_minutes),
            },
        }

    def _list_boxes_for_health(
        self, box_type: str, organization_id: str, limit: int,
    ) -> List[Dict[str, Any]]:
        """Dispatch to the right store method for the given Box type."""
        from clearledgr.core import box_registry

        fetch_limit = max(limit, 1000)
        if box_type == "ap_item":
            return self.list_ap_items(organization_id, limit=fetch_limit)
        if box_type == "vendor_onboarding_session":
            bt = box_registry.get("vendor_onboarding_session")
            return self.list_pending_onboarding_sessions(
                organization_id,
                states=list(bt.open_states),
                limit=fetch_limit,
            )
        raise NotImplementedError(
            f"get_box_health has no lister for box_type={box_type!r}"
        )

    # ------------------------------------------------------------------
    # AP KPIs
    # ------------------------------------------------------------------

    def get_ap_kpis(
        self,
        organization_id: str,
        approval_sla_minutes: int = 240,
    ) -> Dict[str, Any]:
        self.initialize()
        now = datetime.now(timezone.utc)
        items = self.list_ap_items(organization_id, limit=10000)
        approvals = self.list_approvals(organization_id, limit=10000)

        approvals_by_item: Dict[str, List[Dict[str, Any]]] = {}
        for approval in approvals:
            ap_item_id = str(approval.get("ap_item_id") or "")
            if not ap_item_id:
                continue
            approvals_by_item.setdefault(ap_item_id, []).append(approval)

        completed_states = {"closed", "posted_to_erp"}
        completed_items = [item for item in items if str(item.get("state") or "") in completed_states]
        touchless_eligible = len(completed_items)
        touchless_count = 0
        cycle_times_hours: List[float] = []
        exception_count = 0
        discount_candidate_count = 0
        missed_discount_count = 0
        missed_discount_value = 0.0

        for item in items:
            metadata = self._decode_json(item.get("metadata"))
            item_id = str(item.get("id") or "")
            item_approvals = approvals_by_item.get(item_id, [])
            approval_required = bool(item.get("approval_required"))
            if str(item.get("state") or "") in completed_states:
                if (not approval_required) or not item_approvals:
                    touchless_count += 1
                created_at = self._parse_iso(item.get("created_at")) or self._parse_iso(item.get("updated_at"))
                completed_at = (
                    self._parse_iso(item.get("erp_posted_at"))
                    or self._parse_iso(item.get("updated_at"))
                    or now
                )
                if created_at and completed_at and completed_at >= created_at:
                    cycle_times_hours.append((completed_at - created_at).total_seconds() / 3600.0)

            if metadata.get("exception_code"):
                exception_count += 1

            discount = metadata.get("discount") or metadata.get("payment_discount") or {}
            if isinstance(discount, dict) and (
                discount.get("available") is True
                or discount.get("eligible") is True
                or discount.get("amount")
            ):
                discount_candidate_count += 1
                taken = bool(discount.get("taken"))
                deadline = self._parse_iso(discount.get("deadline") or discount.get("due_at"))
                missed = (not taken) and (
                    deadline is None
                    or deadline <= now
                    or str(item.get("state") or "") in completed_states
                )
                if missed:
                    missed_discount_count += 1
                    missed_discount_value += max(0.0, safe_float(discount.get("amount"), 0.0))

        approved_records = [record for record in approvals if str(record.get("status") or "") == "approved"]
        on_time_count = 0
        approval_latencies_hours: List[float] = []
        for approval in approved_records:
            created_at = self._parse_iso(approval.get("created_at"))
            approved_at = self._parse_iso(approval.get("approved_at"))
            if not created_at or not approved_at or approved_at < created_at:
                continue
            latency_hours = (approved_at - created_at).total_seconds() / 3600.0
            approval_latencies_hours.append(latency_hours)
            if latency_hours * 60.0 <= approval_sla_minutes:
                on_time_count += 1

        # Approval friction metrics (handoffs + wait + SLA breach pressure).
        handoff_counts: List[float] = []
        approval_wait_minutes: List[float] = []
        approval_population = 0
        sla_breach_count = 0
        channel_distribution: Dict[str, int] = {}

        for item in items:
            item_id = str(item.get("id") or "")
            if not item_id:
                continue

            item_approvals = approvals_by_item.get(item_id, [])
            needs_approval = bool(item.get("approval_required")) or bool(item_approvals)
            if not needs_approval:
                continue

            approval_population += 1

            if item_approvals:
                ordered = sorted(
                    item_approvals,
                    key=lambda entry: (
                        self._parse_iso(entry.get("created_at")) or datetime.fromtimestamp(0, tz=timezone.utc)
                    ),
                )
                channel_path: List[str] = []
                for entry in ordered:
                    channel = str(entry.get("source_channel") or entry.get("channel_id") or "unknown").strip()
                    if channel:
                        channel_distribution[channel] = channel_distribution.get(channel, 0) + 1
                        if not channel_path or channel_path[-1] != channel:
                            channel_path.append(channel)

                    created_at = self._parse_iso(entry.get("created_at"))
                    resolved_at = (
                        self._parse_iso(entry.get("approved_at"))
                        or self._parse_iso(entry.get("rejected_at"))
                    )
                    if created_at and resolved_at and resolved_at >= created_at:
                        approval_wait_minutes.append((resolved_at - created_at).total_seconds() / 60.0)

                handoff_counts.append(float(max(0, len(channel_path) - 1)))

                latest = ordered[-1]
                latest_created = self._parse_iso(latest.get("created_at"))
                latest_resolved = (
                    self._parse_iso(latest.get("approved_at"))
                    or self._parse_iso(latest.get("rejected_at"))
                )
                anchor = latest_resolved or now
                if latest_created and anchor and (anchor - latest_created).total_seconds() / 60.0 > approval_sla_minutes:
                    sla_breach_count += 1
            else:
                handoff_counts.append(0.0)
                created_at = self._parse_iso(item.get("created_at")) or self._parse_iso(item.get("updated_at"))
                if created_at:
                    open_wait = max(0.0, (now - created_at).total_seconds() / 60.0)
                    approval_wait_minutes.append(open_wait)
                    if str(item.get("state") or "") == "needs_approval" and open_wait > approval_sla_minutes:
                        sla_breach_count += 1

        # Agentic telemetry (AX6): derive transparent, operator-facing metrics
        # from existing AP, approval, audit, and browser-agent records.
        human_intervention_count = max(0, touchless_eligible - touchless_count)

        approval_override_count = 0
        approval_override_breakdown = {
            "budget": 0,
            "confidence": 0,
            "po_exception": 0,
            "other": 0,
        }
        approval_decision_population = 0
        for approval in approvals:
            status = str(approval.get("status") or "").strip().lower()
            if status not in {"approved", "rejected", "needs_info", "failed"}:
                continue
            approval_decision_population += 1
            payload = self._decode_json_any(approval.get("decision_payload"))
            payload_dict = payload if isinstance(payload, dict) else {}
            decision = str(payload_dict.get("decision") or "").strip().lower()
            budget_override = self._coerce_bool(payload_dict.get("budget_override"))
            confidence_override = self._coerce_bool(payload_dict.get("confidence_override"))
            po_override = self._coerce_bool(payload_dict.get("po_override")) or bool(
                str(payload_dict.get("po_override_reason") or "").strip()
            )
            is_override = (
                decision == "approve_override"
                or budget_override
                or confidence_override
                or po_override
            )
            if not is_override:
                continue
            approval_override_count += 1
            bucketed = False
            if budget_override:
                approval_override_breakdown["budget"] += 1
                bucketed = True
            if confidence_override:
                approval_override_breakdown["confidence"] += 1
                bucketed = True
            if po_override:
                approval_override_breakdown["po_exception"] += 1
                bucketed = True
            if not bucketed:
                approval_override_breakdown["other"] += 1

        shadow_audit_events = self.list_audit_events(
            organization_id,
            event_types=[
                "field_correction",
                "erp_post_attempted",
                "erp_post_succeeded",
                "erp_post_failed",
            ],
            limit=20000,
        )
        audit_events_by_item: Dict[str, List[Dict[str, Any]]] = {}
        for event in shadow_audit_events:
            # Only AP-Box audit events contribute to AP metrics.
            if str(event.get("box_type") or "") != "ap_item":
                continue
            item_id = str(event.get("box_id") or "").strip()
            if not item_id:
                continue
            audit_events_by_item.setdefault(item_id, []).append(event)

        blocker_category_counts: Dict[str, int] = {
            "confidence": 0,
            "policy": 0,
            "budget": 0,
            "erp": 0,
            "other": 0,
        }
        blocker_reason_counts: Dict[str, int] = {}
        blocker_open_population = 0
        open_states = {"received", "validated", "needs_info", "needs_approval", "pending_approval", "approved", "ready_to_post", "failed_post"}

        def _inc_reason(reason: str) -> None:
            text = str(reason or "").strip().lower()
            if not text:
                return
            blocker_reason_counts[text] = blocker_reason_counts.get(text, 0) + 1

        for item in items:
            state = str(item.get("state") or "").strip().lower()
            if state not in open_states:
                continue
            blocker_open_population += 1

            metadata = self._decode_json_any(item.get("metadata"))
            metadata_dict = metadata if isinstance(metadata, dict) else {}
            categories_for_item = set()

            confidence_blockers_raw = (
                item.get("confidence_blockers")
                if item.get("confidence_blockers") is not None
                else metadata_dict.get("confidence_blockers")
            )
            confidence_blockers = self._decode_json_any(confidence_blockers_raw)
            if not isinstance(confidence_blockers, list):
                confidence_blockers = []
            requires_field_review = self._coerce_bool(
                item.get("requires_field_review")
                if item.get("requires_field_review") is not None
                else metadata_dict.get("requires_field_review")
            )
            if requires_field_review or confidence_blockers:
                categories_for_item.add("confidence")
                if confidence_blockers:
                    for blocker in confidence_blockers[:6]:
                        if isinstance(blocker, dict):
                            field = str(blocker.get("field") or blocker.get("code") or "critical_field").strip().lower()
                            _inc_reason(f"confidence:{field or 'critical_field'}")
                        else:
                            _inc_reason(f"confidence:{str(blocker).strip().lower() or 'critical_field'}")
                else:
                    _inc_reason("confidence:field_review_required")

            budget_status = str(
                item.get("budget_status")
                or metadata_dict.get("budget_status")
                or (metadata_dict.get("budget_summary") or {}).get("status")
                or ""
            ).strip().lower()
            budget_requires_decision = self._coerce_bool(
                item.get("budget_requires_decision")
                if item.get("budget_requires_decision") is not None
                else metadata_dict.get("budget_requires_decision")
            )
            if budget_requires_decision or budget_status in {"critical", "exceeded"}:
                categories_for_item.add("budget")
                _inc_reason(f"budget:{budget_status or 'requires_decision'}")

            validation_gate = metadata_dict.get("validation_gate")
            if not isinstance(validation_gate, dict):
                validation_gate = {}
            reason_codes = validation_gate.get("reason_codes")
            if not isinstance(reason_codes, list):
                reason_codes = []
            policy_codes = [
                str(code or "").strip().lower()
                for code in reason_codes
                if str(code or "").strip()
                and (
                    str(code).strip().lower().startswith("policy_")
                    or str(code).strip().lower().startswith("po_")
                    or "policy" in str(code).strip().lower()
                )
            ]
            if policy_codes:
                categories_for_item.add("policy")
                for code in policy_codes[:6]:
                    _inc_reason(f"policy:{code}")

            exception_code = str(item.get("exception_code") or metadata_dict.get("exception_code") or "").strip().lower()
            next_action = str(item.get("next_action") or "").strip().lower()
            last_error = str(item.get("last_error") or metadata_dict.get("last_error") or "").strip().lower()
            if (
                state == "failed_post"
                or next_action == "retry_posting"
                or exception_code.startswith("erp_")
                or "erp" in exception_code
                or "erp" in last_error
            ):
                categories_for_item.add("erp")
                _inc_reason(f"erp:{exception_code or next_action or last_error or 'posting_failure'}")

            if state == "needs_info":
                _inc_reason("other:needs_info")
                if not categories_for_item:
                    categories_for_item.add("other")

            if not categories_for_item and exception_count and exception_code:
                categories_for_item.add("other")
                _inc_reason(f"other:{exception_code}")

            for category in categories_for_item:
                blocker_category_counts[category] = blocker_category_counts.get(category, 0) + 1

        top_blocker_reasons = [
            {"reason": reason, "count": count}
            for reason, count in sorted(
                blocker_reason_counts.items(),
                key=lambda pair: (-pair[1], pair[0]),
            )[:7]
        ]

        approval_wait_avg_minutes = round(sum(approval_wait_minutes) / len(approval_wait_minutes), 2) if approval_wait_minutes else 0.0
        approval_wait_p95_minutes = round(self._p95(approval_wait_minutes) or 0.0, 2)
        extraction_drift_metrics = self._build_extraction_drift_metrics(items, now=now)
        shadow_decision_metrics = self._build_shadow_decision_metrics(
            items,
            approvals_by_item=approvals_by_item,
            audit_events_by_item=audit_events_by_item,
        )
        post_action_verification_metrics = self._build_post_action_verification_metrics(
            items,
            audit_events_by_item=audit_events_by_item,
        )
        pilot_window_days = 30
        pilot_cutoff = now - timedelta(days=pilot_window_days)
        human_override_decision_population = 0
        for item in items:
            metadata = self._decode_json_any(item.get("metadata"))
            metadata_dict = metadata if isinstance(metadata, dict) else {}
            created_at = self._parse_iso(item.get("created_at")) or self._parse_iso(item.get("updated_at"))
            if created_at and created_at < pilot_cutoff:
                continue
            if str(metadata_dict.get("ap_decision_recommendation") or "").strip():
                human_override_decision_population += 1
        operator_event_rows = self.list_audit_events(
            organization_id,
            event_types=[
                "approval_escalation_sent",
                "approval_reassigned",
                "entity_route_resolved",
                "ap_decision_override",
            ],
            limit=20000,
        )
        approval_escalation_event_count = 0
        approval_reassignment_event_count = 0
        entity_route_resolution_event_count = 0
        human_override_event_count = 0
        for event in operator_event_rows:
            event_ts = self._parse_iso(event.get("ts"))
            if not event_ts or event_ts < pilot_cutoff:
                continue
            event_type = str(event.get("event_type") or "").strip().lower()
            if event_type == "approval_escalation_sent":
                approval_escalation_event_count += 1
            elif event_type == "approval_reassigned":
                approval_reassignment_event_count += 1
            elif event_type == "entity_route_resolved":
                entity_route_resolution_event_count += 1
            elif event_type == "ap_decision_override":
                human_override_event_count += 1

        approval_queue_count = 0
        approval_sla_breached_open_count = 0
        approval_escalated_open_count = 0
        approval_reassigned_open_count = 0
        entity_route_needs_review_count = 0
        entity_route_resolved_count = 0
        entity_route_single_candidate_resolved_count = 0
        field_review_open_count = 0
        invoice_population = 0
        approval_open_states = {"needs_approval", "pending_approval"}

        for item in items:
            state = str(item.get("state") or "").strip().lower()
            metadata = self._decode_json_any(item.get("metadata"))
            metadata_dict = metadata if isinstance(metadata, dict) else {}
            document_type = str(
                item.get("document_type")
                or item.get("email_type")
                or metadata_dict.get("document_type")
                or metadata_dict.get("email_type")
                or "invoice"
            ).strip().lower() or "invoice"

            if state in open_states and self._coerce_bool(
                item.get("requires_field_review")
                if item.get("requires_field_review") is not None
                else metadata_dict.get("requires_field_review")
            ):
                field_review_open_count += 1

            if document_type == "invoice":
                invoice_population += 1
                entity_routing = resolve_entity_routing(metadata_dict, item)
                routing_status = str(entity_routing.get("status") or "").strip().lower()
                if routing_status == "needs_review" and state in open_states:
                    entity_route_needs_review_count += 1
                elif routing_status == "resolved":
                    entity_route_resolved_count += 1
                    candidates = entity_routing.get("candidates") if isinstance(entity_routing.get("candidates"), list) else []
                    selected = entity_routing.get("selected") if isinstance(entity_routing.get("selected"), dict) else {}
                    if selected and len(candidates) <= 1:
                        entity_route_single_candidate_resolved_count += 1

            if state not in approval_open_states:
                continue

            approval_queue_count += 1
            if metadata_dict.get("approval_last_escalated_at") or int(metadata_dict.get("approval_escalation_count") or 0) > 0:
                approval_escalated_open_count += 1
            if metadata_dict.get("approval_last_reassigned_at") or int(metadata_dict.get("approval_reassignment_count") or 0) > 0:
                approval_reassigned_open_count += 1

            requested_at = (
                self._parse_iso(item.get("approval_requested_at"))
                or self._parse_iso(metadata_dict.get("approval_requested_at"))
            )
            if requested_at is None:
                item_approvals = approvals_by_item.get(str(item.get("id") or ""), [])
                if item_approvals:
                    approval_created_ats = [
                        created_at
                        for created_at in (
                            self._parse_iso(entry.get("created_at"))
                            for entry in item_approvals
                        )
                        if created_at is not None
                    ]
                    requested_at = max(approval_created_ats) if approval_created_ats else None
            if requested_at and (now - requested_at).total_seconds() / 60.0 >= approval_sla_minutes:
                approval_sla_breached_open_count += 1

        operator_metrics = {
            "definitions": {
                "approval_queue_count": "Open invoices currently waiting on approval.",
                "approval_sla_breached_open_count": "Open approval items whose current approval wait exceeds the configured SLA.",
                "approval_escalated_open_count": "Open approval items that have already been escalated at least once.",
                "approval_reassigned_open_count": "Open approval items that have already been reassigned at least once.",
                "entity_route_needs_review_count": "Open invoice items still blocked on manual entity routing review.",
                "field_review_open_count": "Open invoice items still blocked on field-review confirmation.",
            },
            "live_queue": {
                "approval_queue_count": int(approval_queue_count),
                "approval_sla_breached_open_count": int(approval_sla_breached_open_count),
                "approval_escalated_open_count": int(approval_escalated_open_count),
                "approval_reassigned_open_count": int(approval_reassigned_open_count),
                "entity_route_needs_review_count": int(entity_route_needs_review_count),
                "field_review_open_count": int(field_review_open_count),
            },
            "queue_rates": {
                "approval_sla_breached_open_rate": round(
                    (approval_sla_breached_open_count / approval_queue_count) if approval_queue_count else 0.0,
                    4,
                ),
                "entity_route_needs_review_rate": round(
                    (entity_route_needs_review_count / invoice_population) if invoice_population else 0.0,
                    4,
                ),
            },
            "activity_window_days": int(pilot_window_days),
            "activity": {
                "approval_escalation_event_count": int(approval_escalation_event_count),
                "approval_reassignment_event_count": int(approval_reassignment_event_count),
                "entity_route_resolution_event_count": int(entity_route_resolution_event_count),
            },
        }

        touchless_rate = round((touchless_count / touchless_eligible) if touchless_eligible else 0.0, 4)
        human_intervention_rate = round((human_intervention_count / touchless_eligible) if touchless_eligible else 0.0, 4)
        on_time_approval_rate = round((on_time_count / len(approved_records)) if approved_records else 0.0, 4)
        avg_cycle_time_hours = round(sum(cycle_times_hours) / len(cycle_times_hours), 2) if cycle_times_hours else 0.0
        avg_approval_wait_hours = round(approval_wait_avg_minutes / 60.0, 2)
        approval_sla_hours = round(float(approval_sla_minutes) / 60.0, 2)

        pilot_highlights: List[str] = []
        if approval_sla_breached_open_count > 0:
            pilot_highlights.append(
                f"{approval_sla_breached_open_count} approvals are currently beyond the {approval_sla_hours:g}-hour SLA."
            )
        if entity_route_needs_review_count > 0:
            pilot_highlights.append(
                f"{entity_route_needs_review_count} invoices are waiting on manual entity routing review."
            )
        if approval_escalation_event_count > 0:
            pilot_highlights.append(
                f"{approval_escalation_event_count} approval escalations were sent in the last {pilot_window_days} days."
            )
        if touchless_eligible > 0:
            pilot_highlights.append(
                f"{round(touchless_rate * 100.0, 1):.1f}% of completed invoices ran touchless."
            )

        pilot_scorecard = {
            "window_days": int(pilot_window_days),
            "summary": {
                "touchless_rate_pct": round(touchless_rate * 100.0, 2),
                "human_intervention_rate_pct": round(human_intervention_rate * 100.0, 2),
                "avg_cycle_time_hours": float(avg_cycle_time_hours),
                "on_time_approvals_pct": round(on_time_approval_rate * 100.0, 2),
                "avg_approval_wait_hours": float(avg_approval_wait_hours),
                "approval_sla_breached_open_count": int(approval_sla_breached_open_count),
                "entity_route_needs_review_count": int(entity_route_needs_review_count),
            },
            "automation": {
                "completed_item_count": int(touchless_eligible),
                "touchless_count": int(touchless_count),
                "touchless_rate": float(touchless_rate),
                "human_intervention_count": int(human_intervention_count),
                "human_intervention_rate": float(human_intervention_rate),
            },
            "approval_workflow": {
                "population_count": int(approval_population),
                "avg_wait_hours": float(avg_approval_wait_hours),
                "p95_wait_hours": round(approval_wait_p95_minutes / 60.0, 2),
                "sla_hours": float(approval_sla_hours),
                "on_time_rate": float(on_time_approval_rate),
                "on_time_rate_pct": round(on_time_approval_rate * 100.0, 2),
                "sla_breached_open_count": int(approval_sla_breached_open_count),
                "escalated_open_count": int(approval_escalated_open_count),
                "reassigned_open_count": int(approval_reassigned_open_count),
                "escalation_event_count_30d": int(approval_escalation_event_count),
                "reassignment_event_count_30d": int(approval_reassignment_event_count),
            },
            "entity_routing": {
                "invoice_population": int(invoice_population),
                "needs_review_open_count": int(entity_route_needs_review_count),
                "resolved_count": int(entity_route_resolved_count),
                "single_candidate_resolved_count": int(entity_route_single_candidate_resolved_count),
                "manual_resolution_event_count_30d": int(entity_route_resolution_event_count),
            },
            "highlights": pilot_highlights[:4],
        }

        post_verification_summary = (
            post_action_verification_metrics.get("summary")
            if isinstance(post_action_verification_metrics, dict)
            else {}
        )
        post_verification_summary = post_verification_summary if isinstance(post_verification_summary, dict) else {}
        escalation_rate = round(
            (approval_escalation_event_count / approval_population) if approval_population else 0.0,
            4,
        )
        human_override_rate = round(
            (human_override_event_count / human_override_decision_population)
            if human_override_decision_population
            else 0.0,
            4,
        )
        proof_highlights: List[str] = []
        if post_verification_summary.get("attempted_count"):
            proof_highlights.append(
                f"{round(float(post_verification_summary.get('success_rate') or 0.0) * 100.0, 1):.1f}% of ERP posting attempts finished with a success event."
            )
        if post_verification_summary.get("recovery_attempt_count"):
            proof_highlights.append(
                f"{round(float(post_verification_summary.get('recovery_rate') or 0.0) * 100.0, 1):.1f}% of failed ERP posts recovered successfully."
            )
        if human_override_decision_population:
            proof_highlights.append(
                f"{round(human_override_rate * 100.0, 1):.1f}% of Claude recommendation windows ended in a human override."
            )
        if approval_population:
            proof_highlights.append(
                f"{round(escalation_rate * 100.0, 1):.1f}% of approval cases required escalation in the last {pilot_window_days} days."
            )

        proof_scorecard = {
            "window_days": int(pilot_window_days),
            "summary": {
                "auto_approved_rate_pct": round(touchless_rate * 100.0, 2),
                "human_override_rate_pct": round(human_override_rate * 100.0, 2),
                "avg_approval_wait_hours": float(avg_approval_wait_hours),
                "escalation_rate_pct": round(escalation_rate * 100.0, 2),
                "posting_success_rate_pct": round(float(post_verification_summary.get("success_rate") or 0.0) * 100.0, 2),
                "recovery_success_rate_pct": round(float(post_verification_summary.get("recovery_rate") or 0.0) * 100.0, 2),
            },
            "automation": {
                "completed_item_count": int(touchless_eligible),
                "touchless_count": int(touchless_count),
                "touchless_rate": float(touchless_rate),
            },
            "decisions": {
                "decision_count": int(human_override_decision_population),
                "human_override_count": int(human_override_event_count),
                "human_override_rate": float(human_override_rate),
            },
            "approval_followup": {
                "population_count": int(approval_population),
                "avg_wait_hours": float(avg_approval_wait_hours),
                "escalation_event_count_30d": int(approval_escalation_event_count),
                "escalation_rate": float(escalation_rate),
            },
            "posting_reliability": {
                "attempted_count": int(post_verification_summary.get("attempted_count") or 0),
                "success_count": int(post_verification_summary.get("success_event_count") or 0),
                "success_rate": float(post_verification_summary.get("success_rate") or 0.0),
                "verified_count": int(post_verification_summary.get("verified_count") or 0),
                "mismatch_count": int(post_verification_summary.get("mismatch_count") or 0),
            },
            "recovery": {
                "attempted_count": int(post_verification_summary.get("recovery_attempt_count") or 0),
                "recovered_count": int(post_verification_summary.get("recovered_count") or 0),
                "recovery_rate": float(post_verification_summary.get("recovery_rate") or 0.0),
            },
            "highlights": proof_highlights[:4],
        }

        total_items = len(items)

        # DESIGN_THESIS §11 success metric #4 — vendor activation SLA.
        # "A new vendor went from invited to active in under five
        # business days." Compute over the trailing 30 days so the
        # metric reflects current operations, not the full history.
        vendor_activation_sla = self._compute_vendor_activation_sla(
            organization_id=organization_id,
            now=now,
            window_days=30,
        )

        return {
            "organization_id": organization_id,
            "generated_at": now.isoformat(),
            "totals": {
                "items": total_items,
                "completed_items": touchless_eligible,
                "approved_records": len(approved_records),
            },
            "touchless_rate": {
                "eligible_count": touchless_eligible,
                "touchless_count": touchless_count,
                "rate": round((touchless_count / touchless_eligible) if touchless_eligible else 0.0, 4),
            },
            "vendor_activation_sla": vendor_activation_sla,
            "cycle_time_hours": {
                "count": len(cycle_times_hours),
                "avg": round(sum(cycle_times_hours) / len(cycle_times_hours), 2) if cycle_times_hours else 0.0,
                "median": round(self._percentile(cycle_times_hours, 0.5) or 0.0, 2),
                "p95": round(self._p95(cycle_times_hours) or 0.0, 2),
            },
            "exception_rate": {
                "exception_count": exception_count,
                "rate": round((exception_count / total_items) if total_items else 0.0, 4),
            },
            "on_time_approvals": {
                "sla_minutes": int(approval_sla_minutes),
                "approved_count": len(approved_records),
                "on_time_count": on_time_count,
                "rate": round((on_time_count / len(approved_records)) if approved_records else 0.0, 4),
                "avg_latency_hours": round(sum(approval_latencies_hours) / len(approval_latencies_hours), 2)
                if approval_latencies_hours
                else 0.0,
            },
            "missed_discounts_baseline": {
                "candidate_count": discount_candidate_count,
                "missed_count": missed_discount_count,
                "missed_value": round(missed_discount_value, 2),
            },
            "approval_friction": {
                "population_count": int(approval_population),
                "avg_handoffs": round(sum(handoff_counts) / len(handoff_counts), 2) if handoff_counts else 0.0,
                "max_handoffs": int(max(handoff_counts) if handoff_counts else 0),
                "avg_wait_minutes": round(sum(approval_wait_minutes) / len(approval_wait_minutes), 2)
                if approval_wait_minutes
                else 0.0,
                "p95_wait_minutes": round(self._p95(approval_wait_minutes) or 0.0, 2),
                "sla_minutes": int(approval_sla_minutes),
                "sla_breach_count": int(sla_breach_count),
                "sla_breach_rate": round(
                    (sla_breach_count / approval_population) if approval_population else 0.0,
                    4,
                ),
                "channel_distribution": channel_distribution,
            },
            "agentic_telemetry": {
                "definitions": {
                    "straight_through_rate": "completed invoices with no approval handoff record (proxy for touchless AP flow)",
                    "human_intervention_rate": "completed invoices that were not straight-through",
                    "awaiting_approval_time_hours": "approval wait time derived from approval records and open approval items",
                    "approval_override_rate": "approval decisions that used budget/confidence/PO override semantics",
                    "top_blocker_reasons": "open-item blocker categories/reasons derived from AP item state, validation gates, confidence blockers, and ERP failures",
                    "extraction_drift": "vendor-level review-rate, conflict-rate, and provenance-shift scorecards with sampled review recommendations",
                    "shadow_decision_scoring": "agreement between persisted shadow proposals and final/operator truth for action path and critical fields",
                    "post_action_verification": "verification that ERP post outcomes reconciled with terminal AP state and ERP references",
                },
                "straight_through_rate": {
                    "eligible_count": int(touchless_eligible),
                    "count": int(touchless_count),
                    "rate": round((touchless_count / touchless_eligible) if touchless_eligible else 0.0, 4),
                },
                "human_intervention_rate": {
                    "eligible_count": int(touchless_eligible),
                    "count": int(human_intervention_count),
                    "rate": round((human_intervention_count / touchless_eligible) if touchless_eligible else 0.0, 4),
                },
                "awaiting_approval_time_hours": {
                    "population_count": int(approval_population),
                    "avg": round(approval_wait_avg_minutes / 60.0, 2),
                    "p95": round(approval_wait_p95_minutes / 60.0, 2),
                    "sla_hours": round(float(approval_sla_minutes) / 60.0, 2),
                },
                "approval_override_rate": {
                    "decision_population": int(approval_decision_population),
                    "override_count": int(approval_override_count),
                    "rate": round((approval_override_count / approval_decision_population) if approval_decision_population else 0.0, 4),
                    "breakdown": approval_override_breakdown,
                },
                "top_blocker_reasons": {
                    "open_population": int(blocker_open_population),
                    "by_category": blocker_category_counts,
                    "top_reasons": top_blocker_reasons,
                },
                "extraction_drift": extraction_drift_metrics,
                "shadow_decision_scoring": shadow_decision_metrics,
                "post_action_verification": post_action_verification_metrics,
            },
            "operator_metrics": operator_metrics,
            "pilot_scorecard": pilot_scorecard,
            "proof_scorecard": proof_scorecard,
        }

    # ------------------------------------------------------------------
    # DESIGN_THESIS §11 — vendor-activation SLA metric
    # ------------------------------------------------------------------

    def _compute_vendor_activation_sla(
        self,
        *,
        organization_id: str,
        now: datetime,
        window_days: int = 30,
        sla_business_days: int = 5,
    ) -> Dict[str, Any]:
        """Measure the trailing-window vendor-activation time against
        the §11 ≤5-business-day SLA.

        Returns a stable-shape dict even when there are no activations
        in the window, so digest builders don't need to branch on the
        empty case — they can render "0 activations in the last 30
        days" directly from this payload.
        """
        from clearledgr.core.business_days import business_days_from_iso

        since_dt = now - timedelta(days=window_days)
        since_iso = since_dt.isoformat()

        completed: List[Dict[str, Any]] = []
        try:
            if hasattr(self, "list_completed_onboarding_sessions"):
                completed = self.list_completed_onboarding_sessions(
                    organization_id, since_iso=since_iso, limit=500,
                ) or []
        except Exception as exc:
            logger.debug("[metrics] list_completed_onboarding_sessions failed: %s", exc)

        bd_values: List[int] = []
        within_sla = 0
        for sess in completed:
            invited = str(sess.get("invited_at") or "")
            activated = str(sess.get("erp_activated_at") or "")
            if not invited or not activated:
                continue
            bd = business_days_from_iso(invited, activated)
            bd_values.append(bd)
            if bd <= sla_business_days:
                within_sla += 1

        activation_count = len(bd_values)
        avg_bd = round(sum(bd_values) / activation_count, 2) if activation_count else 0.0
        within_sla_rate = round(
            within_sla / activation_count, 4
        ) if activation_count else 0.0

        return {
            "window_days": int(window_days),
            "sla_business_days": int(sla_business_days),
            "activation_count": activation_count,
            "avg_business_days_to_active": avg_bd,
            "within_sla_count": within_sla,
            "within_sla_pct": round(within_sla_rate * 100.0, 2),
        }

    # ------------------------------------------------------------------
    # AP aggregation metrics
    # ------------------------------------------------------------------

    def get_ap_aggregation_metrics(
        self,
        organization_id: str,
        limit: int = 10000,
        vendor_limit: int = 10,
    ) -> Dict[str, Any]:
        """Return multi-system AP aggregation metrics for embedded surfaces."""
        self.initialize()
        safe_limit = max(100, min(int(limit or 10000), 50000))
        safe_vendor_limit = max(1, min(int(vendor_limit or 10), 50))
        now = datetime.now(timezone.utc)

        items = self.list_ap_items(organization_id, limit=safe_limit)
        source_sql = self._prepare_sql(
            """
            SELECT s.ap_item_id, s.source_type, COUNT(*) AS link_count
            FROM ap_item_sources s
            JOIN ap_items i ON i.id = s.ap_item_id
            WHERE i.organization_id = ?
            GROUP BY s.ap_item_id, s.source_type
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(source_sql, (organization_id,))
            source_rows = cur.fetchall()

        open_states = {"received", "validated", "needs_info", "needs_approval", "pending_approval", "approved", "ready_to_post"}
        spend_by_vendor: Dict[str, Dict[str, Any]] = {}
        invoice_numbers: Dict[str, List[str]] = {}
        amount_unavailable = 0
        total_amount = 0.0
        open_items = 0

        for item in items:
            state = str(item.get("state") or "").strip().lower()
            if state in open_states:
                open_items += 1

            amount = safe_float(item.get("amount"), 0.0)
            if amount <= 0:
                amount_unavailable += 1
            else:
                total_amount += amount

            vendor = str(item.get("vendor_name") or "Unknown").strip() or "Unknown"
            bucket = spend_by_vendor.setdefault(
                vendor,
                {"vendor_name": vendor, "invoice_count": 0, "open_count": 0, "total_amount": 0.0},
            )
            bucket["invoice_count"] += 1
            if state in open_states:
                bucket["open_count"] += 1
            bucket["total_amount"] += amount

            invoice_number = str(item.get("invoice_number") or "").strip().lower()
            if invoice_number:
                invoice_numbers.setdefault(invoice_number, []).append(str(item.get("id") or ""))

        vendor_rows = sorted(
            spend_by_vendor.values(),
            key=lambda row: (float(row.get("total_amount") or 0.0), int(row.get("invoice_count") or 0)),
            reverse=True,
        )[:safe_vendor_limit]
        for row in vendor_rows:
            row["total_amount"] = round(float(row.get("total_amount") or 0.0), 2)

        duplicate_clusters = [
            {"invoice_number": key, "item_ids": ids, "count": len(ids)}
            for key, ids in invoice_numbers.items()
            if len(ids) > 1
        ]
        duplicate_clusters.sort(key=lambda row: int(row.get("count") or 0), reverse=True)
        duplicate_count = sum(max(0, int(cluster.get("count", 0)) - 1) for cluster in duplicate_clusters)

        source_type_counts: Dict[str, int] = {}
        source_items_by_type: Dict[str, set] = {}
        source_count_by_item: Dict[str, int] = {}
        total_source_links = 0
        for raw_row in source_rows:
            row = dict(raw_row)
            ap_item_id = str(row.get("ap_item_id") or "")
            source_type = str(row.get("source_type") or "unknown")
            link_count = int(row.get("link_count") or 0)
            if not ap_item_id or link_count <= 0:
                continue
            total_source_links += link_count
            source_type_counts[source_type] = source_type_counts.get(source_type, 0) + link_count
            source_items_by_type.setdefault(source_type, set()).add(ap_item_id)
            source_count_by_item[ap_item_id] = source_count_by_item.get(ap_item_id, 0) + link_count

        items_with_sources = len(source_count_by_item)
        avg_source_links = round(total_source_links / len(items), 2) if items else 0.0
        avg_source_links_nonzero = round(total_source_links / items_with_sources, 2) if items_with_sources else 0.0

        connected_systems = [
            source_type
            for source_type, count in sorted(source_type_counts.items(), key=lambda pair: pair[0])
            if count > 0
        ]

        return {
            "organization_id": organization_id,
            "generated_at": now.isoformat(),
            "totals": {
                "items": len(items),
                "open_items": int(open_items),
                "total_amount": round(total_amount, 2),
                "amount_unavailable_count": int(amount_unavailable),
            },
            "sources": {
                "total_links": int(total_source_links),
                "items_with_sources": int(items_with_sources),
                "avg_links_per_item": avg_source_links,
                "avg_links_per_linked_item": avg_source_links_nonzero,
                "link_count_by_type": source_type_counts,
                "linked_items_by_type": {
                    source_type: len(item_ids) for source_type, item_ids in source_items_by_type.items()
                },
                "connected_systems": connected_systems,
            },
            "duplicates": {
                "duplicate_invoice_count": int(duplicate_count),
                "cluster_count": len(duplicate_clusters),
                "top_clusters": duplicate_clusters[:10],
            },
            "spend_by_vendor": vendor_rows,
        }

