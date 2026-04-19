"""Monitoring and alerting service — threshold-based health checks.

Runs periodic checks and raises alerts when thresholds are breached.
Alerts are delivered via:
1. Slack (existing _slack_alert infrastructure)
2. Outgoing webhooks (event: monitor.alert)
3. Sentry (breadcrumbs on critical events)

Alert channels are configurable via MONITOR_ALERT_CHANNELS env var
(comma-separated: "slack,webhook,log").  Default: "slack,log".
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Default thresholds (overridable via env vars)
_DEFAULTS = {
    "dead_letter_max": 5,           # max dead-letter items before alert
    "posting_failure_rate_pct": 20, # % of posted items that failed
    "auth_failure_max": 3,          # max auth failures across all users
    "stale_poll_hours": 2,          # hours since last autopilot poll
    "overdue_invoices_max": 20,     # max overdue invoices before alert
    "erp_error_rate_pct": 30,       # % of ERP calls that failed recently
    "approver_stale_days": 30,      # days since last login to flag approver as stale
    # Gmail watch subscriptions expire after 7 days; renewal cron runs
    # every 6 days. Alert if any active watch for this org is within
    # 24h of expiry OR already expired.
    "gmail_watch_warn_hours": 24,
}


def _threshold(name: str) -> float:
    env_key = f"MONITOR_THRESHOLD_{name.upper()}"
    raw = os.getenv(env_key, "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return _DEFAULTS.get(name, 0)


def _alert_channels() -> List[str]:
    raw = os.getenv("MONITOR_ALERT_CHANNELS", "slack,log").strip()
    return [c.strip() for c in raw.split(",") if c.strip()]


def alert_cs_team(
    *,
    severity: str,
    title: str,
    detail: str = "",
    organization_id: Optional[str] = None,
) -> None:
    """§11.2.4 + §2.1.2: Alert CS team on operational issues.

    Used for back-pressure (sustained queue depth), Gmail watch failures,
    workspace concurrency stuck, etc. Non-blocking — failure to alert
    must not block agent execution.
    """
    level = {"error": logging.ERROR, "warning": logging.WARNING}.get(
        severity.lower(), logging.INFO,
    )
    logger.log(level, "[CS Alert] %s — %s", title, detail[:500])

    # Best-effort Slack relay via the existing _slack_alert infrastructure
    try:
        import asyncio as _aio
        from clearledgr.services.agent_background import _slack_alert
        msg = f"[{severity.upper()}] {title}\n{detail[:500]}"
        try:
            _aio.get_running_loop()
            # Already in an event loop — schedule instead of await
            _aio.create_task(_slack_alert(msg, organization_id=organization_id or "ops"))
        except RuntimeError:
            # No running loop — run inline
            _aio.run(_slack_alert(msg, organization_id=organization_id or "ops"))
    except Exception as exc:
        logger.debug("[CS Alert] Slack relay failed: %s", exc)


class MonitoringService:
    """Runs health checks and emits alerts on threshold breaches."""

    def __init__(self, organization_id: str = "default") -> None:
        self.organization_id = organization_id
        from clearledgr.core.database import get_db
        self.db = get_db()

    def run_all_checks(self) -> Dict[str, Any]:
        """Run all monitoring checks.  Returns a summary dict."""
        checks = [
            self._check_dead_letters(),
            self._check_auth_failures(),
            self._check_stale_autopilot(),
            self._check_overdue_invoices(),
            self._check_posting_failures(),
            self._check_approver_health(),
            self._check_gmail_watch_expiration(),
        ]
        alerts = [c for c in checks if c.get("alert")]
        return {
            "organization_id": self.organization_id,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "check_count": len(checks),
            "alert_count": len(alerts),
            "checks": checks,
            "alerts": alerts,
            "healthy": len(alerts) == 0,
        }

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_dead_letters(self) -> Dict[str, Any]:
        """Check for dead-lettered notifications (exhausted retries)."""
        try:
            sql = self.db._prepare_sql(
                "SELECT COUNT(*) FROM pending_notifications "
                "WHERE organization_id = ? AND status = 'dead_letter'"
            )
            self.db.initialize()
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (self.organization_id,))
                row = cur.fetchone()
            count = int(row[0]) if row else 0
        except Exception:
            count = 0

        threshold = int(_threshold("dead_letter_max"))
        return {
            "check": "dead_letters",
            "value": count,
            "threshold": threshold,
            "alert": count > threshold,
            "severity": "critical" if count > threshold * 2 else "warning",
            "message": f"{count} dead-lettered notifications (threshold: {threshold})",
        }

    def _check_auth_failures(self) -> Dict[str, Any]:
        """Check for recent OAuth auth failures in autopilot state."""
        try:
            gmail_states = self.db.list_gmail_autopilot_states()
            outlook_states = self.db.list_outlook_autopilot_states()
            all_states = gmail_states + outlook_states
            failures = [
                s for s in all_states
                if s.get("last_error") and "auth" in str(s.get("last_error", "")).lower()
            ]
            count = len(failures)
        except Exception:
            count = 0

        threshold = int(_threshold("auth_failure_max"))
        return {
            "check": "auth_failures",
            "value": count,
            "threshold": threshold,
            "alert": count > threshold,
            "severity": "critical",
            "message": f"{count} auth failures across email providers (threshold: {threshold})",
        }

    def _check_stale_autopilot(self) -> Dict[str, Any]:
        """Check if autopilot hasn't polled recently."""
        try:
            gmail_states = self.db.list_gmail_autopilot_states()
            outlook_states = self.db.list_outlook_autopilot_states()
            all_states = gmail_states + outlook_states

            if not all_states:
                return {
                    "check": "stale_autopilot",
                    "value": 0,
                    "threshold": 0,
                    "alert": False,
                    "severity": "info",
                    "message": "No autopilot users configured",
                }

            now = datetime.now(timezone.utc)
            stale_hours = _threshold("stale_poll_hours")
            stale_cutoff = now - timedelta(hours=stale_hours)
            stale_count = 0

            for s in all_states:
                last_scan = s.get("last_scan_at")
                if not last_scan:
                    stale_count += 1
                    continue
                try:
                    scan_dt = datetime.fromisoformat(last_scan.replace("Z", "+00:00"))
                    if scan_dt < stale_cutoff:
                        stale_count += 1
                except (ValueError, TypeError):
                    stale_count += 1
        except Exception:
            stale_count = 0

        return {
            "check": "stale_autopilot",
            "value": stale_count,
            "threshold": 1,
            "alert": stale_count > 0,
            "severity": "warning",
            "message": f"{stale_count} user(s) with stale autopilot (>{stale_hours}h since last poll)",
        }

    def _check_overdue_invoices(self) -> Dict[str, Any]:
        """Check for excessive overdue AP items."""
        try:
            from clearledgr.services.ap_aging_report import APAgingReport
            report = APAgingReport(self.organization_id)
            data = report.generate()
            overdue_count = data.get("summary", {}).get("overdue_count", 0)
        except Exception:
            overdue_count = 0

        threshold = int(_threshold("overdue_invoices_max"))
        return {
            "check": "overdue_invoices",
            "value": overdue_count,
            "threshold": threshold,
            "alert": overdue_count > threshold,
            "severity": "warning",
            "message": f"{overdue_count} overdue invoices (threshold: {threshold})",
        }

    def _check_posting_failures(self) -> Dict[str, Any]:
        """Check for recent ERP posting failures."""
        try:
            sql = self.db._prepare_sql(
                "SELECT state, COUNT(*) as cnt FROM ap_items "
                "WHERE organization_id = ? "
                "AND updated_at >= ? "
                "AND state IN ('posted_to_erp', 'failed_post') "
                "GROUP BY state"
            )
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            self.db.initialize()
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (self.organization_id, cutoff))
                rows = cur.fetchall()

            counts = {row[0]: int(row[1]) for row in rows}
            posted = counts.get("posted_to_erp", 0)
            failed = counts.get("failed_post", 0)
            total = posted + failed
            failure_rate = (failed / total * 100) if total > 0 else 0.0
        except Exception:
            failure_rate = 0.0
            failed = 0

        threshold = _threshold("posting_failure_rate_pct")
        return {
            "check": "posting_failures",
            "value": round(failure_rate, 1),
            "threshold": threshold,
            "alert": failure_rate > threshold and failed > 0,
            "severity": "critical" if failure_rate > 50 else "warning",
            "message": f"{failure_rate:.0f}% posting failure rate in last 24h ({failed} failed, threshold: {threshold}%)",
        }


    def _check_approver_health(self) -> Dict[str, Any]:
        """Detect stale or unknown approver emails in approval routing rules.

        Checks:
        1. Approver emails not found in the org's user list (unknown/departed)
        2. Approver emails belonging to inactive users (is_active=0)
        3. Approver emails with no recent login (last_seen_at older than threshold)
        4. Pending approval chains stuck on an unknown/inactive approver
        """
        problems: List[Dict[str, str]] = []

        try:
            # Collect all approver emails from org settings
            org = self.db.get_organization(self.organization_id)
            settings = org.get("settings_json") if org else None
            if isinstance(settings, str):
                settings = json.loads(settings) if settings else {}
            settings = settings or {}
            thresholds = settings.get("approval_thresholds") or []

            configured_approvers: Set[str] = set()
            for rule in thresholds:
                for email in (rule.get("approvers") or []):
                    email = str(email).strip().lower()
                    if email:
                        configured_approvers.add(email)

            # Also collect approvers from active delegation rules
            delegation_emails: Set[str] = set()
            try:
                from clearledgr.services.approval_delegation import get_delegation_service
                svc = get_delegation_service(self.organization_id)
                for rule in (svc.list_rules() or []):
                    if rule.get("is_active"):
                        delegation_emails.add(str(rule.get("delegate_email") or "").strip().lower())
            except Exception:
                pass

            all_approver_emails = configured_approvers | delegation_emails

            if not all_approver_emails:
                return {
                    "check": "approver_health",
                    "value": 0,
                    "threshold": 0,
                    "alert": False,
                    "severity": "info",
                    "message": "No approver emails configured in routing rules",
                }

            # Load all org users (including inactive) for cross-reference
            users = self.db.get_users(self.organization_id, include_inactive=True)
            user_by_email = {
                str(u.get("email") or "").strip().lower(): u
                for u in users
                if u.get("email")
            }

            stale_days = int(_threshold("approver_stale_days"))
            stale_cutoff = (datetime.now(timezone.utc) - timedelta(days=stale_days)).isoformat()

            for email in sorted(all_approver_emails):
                user = user_by_email.get(email)
                if not user:
                    problems.append({"email": email, "issue": "unknown_user"})
                    continue
                if not user.get("is_active"):
                    problems.append({"email": email, "issue": "inactive_user"})
                    continue
                last_seen = user.get("last_seen_at") or ""
                if last_seen and last_seen < stale_cutoff:
                    problems.append({"email": email, "issue": "stale_login", "last_seen_at": last_seen})

            # Check pending approval chains for stuck approvers
            try:
                sql = self.db._prepare_sql(
                    "SELECT s.approvers FROM approval_chains c "
                    "JOIN approval_steps s ON s.chain_id = c.id "
                    "WHERE c.organization_id = ? AND c.status = 'pending' AND s.status = 'pending'"
                )
                self.db.initialize()
                with self.db.connect() as conn:
                    cur = conn.cursor()
                    cur.execute(sql, (self.organization_id,))
                    rows = cur.fetchall()

                pending_approver_emails: Set[str] = set()
                for row in rows:
                    raw = row[0] if row else "[]"
                    try:
                        emails = json.loads(raw) if isinstance(raw, str) else raw
                        for e in (emails or []):
                            pending_approver_emails.add(str(e).strip().lower())
                    except (json.JSONDecodeError, TypeError):
                        pass

                for email in sorted(pending_approver_emails):
                    user = user_by_email.get(email)
                    if not user:
                        # Only add if not already flagged from config rules
                        if not any(p["email"] == email for p in problems):
                            problems.append({"email": email, "issue": "pending_chain_unknown_approver"})
                    elif not user.get("is_active"):
                        if not any(p["email"] == email for p in problems):
                            problems.append({"email": email, "issue": "pending_chain_inactive_approver"})
            except Exception as exc:
                logger.debug("Pending chain approver check failed: %s", exc)

        except Exception as exc:
            logger.debug("Approver health check failed: %s", exc)
            return {
                "check": "approver_health",
                "value": 0,
                "threshold": 0,
                "alert": False,
                "severity": "info",
                "message": f"Approver health check skipped: {exc}",
            }

        count = len(problems)
        return {
            "check": "approver_health",
            "value": count,
            "threshold": 0,
            "alert": count > 0,
            "severity": "critical" if any(p["issue"].startswith("pending_chain") for p in problems) else "warning",
            "message": (
                f"{count} approver issue(s) detected: "
                + ", ".join(f"{p['email']} ({p['issue']})" for p in problems[:5])
                + (" ..." if count > 5 else "")
            ) if problems else "All configured approvers are active org members",
            "problems": problems,
        }

    def _check_gmail_watch_expiration(self) -> Dict[str, Any]:
        """Detect Gmail watch subscriptions about to expire or already expired.

        Gmail watch subscriptions expire after 7 days. The renewal cron
        runs every 6 days and calls ``watch()`` on each active mailbox.
        If that cron silently fails (auth revoked, quota hit, new
        workspace not seeded), invoices stop arriving and nothing else
        detects it — the agent just doesn't process anything. This
        check compares ``watch_expiration`` against now + warn window
        and raises a critical alert if ANY active watch in the org is
        about to expire or has already expired.
        """
        expiring: List[Dict[str, Any]] = []
        expired: List[Dict[str, Any]] = []
        missing_watch: List[Dict[str, Any]] = []

        try:
            self.db.initialize()
            # gmail_autopilot_state is keyed by user_id, not org_id.
            # For V1 single-tenant deployments this is sufficient; when
            # multi-tenant sharding is real the query here grows a JOIN
            # to the users table. For now we surface every watch in the
            # DB — a connected Gmail account with no watch_expiration is
            # itself the signal (never renewed / never set up).
            sql = self.db._prepare_sql(
                "SELECT email, watch_expiration, last_watch_at "
                "FROM gmail_autopilot_state"
            )
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql)
                rows = cur.fetchall()
        except Exception:
            rows = []

        now = datetime.now(timezone.utc)
        warn_hours = float(_threshold("gmail_watch_warn_hours"))
        warn_cutoff = now + timedelta(hours=warn_hours)

        for row in rows:
            # Rows may be dict-like (sqlite Row) or tuple-like depending on adapter.
            if hasattr(row, "keys"):
                email = row["email"]
                watch_exp = row["watch_expiration"]
            else:
                email, watch_exp = row[0], row[1]

            if not watch_exp:
                missing_watch.append({"email": email})
                continue

            try:
                exp_dt = datetime.fromisoformat(str(watch_exp).replace("Z", "+00:00"))
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                # Malformed timestamp — treat as missing so it surfaces.
                missing_watch.append({"email": email, "reason": "malformed_expiration"})
                continue

            if exp_dt <= now:
                expired.append({
                    "email": email,
                    "expired_at": exp_dt.isoformat(),
                    "hours_past": round((now - exp_dt).total_seconds() / 3600.0, 1),
                })
            elif exp_dt <= warn_cutoff:
                expiring.append({
                    "email": email,
                    "expires_at": exp_dt.isoformat(),
                    "hours_until": round((exp_dt - now).total_seconds() / 3600.0, 1),
                })

        problem_count = len(expired) + len(expiring) + len(missing_watch)
        has_critical = len(expired) > 0 or len(missing_watch) > 0
        return {
            "check": "gmail_watch_expiration",
            "value": problem_count,
            "threshold": 0,
            "alert": problem_count > 0,
            "severity": "critical" if has_critical else "warning",
            "message": (
                f"{len(expired)} expired, {len(expiring)} expiring soon, "
                f"{len(missing_watch)} missing watch subscription"
            ) if problem_count else "All Gmail watches are healthy",
            "expired": expired,
            "expiring": expiring,
            "missing_watch": missing_watch,
        }


