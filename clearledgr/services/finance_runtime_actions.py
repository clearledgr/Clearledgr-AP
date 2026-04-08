"""Action helpers extracted from FinanceAgentRuntime."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)


def build_finance_lead_summary_payload(
    runtime: Any,
    ap_item: Dict[str, Any],
    *,
    audit_events: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    item = dict(ap_item or {})
    agent_memory = item.get("agent_memory") if isinstance(item.get("agent_memory"), dict) else {}
    agent_next_action = item.get("agent_next_action") if isinstance(item.get("agent_next_action"), dict) else {}
    agent_summary = item.get("agent_summary") if isinstance(item.get("agent_summary"), dict) else {}
    if not agent_memory:
        try:
            from clearledgr.services.agent_memory import get_agent_memory_service

            organization_id = str(item.get("organization_id") or runtime.organization_id or "default").strip() or "default"
            ap_item_id = str(item.get("id") or "").strip()
            if ap_item_id:
                agent_memory = get_agent_memory_service(
                    organization_id,
                    db=getattr(runtime, "db", None),
                ).build_surface(ap_item_id=ap_item_id, skill_id="ap_v1")
                if not agent_next_action:
                    candidate = agent_memory.get("next_action")
                    agent_next_action = candidate if isinstance(candidate, dict) else {}
                if not agent_summary:
                    candidate = agent_memory.get("summary")
                    agent_summary = candidate if isinstance(candidate, dict) else {}
        except Exception:
            agent_memory = {}
            agent_next_action = {}
            agent_summary = {}

    state = str(item.get("state") or "received").strip().lower()
    canonical_label = str(agent_next_action.get("label") or "").strip()
    canonical_type = str(agent_next_action.get("type") or "").strip()
    next_action = canonical_label or str(item.get("next_action") or "").strip().replace("_", " ")
    vendor = str(item.get("vendor_name") or item.get("vendor") or "Unknown vendor").strip()
    invoice_number = str(item.get("invoice_number") or "N/A").strip()
    amount = item.get("amount")
    currency = str(item.get("currency") or "USD").strip().upper()
    due_date = str(item.get("due_date") or "").strip()
    exception_code = str(item.get("exception_code") or "").strip()
    exception_severity = str(item.get("exception_severity") or "").strip()
    requires_field_review = bool(item.get("requires_field_review"))
    confidence_blockers = (
        item.get("confidence_blockers")
        if isinstance(item.get("confidence_blockers"), list)
        else []
    )
    metadata = runtime._parse_json_dict(item.get("metadata"))
    context_summary = str(metadata.get("context_summary") or "").strip()
    belief_state = agent_memory.get("belief") if isinstance(agent_memory.get("belief"), dict) else {}
    belief_reason = str(
        belief_state.get("reason")
        or agent_summary.get("reason")
        or ""
    ).strip()

    amount_text = (
        f"{currency} {float(amount):,.2f}"
        if isinstance(amount, (int, float))
        else f"{currency} amount unavailable"
    )
    lines: List[str] = [
        f"{vendor} · Invoice {invoice_number} · {amount_text}",
        f"Current state: {state.replace('_', ' ')}"
        + (f" · Next action: {next_action}" if next_action else ""),
    ]

    if exception_code:
        exception_line = f"Exception: {exception_code.replace('_', ' ')}"
        if exception_severity:
            exception_line += f" ({exception_severity})"
        lines.append(exception_line)
    if due_date:
        lines.append(f"Due date: {due_date}")
    if requires_field_review:
        fields: List[str] = []
        for entry in confidence_blockers[:4]:
            if isinstance(entry, str):
                fields.append(entry)
            elif isinstance(entry, dict):
                fields.append(str(entry.get("field") or entry.get("code") or "").strip())
        fields = [field for field in fields if field]
        lines.append(
            f"Field review blockers: {', '.join(fields)}"
            if fields
            else "Field review blockers require review before posting."
        )
    if bool(item.get("budget_requires_decision")):
        budget_status = str(item.get("budget_status") or "review").replace("_", " ")
        lines.append(f"Budget decision required ({budget_status}).")
    if belief_reason:
        lines.append(f"Agent belief: {belief_reason[:180]}")
    if context_summary:
        lines.append(f"Context: {context_summary[:180]}")

    recent: List[str] = []
    for event in (audit_events or [])[:4]:
        event_type = str(event.get("event_type") or event.get("eventType") or "").strip()
        if event_type:
            recent.append(event_type.replace("_", " "))
    if recent:
        lines.append(f"Recent activity: {' -> '.join(recent)}")

    deduped: List[str] = []
    seen: set[str] = set()
    for line in lines:
        text = str(line or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(text)

    return {
        "title": "Finance lead exception summary",
        "lines": deduped[:8],
        "state": state,
        "next_action": canonical_type or str(item.get("next_action") or ""),
        "agent_memory": agent_memory,
        "agent_profile": agent_memory.get("profile") if isinstance(agent_memory.get("profile"), dict) else {},
        "agent_next_action": agent_next_action,
        "agent_summary": agent_summary,
    }


async def escalate_invoice_review(
    runtime: Any,
    *,
    email_id: str,
    vendor: Optional[str] = None,
    amount: Optional[float] = None,
    currency: str = "USD",
    confidence: Optional[float] = None,
    mismatches: Optional[List[Dict[str, Any]]] = None,
    message: Optional[str] = None,
    channel: Optional[str] = None,
) -> Dict[str, Any]:
    from clearledgr.workflows.gmail_activities import send_slack_notification_activity

    gmail_ref = str(email_id or "").strip()
    if not gmail_ref:
        raise ValueError("missing_email_id")

    try:
        ap_item = runtime._resolve_ap_item(gmail_ref)
    except Exception:
        ap_item = {}
    ap_item_id = str(ap_item.get("id") or gmail_ref).strip() or gmail_ref
    correlation_id = runtime._correlation_id_for_item(ap_item)

    mismatch_rows = mismatches if isinstance(mismatches, list) else []
    mismatch_text = "\n".join(
        [f"• {entry.get('message', str(entry))}" for entry in mismatch_rows[:5]]
    )
    try:
        confidence_pct = float(confidence or 0.0)
    except (TypeError, ValueError):
        confidence_pct = 0.0
    if 0.0 <= confidence_pct <= 1.0:
        confidence_pct *= 100.0
    amount_text = (
        f"{currency} {float(amount):,.2f}"
        if isinstance(amount, (int, float))
        else "Unknown"
    )
    escalation_message = str(message or "").strip() or (
        f"*Invoice Review Required*\n\n"
        f"*Vendor:* {vendor or 'Unknown'}\n"
        f"*Amount:* {amount_text}\n"
        f"*Confidence:* {confidence_pct:.1f}%\n\n"
        f"*Issues:*\n{mismatch_text or '• Manual review requested'}"
    )

    delivery = await send_slack_notification_activity(
        {
            "type": "escalation",
            "channel": str(channel or "#finance-escalations").strip() or "#finance-escalations",
            "email_id": gmail_ref,
            "ap_item_id": ap_item_id,
            "classification": {"type": "INVOICE"},
            "extraction": {
                "vendor": vendor,
                "amount": amount,
                "currency": currency,
            },
            "confidence_result": {
                "confidence_pct": confidence_pct,
                "mismatches": mismatch_rows,
                "requires_review": True,
            },
            "organization_id": runtime.organization_id,
        }
    )

    audit_row = runtime._append_runtime_audit(
        ap_item_id=ap_item_id,
        event_type="invoice_escalated",
        reason="runtime_escalate_invoice_review",
        metadata={
            "email_id": gmail_ref,
            "vendor": vendor,
            "amount": amount,
            "currency": currency,
            "confidence": confidence,
            "mismatches": mismatch_rows,
            "channel": channel,
            "message": escalation_message[:500],
            "delivery": delivery,
        },
        correlation_id=correlation_id,
        skill_id="ap_v1",
    )

    return {
        "email_id": gmail_ref,
        "ap_item_id": ap_item_id,
        "status": "escalated",
        "channel": str(channel or "#finance-escalations").strip() or "#finance-escalations",
        "message": escalation_message,
        "delivery": delivery,
        "audit_event_id": (audit_row or {}).get("id"),
    }


async def share_finance_summary(
    runtime: Any,
    *,
    reference_id: str,
    target: str = "email_draft",
    preview_only: bool = False,
    recipient_email: Optional[str] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    from clearledgr.services.invoice_workflow import get_invoice_workflow
    from clearledgr.services.teams_notifications import (
        build_finance_summary_reply_activity,
        send_finance_summary_reply,
    )

    ap_item = runtime._resolve_ap_item(reference_id)
    ap_item_id = str(ap_item.get("id") or reference_id).strip() or str(reference_id)
    gmail_ref = str(ap_item.get("thread_id") or reference_id).strip() or str(reference_id)
    correlation_id = runtime._correlation_id_for_item(ap_item)
    resolved_target = str(target or "email_draft").strip().lower()
    if resolved_target not in {"email_draft", "slack_thread", "teams_reply"}:
        raise ValueError("unsupported_share_target")

    audit_events = []
    if hasattr(runtime.db, "list_ap_audit_events"):
        try:
            rows = runtime.db.list_ap_audit_events(ap_item_id)
            audit_events = rows if isinstance(rows, list) else []
        except Exception:
            audit_events = []
    summary = build_finance_lead_summary_payload(runtime, ap_item, audit_events=audit_events)

    resolved_recipient = (
        str(recipient_email or "").strip()
        or os.getenv("CLEARLEDGR_FINANCE_LEAD_EMAIL", "").strip()
        or os.getenv("FINANCE_LEAD_EMAIL", "").strip()
        or ""
    )
    operator_note = str(note or "").strip()
    vendor = str(ap_item.get("vendor_name") or ap_item.get("vendor") or "Unknown vendor").strip()
    invoice_number = str(ap_item.get("invoice_number") or "N/A").strip()
    subject = f"[Clearledgr] Exception summary: {vendor} · Invoice {invoice_number}"
    body_lines = [
        "Hi,",
        "",
        "Clearledgr prepared the following AP exception summary for review:",
        "",
        *[f"- {line}" for line in (summary.get("lines") or [])],
    ]
    if operator_note:
        body_lines.extend(["", "Operator note:", operator_note])
    body_lines.extend(["", "Sent from Clearledgr Gmail Agent Actions."])
    draft = {
        "to": resolved_recipient,
        "subject": subject,
        "body": "\n".join(body_lines),
    }

    if preview_only:
        preview_payload: Dict[str, Any]
        if resolved_target == "email_draft":
            preview_payload = {
                "kind": "email_draft",
                "draft": draft,
                "recipient_email": resolved_recipient,
            }
        elif resolved_target == "slack_thread":
            slack_thread = (
                runtime.db.get_slack_thread(gmail_ref)
                if hasattr(runtime.db, "get_slack_thread")
                else None
            )
            if not slack_thread:
                raise ValueError("slack_thread_not_found")
            text_lines = [f"*{summary.get('title') or 'Finance exception summary'}*"]
            text_lines.extend([f"• {line}" for line in (summary.get("lines") or [])[:8]])
            if operator_note:
                text_lines.extend(["", f"_Operator note:_ {operator_note}"])
            preview_payload = {
                "kind": "slack_thread",
                "channel_id": str(slack_thread.get("channel_id") or ""),
                "thread_ts": str(slack_thread.get("thread_ts") or slack_thread.get("thread_id") or ""),
                "text": "\n".join(text_lines),
            }
        else:
            metadata = runtime._parse_json_dict(ap_item.get("metadata"))
            teams_meta = metadata.get("teams") if isinstance(metadata.get("teams"), dict) else {}
            channel_id = str((teams_meta or {}).get("channel") or "").strip()
            reply_to_id = str((teams_meta or {}).get("message_id") or "").strip()
            if not channel_id:
                raise ValueError("teams_channel_not_found")
            item_payload = {
                "id": ap_item_id,
                "vendor": vendor,
                "amount": ap_item.get("amount") or 0,
                "currency": ap_item.get("currency") or "USD",
                "invoice_number": invoice_number,
            }
            preview_payload = {
                "kind": "teams_reply",
                "channel_id": channel_id,
                "reply_to_id": reply_to_id or None,
                "activity": build_finance_summary_reply_activity(
                    item_payload,
                    list(summary.get("lines") or []),
                    summary_title=str(summary.get("title") or "Finance exception summary"),
                    reply_to_id=reply_to_id or None,
                ),
            }

        response = {
            "status": "preview",
            "target": resolved_target,
            "email_id": gmail_ref,
            "ap_item_id": ap_item_id,
            "summary": summary,
            "preview": preview_payload,
        }
        audit_row = runtime._append_runtime_audit(
            ap_item_id=ap_item_id,
            event_type="finance_summary_share_previewed",
            reason=f"finance_summary_preview_{resolved_target}",
            metadata={
                "target": resolved_target,
                "summary_title": summary.get("title"),
                "summary_lines": summary.get("lines"),
                "preview_kind": preview_payload.get("kind"),
                "recipient_email": resolved_recipient if resolved_target == "email_draft" else None,
                "slack_channel_id": preview_payload.get("channel_id") if resolved_target == "slack_thread" else None,
                "teams_channel_id": preview_payload.get("channel_id") if resolved_target == "teams_reply" else None,
                "response": response,
            },
            correlation_id=correlation_id,
            skill_id="ap_v1",
        )
        response["audit_event_id"] = (audit_row or {}).get("id")
        return response

    if resolved_target == "email_draft":
        response = {
            "status": "prepared",
            "target": resolved_target,
            "email_id": gmail_ref,
            "ap_item_id": ap_item_id,
            "summary": summary,
            "draft": draft,
        }
        audit_row = runtime._append_runtime_audit(
            ap_item_id=ap_item_id,
            event_type="finance_summary_share_prepared",
            reason="finance_summary_email_draft",
            metadata={
                "target": resolved_target,
                "recipient_email": resolved_recipient,
                "summary_title": summary.get("title"),
                "summary_lines": summary.get("lines"),
                "response": response,
            },
            correlation_id=correlation_id,
            skill_id="ap_v1",
        )
        response["audit_event_id"] = (audit_row or {}).get("id")
        return response

    workflow = get_invoice_workflow(runtime.organization_id)
    delivered = False
    if resolved_target == "slack_thread":
        slack_thread = (
            runtime.db.get_slack_thread(gmail_ref)
            if hasattr(runtime.db, "get_slack_thread")
            else None
        )
        if not slack_thread:
            raise ValueError("slack_thread_not_found")
        if not getattr(workflow, "slack_client", None):
            raise ValueError("slack_client_unavailable")
        text_lines = [f"*{summary.get('title') or 'Finance exception summary'}*"]
        text_lines.extend([f"• {line}" for line in (summary.get("lines") or [])[:8]])
        if operator_note:
            text_lines.extend(["", f"_Operator note:_ {operator_note}"])
        try:
            sent = await workflow.slack_client.send_message(
                channel=str(slack_thread.get("channel_id") or ""),
                thread_ts=str(slack_thread.get("thread_ts") or slack_thread.get("thread_id") or ""),
                text="\n".join(text_lines),
            )
            delivery = {
                "channel_id": sent.channel,
                "thread_ts": sent.thread_ts or sent.ts,
                "message_ts": sent.ts,
                "status": "sent",
            }
            delivered = True
        except Exception as exc:
            delivery = {"status": "error", "reason": str(exc)}
    else:
        metadata = runtime._parse_json_dict(ap_item.get("metadata"))
        teams_meta = metadata.get("teams") if isinstance(metadata.get("teams"), dict) else {}
        channel_id = str((teams_meta or {}).get("channel") or "").strip()
        reply_to_id = str((teams_meta or {}).get("message_id") or "").strip()
        if not channel_id:
            raise ValueError("teams_channel_not_found")
        item_payload = {
            "id": ap_item_id,
            "vendor": vendor,
            "amount": ap_item.get("amount") or 0,
            "currency": ap_item.get("currency") or "USD",
            "invoice_number": invoice_number,
            "agent_memory": summary.get("agent_memory") if isinstance(summary.get("agent_memory"), dict) else {},
            "agent_profile": summary.get("agent_profile") if isinstance(summary.get("agent_profile"), dict) else {},
            "agent_next_action": summary.get("agent_next_action") if isinstance(summary.get("agent_next_action"), dict) else {},
        }
        ok = await send_finance_summary_reply(
            item_payload,
            channel_id,
            list(summary.get("lines") or []),
            summary_title=str(summary.get("title") or "Finance exception summary"),
            reply_to_id=reply_to_id or None,
        )
        delivery = {
            "channel_id": channel_id,
            "reply_to_id": reply_to_id or None,
            "status": "sent" if ok else "error",
        }
        delivered = bool(ok)

    response = {
        "status": "shared" if delivered else "error",
        "target": resolved_target,
        "email_id": gmail_ref,
        "ap_item_id": ap_item_id,
        "summary": summary,
        "agent_memory": summary.get("agent_memory") if isinstance(summary.get("agent_memory"), dict) else {},
        "delivery": delivery,
    }
    audit_row = runtime._append_runtime_audit(
        ap_item_id=ap_item_id,
        event_type="finance_summary_shared" if delivered else "finance_summary_share_failed",
        reason=f"finance_summary_{resolved_target}",
        metadata={
            "target": resolved_target,
            "summary_title": summary.get("title"),
            "summary_lines": summary.get("lines"),
            "delivery": delivery,
            "response": response,
        },
        correlation_id=correlation_id,
        skill_id="ap_v1",
    )
    response["audit_event_id"] = (audit_row or {}).get("id")
    return response


def record_field_correction(
    runtime: Any,
    *,
    ap_item_id: str,
    field: str,
    original_value: Any = None,
    corrected_value: Any = None,
    feedback: Optional[str] = None,
    actor_id: Optional[str] = None,
) -> Dict[str, Any]:
    from clearledgr.services.finance_learning import get_finance_learning_service

    ap_item = runtime._resolve_ap_item(ap_item_id)
    resolved_ap_item_id = str(ap_item.get("id") or ap_item_id).strip() or str(ap_item_id)
    correlation_id = runtime._correlation_id_for_item(ap_item)
    resolved_actor = str(actor_id or runtime.actor_email or runtime.actor_id or "operator").strip() or "operator"
    metadata = runtime._parse_json_dict(ap_item.get("metadata"))
    sources = []
    if hasattr(runtime.db, "list_ap_item_sources"):
        try:
            sources = runtime.db.list_ap_item_sources(resolved_ap_item_id) or []
        except Exception:
            sources = []
    primary_source_meta = {}
    for source in sources:
        source_meta = runtime._parse_json_dict((source or {}).get("metadata"))
        if source_meta:
            primary_source_meta = source_meta
            break
    attachment_names = metadata.get("attachment_names")
    if not isinstance(attachment_names, list):
        attachment_names = primary_source_meta.get("attachment_names")
    expected_fields = {
        "vendor": ap_item.get("vendor_name") or ap_item.get("vendor"),
        "primary_amount": ap_item.get("amount"),
        "currency": ap_item.get("currency"),
        "primary_invoice": ap_item.get("invoice_number"),
        "due_date": ap_item.get("due_date"),
        "email_type": metadata.get("document_type") or metadata.get("email_type"),
    }
    if field == "vendor":
        expected_fields["vendor"] = corrected_value
    elif field == "amount":
        expected_fields["primary_amount"] = corrected_value
    elif field == "currency":
        expected_fields["currency"] = corrected_value
    elif field == "invoice_number":
        expected_fields["primary_invoice"] = corrected_value
    elif field == "due_date":
        expected_fields["due_date"] = corrected_value
    elif field == "document_type":
        expected_fields["email_type"] = corrected_value

    confidence_gate = metadata.get("confidence_gate") if isinstance(metadata.get("confidence_gate"), dict) else {}
    learning_context = {
        "ap_item_id": resolved_ap_item_id,
        "field": field,
        "vendor": ap_item.get("vendor_name"),
        "sender": ap_item.get("sender"),
        "subject": ap_item.get("subject"),
        "snippet": metadata.get("source_snippet") or primary_source_meta.get("snippet"),
        "body_excerpt": metadata.get("source_body_excerpt") or primary_source_meta.get("body_excerpt"),
        "attachment_names": attachment_names if isinstance(attachment_names, list) else [],
        "document_type": metadata.get("document_type") or metadata.get("email_type"),
        "source_channel": "gmail_extension",
        "event_source": "runtime_record_field_correction",
        "selected_source": "manual",
        "confidence_profile_id": confidence_gate.get("profile_id") or confidence_gate.get("learned_profile_id"),
        "expected_fields": expected_fields,
    }
    try:
        learning_result = get_finance_learning_service(
            runtime.organization_id,
            db=getattr(runtime, "db", None),
        ).record_manual_field_correction(
            field=field,
            original_value=original_value,
            corrected_value=corrected_value,
            context=learning_context,
            actor_id=resolved_actor,
            invoice_id=ap_item.get("thread_id"),
            feedback=feedback,
        )
    except Exception as exc:
        logger.warning("finance_learning.record_manual_field_correction failed: %s", exc)
        learning_result = {}

    audit_meta = {
        "field": field,
        "original_value": str(original_value) if original_value is not None else None,
        "corrected_value": str(corrected_value) if corrected_value is not None else None,
        "actor_id": resolved_actor,
        "feedback": feedback,
        "learning_result": learning_result,
    }
    response = {
        "status": "recorded",
        "ap_item_id": resolved_ap_item_id,
        "field": field,
        "learning_result": learning_result,
    }
    audit_row = runtime._append_runtime_audit(
        ap_item_id=resolved_ap_item_id,
        event_type="field_correction",
        reason="runtime_record_field_correction",
        metadata={
            **audit_meta,
            "response": response,
        },
        correlation_id=correlation_id,
        skill_id="ap_v1",
    )
    response["audit_event_id"] = (audit_row or {}).get("id")
    return response
