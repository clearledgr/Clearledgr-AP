"""Cycle-time + touchless-rate instrumentation (Wave 5 / G6).

The two AP-cycle KPIs the operator's dashboard ("how is my AP team
doing?") needs to surface:

  * **Cycle time** — median, p90, average days from
    ``received`` to ``posted_to_erp``, per period. Broken down by
    state-pair so operators can see where bills are sitting:
    received -> validated, validated -> needs_approval,
    needs_approval -> approved, approved -> ready_to_post,
    ready_to_post -> posted_to_erp.

  * **Touchless rate** — % of bills posted in the period that
    transitioned through the canonical pipeline WITHOUT a human
    touching them (no actor_type='user' on any audit event for
    the AP item). Industry benchmark (Levvel, Hackett): top-tier
    = 70-85%; median = 30-50%; manual-shop = <10%.

Source of truth: the immutable ``audit_events`` table. Every state
transition lands as one event with prev_state -> new_state +
actor_type. We walk events in the period for each AP item and
compute the metrics.

The functions are pure DB reads; results live on a sibling
``period_close_metrics`` projection (out of scope for v1; the API
returns computed metrics each call).
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


_TARGET_FINAL_STATES = (
    "posted_to_erp",
    "awaiting_payment",
    "payment_in_flight",
    "payment_executed",
    "closed",
)


@dataclass
class StagePairMetrics:
    """Time spent transitioning from one state to the next."""

    from_state: str
    to_state: str
    sample_count: int = 0
    median_hours: Optional[float] = None
    p90_hours: Optional[float] = None
    average_hours: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from_state": self.from_state,
            "to_state": self.to_state,
            "sample_count": self.sample_count,
            "median_hours": self.median_hours,
            "p90_hours": self.p90_hours,
            "average_hours": self.average_hours,
        }


@dataclass
class CycleTimeReport:
    organization_id: str
    period_start: str
    period_end: str
    bills_in_period: int = 0
    bills_posted_in_period: int = 0
    end_to_end_median_hours: Optional[float] = None
    end_to_end_p90_hours: Optional[float] = None
    end_to_end_average_hours: Optional[float] = None
    touchless_count: int = 0
    touchless_rate: Optional[float] = None
    stages: List[StagePairMetrics] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "organization_id": self.organization_id,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "bills_in_period": self.bills_in_period,
            "bills_posted_in_period": self.bills_posted_in_period,
            "end_to_end_median_hours": self.end_to_end_median_hours,
            "end_to_end_p90_hours": self.end_to_end_p90_hours,
            "end_to_end_average_hours": self.end_to_end_average_hours,
            "touchless_count": self.touchless_count,
            "touchless_rate": self.touchless_rate,
            "stages": [s.to_dict() for s in self.stages],
            "notes": list(self.notes),
        }


# ── Time helpers ───────────────────────────────────────────────────


def _to_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _hours_between(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() / 3600.0


def _quantile(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 2)
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return round(sorted_vals[f], 2)
    return round(
        sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f),
        2,
    )


# ── Per-AP-item walker ────────────────────────────────────────────


def _classify_ap_item(
    db,
    *,
    organization_id: str,
    ap_item_id: str,
    period_start: str,
    period_end: str,
) -> Dict[str, Any]:
    """Walk audit_events for one AP item; return state-transition
    timestamps + touchless flag for the period.

    Returns ``{
        "state_timestamps": {"validated": "...", "posted_to_erp": "..."},
        "first_state": "received",
        "first_state_at": "...",
        "final_state": "posted_to_erp" or None,
        "final_state_at": "..." or None,
        "human_touched": bool,
    }``
    """
    db.initialize()
    sql = (
        "SELECT event_type, prev_state, new_state, actor_type, ts "
        "FROM audit_events "
        "WHERE organization_id = %s "
        "  AND box_id = %s AND box_type = 'ap_item' "
        "  AND new_state IS NOT NULL "
        "ORDER BY ts ASC"
    )
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (organization_id, ap_item_id))
        rows = cur.fetchall()

    state_timestamps: Dict[str, str] = {}
    first_state = None
    first_state_at = None
    final_state = None
    final_state_at = None
    human_touched = False

    for r in rows:
        row = dict(r)
        new_state = row.get("new_state")
        ts = row.get("ts")
        actor = (row.get("actor_type") or "").strip().lower()
        if first_state is None and row.get("prev_state"):
            first_state = row.get("prev_state")
            first_state_at = ts
        if first_state is None:
            first_state = new_state
            first_state_at = ts
        if new_state and new_state not in state_timestamps:
            state_timestamps[new_state] = ts
        if new_state in _TARGET_FINAL_STATES:
            final_state = new_state
            final_state_at = ts
        if actor == "user":
            human_touched = True

    return {
        "state_timestamps": state_timestamps,
        "first_state": first_state,
        "first_state_at": first_state_at,
        "final_state": final_state,
        "final_state_at": final_state_at,
        "human_touched": human_touched,
    }


# ── Builder ────────────────────────────────────────────────────────


_CANONICAL_STAGE_PAIRS: List[Tuple[str, str]] = [
    ("received", "validated"),
    ("validated", "needs_approval"),
    ("needs_approval", "approved"),
    ("approved", "ready_to_post"),
    ("ready_to_post", "posted_to_erp"),
]


def compute_cycle_time_report(
    db,
    *,
    organization_id: str,
    period_start: str,
    period_end: str,
) -> CycleTimeReport:
    """Build the per-period cycle-time + touchless report.

    A bill is counted as "in the period" if it was created in the
    period (regardless of whether it has posted yet). It's counted
    as "posted in the period" if it transitioned to posted_to_erp
    within the period — that's the basis for cycle-time + touchless
    rate (we measure completed bills, not in-flight ones).
    """
    report = CycleTimeReport(
        organization_id=organization_id,
        period_start=period_start,
        period_end=period_end,
    )

    db.initialize()
    sql = (
        "SELECT id, created_at FROM ap_items "
        "WHERE organization_id = %s "
        "  AND created_at >= %s "
        "  AND created_at <= %s"
    )
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            sql, (organization_id, period_start, period_end + "T23:59:59"),
        )
        ap_rows = cur.fetchall()

    report.bills_in_period = len(ap_rows)

    end_to_end_hours: List[float] = []
    posted_count = 0
    touchless_count = 0
    stage_samples: Dict[Tuple[str, str], List[float]] = {
        pair: [] for pair in _CANONICAL_STAGE_PAIRS
    }

    for r in ap_rows:
        row = dict(r)
        ap_id = row["id"]
        created_at_dt = _to_dt(row.get("created_at"))
        if not created_at_dt:
            continue

        info = _classify_ap_item(
            db,
            organization_id=organization_id,
            ap_item_id=ap_id,
            period_start=period_start,
            period_end=period_end,
        )

        if info.get("final_state") not in _TARGET_FINAL_STATES:
            continue
        final_dt = _to_dt(info.get("final_state_at"))
        if not final_dt:
            continue
        if final_dt < _to_dt(period_start) or final_dt > _to_dt(
            period_end + "T23:59:59"
        ):
            continue

        posted_count += 1
        end_to_end_hours.append(_hours_between(created_at_dt, final_dt))
        if not info.get("human_touched"):
            touchless_count += 1

        # Per-stage samples (skip stages the item didn't traverse).
        st_map = info.get("state_timestamps") or {}
        for from_state, to_state in _CANONICAL_STAGE_PAIRS:
            from_ts = (
                _to_dt(st_map.get(from_state))
                if from_state in st_map
                else (created_at_dt if from_state == "received" else None)
            )
            to_ts = _to_dt(st_map.get(to_state))
            if from_ts and to_ts and to_ts > from_ts:
                stage_samples[(from_state, to_state)].append(
                    _hours_between(from_ts, to_ts),
                )

    report.bills_posted_in_period = posted_count

    if posted_count > 0:
        report.touchless_count = touchless_count
        report.touchless_rate = round(touchless_count / posted_count, 4)
        report.end_to_end_median_hours = _quantile(end_to_end_hours, 0.5)
        report.end_to_end_p90_hours = _quantile(end_to_end_hours, 0.9)
        report.end_to_end_average_hours = round(
            statistics.fmean(end_to_end_hours), 2,
        )
    else:
        report.notes.append("no_bills_posted_in_period")

    for (from_state, to_state), samples in stage_samples.items():
        report.stages.append(StagePairMetrics(
            from_state=from_state,
            to_state=to_state,
            sample_count=len(samples),
            median_hours=_quantile(samples, 0.5),
            p90_hours=_quantile(samples, 0.9),
            average_hours=(
                round(statistics.fmean(samples), 2) if samples else None
            ),
        ))

    return report