async def run_monitoring_checks(organization_id: str = "default") -> Dict[str, Any]:
    """Run all monitoring checks and emit alerts for breaches."""
    service = MonitoringService(organization_id)
    result = service.run_all_checks()

    if not result["alerts"]:
        return result

    channels = _alert_channels()

    for alert in result["alerts"]:
        msg = f":rotating_light: *Monitor Alert* [{alert['severity'].upper()}]\n{alert['message']}"

        if "log" in channels:
            log_fn = logger.critical if alert["severity"] == "critical" else logger.warning
            log_fn("[Monitor] %s: %s", alert["check"], alert["message"])

        if "slack" in channels:
            try:
                from clearledgr.services.agent_background import _slack_alert
                await _slack_alert(msg, organization_id=organization_id)
            except Exception as exc:
                logger.debug("Slack alert delivery failed: %s", exc)

        if "webhook" in channels:
            try:
                from clearledgr.services.webhook_delivery import emit_webhook_event
                await emit_webhook_event(
                    organization_id=organization_id,
                    event_type="monitor.alert",
                    payload=alert,
                )
            except Exception as exc:
                logger.debug("Webhook alert delivery failed: %s", exc)

        # Sentry breadcrumb
        try:
            import sentry_sdk
            sentry_sdk.add_breadcrumb(
                category="monitor",
                message=alert["message"],
                level=alert["severity"],
            )
            if alert["severity"] == "critical":
                sentry_sdk.capture_message(
                    f"[Monitor] {alert['check']}: {alert['message']}",
                    level="error",
                )
        except Exception:
            pass

    return result
