"""Penny-exactness guardrails for monetary arithmetic.

These tests lock in the invariant that every aggregation path touching
money goes through Decimal, not float. The canonical trap:
``0.1 + 0.2 == 0.30000000000000004`` in Python float — compounding
this across 100 invoices drifts the total by a full cent or two. We
had exactly this bug silently present in aging reports and vendor
statement reconciliation before the money.py migration.

Each test picks a payload designed to fail on the float path and
pass on the Decimal path.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from clearledgr.core.money import (
    Money,
    Q2,
    ZERO,
    money_sum,
    money_to_float,
    to_decimal,
)
from pydantic import BaseModel


def test_money_sum_is_exact_over_float_drift_inputs():
    """Classic 0.1+0.2 case, plus a long list that drifts on float."""
    assert money_sum([0.1, 0.2]) == Decimal("0.30")
    # 100 invoices of $33.33 — float gives 3332.9999...some, Decimal gives 3333.00
    assert money_sum([33.33] * 100) == Decimal("3333.00")
    # Mixed int/str/float/Decimal in one iterable
    assert money_sum([Decimal("1.01"), "2.02", 3.03, 4]) == Decimal("10.06")


def test_to_decimal_quantizes_to_two_places():
    # Float fuzz is scrubbed via str() intermediate — no binary rep bleed
    assert to_decimal(0.1 + 0.2) == Decimal("0.30")
    assert to_decimal("1.239") == Decimal("1.24")  # ROUND_HALF_UP
    assert to_decimal("1.235") == Decimal("1.24")  # ROUND_HALF_UP (banker's would be 1.24 too, coincidence)
    assert to_decimal(None) == ZERO
    assert to_decimal("") == ZERO


def test_to_decimal_rejects_bool():
    """bool subclasses int, but a boolean is never a monetary value."""
    from decimal import InvalidOperation
    with pytest.raises(InvalidOperation):
        to_decimal(True)


def test_pydantic_money_type_roundtrip_as_json_number():
    """Wire format stays a JSON number — UI reads it as Number, not String.

    This is the single guard rail that keeps the Gmail extension UI
    working after the migration. If someone accidentally changes the
    ``when_used="json"`` policy, this test fails and the extension
    doesn't silently break.
    """
    class B(BaseModel):
        amount: Money

    m = B(amount=0.1)
    # Python side: Decimal, exact
    assert isinstance(m.amount, Decimal)
    assert m.amount == Decimal("0.10")
    # JSON side: number, not string
    assert '"amount":0.1' in m.model_dump_json()
    # Round-trip coerces the JSON number back to Decimal with no drift
    revived = B.model_validate_json(m.model_dump_json())
    assert revived.amount == Decimal("0.10")


def test_money_sum_penny_drift_proof():
    """The exact failure mode the migration fixed.

    Pairwise 0.1+0.2 drifts to 0.30000000000000004 in float — the
    canonical floating-point surprise. money_sum goes through Decimal
    and lands on 0.30 exactly. The test asserts both halves so the
    precondition (float drifts) and the guarantee (Decimal doesn't)
    are locked in together.
    """
    # Precondition: float drifts on this exact input
    assert 0.1 + 0.2 == 0.30000000000000004
    # Guarantee: money_sum is exact
    assert money_sum([0.1, 0.2]) == Decimal("0.30")
    # 100 invoices of $33.33 — Decimal lands on the clean total
    assert money_sum([33.33] * 100) == Decimal("3333.00")
    # Mixed-type input stays exact
    assert money_sum([Decimal("1.01"), "2.02", 3.03, 4]) == Decimal("10.06")


def test_money_to_float_roundtrip_is_loss_free_at_two_decimals():
    """Every 2dp Decimal survives a float round-trip for realistic values.

    This is the load-bearing property behind posting to ERPs as JSON
    numbers rather than strings. We check it across a span of values
    that covers small, medium, and large invoice amounts.
    """
    for v in ["0.01", "0.99", "1.00", "123.45", "9999.99", "1234567.89"]:
        d = Decimal(v)
        f = money_to_float(d)
        assert Decimal(str(f)).quantize(Q2) == d, f"drift on {v}: float={f}"
