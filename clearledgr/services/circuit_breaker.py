"""
Circuit Breaker — DESIGN_THESIS.md §7.8

"The Backoffice exception override rate monitor fires within 4 hours.
AP Managers reviewing the invoices override the match results at an
elevated rate. The engineering on-call is paged."

When the override rate exceeds the threshold, the circuit breaker:
1. Holds all unprocessed invoices (sets a flag on the org)
2. Logs an alert to the audit trail
3. Notifies via Slack
4. The hold is lifted manually after the issue is resolved

This is the automated safety net — the thesis calls it the
"Rollback Guarantee."
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

OVERRIDE_RATE_THRESHOLD = 0.30  # 30% override rate triggers the breaker
OVERRIDE_WINDOW_HOURS = 4       # Check the last 4 hours
MIN_DECISIONS_FOR_TRIGGER = 5   # Need at least 5 decisions to trigger


async def check_circuit_breaker(
    organization_id: str,
    db: Any = None,
) -> Dict[str, Any]:
    """Check if the circuit breaker should trip.

    Looks at the override rate in the last 4 hours. If it exceeds
    30% with at least 5 decisions, trips the breaker.

    Returns {"tripped": bool, "override_rate": float, ...}
    """
    if db is None:
        from clearledgr.core.database import get_db
        db = get_db()

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=OVERRIDE_WINDOW_HOURS)

    # Count decisions and overrides in the window
    try:
        events = []
        if hasattr(db, "list_recent_audit_events"):
            events = db.list_recent_audit_events(organization_id, limit=200)
        elif hasattr(db, "list_ap_audit_events"):
            events = db.list_ap_audit_events(organization_id, limit=200)

        recent_events = []
        for e in events:
            ts = e.get("ts") or e.get("timestamp") or e.get("created_at") or ""
            try:
                event_dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                if event_dt >= window_start:
                    recent_events.append(e)
            except (ValueError, TypeError):
                pass

        decisions = [
            e for e in recent_events
            if e.get("event_type") in (
                "ap_decision_made", "state_transition",
                "record_comment_added", "field_corrected",
            )
        ]
        overrides = [
            e for e in recent_events
            if e.get("event_type") in (
                "llm_gate_override_applied", "override_approved",
                "field_corrected", "extraction_corrected",
            )
        ]

        total = len(decisions)
        override_count = len(overrides)
        rate = override_count / total if total > 0 else 0.0

    except Exception as exc:
        logger.warning("[circuit_breaker] check failed: %s", exc)
        return {"tripped": False, "error": str(exc)}

    result = {
        "tripped": False,
        "override_rate": round(rate, 4),
        "total_decisions": total,
        "override_count": override_count,
        "window_hours": OVERRIDE_WINDOW_HOURS,
        "threshold": OVERRIDE_RATE_THRESHOLD,
        "checked_at": now.isoformat(),
    }

    if total >= MIN_DECISIONS_FOR_TRIGGER and rate >= OVERRIDE_RATE_THRESHOLD:
        result["tripped"] = True
        result["reason"] = (
            f"Override rate {rate:.0%} ({override_count}/{total}) exceeds "
            f"{OVERRIDE_RATE_THRESHOLD:.0%} threshold in the last {OVERRIDE_WINDOW_HOURS}h."
        )

        # Trip the breaker — set a hold flag on the org
        try:
            org = db.get_organization(organization_id)
            if org:
                import json
                settings = org.get("settings_json") or {}
                if isinstance(settings, str):
                    settings = json.loads(settings)
                settings["circuit_breaker_tripped"] = True
                settings["circuit_breaker_tripped_at"] = now.isoformat()
                settings["circuit_breaker_reason"] = result["reason"]
                db.update_organization(organization_id, settings_json=json.dumps(settings))
        except Exception:
            pass

        # Audit event
        try:
            db.append_ap_audit_event({
                "event_type": "circuit_breaker_tripped",
                "actor_type": "system",
                "actor_id": "circuit_breaker",
                "organization_id": organization_id,
                "source": "circuit_breaker",
                "payload_json": result,
            })
        except Exception:
            pass

        # Slack alert
        try:
            from clearledgr.services.slack_notifications import _post_slack_blocks
            await _post_slack_blocks(
                blocks=[{
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*Circuit breaker tripped*\n"
                            f"Override rate {rate:.0%} ({override_count}/{total}) in the last {OVERRIDE_WINDOW_HOURS}h.\n"
                            f"New invoice processing is held until the issue is resolved.\n"
                            f"_Review recent overrides and contact engineering if this is a model issue._"
                        ),
                    },
                }],
                text=f"Circuit breaker tripped: {rate:.0%} override rate",
                organization_id=organization_id,
            )
        except Exception:
            pass

        logger.warning(
            "[circuit_breaker] TRIPPED for org=%s: rate=%.0f%% (%d/%d)",
            organization_id, rate * 100, override_count, total,
        )

    return result


def is_circuit_breaker_tripped(organization_id: str, db: Any = None) -> bool:
    """Check if the circuit breaker is currently tripped for an org."""
    if db is None:
        from clearledgr.core.database import get_db
        db = get_db()
    try:
        org = db.get_organization(organization_id)
        if not org:
            return False
        settings = org.get("settings_json") or {}
        if isinstance(settings, str):
            import json
            settings = json.loads(settings)
        return bool(settings.get("circuit_breaker_tripped"))
    except Exception:
        return False
