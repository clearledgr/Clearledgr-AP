"""Bank feed API endpoints.

Provides unified access to bank account data across:
- Okra (Africa: Nigeria, Kenya, South Africa)
- TrueLayer (UK and Europe)
- Nordigen/GoCardless (EU Open Banking)
"""
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from clearledgr.services.bank_feeds import (
    get_bank_service,
    OkraService,
    TrueLayerService,
    NordigenService,
)

router = APIRouter(prefix="/bank-feeds", tags=["bank-feeds"])


# ============================================================================
# REQUEST/RESPONSE MODELS
# ============================================================================

class ConnectRequest(BaseModel):
    """Request to connect a bank account."""
    provider: str  # okra, truelayer, nordigen
    authorization_code: str
    redirect_uri: Optional[str] = None


class TransactionsRequest(BaseModel):
    """Request for account transactions."""
    access_token: str
    account_id: str
    from_date: Optional[str] = None  # ISO date
    to_date: Optional[str] = None  # ISO date
    limit: int = 500


class BalanceRequest(BaseModel):
    """Request for account balance."""
    access_token: str
    account_id: str


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.get("/providers")
async def list_providers():
    """List available bank feed providers and their status."""
    return {
        "providers": [
            {
                "id": "okra",
                "name": "Okra",
                "description": "Open finance APIs for Africa",
                "countries": ["NG", "KE", "ZA"],
                "configured": OkraService().is_configured(),
            },
            {
                "id": "truelayer",
                "name": "TrueLayer",
                "description": "Open Banking for UK and Europe",
                "countries": ["GB", "IE", "ES", "FR", "DE", "IT", "NL"],
                "configured": TrueLayerService().is_configured(),
            },
            {
                "id": "nordigen",
                "name": "Nordigen (GoCardless)",
                "description": "Free EU Open Banking API",
                "countries": ["GB", "DE", "FR", "ES", "IT", "NL", "PL", "SE", "NO", "FI"],
                "configured": NordigenService().is_configured(),
            },
        ]
    }


@router.get("/institutions/{country}")
async def list_institutions(
    country: str,
    provider: str = Query("nordigen", description="Bank feed provider"),
):
    """
    List available bank institutions for a country.
    
    Currently only Nordigen supports institution listing.
    For Okra, the widget shows banks automatically.
    """
    if provider.lower() == "nordigen":
        service = NordigenService()
        if not service.is_configured():
            raise HTTPException(status_code=503, detail="Nordigen not configured")
        
        institutions = await service.get_institutions(country)
        return {"institutions": institutions, "country": country, "provider": "nordigen"}
    
    raise HTTPException(
        status_code=400, 
        detail=f"Institution listing not supported for {provider}"
    )


