"""
Multi-Currency Service

Handles foreign exchange and multi-currency support:
- Real-time FX rate fetching
- Currency conversion
- FX gain/loss calculations
- Historical rate tracking
"""

import logging
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
import asyncio
import os

logger = logging.getLogger(__name__)


@dataclass
class ExchangeRate:
    """Exchange rate record."""
    base_currency: str
    target_currency: str
    rate: float
    rate_date: date
    source: str = "api"  # api, manual, ecb, openexchange
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "base_currency": self.base_currency,
            "target_currency": self.target_currency,
            "rate": self.rate,
            "rate_date": self.rate_date.isoformat(),
            "source": self.source,
        }


@dataclass
class CurrencyConversion:
    """Record of a currency conversion."""
    conversion_id: str = ""
    from_currency: str = ""
    to_currency: str = ""
    from_amount: float = 0.0
    to_amount: float = 0.0
    exchange_rate: float = 0.0
    rate_date: date = field(default_factory=date.today)
    
    # For FX gain/loss tracking
    original_rate: float = 0.0
    settlement_rate: float = 0.0
    fx_gain_loss: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "conversion_id": self.conversion_id,
            "from_currency": self.from_currency,
            "to_currency": self.to_currency,
            "from_amount": self.from_amount,
            "to_amount": self.to_amount,
            "exchange_rate": self.exchange_rate,
            "rate_date": self.rate_date.isoformat(),
            "fx_gain_loss": self.fx_gain_loss,
        }


