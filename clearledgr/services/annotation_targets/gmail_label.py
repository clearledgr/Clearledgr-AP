"""Gmail label target — refactor of the legacy GmailLabelObserver
into the unified annotation framework.

Skips automatically for ERP-native or non-Gmail-source events
(Phase A guard). For Gmail-arrived bills, applies finance labels
matching the new state via the existing ``sync_finance_labels``
helper.
"""
from __future__ import annotations

import logging
from typing import Any

from clearledgr.services.annotation_targets.base import (
    AnnotationContext,
    AnnotationResult,
    register_target,
)

logger = logging.getLogger(__name__)


class GmailLabelTarget:
    target_type = "gmail_label"

    async def apply(self, context: AnnotationContext) -> AnnotationResult:
        # ERP-native bills don't have Gmail context. The synthetic
        # gmail_id (`netsuite-bill:5135`) isn't a Gmail message.
        if context.source_type != "gmail" or context.erp_native:
            return AnnotationResult(
                status="skipped",
                skip_reason="not_gmail_source",
            )

        from clearledgr.core.database import get_db
        db = get_db()
        if not hasattr(db, "get_invoice_status"):
            return AnnotationResult(
                status="skipped",
                skip_reason="db_lacks_invoice_status",
            )

        # Resolve Gmail message_id from the AP item row.
        try:
            from clearledgr.core.stores.ap_store import APStore  # noqa: F401
        except ImportError:
            pass
        try:
            row = db.get_ap_item(context.box_id) if hasattr(db, "get_ap_item") else None
        except Exception:
            row = None
        if not row:
            return AnnotationResult(
                status="skipped", skip_reason="ap_item_not_found",
            )
        message_id = str(row.get("message_id") or row.get("thread_id") or "").strip()
        user_id = str(row.get("user_id") or "").strip()
        if not message_id or not user_id:
            return AnnotationResult(
                status="skipped",
                skip_reason="no_gmail_handle",
                metadata={"have_message_id": bool(message_id), "have_user_id": bool(user_id)},
            )

        try:
            from clearledgr.services.gmail_api import GmailAPIClient
            from clearledgr.services.gmail_labels import sync_finance_labels
            client = GmailAPIClient(user_id)
            if not await client.ensure_authenticated():
                return AnnotationResult(
                    status="skipped", skip_reason="gmail_auth_failed",
                )
            # finance_email lookup is optional — sync_finance_labels
            # tolerates None.
            finance_email = None
            if hasattr(db, "get_finance_email_by_gmail_id"):
                try:
                    finance_email = db.get_finance_email_by_gmail_id(message_id)
                except Exception:
                    finance_email = None
            await sync_finance_labels(
                client, message_id,
                ap_item=row, finance_email=finance_email,
                user_email=user_id,
            )
        except Exception as exc:  # noqa: BLE001
            # Re-raise so the outbox retries.
            logger.warning(
                "gmail_label_target: sync failed for message=%s — %s",
                message_id, exc,
            )
            raise

        return AnnotationResult(
            status="succeeded",
            applied_value=context.new_state,
            external_id=message_id,
            metadata={"target_label_set": "finance"},
        )


register_target(GmailLabelTarget())
