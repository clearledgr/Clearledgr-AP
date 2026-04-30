"""Customer-webhook annotation target.

Fires the customer's outbound webhook subscriptions (existing
``webhook_subscriptions`` infrastructure) on every Box-state
change. Lets customers wire their own admin tools / data warehouse /
BI dashboards / Slack notifications / whatever to Clearledgr's
state stream without polling.

Per-target config:
    {
        "enabled": true,
        "filter_event_types": ["state.posted_to_erp", "state.closed"],  # optional
        "include_metadata": true | false  # default true
    }

Filter is inclusive — only events whose new_state is in the list
fire the webhook. Empty / missing filter = all transitions.
"""
from __future__ import annotations

import hmac
import hashlib
import json
import logging
import time
from typing import Any, Dict, List

from clearledgr.services.annotation_targets.base import (
    AnnotationContext,
    AnnotationResult,
    register_target,
)

logger = logging.getLogger(__name__)


class CustomerWebhookTarget:
    target_type = "customer_webhook"

    async def apply(self, context: AnnotationContext) -> AnnotationResult:
        # Filter check
        filter_states = context.target_config.get("filter_event_types") or []
        if filter_states:
            target_event = f"state.{context.new_state}"
            if target_event not in filter_states:
                return AnnotationResult(
                    status="skipped",
                    skip_reason="event_filtered_out",
                    metadata={"filter": filter_states, "event": target_event},
                )

        subscriptions = self._fetch_active_subscriptions(context.organization_id)
        if not subscriptions:
            return AnnotationResult(
                status="skipped",
                skip_reason="no_active_subscriptions",
            )

        from clearledgr.core.http_client import get_http_client
        client = get_http_client()
        results: List[Dict[str, Any]] = []
        any_failed = False

        body_payload = self._build_payload(context)
        body_bytes = json.dumps(body_payload, sort_keys=True).encode("utf-8")
        for sub in subscriptions:
            sub_id = str(sub.get("id") or "").strip()
            url = str(sub.get("url") or "").strip()
            secret = str(sub.get("secret") or "").strip()
            if not url:
                continue
            timestamp = str(int(time.time()))
            signature = self._sign(timestamp, body_bytes, secret) if secret else ""
            headers = {
                "Content-Type": "application/json",
                "X-Clearledgr-Event": f"state.{context.new_state}",
                "X-Clearledgr-Timestamp": timestamp,
                "X-Clearledgr-Subscription-Id": sub_id,
            }
            if signature:
                headers["X-Clearledgr-Signature"] = f"v1={signature}"
            try:
                response = await client.post(
                    url, headers=headers, content=body_bytes, timeout=15,
                )
                status_code = response.status_code
                ok = 200 <= status_code < 300
                results.append({
                    "subscription_id": sub_id, "url": url,
                    "status": status_code, "ok": ok,
                })
                if not ok:
                    any_failed = True
            except Exception as exc:  # noqa: BLE001
                results.append({
                    "subscription_id": sub_id, "url": url,
                    "status": None, "error": str(exc)[:200], "ok": False,
                })
                any_failed = True

        if any_failed:
            # Raise so outbox retries the whole annotation. Future
            # enhancement: split per-subscription so a single failing
            # webhook doesn't retry all of them. For now the dedupe
            # key + idempotent webhook receivers handle the safety.
            raise RuntimeError(
                f"customer_webhook: at least one subscription failed: {results}"
            )

        return AnnotationResult(
            status="succeeded",
            applied_value=context.new_state,
            response_code=200,
            metadata={"subscriptions_fired": len(results)},
        )

    @staticmethod
    def _build_payload(context: AnnotationContext) -> Dict[str, Any]:
        include_meta = bool(context.target_config.get("include_metadata", True))
        payload: Dict[str, Any] = {
            "event_type": f"state.{context.new_state}",
            "box_type": context.box_type,
            "box_id": context.box_id,
            "old_state": context.old_state,
            "new_state": context.new_state,
            "actor_id": context.actor_id,
            "correlation_id": context.correlation_id,
            "organization_id": context.organization_id,
            "source_type": context.source_type,
        }
        if include_meta:
            payload["metadata"] = context.metadata
        return payload

    @staticmethod
    def _sign(timestamp: str, body_bytes: bytes, secret: str) -> str:
        signed_input = (timestamp + ".").encode("utf-8") + body_bytes
        return hmac.new(
            secret.encode("utf-8"), signed_input, hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _fetch_active_subscriptions(organization_id: str) -> List[Dict[str, Any]]:
        from clearledgr.core.database import get_db
        db = get_db()
        if not hasattr(db, "connect"):
            return []
        db.initialize()
        try:
            with db.connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT id, url, secret, event_types FROM webhook_subscriptions
                    WHERE organization_id = %s AND is_active = 1
                    """,
                    (organization_id,),
                )
                rows = cur.fetchall()
        except Exception:
            return []
        return [dict(r) for r in rows or []]


register_target(CustomerWebhookTarget())
