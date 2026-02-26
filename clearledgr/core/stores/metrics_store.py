"""Metrics and KPI data-access mixin for ClearledgrDB.

``MetricsStore`` is a **mixin class** -- it has no ``__init__`` of its own and
expects the concrete class that inherits it to provide:

* ``self.connect()``                       -- returns a DB connection (context manager)
* ``self._prepare_sql()``                  -- adapts ``?`` placeholders for the active engine
* ``self.initialize()``                    -- ensures tables exist
* ``self._decode_json()``                  -- safely parses a JSON string or returns ``{}``
* ``self._safe_float()``                   -- safe float coercion
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

logger = logging.getLogger(__name__)


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
        sql = "SELECT * FROM audit_events WHERE organization_id = ?"
        if event_types:
            placeholders = ",".join("?" for _ in event_types)
            sql += f" AND event_type IN ({placeholders})"
            params.extend(event_types)
        sql += " ORDER BY ts DESC LIMIT ?"
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

        cutoff = now - timedelta(hours=24)
        attempted_24h = 0
        failed_24h = 0
        for event in post_events:
            ts = self._parse_iso(event.get("ts"))
            if not ts or ts < cutoff:
                continue
            if event.get("event_type") == "erp_post_attempted":
                attempted_24h += 1
            elif event.get("event_type") == "erp_post_failed":
                failed_24h += 1

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
                    missed_discount_value += max(0.0, self._safe_float(discount.get("amount"), 0.0))

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

        browser_metrics_window_hours = 24 * 7
        try:
            browser_metrics = self.get_browser_agent_metrics(
                organization_id=organization_id,
                window_hours=browser_metrics_window_hours,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to compute browser agent metrics for AX6 KPI bundle: %s", exc)
            browser_metrics = {
                "window_hours": browser_metrics_window_hours,
                "api_first_routing": {"attempt_count": 0, "fallback_requested_count": 0, "fallback_rate": 0.0},
                "human_control": {
                    "manual_override_required_count": 0,
                    "manual_override_required_rate": 0.0,
                    "suggestion_accepted_count": 0,
                    "suggestion_acceptance_rate": 0.0,
                },
                "totals": {"events": 0},
            }

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
        browser_human_control = browser_metrics.get("human_control") if isinstance(browser_metrics, dict) else {}
        browser_routing = browser_metrics.get("api_first_routing") if isinstance(browser_metrics, dict) else {}

        total_items = len(items)
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
                "window_hours": int(browser_metrics.get("window_hours") or browser_metrics_window_hours) if isinstance(browser_metrics, dict) else browser_metrics_window_hours,
                "definitions": {
                    "straight_through_rate": "completed invoices with no approval handoff record (proxy for touchless AP flow)",
                    "human_intervention_rate": "completed invoices that were not straight-through",
                    "awaiting_approval_time_hours": "approval wait time derived from approval records and open approval items",
                    "erp_browser_fallback_rate": "fallback_requested / erp_api_attempt within window",
                    "agent_suggestion_acceptance": "browser_command_confirmed / browser actions requiring confirmation within window",
                    "agent_actions_requiring_manual_override": "browser actions requiring confirmation / total browser actions within window",
                    "approval_override_rate": "approval decisions that used budget/confidence/PO override semantics",
                    "top_blocker_reasons": "open-item blocker categories/reasons derived from AP item state, validation gates, confidence blockers, and ERP failures",
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
                "erp_browser_fallback_rate": {
                    "attempt_count": int(browser_routing.get("attempt_count") or 0),
                    "fallback_requested_count": int(browser_routing.get("fallback_requested_count") or 0),
                    "rate": round(float(browser_routing.get("fallback_rate") or 0.0), 4),
                },
                "agent_suggestion_acceptance": {
                    "prompted_count": int(browser_human_control.get("manual_override_required_count") or 0),
                    "accepted_count": int(browser_human_control.get("suggestion_accepted_count") or 0),
                    "rate": round(float(browser_human_control.get("suggestion_acceptance_rate") or 0.0), 4),
                },
                "agent_actions_requiring_manual_override": {
                    "total_actions": int((browser_metrics.get("totals") or {}).get("events") or 0) if isinstance(browser_metrics, dict) else 0,
                    "count": int(browser_human_control.get("manual_override_required_count") or 0),
                    "rate": round(float(browser_human_control.get("manual_override_required_rate") or 0.0), 4),
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
            },
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

            amount = self._safe_float(item.get("amount"), 0.0)
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

    # ------------------------------------------------------------------
    # Browser agent metrics
    # ------------------------------------------------------------------

    def get_browser_agent_metrics(
        self,
        organization_id: str,
        window_hours: int = 24,
    ) -> Dict[str, Any]:
        self.initialize()
        now = datetime.now(timezone.utc)
        safe_window_hours = max(1, int(window_hours or 24))
        window_start = now - timedelta(hours=safe_window_hours)

        sql = self._prepare_sql(
            "SELECT * FROM browser_action_events WHERE organization_id = ? ORDER BY created_at DESC LIMIT ?"
        )
        audit_sql = self._prepare_sql(
            "SELECT event_type, ts FROM audit_events WHERE organization_id = ? "
            "AND event_type IN ('erp_api_attempt', 'erp_api_success', 'erp_api_fallback_requested', 'erp_api_failed', 'browser_command_confirmed') "
            "ORDER BY ts DESC LIMIT ?"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, 5000))
            rows = cur.fetchall()
            cur.execute(audit_sql, (organization_id, 5000))
            audit_rows = cur.fetchall()

        status_counts: Dict[str, int] = {}
        tool_usage: Dict[str, int] = {}
        failure_reasons: Dict[str, int] = {}
        session_steps: Dict[str, int] = {}
        latencies: List[float] = []
        confirmation_required = 0
        high_risk_count = 0
        total_events = 0

        high_risk_fallback_tools = {"click", "type", "select", "open_tab", "upload_file", "drag_drop"}

        for raw_row in rows:
            row = self._deserialize_browser_action_event(dict(raw_row))
            ts = self._parse_iso(row.get("updated_at") or row.get("created_at"))
            if ts and ts < window_start:
                continue

            total_events += 1
            status = str(row.get("status") or "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1

            tool_name = str(row.get("tool_name") or "unknown")
            tool_usage[tool_name] = tool_usage.get(tool_name, 0) + 1

            session_id = str(row.get("session_id") or "")
            if session_id:
                session_steps[session_id] = session_steps.get(session_id, 0) + 1

            if bool(row.get("requires_confirmation")):
                confirmation_required += 1

            request_payload = row.get("request_payload") or {}
            if not isinstance(request_payload, dict):
                request_payload = {}
            tool_risk = str(request_payload.get("tool_risk") or "").strip().lower()
            if not tool_risk and tool_name in high_risk_fallback_tools:
                tool_risk = "high_risk"
            if tool_risk == "high_risk":
                high_risk_count += 1

            result_payload = row.get("result_payload") or {}
            if not isinstance(result_payload, dict):
                result_payload = {}
            if status in {"failed", "denied_policy"}:
                reason = (
                    str(result_payload.get("error") or "")
                    or str(row.get("policy_reason") or "")
                    or "unknown"
                )
                failure_reasons[reason] = failure_reasons.get(reason, 0) + 1

            if status in {"completed", "failed"}:
                created_at = self._parse_iso(row.get("created_at"))
                updated_at = self._parse_iso(row.get("updated_at"))
                if created_at and updated_at and updated_at >= created_at:
                    latencies.append((updated_at - created_at).total_seconds())

        completed = status_counts.get("completed", 0)
        failed = status_counts.get("failed", 0)
        denied = status_counts.get("denied_policy", 0)
        terminal_count = completed + failed + denied
        session_step_values = [float(count) for count in session_steps.values()]

        routing_counts: Dict[str, int] = {
            "erp_api_attempt": 0,
            "erp_api_success": 0,
            "erp_api_fallback_requested": 0,
            "erp_api_failed": 0,
        }
        browser_command_confirmed_count = 0
        for raw_row in audit_rows:
            row = dict(raw_row)
            ts = self._parse_iso(row.get("ts"))
            if ts and ts < window_start:
                continue
            event_type = str(row.get("event_type") or "")
            if event_type in routing_counts:
                routing_counts[event_type] = routing_counts.get(event_type, 0) + 1
            if event_type == "browser_command_confirmed":
                browser_command_confirmed_count += 1

        attempt_count = int(routing_counts.get("erp_api_attempt") or 0)
        api_success_count = int(routing_counts.get("erp_api_success") or 0)
        fallback_requested_count = int(routing_counts.get("erp_api_fallback_requested") or 0)
        api_failed_count = int(routing_counts.get("erp_api_failed") or 0)
        manual_override_required_count = int(confirmation_required)
        suggestion_accepted_count = int(browser_command_confirmed_count)
        suggestion_acceptance_rate = (
            suggestion_accepted_count / manual_override_required_count
            if manual_override_required_count
            else 0.0
        )
        manual_override_required_rate = (
            manual_override_required_count / total_events
            if total_events
            else 0.0
        )

        return {
            "organization_id": organization_id,
            "generated_at": now.isoformat(),
            "window_hours": safe_window_hours,
            "window_start": window_start.isoformat(),
            "totals": {
                "events": int(total_events),
                "sessions": int(len(session_steps)),
                "terminal_events": int(terminal_count),
            },
            "status_counts": status_counts,
            "tool_usage": tool_usage,
            "policy": {
                "confirmation_required_count": int(confirmation_required),
                "high_risk_count": int(high_risk_count),
                "denied_policy_count": int(denied),
            },
            "human_control": {
                "manual_override_required_count": manual_override_required_count,
                "manual_override_required_rate": round(manual_override_required_rate, 4),
                "suggestion_accepted_count": suggestion_accepted_count,
                "suggestion_acceptance_rate": round(suggestion_acceptance_rate, 4),
                "definition": {
                    "manual_override_required_count": "browser_action_events requiring human confirmation",
                    "suggestion_accepted_count": "browser_command_confirmed audit events",
                },
            },
            "execution": {
                "success_rate": round((completed / terminal_count) if terminal_count else 0.0, 4),
                "avg_steps_per_session": round(
                    (sum(session_step_values) / len(session_step_values)) if session_step_values else 0.0,
                    2,
                ),
                "p95_steps_per_session": round(self._p95(session_step_values) or 0.0, 2),
                "avg_latency_seconds": round((sum(latencies) / len(latencies)) if latencies else 0.0, 2),
                "p95_latency_seconds": round(self._p95(latencies) or 0.0, 2),
            },
            "api_first_routing": {
                "attempt_count": attempt_count,
                "api_success_count": api_success_count,
                "fallback_requested_count": fallback_requested_count,
                "api_failed_count": api_failed_count,
                "api_success_rate": round((api_success_count / attempt_count) if attempt_count else 0.0, 4),
                "fallback_rate": round((fallback_requested_count / attempt_count) if attempt_count else 0.0, 4),
            },
            "failure_reasons": failure_reasons,
        }
