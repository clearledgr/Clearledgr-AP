"""Early payment discount optimization.

When an invoice has payment terms like "2/10 NET 30" (2% discount if paid
within 10 days, otherwise full amount due in 30 days), this service
calculates the annualized return of taking the discount vs paying late.

A 2% discount for paying 20 days early = 36.7% annualized return.
That's better than almost any investment the company could make.

This service surfaces discount opportunities in the AP decision and
approval flow so finance teams can prioritize paying discountable
invoices early.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Pattern: "2/10 NET 30" → 2% discount if paid within 10 days, net 30
DISCOUNT_TERMS_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*/\s*(\d+)\s*(?:net|NET)\s*(\d+)",
)


def parse_discount_terms(terms: str) -> Optional[Dict[str, Any]]:
    """Parse discount terms string into structured data.

    Examples:
        "2/10 NET 30" → {discount_pct: 2.0, discount_days: 10, net_days: 30}
        "1.5/15 NET 45" → {discount_pct: 1.5, discount_days: 15, net_days: 45}
    """
    if not terms:
        return None
    match = DISCOUNT_TERMS_PATTERN.search(str(terms))
    if not match:
        return None
    return {
        "discount_pct": float(match.group(1)),
        "discount_days": int(match.group(2)),
        "net_days": int(match.group(3)),
    }


def calculate_discount_opportunity(
    amount: float,
    payment_terms: str,
    invoice_date: Optional[str] = None,
    currency: str = "USD",
) -> Optional[Dict[str, Any]]:
    """Calculate the value of taking an early payment discount.

    Returns None if no discount terms are detected.
    """
    parsed = parse_discount_terms(payment_terms)
    if not parsed:
        # Also check for simple "X% early payment discount"
        simple_match = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:early|prompt)\s*(?:payment)?\s*discount", str(payment_terms), re.IGNORECASE)
        if simple_match:
            parsed = {
                "discount_pct": float(simple_match.group(1)),
                "discount_days": 10,  # Assume 10 days
                "net_days": 30,       # Assume NET 30
            }
        else:
            return None

    discount_pct = parsed["discount_pct"]
    discount_days = parsed["discount_days"]
    net_days = parsed["net_days"]
    days_saved = net_days - discount_days

    if days_saved <= 0 or discount_pct <= 0:
        return None

    discount_amount = round(amount * discount_pct / 100, 2)
    net_amount = round(amount - discount_amount, 2)

    # Annualized return: if you save X% by paying Y days early,
    # annualized = (discount / (1 - discount)) * (365 / days_saved)
    annualized_return = (discount_pct / 100) / (1 - discount_pct / 100) * (365 / days_saved)
    annualized_pct = round(annualized_return * 100, 1)

    # Calculate deadline
    discount_deadline = None
    if invoice_date:
        try:
            inv_date = date.fromisoformat(str(invoice_date)[:10])
            discount_deadline = (inv_date + timedelta(days=discount_days)).isoformat()
            days_remaining = (inv_date + timedelta(days=discount_days) - date.today()).days
        except (ValueError, TypeError):
            days_remaining = discount_days
    else:
        days_remaining = discount_days

    return {
        "has_discount": True,
        "discount_pct": discount_pct,
        "discount_amount": discount_amount,
        "net_amount_if_discounted": net_amount,
        "original_amount": amount,
        "currency": currency,
        "discount_days": discount_days,
        "net_days": net_days,
        "days_saved": days_saved,
        "annualized_return_pct": annualized_pct,
        "discount_deadline": discount_deadline,
        "days_remaining": max(0, days_remaining),
        "is_expired": days_remaining < 0,
        "recommendation": (
            "TAKE DISCOUNT" if annualized_pct > 10 and days_remaining > 0
            else "DISCOUNT EXPIRED" if days_remaining < 0
            else "EVALUATE" if annualized_pct > 5
            else "SKIP"
        ),
        "summary": (
            f"Pay {currency} {net_amount:,.2f} by {discount_deadline or f'{discount_days} days'} "
            f"to save {currency} {discount_amount:,.2f} ({discount_pct}%). "
            f"Annualized return: {annualized_pct}%."
        ),
    }
