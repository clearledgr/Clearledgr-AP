"""Bank feed integrations for Clearledgr.

Supports:
- Okra: Nigeria (full), Kenya & South Africa (beta)
- TrueLayer: UK and Europe
- Nordigen (GoCardless): EU Open Banking (free tier)

Usage:
    from clearledgr.services.bank_feeds import get_bank_service
    
    # Auto-selects based on region
    service = get_bank_service(region="africa")
    transactions = await service.get_transactions(account_id, days=30)
"""
from .base import BankFeedService, BankTransaction, BankAccount
from .okra import OkraService
from .truelayer import TrueLayerService
from .nordigen import NordigenService


def get_bank_service(region: str = "auto", provider: str = None) -> BankFeedService:
    """
    Get the appropriate bank feed service for a region.
    
    Args:
        region: "africa", "europe", "uk", or "auto"
        provider: Force specific provider ("okra", "truelayer", "nordigen")
        
    Returns:
        Configured BankFeedService instance
    """
    if provider:
        providers = {
            "okra": OkraService,
            "truelayer": TrueLayerService,
            "nordigen": NordigenService,
        }
        if provider.lower() not in providers:
            raise ValueError(f"Unknown provider: {provider}")
        return providers[provider.lower()]()
    
    region = region.lower()
    
    if region == "africa":
        return OkraService()
    elif region in ("europe", "eu"):
        return NordigenService()  # Free tier, good EU coverage
    elif region == "uk":
        return TrueLayerService()  # Best UK coverage
    else:
        # Auto-detect: try Okra first (Africa MVP), then Nordigen (free)
        return OkraService()


__all__ = [
    "BankFeedService",
    "BankTransaction",
    "BankAccount",
    "OkraService",
    "TrueLayerService",
    "NordigenService",
    "get_bank_service",
]
