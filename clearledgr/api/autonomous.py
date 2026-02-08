"""
Autonomous Agent API Endpoints

Exposes the autonomous agent runtime for:
- Starting/stopping agents
- Monitoring agent status
- Receiving webhooks from Gmail/Sheets
- Recording corrections and learning
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field

from clearledgr.agents.runtime import (
    AgentRuntime,
    Event,
    EventType,
    get_runtime,
    start_runtime,
    stop_runtime,
)
from clearledgr.agents.gmail_watcher import GmailWatcherAgent, GmailWebhookHandler
from clearledgr.agents.autonomous_reconciliation import AutonomousReconciliationAgent
from clearledgr.services.compounding_learning import (
    get_learning_service,
    Correction,
    LearningMetrics,
)

router = APIRouter(prefix="/autonomous", tags=["Autonomous Agents"])

# Global state
_initialized = False
_gmail_watcher: Optional[GmailWatcherAgent] = None
_recon_agent: Optional[AutonomousReconciliationAgent] = None


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================

class AgentConfig(BaseModel):
    """Configuration for an agent."""
    auto_execute_threshold: float = 0.95
    notify_after_threshold: float = 0.85
    ask_confirmation_threshold: float = 0.70


class RuntimeConfig(BaseModel):
    """Configuration for the runtime."""
    agents: Dict[str, AgentConfig] = {}


class EmailWebhookPayload(BaseModel):
    """Payload from Gmail webhook."""
    type: str  # email_received, inbox_scan
    email: Optional[Dict[str, Any]] = None
    emails: Optional[List[Dict[str, Any]]] = None


class SheetsWebhookPayload(BaseModel):
    """Payload from Sheets webhook."""
    type: str  # data_updated, reconciliation_requested
    sheet_id: str
    sheet_name: Optional[str] = None
    rows_changed: Optional[int] = None
    data: Optional[Dict[str, Any]] = None


class CorrectionRequest(BaseModel):
    """Request to record a correction."""
    correction_type: str  # match, categorization, routing
    original_value: Dict[str, Any]
    corrected_value: Dict[str, Any]
    user_email: str
    context: Optional[Dict[str, Any]] = None
    organization_id: Optional[str] = None


class ApprovalRequest(BaseModel):
    """Request to approve/reject a pending decision."""
    decision_id: str
    approved: bool
    approver_email: str
    notes: Optional[str] = None


# =============================================================================
# INITIALIZATION
# =============================================================================

async def initialize_agents() -> None:
    """Initialize all autonomous agents."""
    global _initialized, _gmail_watcher, _recon_agent
    
    if _initialized:
        return
    
    runtime = get_runtime()
    
    # Create agents
    _gmail_watcher = GmailWatcherAgent(runtime.event_bus)
    _recon_agent = AutonomousReconciliationAgent(runtime.event_bus)
    
    # Register agents
    runtime.register_agent(_gmail_watcher)
    runtime.register_agent(_recon_agent)
    
    # Start runtime
    await runtime.start()
    
    _initialized = True


# =============================================================================
# RUNTIME ENDPOINTS
# =============================================================================

@router.post("/start")
async def start_agents(background_tasks: BackgroundTasks):
    """
    Start the autonomous agent runtime.
    
    This starts all agents and they begin monitoring for events.
    """
    try:
        await initialize_agents()
        return {
            "status": "started",
            "message": "Autonomous agents are now running",
            "agents": list(get_runtime().agents.keys()),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stop")
async def stop_agents():
    """
    Stop the autonomous agent runtime.
    """
    global _initialized
    
    try:
        await stop_runtime()
        _initialized = False
        return {
            "status": "stopped",
            "message": "Autonomous agents have been stopped",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_status():
    """
    Get the status of the autonomous runtime.
    """
    runtime = get_runtime()
    try:
        from clearledgr.core.database import get_db
        from clearledgr.services.gmail_api import token_store
        states = get_db().list_gmail_autopilot_states()
        token_count = len(token_store.list_all())
        autopilot_summary = {
            # Treat OAuth-connected users as active even before first inbox scan.
            "users": max(len(states), token_count),
            "connected_users": token_count,
            "watched_users": len(states),
            "last_scan_at": max([s.get("last_scan_at") for s in states if s.get("last_scan_at")] or [None]),
            "last_error": next((s.get("last_error") for s in states if s.get("last_error")), None),
        }
    except Exception:
        autopilot_summary = {"users": 0, "last_scan_at": None, "last_error": None}
    
    return {
        "initialized": _initialized,
        "runtime_status": runtime.get_status(),
        "learning_metrics": get_learning_service().get_learning_metrics().__dict__,
        "gmail_autopilot": autopilot_summary,
    }


@router.post("/configure")
async def configure_runtime(config: RuntimeConfig):
    """
    Configure the runtime and agents.
    """
    runtime = get_runtime()
    runtime.configure(config.model_dump())
    
    return {
        "status": "configured",
        "config": config.model_dump(),
    }


# =============================================================================
# WEBHOOK ENDPOINTS
# =============================================================================

@router.post("/webhook/gmail")
async def gmail_webhook(payload: EmailWebhookPayload):
    """
    Receive webhook from Gmail extension.
    
    The Gmail extension calls this when:
    - A new email is received
    - The inbox is scanned
    """
    global _gmail_watcher
    
    # Ensure agents are initialized
    if not _initialized:
        await initialize_agents()
    
    if not _gmail_watcher:
        raise HTTPException(status_code=500, detail="Gmail watcher not initialized")
    
    handler = GmailWebhookHandler(_gmail_watcher)
    result = await handler.handle_webhook(payload.model_dump())
    
    return result


@router.post("/webhook/sheets")
async def sheets_webhook(payload: SheetsWebhookPayload):
    """
    Receive webhook from Google Sheets.
    
    Sheets calls this when:
    - Data is updated in a watched sheet
    - Reconciliation is requested
    """
    if not _initialized:
        await initialize_agents()
    
    runtime = get_runtime()
    
    # Determine event type
    if payload.type == "data_updated":
        event_type = EventType.SHEETS_DATA_UPDATED
    elif payload.type == "reconciliation_requested":
        event_type = EventType.SHEETS_RECONCILIATION_REQUESTED
    else:
        event_type = EventType.SHEETS_DATA_UPDATED
    
    # Publish event
    await runtime.publish_event(Event(
        event_type=event_type,
        payload={
            "sheet_id": payload.sheet_id,
            "sheet_name": payload.sheet_name,
            "rows_changed": payload.rows_changed,
            "data": payload.data,
        },
        source="sheets_webhook",
    ))
    
    return {
        "status": "received",
        "event_type": event_type.value,
    }


# =============================================================================
# LEARNING ENDPOINTS
# =============================================================================

@router.post("/learn/correction")
async def record_correction(request: CorrectionRequest):
    """
    Record a user correction for learning.
    
    When a user corrects an agent's decision, call this endpoint
    to enable the system to learn from the correction.
    """
    service = get_learning_service()
    
    correction = service.record_correction(
        correction_type=request.correction_type,
        original_value=request.original_value,
        corrected_value=request.corrected_value,
        user_email=request.user_email,
        context=request.context,
        organization_id=request.organization_id,
    )
    
    return {
        "status": "recorded",
        "correction_id": correction.correction_id,
        "message": "Correction recorded, system will learn from this",
    }


@router.get("/learn/metrics")
async def get_learning_metrics():
    """
    Get learning metrics.
    
    Shows how much the system has learned and accuracy improvements.
    """
    service = get_learning_service()
    metrics = service.get_learning_metrics()
    
    return {
        "total_corrections": metrics.total_corrections,
        "patterns_learned": metrics.patterns_learned,
        "accuracy_rate": metrics.accuracy_after,
        "by_type": metrics.by_type,
    }


@router.get("/learn/patterns")
async def get_learned_patterns(
    pattern_type: Optional[str] = None,
    min_confidence: float = 0.5,
):
    """
    Get learned patterns.
    """
    service = get_learning_service()
    patterns = []
    
    for pattern in service._pattern_cache.values():
        if pattern_type and pattern.pattern_type != pattern_type:
            continue
        if pattern.confidence < min_confidence:
            continue
        
        patterns.append({
            "pattern_id": pattern.pattern_id,
            "pattern_type": pattern.pattern_type,
            "confidence": pattern.confidence,
            "usage_count": pattern.usage_count,
            "success_rate": pattern.success_rate,
            "pattern_data": pattern.pattern_data,
        })
    
    return {
        "patterns": patterns,
        "count": len(patterns),
    }


# =============================================================================
# APPROVAL ENDPOINTS
# =============================================================================

@router.post("/approve")
async def approve_decision(request: ApprovalRequest):
    """
    Approve or reject a pending agent decision.
    """
    if not _initialized:
        await initialize_agents()
    
    runtime = get_runtime()
    
    event_type = EventType.APPROVAL_GRANTED if request.approved else EventType.APPROVAL_REJECTED
    
    await runtime.publish_event(Event(
        event_type=event_type,
        payload={
            "decision_id": request.decision_id,
            "approved": request.approved,
            "approver_email": request.approver_email,
            "notes": request.notes,
        },
        source="approval_api",
    ))
    
    return {
        "status": "approved" if request.approved else "rejected",
        "decision_id": request.decision_id,
    }


# =============================================================================
# EVENT ENDPOINTS
# =============================================================================

@router.get("/events/recent")
async def get_recent_events(
    limit: int = 100,
    event_type: Optional[str] = None,
):
    """
    Get recent events from the event bus.
    """
    runtime = get_runtime()
    
    event_types = None
    if event_type:
        try:
            event_types = [EventType(event_type)]
        except ValueError:
            pass
    
    events = runtime.event_bus.get_recent_events(
        event_types=event_types,
        limit=limit,
    )
    
    return {
        "events": [e.to_dict() for e in events],
        "count": len(events),
    }


@router.post("/events/publish")
async def publish_event(
    event_type: str,
    payload: Dict[str, Any],
    source: str = "api",
):
    """
    Manually publish an event to the event bus.
    
    Useful for testing or triggering specific agent behaviors.
    """
    if not _initialized:
        await initialize_agents()
    
    try:
        evt_type = EventType(event_type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid event type: {event_type}",
        )
    
    runtime = get_runtime()
    
    await runtime.publish_event(Event(
        event_type=evt_type,
        payload=payload,
        source=source,
    ))
    
    return {
        "status": "published",
        "event_type": event_type,
    }


# =============================================================================
# HEALTH & DIAGNOSTICS
# =============================================================================

@router.get("/health")
async def health_check():
    """
    Health check for autonomous system.
    """
    runtime = get_runtime()
    
    return {
        "status": "healthy" if _initialized else "not_started",
        "agents_running": runtime.is_running,
        "agent_count": len(runtime.agents),
        "event_history_size": len(runtime.event_bus._event_history),
    }
