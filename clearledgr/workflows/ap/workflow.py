"""Temporal AP workflow definition.

This workflow keeps durable command state and accepts approval/retry signals.
Deterministic state mutations remain enforced by server-side transition rules.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict

from temporalio import workflow

from .types import APWorkflowCommand


@workflow.defn(name="clearledgr.ap.workflow")
class APTemporalWorkflow:
    def __init__(self) -> None:
        self.state = "received"
        self.ap_item_id = ""
        self.run_id = ""
        self.organization_id = "default"
        self.correlation_id = ""
        self.last_command: Dict[str, Any] = {}
        self.last_approval_decision: Dict[str, Any] | None = None
        self.retry_requested = False
        self.more_info_requested = False
        self.closed = False

    @workflow.run
    async def run(self, command: Dict[str, Any]) -> Dict[str, Any]:
        initial = APWorkflowCommand(**command)
        self.ap_item_id = initial.ap_item_id
        self.run_id = initial.run_id
        self.organization_id = initial.organization_id
        self.correlation_id = initial.correlation_id
        self.last_command = command

        if initial.payload.get("initial_state"):
            self.state = str(initial.payload.get("initial_state"))

        await workflow.wait_condition(lambda: self.closed, timeout=timedelta(days=7))

        return {
            "ap_item_id": self.ap_item_id,
            "run_id": self.run_id,
            "organization_id": self.organization_id,
            "state": self.state,
            "correlation_id": self.correlation_id,
            "last_command": self.last_command,
        }

    @workflow.signal
    def approval_decision(self, payload: Dict[str, Any]) -> None:
        self.last_approval_decision = payload
        action = str(payload.get("action") or "").lower()
        if action == "approve":
            if self.state == "needs_approval":
                self.state = "approved"
        elif action == "reject":
            if self.state in {"needs_approval", "approved"}:
                self.state = "rejected"
                self.closed = True

    @workflow.signal
    def request_more_info(self, payload: Dict[str, Any]) -> None:
        self.more_info_requested = True
        if self.state in {"validated", "needs_approval"}:
            self.state = "needs_info"

    @workflow.signal
    def retry_post(self, payload: Dict[str, Any]) -> None:
        self.retry_requested = True
        if self.state == "failed_post":
            self.state = "ready_to_post"

    @workflow.signal
    def mark_closed(self) -> None:
        self.closed = True

    @workflow.query
    def get_status(self) -> Dict[str, Any]:
        return {
            "ap_item_id": self.ap_item_id,
            "run_id": self.run_id,
            "organization_id": self.organization_id,
            "state": self.state,
            "approval_decision": self.last_approval_decision,
            "retry_requested": self.retry_requested,
            "more_info_requested": self.more_info_requested,
            "closed": self.closed,
        }
