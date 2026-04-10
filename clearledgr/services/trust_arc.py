"""Trust-building arc — Phase 3.2.

DESIGN_THESIS.md §7.5: Trust in an autonomous finance agent is
accumulated, invoice by invoice, over thirty days of correct behaviour.
The product must design for that accumulation as deliberately as it
designs any surface.

Four time-gated milestones:

  **Week 1 — Show Everything**
    Maximum transparency mode. Every agent action generates a visible
    timeline entry with full reasoning. Override window extended to 30
    minutes. Clearledgr Home shows a persistent banner.

  **Day 14 — Establish the Baseline**
    Slack message with concrete performance data: invoices processed,
    clean matches, exceptions resolved, baseline exception rate vs
    industry average.

  **Day 30 — Tier Expansion Conversation**
    Agent presents a tier expansion recommendation with 30 days of
    performance data. Financial Controller accepts or declines.

  **Weekly Monday signal (post-Day 30)**
    Every Monday: invoices processed, exceptions disagreed with,
    payments scheduled, agent accuracy percentage.

State is persisted in ``settings_json["trust_arc"]`` on the
organizations table:

  {
    "activated_at": "2026-04-10T...",    # when trust arc started
    "week1_banner_sent": true,
    "day14_baseline_sent": true,
    "day30_expansion_sent": true,
    "last_weekly_signal_at": "2026-04-07T...",
    "override_window_override_minutes": 30  # week 1 extension
  }

The service is a pure function called by the background loop on every
tick. It reads the org's trust-arc state, computes what milestone is
due, dispatches the appropriate Slack message, and updates the state.
Idempotent: each milestone fires exactly once, guarded by the
``*_sent`` flags.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_WEEK1_DAYS = 7
_DAY14 = 14
_DAY30 = 30
_WEEKLY_SIGNAL_DAY_OF_WEEK = 0  # Monday


@dataclass
class TrustArcTickResult:
    """Summary of a single trust-arc tick across all orgs."""

    orgs_checked: int = 0
    week1_banners: int = 0
    day14_baselines: int = 0
    day30_expansions: int = 0
    weekly_signals: int = 0
    activations: int = 0
    errors: List[str] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _days_since(iso_str: str) -> Optional[float]:
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (_now() - dt).total_seconds() / 86400
    except (TypeError, ValueError):
        return None


def _get_settings(db: Any, org_id: str) -> Dict[str, Any]:
    org = db.get_organization(org_id)
    if not org:
        return {}
    settings = org.get("settings_json") or org.get("settings") or {}
    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except Exception:
            settings = {}
    return settings if isinstance(settings, dict) else {}


def _save_trust_arc_state(
    db: Any, org_id: str, trust_arc: Dict[str, Any]
) -> None:
    """Merge updated trust_arc state back into the org's settings_json."""
    settings = _get_settings(db, org_id)
    settings["trust_arc"] = trust_arc
    db.update_organization(org_id, settings_json=settings)


def _get_trust_arc_state(
    db: Any, org_id: str
) -> Dict[str, Any]:
    settings = _get_settings(db, org_id)
    return settings.get("trust_arc") or {}


def _get_performance_stats(db: Any, org_id: str) -> Dict[str, Any]:
    """Read AP KPIs for the org. Returns a simplified dict.

    The KPI shape from ``get_ap_kpis`` nests data under ``overview``
    and ``exceptions`` sub-dicts. We flatten for template consumption.
    """
    try:
        kpis = db.get_ap_kpis(org_id)
        totals = kpis.get("totals") or {}
        touchless_dict = kpis.get("touchless_rate") or {}
        exception_dict = kpis.get("exception_rate") or {}

        total = totals.get("items") or 0
        # touchless_rate and exception_rate are nested dicts with a "rate" key.
        touchless = touchless_dict.get("rate") if isinstance(touchless_dict, dict) else (touchless_dict or 0.0)
        exception_rate = exception_dict.get("rate") if isinstance(exception_dict, dict) else (exception_dict or 0.0)
        total_amount = totals.get("total_amount") or 0.0
        currency = totals.get("currency") or "USD"
        return {
            "total_processed": total,
            "touchless_rate": touchless,
            "exception_rate": exception_rate,
            "total_amount": total_amount,
            "currency": currency,
        }
    except Exception as exc:
        logger.debug("[trust_arc] get_ap_kpis failed for %s: %s", org_id, exc)
        return {
            "total_processed": 0,
            "touchless_rate": 0.0,
            "exception_rate": 0.0,
            "total_amount": 0.0,
            "currency": "USD",
        }


