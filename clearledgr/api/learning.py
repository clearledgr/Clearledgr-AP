"""Learning API endpoints.

Provides access to the feedback loop / learning service:
- View learned patterns
- Manually add/edit patterns
- Export/import patterns
- View learning statistics
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from clearledgr.services.learning import get_learning_service

router = APIRouter(prefix="/learning", tags=["learning"])


# ============================================================================
# REQUEST MODELS
# ============================================================================

class RecordApprovalRequest(BaseModel):
    """Request to record an approved invoice."""
    vendor: str
    gl_code: str
    gl_description: str
    amount: float
    currency: str = "USD"
    was_auto_approved: bool = False
    was_corrected: bool = False
    original_suggestion: Optional[str] = None


class SuggestGLRequest(BaseModel):
    """Request for GL code suggestion."""
    vendor: str
    amount: Optional[float] = None
    description: Optional[str] = None


class ImportPatternsRequest(BaseModel):
    """Request to import patterns."""
    json_data: str


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.get("/statistics/{organization_id}")
async def get_statistics(organization_id: str = "default"):
    """Get learning statistics for an organization."""
    service = get_learning_service(organization_id)
    return service.get_statistics()


@router.get("/patterns/{organization_id}")
async def get_patterns(organization_id: str = "default"):
    """Get all learned vendor→GL patterns."""
    service = get_learning_service(organization_id)
    patterns = service.get_all_patterns()
    
    return {
        "patterns": patterns,
        "count": len(patterns),
        "organization_id": organization_id,
    }


@router.get("/vendor/{vendor}")
async def get_vendor_history(vendor: str, organization_id: str = "default"):
    """Get full history for a specific vendor."""
    service = get_learning_service(organization_id)
    return service.get_vendor_history(vendor)


@router.post("/suggest")
async def suggest_gl_code(
    request: SuggestGLRequest, 
    organization_id: str = "default"
):
    """
    Get GL code suggestion based on learned patterns.
    
    Returns the most likely GL code for this vendor based on history.
    """
    service = get_learning_service(organization_id)
    suggestion = service.suggest_gl_code(
        vendor=request.vendor,
        amount=request.amount,
        description=request.description,
    )
    
    if suggestion:
        return {
            "has_suggestion": True,
            "suggestion": suggestion,
        }
    
    return {
        "has_suggestion": False,
        "message": f"No pattern found for vendor: {request.vendor}",
    }


@router.post("/record")
async def record_approval(
    request: RecordApprovalRequest,
    organization_id: str = "default"
):
    """
    Record an approved invoice to learn from.
    
    Call this when an invoice is approved to train the system.
    If the user corrected the GL code, set was_corrected=true and
    include original_suggestion.
    """
    service = get_learning_service(organization_id)
    
    service.record_approval(
        vendor=request.vendor,
        gl_code=request.gl_code,
        gl_description=request.gl_description,
        amount=request.amount,
        currency=request.currency,
        was_auto_approved=request.was_auto_approved,
        was_corrected=request.was_corrected,
        original_suggestion=request.original_suggestion,
    )
    
    return {
        "status": "recorded",
        "vendor": request.vendor,
        "gl_code": request.gl_code,
        "statistics": service.get_statistics(),
    }


@router.post("/export/{organization_id}")
async def export_patterns(organization_id: str = "default"):
    """Export all patterns as JSON for backup."""
    service = get_learning_service(organization_id)
    json_data = service.export_patterns()
    
    return {
        "status": "exported",
        "json_data": json_data,
    }


@router.post("/import")
async def import_patterns(
    request: ImportPatternsRequest,
    organization_id: str = "default"
):
    """Import patterns from JSON backup."""
    service = get_learning_service(organization_id)
    
    try:
        count = service.import_patterns(request.json_data)
        return {
            "status": "imported",
            "patterns_imported": count,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")


# ============================================================================
# INTEGRATION ENDPOINT - Called by invoice approval flow
# ============================================================================

@router.post("/on-approval")
async def on_invoice_approved(
    vendor: str,
    gl_code: str,
    gl_description: str,
    amount: float,
    currency: str = "USD",
    confidence: float = 0.0,
    was_auto: bool = False,
    suggested_gl: Optional[str] = None,
    organization_id: str = "default",
):
    """
    Webhook called when an invoice is approved.
    
    This is the main integration point - call this from the approval flow.
    The learning service will:
    1. Record the vendor→GL mapping
    2. Track if the user corrected the suggestion
    3. Update confidence scores
    """
    service = get_learning_service(organization_id)
    
    was_corrected = suggested_gl is not None and suggested_gl != gl_code
    
    service.record_approval(
        vendor=vendor,
        gl_code=gl_code,
        gl_description=gl_description,
        amount=amount,
        currency=currency,
        was_auto_approved=was_auto,
        was_corrected=was_corrected,
        original_suggestion=suggested_gl,
    )
    
    return {
        "learned": True,
        "vendor": vendor,
        "gl_code": gl_code,
        "was_corrected": was_corrected,
    }
