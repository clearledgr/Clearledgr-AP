"""
Clearledgr Chat API

REST API for Vita - the autonomous finance agent.
Vita doesn't just chat - Vita EXECUTES.

Used by Gmail extension, Sheets add-on, and Slack.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from clearledgr.agents.vita import get_vita_agent

# Audit service for tracking Vita commands
try:
    from clearledgr.services.vita_audit import get_vita_audit_service, ActionStatus
    AUDIT_AVAILABLE = True
except ImportError:
    AUDIT_AVAILABLE = False

router = APIRouter(prefix="/chat", tags=["Chat"])


class ChatMessage(BaseModel):
    """A message in the conversation."""
    text: str = Field(..., description="Message text")
    user_id: str = Field(..., description="User identifier")
    channel: str = Field(default="api", description="Channel (api, gmail, web)")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Additional context")


class ChatResponse(BaseModel):
    """Response from the chat API."""
    text: str = Field(..., description="Response text")
    data: Optional[Dict[str, Any]] = Field(default=None, description="Structured data")
    actions: List[Dict[str, Any]] = Field(default=[], description="Available actions")
    suggestions: List[str] = Field(default=[], description="Suggested follow-ups")
    requires_confirmation: bool = Field(default=False, description="Whether action needs confirmation")


class ChatHistoryResponse(BaseModel):
    """Conversation history."""
    messages: List[Dict[str, Any]]
    current_topic: Optional[str]


@router.post("/message", response_model=ChatResponse)
async def send_message(message: ChatMessage):
    """
    Send a message to Vita and get action.
    
    Vita is an autonomous agent - she executes, not just responds.
    
    Examples:
    - "Run reconciliation" → Actually runs reconciliation
    - "Approve all drafts" → Actually approves them
    - "What needs attention?" → Proactive status with priorities
    """
    agent = get_vita_agent()
    
    try:
        response = await agent.process(
            text=message.text,
            user_id=message.user_id,
            channel=message.channel,
            metadata=message.metadata,
        )
        
        return ChatResponse(
            text=response.text,
            data=response.data,
            actions=response.actions,
            suggestions=response.suggestions,
            requires_confirmation=response.requires_confirmation,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history/{user_id}", response_model=ChatHistoryResponse)
async def get_history(user_id: str, channel: str = "api"):
    """Get conversation history for a user."""
    # Vita is stateless - each request is independent
    return ChatHistoryResponse(
        messages=[],
        current_topic=None,
    )


@router.delete("/history/{user_id}")
async def clear_history(user_id: str, channel: str = "api"):
    """Clear conversation history for a user."""
    # Vita is stateless
    return {"status": "cleared", "user_id": user_id}


class ConfirmActionRequest(BaseModel):
    """Request to confirm a pending action."""
    audit_id: str = Field(..., description="Audit ID of the pending action")
    user_id: str = Field(..., description="User confirming the action")
    user_email: Optional[str] = Field(default=None, description="User's email")


@router.post("/action")
async def execute_action(
    action_id: str,
    user_id: str,
    value: Optional[str] = None,
    channel: str = "api",
    user_email: Optional[str] = None,
    user_name: Optional[str] = None,
    organization_id: str = "default",
):
    """
    Execute an action from Vita's response.
    
    Actions map directly to Vita commands - she does the work.
    """
    agent = get_vita_agent()
    metadata = {"organization_id": organization_id, "user_email": user_email, "user_name": user_name}
    
    # Map action_id to Vita command
    action_commands = {
        "view_exceptions": "Show exceptions",
        "view_pending_approvals": "Show pending approvals",
        "approve_drafts": "Approve all drafts",
        "approve_all": "Approve all drafts",
        "approve_high_confidence": "Approve high confidence drafts",
        "run_reconciliation": "Run reconciliation",
        "process_all_emails": "Process finance emails",
        "generate_entries": "Generate journal entries",
        "post_to_sap": "Post to SAP",
        "review_exceptions": "Show exceptions",
        "auto_resolve_routine": "Resolve routine exceptions",
    }
    
    command = action_commands.get(action_id)
    if command:
        response = await agent.process(command, user_id, channel, metadata)
        return ChatResponse(
            text=response.text,
            data=response.data,
            actions=response.actions,
            suggestions=response.suggestions,
            requires_confirmation=response.requires_confirmation,
        )
    
    # Handle cancel/decline
    if action_id in ["cancel", "cancel_reconcile", "cancel_sap_post"]:
        return {
            "text": "Cancelled.",
            "data": None,
            "actions": [],
            "suggestions": ["What's my status?"],
        }
    
    raise HTTPException(status_code=400, detail=f"Unknown action: {action_id}")


@router.post("/confirm")
async def confirm_action(request: ConfirmActionRequest):
    """
    Confirm a pending action by audit ID.
    
    Used when actions require explicit confirmation before execution.
    """
    if not AUDIT_AVAILABLE:
        raise HTTPException(status_code=501, detail="Audit service not available")
    
    audit_service = get_vita_audit_service()
    
    # Confirm the action
    entry = audit_service.confirm_action(
        audit_id=request.audit_id,
        confirmed_by=request.user_id,
        confirmed_by_email=request.user_email,
    )
    
    if not entry:
        raise HTTPException(status_code=404, detail="Audit entry not found")
    
    return {
        "status": "confirmed",
        "audit_id": entry.audit_id,
        "action_type": entry.action_type,
        "confirmed_by": request.user_id,
        "confirmed_at": entry.confirmed_at,
    }


@router.get("/audit/{audit_id}")
async def get_audit_entry(audit_id: str):
    """Get details of a specific audit entry."""
    if not AUDIT_AVAILABLE:
        raise HTTPException(status_code=501, detail="Audit service not available")
    
    audit_service = get_vita_audit_service()
    entry = audit_service.get_entry(audit_id)
    
    if not entry:
        raise HTTPException(status_code=404, detail="Audit entry not found")
    
    return entry.to_dict()


@router.get("/audit/user/{user_id}")
async def get_user_audit_history(user_id: str, limit: int = 50):
    """Get audit history for a user."""
    if not AUDIT_AVAILABLE:
        raise HTTPException(status_code=501, detail="Audit service not available")
    
    audit_service = get_vita_audit_service()
    entries = audit_service.get_user_history(user_id, limit=limit)
    
    return {
        "user_id": user_id,
        "entries": [e.to_dict() for e in entries],
        "count": len(entries),
    }


@router.get("/audit/pending")
async def get_pending_confirmations(user_id: Optional[str] = None):
    """Get all pending action confirmations."""
    if not AUDIT_AVAILABLE:
        raise HTTPException(status_code=501, detail="Audit service not available")
    
    audit_service = get_vita_audit_service()
    entries = audit_service.get_pending_confirmations(user_id)
    
    return {
        "pending": [e.to_dict() for e in entries],
        "count": len(entries),
    }


@router.get("/suggestions")
async def get_suggestions(user_id: str, channel: str = "api", organization_id: str = "default"):
    """Get proactive suggestions based on current state."""
    agent = get_vita_agent()
    
    # Get current status to make suggestions relevant
    try:
        dashboard = agent.engine.get_dashboard_data(organization_id)
        
        suggestions = []
        
        # Prioritize by urgency
        if dashboard.get("open_exceptions", 0) > 0:
            suggestions.append("Show exceptions")
        
        if dashboard.get("pending_drafts", 0) > 0:
            suggestions.append("Approve drafts")
        
        if dashboard.get("email_count", 0) > 0:
            suggestions.append("Process finance emails")
        
        suggestions.extend([
            "Run reconciliation",
            "What needs attention?",
        ])
        
        return {"suggestions": suggestions[:5]}
    except Exception:
        return {"suggestions": [
            "What's my status?",
            "Run reconciliation",
            "Show exceptions",
        ]}