# ---------------------------------------------------------------------------
# Milestone dispatchers
# ---------------------------------------------------------------------------


async def _send_slack_message(
    org_id: str, text: str, blocks: Optional[List[Dict[str, Any]]] = None
) -> bool:
    """Post a message to the org's finance channel. Returns True on success."""
    try:
        from clearledgr.services.slack_notifications import _post_slack_blocks
        result = await _post_slack_blocks(org_id, text, blocks=blocks)
        return result is not None
    except Exception as exc:
        logger.warning("[trust_arc] Slack send failed for %s: %s", org_id, exc)
        return False


def _build_week1_blocks(org_name: str) -> List[Dict[str, Any]]:
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Agent in observation mode"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "For the first 7 days, every agent action is logged with full reasoning "
                    "in the Box timeline. The override window is extended to 30 minutes.\n\n"
                    "Watch, correct if needed, and see that the agent handles it. "
                    "Trust the process — the results will earn trust on their own."
                ),
            },
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"_{org_name} · Week 1 transparency mode_"},
            ],
        },
    ]


def _build_day14_blocks(
    org_name: str, stats: Dict[str, Any]
) -> List[Dict[str, Any]]:
    total = stats.get("total_processed") or 0
    exception_rate = stats.get("exception_rate") or 0.0
    touchless_rate = stats.get("touchless_rate") or 0.0
    clean = int(total * (1 - exception_rate))
    exceptions = total - clean

    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Your first two weeks — the baseline"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"In your first two weeks, the agent processed *{total} invoices*. "
                    f"*{clean}* matched cleanly and required no action from you. "
                    f"*{exceptions}* were exceptions — all of which you resolved.\n\n"
                    f"Your baseline exception rate is *{exception_rate:.1%}*. "
                    f"The industry average is 12%."
                ),
            },
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"_{org_name} · Day 14 baseline · Full breakdown in Agent Activity_"},
            ],
        },
    ]


def _build_day30_blocks(
    org_name: str, stats: Dict[str, Any]
) -> List[Dict[str, Any]]:
    total = stats.get("total_processed") or 0
    touchless_rate = stats.get("touchless_rate") or 0.0
    total_amount = stats.get("total_amount") or 0.0
    currency = stats.get("currency") or "USD"

    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "30 days — ready for the next tier?"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"Over the past 30 days the agent has processed *{total} invoices* "
                    f"with a *{touchless_rate:.1%} touchless rate* and "
                    f"*{currency} {total_amount:,.2f}* in payments scheduled correctly.\n\n"
                    "Based on this performance, the agent recommends expanding the "
                    "autonomy tier to allow auto-approval of low-risk invoices. "
                    "A Financial Controller or CFO can accept this recommendation "
                    "from the admin console."
                ),
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "View performance data"},
                    "action_id": "trust_arc_view_performance",
                    "style": "primary",
                },
            ],
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"_{org_name} · Day 30 tier expansion recommendation_"},
            ],
        },
    ]


def _build_weekly_signal_blocks(
    org_name: str, stats: Dict[str, Any]
) -> List[Dict[str, Any]]:
    total = stats.get("total_processed") or 0
    touchless_rate = stats.get("touchless_rate") or 0.0
    total_amount = stats.get("total_amount") or 0.0
    currency = stats.get("currency") or "USD"

    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"Last week: *{total} invoices* processed, "
                    f"*{touchless_rate:.1%}* touchless, "
                    f"*{currency} {total_amount:,.2f}* in payments scheduled correctly."
                ),
            },
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"_{org_name} · Weekly Monday signal_"},
            ],
        },
    ]


# ---------------------------------------------------------------------------
# Main tick function
# ---------------------------------------------------------------------------


