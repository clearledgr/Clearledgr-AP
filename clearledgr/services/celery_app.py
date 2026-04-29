"""Celery Application — Agent Design Specification §11.2.1.

Celery worker fleet configuration. Workers are stateless Python processes
that pull events from Redis and process them through the planning engine.

Start a worker:
    celery -A clearledgr.services.celery_app worker -l info -c 4

Start the scheduler (Celery Beat):
    celery -A clearledgr.services.celery_app beat -l info
"""
from __future__ import annotations

import logging
import os

from celery import Celery
from celery.schedules import crontab as _crontab

logger = logging.getLogger(__name__)

# Redis URL from environment (same Redis used for rate limiting and event streams)
_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# Sentry error tracking — wire in both worker and beat processes so task
# exceptions (planning loop, ERP posting, Gmail push decode) are captured.
# Same pattern as main.py: opt-in via SENTRY_DSN, graceful if sentry-sdk
# isn't installed.
_sentry_dsn = os.getenv("SENTRY_DSN", "").strip()
if _sentry_dsn:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.celery import CeleryIntegration
        from sentry_sdk.integrations.httpx import HttpxIntegration
        from clearledgr.core.sentry_config import build_sentry_before_send

        sentry_sdk.init(
            dsn=_sentry_dsn,
            environment=os.getenv("ENV", "development"),
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
            send_default_pii=False,
            # Scrub local-variable capture in exception frames — see
            # sentry_config.build_sentry_before_send docstring.
            # Critical for Celery because task payloads (invoice
            # dicts, bank_details) routinely land in stack vars.
            before_send=build_sentry_before_send(),
            integrations=[CeleryIntegration(), HttpxIntegration()],
        )
        logger.info("Sentry error tracking initialized for Celery")
    except ImportError:
        logger.warning("SENTRY_DSN set but sentry-sdk not installed")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Sentry initialization failed: %s", exc)

app = Celery("clearledgr")

app.config_from_object(
    {
        "broker_url": _REDIS_URL,
        "result_backend": _REDIS_URL,
        "task_serializer": "json",
        "result_serializer": "json",
        "accept_content": ["json"],
        # §12: late ack ensures task survives worker crash
        "task_acks_late": True,
        # §11.2.1: one event at a time per worker process
        "worker_prefetch_multiplier": 1,
        # Don't store results by default (we write to DB directly)
        "task_ignore_result": True,
        # Visibility timeout: 5 minutes (reclaim if worker dies)
        "broker_transport_options": {
            "visibility_timeout": 300,
        },
        # Celery Beat schedule for timer-based events
        "beat_schedule": {
            # §2: Consume events from Redis Streams
            "drain-event-stream": {
                "task": "clearledgr.services.celery_tasks.drain_event_stream",
                "schedule": 2.0,  # Every 2 seconds — near-real-time consumption
            },
            # §4.3: GRN checks, approval timeouts, vendor chases
            "fire-pending-timers": {
                "task": "clearledgr.services.celery_tasks.fire_pending_timers",
                "schedule": 60.0,  # Every 60 seconds (vs old 15-min polling)
            },
            # §12.1: Reclaim stale events from dead workers
            "reclaim-stale-events": {
                "task": "clearledgr.services.celery_tasks.reclaim_stale_events",
                "schedule": 30.0,  # Every 30 seconds
            },
            # Daily retention sweep for the agent_retry_jobs table.
            # Without this, the UNIQUE idempotency_key index grows for
            # the life of the deployment and lookups slow over time.
            # Audit history lives elsewhere (append-only audit_events),
            # so retiring terminal retry rows after 90 days is safe.
            "reap-completed-retry-jobs": {
                "task": "clearledgr.services.celery_tasks.reap_completed_retry_jobs",
                "schedule": 24 * 60 * 60.0,  # Daily
            },
            # Daily hard-purge of soft-deleted orgs past the legal-hold
            # window (ORG_LEGAL_HOLD_DAYS, default 30). Completes the
            # right-to-be-forgotten path: deleted_at marks the tomb-
            # stone, this task drops every tenant row across the ~30
            # org-scoped tables. Audit events are preserved by design
            # (append-only trigger + 7-year regulatory retention).
            "purge-soft-deleted-orgs": {
                "task": "clearledgr.services.celery_tasks.purge_soft_deleted_orgs",
                "schedule": 24 * 60 * 60.0,  # Daily
            },
            # Wave 2 / C3 carry-over: SAP B1 doesn't ship a payment
            # webhook, so we poll connected orgs every 5 minutes for
            # cleared outgoing payments. The payment-tracking layer
            # (C2) deduplicates at the (org, source, payment_id,
            # ap_item_id) compound key, so missed-then-recovered runs
            # never double-record.
            "poll-sap-b1-payments": {
                "task": "clearledgr.services.celery_tasks.poll_sap_b1_payments_all_orgs",
                "schedule": 5 * 60.0,  # Every 5 minutes
            },
            # Wave 5 / G5 carry-over: month-end accrual close.
            # Walks every active org on the 1st of the month at
            # 02:00 UTC, builds the prior-month accrual JE proposal,
            # posts to each org's connected ERP. Idempotent at the
            # DB layer — partial unique index on accrual_je_runs
            # blocks duplicate successful posts for the same period.
            "post-month-end-accruals": {
                "task": "clearledgr.services.celery_tasks.post_month_end_accruals_all_orgs",
                "schedule": _crontab(
                    minute=0, hour=2, day_of_month="1",
                ),
            },
            # Wave 5 / G5 carry-over: daily reversal sweep.
            # Walks accrual_je_runs WHERE status='posted' AND
            # reversal_posted_at IS NULL AND reversal_date <= today;
            # posts the reversal entry. Daily at 03:00 UTC.
            "post-pending-accrual-reversals": {
                "task": "clearledgr.services.celery_tasks.post_pending_accrual_reversals",
                "schedule": _crontab(minute=0, hour=3),
            },
        },
    }
)

# Auto-discover tasks
app.autodiscover_tasks(["clearledgr.services"])
