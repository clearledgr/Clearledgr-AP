"""
Enhanced AI API Endpoints for Clearledgr v1

Exposes the enhanced AI capabilities via REST API:
- POST /ai/categorize - Categorize transactions to GL accounts
- POST /ai/anomaly - Detect anomalies in transactions
- POST /ai/pattern-match - Find matches using learned patterns
- POST /ai/confidence - Adjust match confidence with LLM
- POST /ai/route - Route exceptions intelligently
- POST /ai/analyze-batch - Batch analysis endpoint
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from clearledgr.services.ai_enhanced import (
    EnhancedAIService,
    get_enhanced_ai_service,
    AnomalySeverity,
    ExceptionPriority,
)

router = APIRouter(prefix="/ai", tags=["Enhanced AI"])


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================

class GLAccount(BaseModel):
    """GL Account definition."""
    code: str
    name: str
    keywords: Optional[List[str]] = []


class Transaction(BaseModel):
    """Transaction data."""
    id: Optional[str] = None
    transaction_id: Optional[str] = None
    amount: float
    date: Optional[str] = None
    description: Optional[str] = ""
    vendor: Optional[str] = None
    counterparty: Optional[str] = None
    reference: Optional[str] = None
    source: Optional[str] = None


class CategorizationRequest(BaseModel):
    """Request to categorize a transaction."""
    description: str
    vendor: str
    amount: float
    gl_accounts: List[GLAccount]
    historical_examples: Optional[List[Dict[str, Any]]] = None


class CategorizationResponse(BaseModel):
    """Categorization result."""
    gl_code: str
    gl_name: str
    confidence: float
    reasoning: str
    alternative_codes: List[Dict[str, Any]] = []


class AnomalyRequest(BaseModel):
    """Request to detect anomalies."""
    transaction: Transaction
    historical_transactions: List[Transaction]
    vendor_history: Optional[Dict[str, Any]] = None


class AnomalyResponse(BaseModel):
    """Anomaly detection result."""
    is_anomaly: bool
    severity: str
    anomaly_type: Optional[str]
    explanation: str
    suggested_action: str
    historical_context: Dict[str, Any] = {}


class PatternMatchRequest(BaseModel):
    """Request to find pattern-based match."""
    unmatched_transaction: Transaction
    candidate_transactions: List[Transaction]
    learned_patterns: List[Dict[str, Any]] = []


class PatternMatchResponse(BaseModel):
    """Pattern match result."""
    matched_transaction_id: Optional[str]
    confidence: float
    pattern_used: str
    reasoning: str
    is_generalized: bool


class ConfidenceRequest(BaseModel):
    """Request to adjust match confidence."""
    source_transaction: Transaction
    target_transaction: Transaction
    algorithmic_score: float
    score_breakdown: Dict[str, Any]


class ConfidenceResponse(BaseModel):
    """Confidence adjustment result."""
    original_score: float
    adjusted_score: float
    should_match: bool
    reasoning: str
    factors: List[str] = []


class TeamMember(BaseModel):
    """Team member for routing."""
    name: str
    role: str
    expertise: Optional[str] = None


class RoutingRequest(BaseModel):
    """Request to route an exception."""
    exception: Dict[str, Any]
    team_members: List[TeamMember]
    historical_resolutions: Optional[List[Dict[str, Any]]] = None


class RoutingResponse(BaseModel):
    """Routing decision result."""
    assignee: str
    escalate_to: Optional[str]
    priority: str
    reasoning: str
    estimated_resolution_time: str
    suggested_actions: List[str] = []


class BatchCategorizationRequest(BaseModel):
    """Request for batch categorization."""
    transactions: List[Transaction]
    gl_accounts: List[GLAccount]
    historical_examples: Optional[List[Dict[str, Any]]] = None


class BatchAnomalyRequest(BaseModel):
    """Request for batch anomaly detection."""
    transactions: List[Transaction]
    historical_transactions: List[Transaction]


# =============================================================================
# API ENDPOINTS
# =============================================================================

@router.post("/categorize", response_model=CategorizationResponse)
async def categorize_transaction(request: CategorizationRequest):
    """
    Categorize a transaction to the appropriate GL account.
    
    Uses LLM with few-shot learning for intelligent categorization.
    Falls back to rule-based matching if LLM unavailable.
    """
    service = get_enhanced_ai_service()
    
    gl_accounts = [acc.model_dump() for acc in request.gl_accounts]
    
    result = service.categorize_transaction(
        description=request.description,
        vendor=request.vendor,
        amount=request.amount,
        gl_accounts=gl_accounts,
        historical_examples=request.historical_examples,
    )
    
    return CategorizationResponse(
        gl_code=result.gl_code,
        gl_name=result.gl_name,
        confidence=result.confidence,
        reasoning=result.reasoning,
        alternative_codes=result.alternative_codes,
    )


@router.post("/anomaly", response_model=AnomalyResponse)
async def detect_anomaly(request: AnomalyRequest):
    """
    Detect if a transaction is anomalous based on historical patterns.
    
    Analyzes amount spikes, timing anomalies, frequency changes,
    and other patterns that may indicate issues.
    """
    service = get_enhanced_ai_service()
    
    result = service.detect_anomaly(
        transaction=request.transaction.model_dump(),
        historical_transactions=[t.model_dump() for t in request.historical_transactions],
        vendor_history=request.vendor_history,
    )
    
    return AnomalyResponse(
        is_anomaly=result.is_anomaly,
        severity=result.severity.value,
        anomaly_type=result.anomaly_type,
        explanation=result.explanation,
        suggested_action=result.suggested_action,
        historical_context=result.historical_context,
    )


@router.post("/pattern-match", response_model=PatternMatchResponse)
async def find_pattern_match(request: PatternMatchRequest):
    """
    Find a matching transaction using learned patterns.
    
    Uses LLM to generalize patterns from historical successful matches
    and apply them to new transactions.
    """
    service = get_enhanced_ai_service()
    
    result = service.find_pattern_match(
        unmatched_transaction=request.unmatched_transaction.model_dump(),
        candidate_transactions=[t.model_dump() for t in request.candidate_transactions],
        learned_patterns=request.learned_patterns,
    )
    
    return PatternMatchResponse(
        matched_transaction_id=result.matched_transaction_id,
        confidence=result.confidence,
        pattern_used=result.pattern_used,
        reasoning=result.reasoning,
        is_generalized=result.is_generalized,
    )


@router.post("/confidence", response_model=ConfidenceResponse)
async def adjust_confidence(request: ConfidenceRequest):
    """
    Adjust match confidence using LLM contextual analysis.
    
    The LLM considers factors the algorithm might miss:
    - Fee patterns (0.25%, 2.9%+€0.30, etc.)
    - Settlement timing (1-3 day delays)
    - Different description formats
    - Embedded reference IDs
    """
    service = get_enhanced_ai_service()
    
    result = service.adjust_match_confidence(
        source_txn=request.source_transaction.model_dump(),
        target_txn=request.target_transaction.model_dump(),
        algorithmic_score=request.algorithmic_score,
        score_breakdown=request.score_breakdown,
    )
    
    return ConfidenceResponse(
        original_score=result.original_score,
        adjusted_score=result.adjusted_score,
        should_match=result.should_match,
        reasoning=result.reasoning,
        factors=result.factors,
    )


@router.post("/route", response_model=RoutingResponse)
async def route_exception(request: RoutingRequest):
    """
    Intelligently route an exception to the right team member.
    
    Considers:
    - Exception type and amount
    - Team member expertise
    - Historical resolution patterns
    - Escalation requirements
    """
    service = get_enhanced_ai_service()
    
    team_members = [m.model_dump() for m in request.team_members]
    
    result = service.route_exception(
        exception=request.exception,
        team_members=team_members,
        historical_resolutions=request.historical_resolutions,
    )
    
    return RoutingResponse(
        assignee=result.assignee,
        escalate_to=result.escalate_to,
        priority=result.priority.value,
        reasoning=result.reasoning,
        estimated_resolution_time=result.estimated_resolution_time,
        suggested_actions=result.suggested_actions,
    )


@router.post("/categorize-batch", response_model=List[CategorizationResponse])
async def categorize_batch(request: BatchCategorizationRequest):
    """
    Categorize multiple transactions in a batch.
    """
    service = get_enhanced_ai_service()
    
    gl_accounts = [acc.model_dump() for acc in request.gl_accounts]
    transactions = [t.model_dump() for t in request.transactions]
    
    results = service.categorize_batch(
        transactions=transactions,
        gl_accounts=gl_accounts,
        historical_examples=request.historical_examples,
    )
    
    return [
        CategorizationResponse(
            gl_code=r.gl_code,
            gl_name=r.gl_name,
            confidence=r.confidence,
            reasoning=r.reasoning,
            alternative_codes=r.alternative_codes,
        )
        for r in results
    ]


@router.post("/anomaly-batch")
async def detect_anomalies_batch(request: BatchAnomalyRequest):
    """
    Detect anomalies in multiple transactions.
    
    Returns transactions that are flagged as anomalous along with details.
    """
    service = get_enhanced_ai_service()
    
    transactions = [t.model_dump() for t in request.transactions]
    historical = [t.model_dump() for t in request.historical_transactions]
    
    results = service.detect_anomalies_batch(
        transactions=transactions,
        historical_transactions=historical,
    )
    
    return {
        "total_analyzed": len(results),
        "anomalies_found": sum(1 for _, r in results if r.is_anomaly),
        "results": [
            {
                "transaction": txn,
                "is_anomaly": result.is_anomaly,
                "severity": result.severity.value,
                "anomaly_type": result.anomaly_type,
                "explanation": result.explanation,
                "suggested_action": result.suggested_action,
            }
            for txn, result in results
        ],
    }


@router.get("/health")
async def ai_health_check():
    """
    Check AI service health and capabilities.
    """
    service = get_enhanced_ai_service()
    
    return {
        "status": "healthy",
        "llm_available": service._has_llm,
        "capabilities": [
            "categorization",
            "anomaly_detection",
            "pattern_matching",
            "confidence_adjustment",
            "exception_routing",
        ],
        "fallback_mode": not service._has_llm,
    }