@router.post("/connect/initiate")
async def initiate_connection(
    provider: str,
    redirect_uri: str,
    state: str,
    institution_id: Optional[str] = None,
    country: Optional[str] = None,
):
    """
    Initiate bank connection flow.
    
    Returns the authorization URL to redirect the user to.
    
    For Nordigen: Also requires institution_id (get from /institutions/{country})
    """
    try:
        service = get_bank_service(provider=provider)
        
        if not service.is_configured():
            raise HTTPException(
                status_code=503, 
                detail=f"{provider} credentials not configured"
            )
        
        # Nordigen has a special flow
        if provider.lower() == "nordigen" and institution_id:
            service = NordigenService()
            result = await service.create_requisition(
                institution_id=institution_id,
                redirect_uri=redirect_uri,
                reference=state,
            )
            return {
                "authorization_url": result["link"],
                "requisition_id": result["requisition_id"],
                "provider": "nordigen",
            }
        
        # Standard OAuth flow for Okra and TrueLayer
        url = service.get_authorization_url(
            redirect_uri=redirect_uri,
            state=state,
            institution_id=institution_id,
        )
        
        return {
            "authorization_url": url,
            "provider": provider,
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/connect/complete")
async def complete_connection(request: ConnectRequest):
    """
    Complete bank connection after user authorizes.
    
    Args:
        provider: Bank feed provider (okra, truelayer, nordigen)
        authorization_code: Code from OAuth callback (or requisition_id for nordigen)
        redirect_uri: Original redirect URI (required for TrueLayer)
    """
    try:
        service = get_bank_service(provider=request.provider)
        
        if not service.is_configured():
            raise HTTPException(
                status_code=503,
                detail=f"{request.provider} credentials not configured"
            )
        
        result = await service.connect_account(
            authorization_code=request.authorization_code,
            redirect_uri=request.redirect_uri,
        )
        
        return {
            "status": "connected",
            "connection": result,
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/accounts")
async def get_accounts(
    provider: str,
    access_token: str,  # or requisition_id for nordigen
):
    """
    Get all connected bank accounts.
    
    Args:
        provider: Bank feed provider
        access_token: Access token from connection (requisition_id for nordigen)
    """
    try:
        service = get_bank_service(provider=provider)
        
        if not service.is_configured():
            raise HTTPException(
                status_code=503,
                detail=f"{provider} credentials not configured"
            )
        
        accounts = await service.get_accounts(access_token)
        
        return {
            "accounts": [acc.to_dict() for acc in accounts],
            "count": len(accounts),
            "provider": provider,
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/transactions")
async def get_transactions(request: TransactionsRequest, provider: str):
    """
    Get transactions for a bank account.
    
    Args:
        provider: Bank feed provider
        access_token: Access token
        account_id: Bank account ID
        from_date: Start date (ISO format, defaults to 30 days ago)
        to_date: End date (ISO format, defaults to today)
        limit: Maximum transactions to return
    """
    try:
        service = get_bank_service(provider=provider)
        
        if not service.is_configured():
            raise HTTPException(
                status_code=503,
                detail=f"{provider} credentials not configured"
            )
        
        # Parse dates
        from_date = date.fromisoformat(request.from_date) if request.from_date else None
        to_date = date.fromisoformat(request.to_date) if request.to_date else None
        
        transactions = await service.get_transactions(
            access_token=request.access_token,
            account_id=request.account_id,
            from_date=from_date,
            to_date=to_date,
            limit=request.limit,
        )
        
        return {
            "transactions": [txn.to_dict() for txn in transactions],
            "count": len(transactions),
            "account_id": request.account_id,
            "provider": provider,
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/balance")
async def get_balance(request: BalanceRequest, provider: str):
    """Get current balance for a bank account."""
    try:
        service = get_bank_service(provider=provider)
        
        if not service.is_configured():
            raise HTTPException(
                status_code=503,
                detail=f"{provider} credentials not configured"
            )
        
        balance = await service.get_balance(
            access_token=request.access_token,
            account_id=request.account_id,
        )
        
        return {
            "balance": balance,
            "account_id": request.account_id,
            "provider": provider,
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/refresh")
async def refresh_token(provider: str, refresh_token: str):
    """Refresh an expired access token."""
    try:
        service = get_bank_service(provider=provider)
        
        if not service.is_configured():
            raise HTTPException(
                status_code=503,
                detail=f"{provider} credentials not configured"
            )
        
        result = await service.refresh_token(refresh_token)
        
        return {
            "status": "refreshed",
            "tokens": result,
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# MATCHING ENDPOINT - Used by invoice workflow
# ============================================================================

@router.post("/match")
async def match_transaction(
    provider: str,
    access_token: str,
    account_id: str,
    vendor: str,
    amount: float,
    currency: str = "USD",
    tolerance_pct: float = 0.05,
    days_back: int = 30,
):
    """
    Find a bank transaction matching an invoice.
    
    Used by the invoice approval workflow to verify payments.
    
    Args:
        provider: Bank feed provider
        access_token: Bank access token
        account_id: Bank account to search
        vendor: Vendor name to match
        amount: Expected amount
        currency: Currency code
        tolerance_pct: Amount tolerance (default 5%)
        days_back: How many days to search back
    """
    try:
        service = get_bank_service(provider=provider)
        
        if not service.is_configured():
            return {
                "matched": False,
                "reason": f"{provider} not configured",
            }
        
        # Get recent transactions
        from_date = date.today() - timedelta(days=days_back)
        transactions = await service.get_transactions(
            access_token=access_token,
            account_id=account_id,
            from_date=from_date,
            limit=500,
        )
        
        # Find matching transaction
        min_amount = amount * (1 - tolerance_pct)
        max_amount = amount * (1 + tolerance_pct)
        vendor_lower = vendor.lower()
        
        matches = []
        for txn in transactions:
            # Check amount in range
            txn_amount = float(txn.amount)
            if not (min_amount <= txn_amount <= max_amount):
                continue
            
            # Check currency
            if txn.currency.upper() != currency.upper():
                continue
            
            # Check vendor name (fuzzy)
            description = (txn.description or "").lower()
            counterparty = (txn.counterparty_name or "").lower()
            
            if vendor_lower in description or vendor_lower in counterparty:
                match_score = 1.0
            elif any(word in description or word in counterparty for word in vendor_lower.split()[:2]):
                match_score = 0.8
            else:
                continue
            
            matches.append({
                "transaction": txn.to_dict(),
                "match_score": match_score,
                "amount_diff": abs(txn_amount - amount),
            })
        
        # Sort by match score
        matches.sort(key=lambda x: (-x["match_score"], x["amount_diff"]))
        
        if matches:
            best = matches[0]
            return {
                "matched": True,
                "match_type": "exact" if best["match_score"] >= 0.95 else "fuzzy",
                "matched_transaction": best["transaction"],
                "match_score": best["match_score"],
                "all_matches": matches[:5],
                "provider": provider,
            }
        
        return {
            "matched": False,
            "reason": "No matching transaction found",
            "searched_transactions": len(transactions),
            "provider": provider,
        }
        
    except Exception as e:
        return {
            "matched": False,
            "reason": str(e),
            "provider": provider,
        }
