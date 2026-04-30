"""Money helpers — penny-exact Decimal arithmetic everywhere.

Project rule: every time a monetary value is summed, compared, or
arithmetic'd internally in Clearledgr, it travels as `decimal.Decimal`
quantized to 2 decimal places. Float never touches internal math.

Why this module exists:

Float can't represent decimal fractions exactly. ``0.1 + 0.2 ==
0.30000000000000004`` in Python, which means summing 1,000 invoice
amounts stored as floats compounds a tiny error every add and drifts
the total by a full cent or two. For an AP aging report, a
reconciliation total, or a cross-invoice duplicate check, that's the
difference between "balanced" and "investigate why we're off by
$0.03". The fix isn't "round at the end" — the accumulation itself
is wrong. The fix is "use Decimal, which is exact for finite base-10
fractions".

JSON boundary policy:

Pydantic v2's default is to emit Decimal as a JSON string. Our UI
(Gmail extension) reads `amount` as a number and formats it for
display. Switching to string would break every `.toLocaleString()`
call in the extension. So the ``Money`` type defined here serializes
as a JSON number in `when_used="json"` mode — the Decimal is
``quantize``'d to 2dp first, then passed through ``float()`` for the
wire. That round-trip is safe because the Decimal is already clamped
to 2dp; all values representable in ≤15 significant digits (every
realistic invoice) round-trip exactly through float.

.model_dump(mode="python") still returns the Decimal, so downstream
Python consumers get exact values. Only the JSON encoder reverts to
float for wire compatibility.

Storage policy:

DB columns remain ``REAL`` (SQLite has no native DECIMAL, and
re-typing every column is a bigger migration than the bug warrants).
Reads go through ``row_to_decimal`` which wraps the stored float in
``Decimal(str(v)).quantize(Q2)`` — the ``str()`` step is important,
because ``Decimal(float_value)`` inherits the float's binary-rep
fuzz. Writes pass Decimal through ``float()`` at the last moment.
Authoritative sums (aging, reconciliation) are always recomputed in
Python Decimal after fetch; SQL ``SUM()`` is only used for display-
class widgets where a 0.01 drift is invisible.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Any, Iterable

from pydantic import BeforeValidator, PlainSerializer
from typing_extensions import Annotated


Q2: Decimal = Decimal("0.01")
"""Standard 2dp quantizer for most currencies."""

ZERO: Decimal = Decimal("0.00")


def to_decimal(value: Any) -> Decimal:
    """Coerce any reasonable input to a quantized-to-2dp Decimal.

    Accepts ``Decimal``, ``int``, ``str``, ``float``, ``None``.
    ``None`` maps to ZERO. Unparseable values raise ``InvalidOperation``
    so the caller knows to handle the boundary case rather than
    silently dropping to zero.

    Floats are converted via ``str()`` first. ``Decimal(0.1)`` gives
    ``Decimal('0.1000000000000000055511151231257827021181583404541015625')``
    — carrying the float's binary fuzz into the Decimal world. Going
    through ``str()`` gives the human-expected ``Decimal('0.1')``.
    """
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value.quantize(Q2, rounding=ROUND_HALF_UP)
    if isinstance(value, bool):
        # bool is a subclass of int — reject explicitly, a boolean is
        # never a monetary value and accepting it hides callsite bugs.
        raise InvalidOperation(f"cannot coerce bool to Money: {value!r}")
    if isinstance(value, int):
        return Decimal(value).quantize(Q2, rounding=ROUND_HALF_UP)
    if isinstance(value, float):
        return Decimal(str(value)).quantize(Q2, rounding=ROUND_HALF_UP)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return ZERO
        return Decimal(stripped).quantize(Q2, rounding=ROUND_HALF_UP)
    raise InvalidOperation(f"cannot coerce {type(value).__name__} to Money: {value!r}")


def row_to_decimal(row_value: Any) -> Decimal:
    """Thin alias for ``to_decimal`` at the DB read boundary.

    Exists as a separate name so "where do we cross the storage
    boundary?" is greppable. If we ever migrate to NUMERIC columns in
    Postgres, this is the one function that changes.
    """
    return to_decimal(row_value)


def money_sum(values: Iterable[Any], *, start: Decimal = ZERO) -> Decimal:
    """Sum an iterable of monetary values into a quantized Decimal.

    Designed to be a drop-in replacement for the naive ``sum(
    float(x) for x in items)`` pattern. Every element is run through
    ``to_decimal`` first so mixed-type iterables (DB REAL rows, user-
    typed strings, already-Decimal aggregates) all land on the same
    exact scale before the add.
    """
    total = start
    for v in values:
        total = total + to_decimal(v)
    return total.quantize(Q2, rounding=ROUND_HALF_UP)


def money_round(value: Any) -> Decimal:
    """Coerce + quantize in one call. Alias for ``to_decimal``."""
    return to_decimal(value)


def money_to_float(value: Any) -> float:
    """Convert to a JSON-safe float at the very last boundary.

    Only call this when building outbound JSON payloads (ERP bills,
    API responses). Internal arithmetic should stay in Decimal.
    """
    return float(to_decimal(value))


def _money_validate(v: Any) -> Decimal:
    return to_decimal(v)


def _money_serialize_json(v: Decimal) -> float:
    # quantize defensively in case a callsite mutated v post-validation
    return float(v.quantize(Q2, rounding=ROUND_HALF_UP))


Money = Annotated[
    Decimal,
    BeforeValidator(_money_validate),
    PlainSerializer(_money_serialize_json, return_type=float, when_used="json"),
]
"""Pydantic type alias for a monetary amount.

Fields typed ``Money`` accept Decimal / int / str / float on input
(coerced + quantized to 2dp), travel as Decimal through Python code,
and emit as a JSON number in API responses.

Usage::

    from clearledgr.core.money import Money

    class Bill(BaseModel):
        amount: Money
        tax_amount: Money = ZERO
"""


__all__ = [
    "Q2",
    "ZERO",
    "Money",
    "to_decimal",
    "row_to_decimal",
    "money_sum",
    "money_round",
    "money_to_float",
]