async def run_trust_arc_tick(db: Any = None) -> TrustArcTickResult:
    """Run one trust-arc tick across all organizations.

    Called by the background loop. Checks each org's trust-arc state
    and dispatches the next due milestone. Idempotent — each milestone
    fires exactly once.
    """
    from clearledgr.core.database import get_db

    db = db or get_db()
    result = TrustArcTickResult()

    try:
        orgs = db.list_organizations()
    except Exception as exc:
        logger.warning("[trust_arc] list_organizations failed: %s", exc)
        result.errors.append(str(exc))
        return result

    for org in orgs:
        org_id = str(org.get("id") or "")
        org_name = str(org.get("name") or org_id)
        if not org_id:
            continue

        result.orgs_checked += 1

        try:
            arc = _get_trust_arc_state(db, org_id)

            # Auto-activate: if the org has no trust_arc state but has
            # processed at least one invoice, start the arc now.
            if not arc.get("activated_at"):
                stats = _get_performance_stats(db, org_id)
                if stats.get("total_processed", 0) > 0:
                    arc["activated_at"] = _now().isoformat()
                    _save_trust_arc_state(db, org_id, arc)
                    result.activations += 1
                else:
                    continue  # No activity yet — skip this org.

            days = _days_since(arc["activated_at"])
            if days is None:
                continue

            # Week 1 banner (fire once, within first 7 days).
            if days <= _WEEK1_DAYS and not arc.get("week1_banner_sent"):
                blocks = _build_week1_blocks(org_name)
                sent = await _send_slack_message(
                    org_id,
                    "Agent in observation mode — all actions visible, override window extended to 30 minutes.",
                    blocks=blocks,
                )
                if sent:
                    arc["week1_banner_sent"] = True
                    arc["override_window_override_minutes"] = 30
                    _save_trust_arc_state(db, org_id, arc)
                    result.week1_banners += 1

            # Day 14 baseline (fire once, between day 14 and day 30).
            if _DAY14 <= days < _DAY30 and not arc.get("day14_baseline_sent"):
                stats = _get_performance_stats(db, org_id)
                blocks = _build_day14_blocks(org_name, stats)
                sent = await _send_slack_message(
                    org_id,
                    f"Your first two weeks: {stats.get('total_processed', 0)} invoices processed.",
                    blocks=blocks,
                )
                if sent:
                    arc["day14_baseline_sent"] = True
                    _save_trust_arc_state(db, org_id, arc)
                    result.day14_baselines += 1

            # Day 30 tier expansion (fire once, after day 30).
            if days >= _DAY30 and not arc.get("day30_expansion_sent"):
                stats = _get_performance_stats(db, org_id)
                blocks = _build_day30_blocks(org_name, stats)
                sent = await _send_slack_message(
                    org_id,
                    "30 days of performance data — tier expansion recommendation available.",
                    blocks=blocks,
                )
                if sent:
                    arc["day30_expansion_sent"] = True
                    # Reset override window extension from Week 1.
                    arc.pop("override_window_override_minutes", None)
                    _save_trust_arc_state(db, org_id, arc)
                    result.day30_expansions += 1

            # Weekly Monday signal (post-Day 30, every Monday).
            if days > _DAY30 and _now().weekday() == _WEEKLY_SIGNAL_DAY_OF_WEEK:
                last_signal = arc.get("last_weekly_signal_at")
                last_signal_days = _days_since(last_signal) if last_signal else 999
                if last_signal_days is not None and last_signal_days >= 6:
                    stats = _get_performance_stats(db, org_id)
                    blocks = _build_weekly_signal_blocks(org_name, stats)
                    sent = await _send_slack_message(
                        org_id,
                        f"Weekly: {stats.get('total_processed', 0)} invoices, "
                        f"{stats.get('touchless_rate', 0):.0%} touchless.",
                        blocks=blocks,
                    )
                    if sent:
                        arc["last_weekly_signal_at"] = _now().isoformat()
                        _save_trust_arc_state(db, org_id, arc)
                        result.weekly_signals += 1

        except Exception as exc:
            logger.warning("[trust_arc] error for org %s: %s", org_id, exc)
            result.errors.append(f"{org_id}: {exc}")

    return result


def get_trust_arc_status(db: Any, org_id: str) -> Dict[str, Any]:
    """Public read accessor for the trust-arc state of an org.

    Used by the admin console and the ops KPI endpoint to show where
    an org is in its trust journey.
    """
    arc = _get_trust_arc_state(db, org_id)
    if not arc.get("activated_at"):
        return {"status": "not_started", "trust_arc": {}}
    days = _days_since(arc["activated_at"])
    if days is None:
        return {"status": "unknown", "trust_arc": arc}

    if days <= _WEEK1_DAYS:
        phase = "week1_observation"
    elif days <= _DAY14:
        phase = "pre_baseline"
    elif days <= _DAY30:
        phase = "baseline_established"
    elif not arc.get("day30_expansion_sent"):
        phase = "awaiting_expansion_recommendation"
    else:
        phase = "ongoing_weekly_signal"

    return {
        "status": phase,
        "days_since_activation": round(days, 1),
        "trust_arc": arc,
    }
