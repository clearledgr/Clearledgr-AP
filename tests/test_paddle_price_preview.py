"""Paddle localized price-preview (PlanPage currency localization).

Covers solden.services.paddle_billing.preview_plan_prices: the happy path
(maps Paddle /pricing-preview line items back to plan tiers in the buyer's
currency) plus the two graceful-degradation paths the SPA relies on to fall
back to its static USD labels (Paddle not configured / no price IDs set).
No DB, no network — _http is monkeypatched.
"""
from __future__ import annotations

import solden.services.paddle_billing as paddle


def _set_keys(monkeypatch, *, api_key="pk_test", price_ids=True):
    monkeypatch.setenv("PADDLE_API_KEY", api_key)
    if price_ids:
        monkeypatch.setenv("PADDLE_PRICE_ID_STARTER", "pri_starter")
        monkeypatch.setenv("PADDLE_PRICE_ID_PROFESSIONAL", "pri_pro")
        monkeypatch.setenv("PADDLE_PRICE_ID_ENTERPRISE", "pri_ent")
    else:
        monkeypatch.delenv("PADDLE_PRICE_ID_STARTER", raising=False)
        monkeypatch.delenv("PADDLE_PRICE_ID_PROFESSIONAL", raising=False)
        monkeypatch.delenv("PADDLE_PRICE_ID_ENTERPRISE", raising=False)


def test_preview_not_configured_returns_fallback(monkeypatch):
    monkeypatch.delenv("PADDLE_API_KEY", raising=False)
    out = paddle.preview_plan_prices(["starter", "professional", "enterprise"])
    assert out["status"] == "not_configured"
    assert out["prices"] == {}


def test_preview_configured_but_no_price_ids(monkeypatch):
    _set_keys(monkeypatch, price_ids=False)
    out = paddle.preview_plan_prices(["starter", "professional", "enterprise"])
    assert out["status"] == "missing_prices"
    assert out["prices"] == {}


def test_preview_maps_line_items_to_plans_in_buyer_currency(monkeypatch):
    _set_keys(monkeypatch)

    captured = {}

    def _fake_http(method, path, *, json_body=None):
        captured["method"] = method
        captured["path"] = path
        captured["body"] = json_body
        return {
            "currency_code": "EUR",
            "details": {
                "line_items": [
                    {"price": {"id": "pri_starter"},
                     "formatted_unit_totals": {"total": "€65.00"},
                     "unit_totals": {"total": "6500"}},
                    {"price": {"id": "pri_pro"},
                     "formatted_unit_totals": {"total": "€125.00"},
                     "unit_totals": {"total": "12500"}},
                    {"price": {"id": "pri_ent"},
                     "formatted_unit_totals": {"total": "€249.00"},
                     "unit_totals": {"total": "24900"}},
                ],
            },
        }

    monkeypatch.setattr(paddle, "_http", _fake_http)

    out = paddle.preview_plan_prices(
        ["starter", "professional", "enterprise"],
        ip_address="203.0.113.7",
    )

    assert captured["method"] == "POST"
    assert captured["path"] == "/pricing-preview"
    # All three configured price IDs sent in one call, buyer IP forwarded.
    assert captured["body"]["customer_ip_address"] == "203.0.113.7"
    assert {it["price_id"] for it in captured["body"]["items"]} == {
        "pri_starter", "pri_pro", "pri_ent",
    }

    assert out["status"] == "ok"
    assert out["currency_code"] == "EUR"
    assert out["prices"]["starter"]["formatted"] == "€65.00"
    assert out["prices"]["starter"]["amount_minor"] == "6500"
    assert out["prices"]["starter"]["currency_code"] == "EUR"
    assert out["prices"]["enterprise"]["formatted"] == "€249.00"


def test_preview_http_error_degrades_gracefully(monkeypatch):
    _set_keys(monkeypatch)

    def _boom(method, path, *, json_body=None):
        raise RuntimeError("paddle_http_500")

    monkeypatch.setattr(paddle, "_http", _boom)
    out = paddle.preview_plan_prices(["starter"], ip_address="203.0.113.7")
    assert out["status"] == "error"
    assert out["prices"] == {}


def test_preview_country_code_when_no_ip(monkeypatch):
    _set_keys(monkeypatch)

    captured = {}

    def _fake_http(method, path, *, json_body=None):
        captured["body"] = json_body
        return {"currency_code": "GBP", "details": {"line_items": []}}

    monkeypatch.setattr(paddle, "_http", _fake_http)
    paddle.preview_plan_prices(["starter"], country_code="GB")
    assert captured["body"]["address"] == {"country_code": "GB"}
    assert "customer_ip_address" not in captured["body"]
