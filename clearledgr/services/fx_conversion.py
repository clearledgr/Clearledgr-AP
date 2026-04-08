"""Foreign exchange conversion service.

Provides exchange rate lookups and currency conversion for multi-currency
AP processing. Uses the European Central Bank (ECB) free API for rates.

For Africa: ECB covers EUR, USD, GBP, ZAR. For NGN, KES, GHS we use
a fallback to the latest known rates (updated via background sync).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

# ECB free exchange rate API (no API key needed)
ECB_API_URL = "https://data-api.ecb.europa.eu/service/data/EXR/D.{currency}.EUR.SP00.A?lastNObservations=1&format=jsondata"

# Fallback rates for currencies not in ECB (approximate, refreshed by background sync)
_FALLBACK_RATES_TO_EUR = {
    "NGN": 1650.0,   # Nigerian Naira
    "KES": 165.0,    # Kenyan Shilling
    "GHS": 16.5,     # Ghanaian Cedi
    "UGX": 4600.0,   # Ugandan Shilling
    "TZS": 3200.0,   # Tanzanian Shilling
    "XOF": 655.96,   # West African CFA Franc (fixed to EUR)
    "XAF": 655.96,   # Central African CFA Franc (fixed to EUR)
}

# Cache: currency pair → (rate, fetched_at)
_rate_cache: Dict[str, tuple] = {}
_CACHE_TTL_SECONDS = 3600  # 1 hour


def convert(
    amount: float,
    from_currency: str,
    to_currency: str,
) -> Dict[str, Any]:
    """Convert an amount between currencies.

    Returns {converted_amount, rate, from_currency, to_currency, source}.
    Uses cached rates when available.
    """
    from_c = from_currency.upper().strip()
    to_c = to_currency.upper().strip()

    if from_c == to_c:
        return {
            "converted_amount": round(amount, 2),
            "rate": 1.0,
            "from_currency": from_c,
            "to_currency": to_c,
            "source": "same_currency",
        }

    rate = get_exchange_rate(from_c, to_c)
    if rate is None:
        return {
            "converted_amount": None,
            "rate": None,
            "from_currency": from_c,
            "to_currency": to_c,
            "source": "unavailable",
            "error": f"No exchange rate available for {from_c}/{to_c}",
        }

    converted = round(amount * rate, 2)
    return {
        "converted_amount": converted,
        "rate": round(rate, 6),
        "from_currency": from_c,
        "to_currency": to_c,
        "source": "ecb" if from_c not in _FALLBACK_RATES_TO_EUR and to_c not in _FALLBACK_RATES_TO_EUR else "fallback",
    }


def get_exchange_rate(from_currency: str, to_currency: str) -> Optional[float]:
    """Get exchange rate between two currencies. Returns None if unavailable."""
    from_c = from_currency.upper().strip()
    to_c = to_currency.upper().strip()

    if from_c == to_c:
        return 1.0

    cache_key = f"{from_c}_{to_c}"
    cached = _rate_cache.get(cache_key)
    if cached:
        rate, fetched_at = cached
        if (datetime.now(timezone.utc) - fetched_at).total_seconds() < _CACHE_TTL_SECONDS:
            return rate

    # Convert via EUR as base
    from_to_eur = _get_rate_to_eur(from_c)
    to_to_eur = _get_rate_to_eur(to_c)

    if from_to_eur is None or to_to_eur is None:
        return None

    # from_c → EUR → to_c
    rate = from_to_eur / to_to_eur if to_to_eur != 0 else None
    if rate is not None:
        _rate_cache[cache_key] = (rate, datetime.now(timezone.utc))

    return rate


def _get_rate_to_eur(currency: str) -> Optional[float]:
    """Get rate: 1 currency = X EUR."""
    if currency == "EUR":
        return 1.0

    # Check fallback first for non-ECB currencies
    if currency in _FALLBACK_RATES_TO_EUR:
        return 1.0 / _FALLBACK_RATES_TO_EUR[currency]

    # Try ECB API
    try:
        url = ECB_API_URL.format(currency=currency)
        response = httpx.get(url, timeout=10)
        if response.status_code != 200:
            return None

        data = response.json()
        # ECB jsondata format: dataSets[0].series.0:0:0:0:0.observations.0[0]
        observations = (
            data.get("dataSets", [{}])[0]
            .get("series", {})
            .get("0:0:0:0:0", {})
            .get("observations", {})
        )
        if observations:
            latest = list(observations.values())[-1]
            rate_eur_per_currency = float(latest[0])
            return 1.0 / rate_eur_per_currency  # Convert to "1 currency = X EUR"
    except Exception as exc:
        logger.debug("ECB rate fetch failed for %s: %s", currency, exc)

    return None


def get_supported_currencies() -> list:
    """List of currencies we can convert."""
    ecb_currencies = [
        "USD", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "SEK", "NOK", "DKK",
        "CZK", "PLN", "HUF", "RON", "BGN", "HRK", "ISK", "TRY", "ZAR", "BRL",
        "CNY", "INR", "MXN", "SGD", "HKD", "KRW", "THB", "MYR", "PHP", "IDR",
    ]
    africa_currencies = list(_FALLBACK_RATES_TO_EUR.keys())
    return sorted(set(["EUR"] + ecb_currencies + africa_currencies))