class MultiCurrencyService:
    """
    Service for multi-currency operations and FX rate management.
    """
    
    # Supported currencies
    SUPPORTED_CURRENCIES = [
        "USD", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF", "CNY", 
        "INR", "MXN", "BRL", "SGD", "HKD", "NZD", "SEK", "NOK",
        "DKK", "ZAR", "KRW", "NGN", "GHS", "KES", "EGP", "AED"
    ]
    
    # Fallback rates (updated periodically)
    FALLBACK_RATES = {
        ("USD", "EUR"): 0.92,
        ("USD", "GBP"): 0.79,
        ("USD", "CAD"): 1.36,
        ("USD", "AUD"): 1.53,
        ("USD", "JPY"): 149.50,
        ("USD", "CHF"): 0.88,
        ("USD", "CNY"): 7.24,
        ("USD", "INR"): 83.12,
        ("USD", "MXN"): 17.15,
        ("USD", "BRL"): 4.97,
        ("USD", "SGD"): 1.34,
        ("USD", "HKD"): 7.82,
        ("USD", "NZD"): 1.64,
        ("USD", "NGN"): 1550.0,
        ("USD", "GHS"): 12.50,
        ("USD", "KES"): 153.0,
        ("USD", "AED"): 3.67,
        ("EUR", "USD"): 1.09,
        ("GBP", "USD"): 1.27,
    }
    
    def __init__(self, organization_id: str = "default", base_currency: str = "USD"):
        self.organization_id = organization_id
        self.base_currency = base_currency
        self._rate_cache: Dict[Tuple[str, str, date], ExchangeRate] = {}
        self._conversions: Dict[str, CurrencyConversion] = {}
        
        # API configuration
        self.api_key = os.getenv("EXCHANGE_RATE_API_KEY", "")
        self.api_provider = os.getenv("EXCHANGE_RATE_PROVIDER", "openexchangerates")
    
    async def get_exchange_rate(
        self,
        from_currency: str,
        to_currency: str,
        rate_date: date = None,
    ) -> ExchangeRate:
        """
        Get exchange rate between two currencies.
        Tries API first, falls back to cached/static rates.
        """
        rate_date = rate_date or date.today()
        from_currency = from_currency.upper()
        to_currency = to_currency.upper()
        
        # Same currency
        if from_currency == to_currency:
            return ExchangeRate(
                base_currency=from_currency,
                target_currency=to_currency,
                rate=1.0,
                rate_date=rate_date,
                source="identity",
            )
        
        # Check cache
        cache_key = (from_currency, to_currency, rate_date)
        if cache_key in self._rate_cache:
            return self._rate_cache[cache_key]
        
        # Try API
        rate = await self._fetch_rate_from_api(from_currency, to_currency, rate_date)
        
        if not rate:
            # Use fallback rates
            rate = self._get_fallback_rate(from_currency, to_currency, rate_date)
        
        if rate:
            self._rate_cache[cache_key] = rate
        
        return rate
    
    async def _fetch_rate_from_api(
        self,
        from_currency: str,
        to_currency: str,
        rate_date: date,
    ) -> Optional[ExchangeRate]:
        """Fetch rate from external API."""
        if not self.api_key:
            return None
        
        try:
            import aiohttp
            
            if self.api_provider == "openexchangerates":
                url = f"https://openexchangerates.org/api/latest.json?app_id={self.api_key}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=10) as response:
                        if response.status == 200:
                            data = await response.json()
                            rates = data.get("rates", {})
                            
                            # OpenExchangeRates uses USD as base
                            if from_currency == "USD":
                                rate_value = rates.get(to_currency, 0)
                            elif to_currency == "USD":
                                rate_value = 1 / rates.get(from_currency, 1)
                            else:
                                # Cross rate
                                from_usd = rates.get(from_currency, 1)
                                to_usd = rates.get(to_currency, 1)
                                rate_value = to_usd / from_usd
                            
                            if rate_value:
                                return ExchangeRate(
                                    base_currency=from_currency,
                                    target_currency=to_currency,
                                    rate=rate_value,
                                    rate_date=rate_date,
                                    source="openexchangerates",
                                )
            
            elif self.api_provider == "exchangerate-api":
                url = f"https://v6.exchangerate-api.com/v6/{self.api_key}/pair/{from_currency}/{to_currency}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=10) as response:
                        if response.status == 200:
                            data = await response.json()
                            rate_value = data.get("conversion_rate", 0)
                            if rate_value:
                                return ExchangeRate(
                                    base_currency=from_currency,
                                    target_currency=to_currency,
                                    rate=rate_value,
                                    rate_date=rate_date,
                                    source="exchangerate-api",
                                )
        
        except Exception as e:
            logger.warning(f"Failed to fetch FX rate from API: {e}")
        
        return None
    
    def _get_fallback_rate(
        self,
        from_currency: str,
        to_currency: str,
        rate_date: date,
    ) -> ExchangeRate:
        """Get fallback rate from static table."""
        # Direct lookup
        key = (from_currency, to_currency)
        if key in self.FALLBACK_RATES:
            return ExchangeRate(
                base_currency=from_currency,
                target_currency=to_currency,
                rate=self.FALLBACK_RATES[key],
                rate_date=rate_date,
                source="fallback",
            )
        
        # Try inverse
        inverse_key = (to_currency, from_currency)
        if inverse_key in self.FALLBACK_RATES:
            return ExchangeRate(
                base_currency=from_currency,
                target_currency=to_currency,
                rate=1 / self.FALLBACK_RATES[inverse_key],
                rate_date=rate_date,
                source="fallback_inverse",
            )
        
        # Try cross rate via USD
        if from_currency != "USD" and to_currency != "USD":
            from_usd_key = (from_currency, "USD")
            to_usd_key = ("USD", to_currency)
            
            from_rate = self.FALLBACK_RATES.get(from_usd_key)
            to_rate = self.FALLBACK_RATES.get(to_usd_key)
            
            if not from_rate:
                inv_key = ("USD", from_currency)
                if inv_key in self.FALLBACK_RATES:
                    from_rate = 1 / self.FALLBACK_RATES[inv_key]
            
            if from_rate and to_rate:
                return ExchangeRate(
                    base_currency=from_currency,
                    target_currency=to_currency,
                    rate=from_rate * to_rate,
                    rate_date=rate_date,
                    source="fallback_cross",
                )
        
        # Default to 1:1 if all else fails
        logger.warning(f"No rate found for {from_currency}/{to_currency}, using 1.0")
        return ExchangeRate(
            base_currency=from_currency,
            target_currency=to_currency,
            rate=1.0,
            rate_date=rate_date,
            source="default",
        )
    
    async def convert(
        self,
        amount: float,
        from_currency: str,
        to_currency: str,
        rate_date: date = None,
    ) -> CurrencyConversion:
        """
        Convert an amount from one currency to another.
        """
        rate = await self.get_exchange_rate(from_currency, to_currency, rate_date)
        
        converted_amount = round(amount * rate.rate, 2)
        
        conversion = CurrencyConversion(
            conversion_id=f"conv-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            from_currency=from_currency,
            to_currency=to_currency,
            from_amount=amount,
            to_amount=converted_amount,
            exchange_rate=rate.rate,
            rate_date=rate.rate_date,
        )
        
        self._conversions[conversion.conversion_id] = conversion
        return conversion
    
    def convert_sync(
        self,
        amount: float,
        from_currency: str,
        to_currency: str,
    ) -> Tuple[float, float]:
        """
        Synchronous conversion using cached/fallback rates only.
        Returns (converted_amount, rate).
        """
        from_currency = from_currency.upper()
        to_currency = to_currency.upper()
        
        if from_currency == to_currency:
            return (amount, 1.0)
        
        rate = self._get_fallback_rate(from_currency, to_currency, date.today())
        converted = round(amount * rate.rate, 2)
        
        return (converted, rate.rate)
    
    async def convert_to_base(
        self,
        amount: float,
        from_currency: str,
        rate_date: date = None,
    ) -> CurrencyConversion:
        """Convert to organization's base currency."""
        return await self.convert(amount, from_currency, self.base_currency, rate_date)
    
    def calculate_fx_gain_loss(
        self,
        original_amount: float,
        original_currency: str,
        original_rate: float,
        settlement_rate: float,
    ) -> float:
        """
        Calculate FX gain/loss on settlement.
        
        Gain = Positive (favorable rate change)
        Loss = Negative (unfavorable rate change)
        """
        original_base = original_amount * original_rate
        settlement_base = original_amount * settlement_rate
        
        return round(settlement_base - original_base, 2)
    
    def revalue_position(
        self,
        amount: float,
        currency: str,
        book_rate: float,
        current_rate: float,
    ) -> Dict[str, Any]:
        """
        Revalue a foreign currency position at current rates.
        Used for month-end FX revaluation.
        """
        book_value = amount * book_rate
        current_value = amount * current_rate
        unrealized_gain_loss = current_value - book_value
        
        return {
            "amount": amount,
            "currency": currency,
            "book_rate": book_rate,
            "current_rate": current_rate,
            "book_value": round(book_value, 2),
            "current_value": round(current_value, 2),
            "unrealized_gain_loss": round(unrealized_gain_loss, 2),
        }
    
    def set_manual_rate(
        self,
        from_currency: str,
        to_currency: str,
        rate: float,
        rate_date: date = None,
    ) -> ExchangeRate:
        """Set a manual exchange rate override."""
        rate_date = rate_date or date.today()
        
        exchange_rate = ExchangeRate(
            base_currency=from_currency.upper(),
            target_currency=to_currency.upper(),
            rate=rate,
            rate_date=rate_date,
            source="manual",
        )
        
        cache_key = (from_currency.upper(), to_currency.upper(), rate_date)
        self._rate_cache[cache_key] = exchange_rate
        
        logger.info(f"Set manual rate: {from_currency}/{to_currency} = {rate}")
        return exchange_rate
    
    def get_rate_history(
        self,
        from_currency: str,
        to_currency: str,
        days: int = 30,
    ) -> List[ExchangeRate]:
        """Get historical rates from cache."""
        from_currency = from_currency.upper()
        to_currency = to_currency.upper()
        
        history = []
        today = date.today()
        
        for i in range(days):
            rate_date = today - timedelta(days=i)
            cache_key = (from_currency, to_currency, rate_date)
            if cache_key in self._rate_cache:
                history.append(self._rate_cache[cache_key])
        
        return sorted(history, key=lambda r: r.rate_date)
    
    def format_currency(
        self,
        amount: float,
        currency: str,
        include_symbol: bool = True,
    ) -> str:
        """Format amount with currency symbol."""
        symbols = {
            "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥",
            "CNY": "¥", "INR": "₹", "NGN": "₦", "GHS": "₵",
            "KES": "KSh", "ZAR": "R", "BRL": "R$", "MXN": "$",
            "CAD": "C$", "AUD": "A$", "CHF": "CHF", "SGD": "S$",
        }
        
        symbol = symbols.get(currency.upper(), currency)
        formatted = f"{amount:,.2f}"
        
        if include_symbol:
            return f"{symbol}{formatted}"
        return formatted
    
    def get_summary(self) -> Dict[str, Any]:
        """Get currency service summary."""
        return {
            "base_currency": self.base_currency,
            "supported_currencies": len(self.SUPPORTED_CURRENCIES),
            "cached_rates": len(self._rate_cache),
            "conversions_performed": len(self._conversions),
            "api_provider": self.api_provider,
            "api_configured": bool(self.api_key),
        }


# Singleton instance cache
_instances: Dict[str, MultiCurrencyService] = {}


def get_multi_currency_service(
    organization_id: str = "default",
    base_currency: str = "USD",
) -> MultiCurrencyService:
    """Get or create multi-currency service for organization."""
    if organization_id not in _instances:
        _instances[organization_id] = MultiCurrencyService(organization_id, base_currency)
    return _instances[organization_id]
