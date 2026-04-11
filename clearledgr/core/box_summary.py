"""Box Summary Object — Agent Design Specification §8.1.

The planning engine maintains a structured summary of each Box alongside
the full timeline. The summary is updated after every state change. When
the agent needs to reason about a Box that has a long history, it uses
the summary rather than the timeline.

Fields:
  current_stage:        The Box's current pipeline stage.
  key_fields:           5 most important extracted fields.
  match_result_summary: One-line match result.
  last_3_actions:       Last 3 timeline entries, condensed.
  open_issues:          Unresolved flags.
  waiting_since:        What it's waiting for and since when.

Usage:
    from clearledgr.core.box_summary import build_box_summary
    summary = build_box_summary(ap_item_id, db=db)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class BoxSummary:
    """Structured summary of a Box for context-efficient Claude calls."""

    current_stage: str = ""
    key_fields: Dict[str, Any] = field(default_factory=dict)
    match_result_summary: str = ""
    last_3_actions: List[str] = field(default_factory=list)
    open_issues: List[Dict[str, str]] = field(default_factory=list)
    waiting_since: Optional[str] = None

    def to_prompt_text(self) -> str:
        """Render as compact text for Claude context (not JSON — tokens matter)."""
        lines = [f"Stage: {self.current_stage}"]

        if self.key_fields:
            kf = self.key_fields
            lines.append(
                f"Vendor: {kf.get('vendor_name', '?')} | "
                f"Amount: {kf.get('currency', '')} {kf.get('amount', '?')} | "
                f"Invoice: {kf.get('invoice_number', '?')} | "
                f"Due: {kf.get('due_date', '?')} | "
                f"PO: {kf.get('po_number', 'none')}"
            )

        if self.match_result_summary:
            lines.append(f"Match: {self.match_result_summary}")

        if self.last_3_actions:
            lines.append("Recent actions:")
            for action in self.last_3_actions:
                lines.append(f"  - {action}")

        if self.open_issues:
            lines.append("Open issues:")
            for issue in self.open_issues:
                lines.append(f"  - [{issue.get('severity', 'info')}] {issue.get('description', '?')}")

        if self.waiting_since:
            lines.append(f"Waiting since: {self.waiting_since}")

        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "current_stage": self.current_stage,
            "key_fields": self.key_fields,
            "match_result_summary": self.match_result_summary,
            "last_3_actions": self.last_3_actions,
            "open_issues": self.open_issues,
            "waiting_since": self.waiting_since,
        }


def build_box_summary(
    ap_item_id: str,
    db: Any = None,
) -> BoxSummary:
    """Build a BoxSummary from the current AP item state.

    Reads the item, its timeline, and its fraud/waiting state
    to produce a compact summary suitable for Claude context.
    """
    if db is None:
        from clearledgr.core.database import get_db
        db = get_db()

    summary = BoxSummary()

    try:
        item = db.get_ap_item(ap_item_id)
        if not item:
            return summary

        # Current stage
        summary.current_stage = item.get("state") or "unknown"

        # Key fields
        summary.key_fields = {
            "vendor_name": item.get("vendor_name") or "Unknown",
            "amount": item.get("amount"),
            "currency": item.get("currency") or "USD",
            "invoice_number": item.get("invoice_number") or "",
            "due_date": item.get("due_date") or "",
            "po_number": item.get("po_number") or "",
        }

        # Match result summary
        match_status = item.get("match_status") or ""
        exception_reason = item.get("exception_reason") or ""
        if match_status:
            summary.match_result_summary = match_status
            if exception_reason:
                summary.match_result_summary += f" — {exception_reason[:100]}"

        # Last 3 actions from timeline
        try:
            timeline = item.get("timeline") or []
            if isinstance(timeline, str):
                timeline = json.loads(timeline) if timeline else []
            if isinstance(timeline, list):
                recent = timeline[-3:] if len(timeline) > 3 else timeline
                for entry in recent:
                    if isinstance(entry, dict):
                        action = entry.get("summary") or entry.get("event_type") or entry.get("action") or "action"
                        ts = entry.get("timestamp") or entry.get("ts") or entry.get("created_at") or ""
                        summary.last_3_actions.append(f"{action} ({ts[:16]})" if ts else action)
                    elif isinstance(entry, str):
                        summary.last_3_actions.append(entry[:100])
        except Exception:
            pass

        # Open issues from fraud_flags
        fraud_flags = item.get("fraud_flags")
        if fraud_flags:
            if isinstance(fraud_flags, str):
                try:
                    fraud_flags = json.loads(fraud_flags)
                except Exception:
                    fraud_flags = []
            if isinstance(fraud_flags, list):
                for flag in fraud_flags:
                    if isinstance(flag, dict) and not flag.get("resolved_at"):
                        summary.open_issues.append({
                            "severity": "warning",
                            "description": flag.get("flag_type", "unknown fraud flag"),
                        })

        # Field confidence issues
        field_confidences = item.get("field_confidences")
        if field_confidences:
            if isinstance(field_confidences, str):
                try:
                    field_confidences = json.loads(field_confidences)
                except Exception:
                    field_confidences = {}
            if isinstance(field_confidences, dict):
                for field_name, conf in field_confidences.items():
                    if isinstance(conf, (int, float)) and conf < 0.5:
                        summary.open_issues.append({
                            "severity": "info",
                            "description": f"Low confidence on {field_name}: {conf:.1%}",
                        })

        # Waiting condition
        waiting = item.get("waiting_condition")
        if waiting:
            if isinstance(waiting, str):
                try:
                    waiting = json.loads(waiting)
                except Exception:
                    waiting = None
            if isinstance(waiting, dict) and waiting.get("type"):
                summary.waiting_since = (
                    f"Waiting for {waiting['type']} since {waiting.get('set_at', '?')}"
                )

    except Exception as exc:
        logger.debug("[BoxSummary] Failed to build summary for %s: %s", ap_item_id, exc)

    return summary
