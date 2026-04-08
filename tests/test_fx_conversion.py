"""Tests for the foreign exchange conversion service.

Mocks httpx calls to ECB API so tests run offline.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.services import fx_conversion  # noqa: E402
from clearledgr.services.fx_conversion import (  # noqa: E402
    convert,
    get_exchange_rate,
    get_supported_currencies,
    _rate_cache,
)


@pytest.fixture(autouse=True)
def clear_rate_cache():
    """Clear the module-level rate cache before each test."""
    _rate_cache.clear()
    yield
    _rate_cache.clear()


def _mock_ecb_response(rate_eur_per_currency: float):
    """Build a mock httpx response mimicking the ECB JSON API format."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "dataSets": [{
            "series": {
                "0:0:0:0:0": {
                    "observations": {
                        "0": [rate_eur_per_currency],
                    }
                }
            }
        }]
    }
    return mock_resp


def _mock_ecb_error():
    """Build a mock httpx response for a failed ECB call."""
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    return mock_resp


# ---------------------------------------------------------------------------
# convert — same currency
# ---------------------------------------------------------------------------


class TestConvertSameCurrency:
    def test_same_currency_returns_1_rate(self):
        result = convert(100.0, "USD", "USD")
        assert result["rate"] == 1.0
        assert result["converted_amount"] == 100.0
        assert result["source"] == "same_currency"

    def test_same_currency_case_insensitive(self):
        result = convert(50.0, "eur", "EUR")
        assert result["rate"] == 1.0
        assert result["converted_amount"] == 50.0


# ---------------------------------------------------------------------------
# get_exchange_rate — same currency
# ---------------------------------------------------------------------------


class TestGetExchangeRateSameCurrency:
    def test_same_currency_returns_1(self):
        assert get_exchange_rate("USD", "USD") == 1.0

    def test_same_currency_case_insensitive(self):
        assert get_exchange_rate("gbp", "GBP") == 1.0


# ---------------------------------------------------------------------------
# get_supported_currencies
# ---------------------------------------------------------------------------


class TestGetSupportedCurrencies:
    def test_returns_a_list(self):
        result = get_supported_currencies()
        assert isinstance(result, list)
        assert len(result) > 0

    def test_contains_eur(self):
        assert "EUR" in get_supported_currencies()

    def test_contains_usd(self):
        assert "USD" in get_supported_currencies()

    def test_contains_african_currencies(self):
        currencies = get_supported_currencies()
        for c in ("NGN", "KES", "GHS"):
            assert c in currencies

    def test_result_is_sorted(self):
        result = get_supported_currencies()
        assert result == sorted(result)


# ---------------------------------------------------------------------------
# convert — with mocked ECB
# ---------------------------------------------------------------------------


class TestConvertWithMockedECB:
    def test_convert_usd_to_eur(self):
        """Mock: 1 EUR = 1.10 USD → 1 USD = 1/1.10 EUR."""
        with patch("clearledgr.services.fx_conversion.httpx") as mock_httpx:
            mock_httpx.get.return_value = _mock_ecb_response(1.10)
            result = convert(110.0, "USD", "EUR")
        assert result["converted_amount"] is not None
        assert result["rate"] is not None
        assert "error" not in result

    def test_convert_unknown_currency_returns_error(self):
        """If ECB returns 404 for an unknown currency, convert should return error."""
        with patch("clearledgr.services.fx_conversion.httpx") as mock_httpx:
            mock_httpx.get.return_value = _mock_ecb_error()
            result = convert(100.0, "XYZ", "EUR")
        assert result["converted_amount"] is None
        assert result["rate"] is None
        assert result["source"] == "unavailable"
        assert "error" in result

    def test_convert_to_unknown_currency_returns_error(self):
        with patch("clearledgr.services.fx_conversion.httpx") as mock_httpx:
            mock_httpx.get.return_value = _mock_ecb_error()
            result = convert(100.0, "EUR", "XYZ")
        assert result["converted_amount"] is None
        assert result["source"] == "unavailable"

    def test_convert_between_fallback_currencies(self):
        """NGN → KES should use fallback rates without any HTTP call."""
        with patch("clearledgr.services.fx_conversion.httpx") as mock_httpx:
            result = convert(16500.0, "NGN", "KES")
            mock_httpx.get.assert_not_called()
        assert result["converted_amount"] is not None
        assert result["rate"] is not None
        assert result["source"] == "fallback"
