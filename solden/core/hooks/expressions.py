"""Safe condition expression language over box data.

The common case for customer logic on a declarative Box type is a *condition*
(a transition guard: "only auto-approve when ``amount <= 5000 and
vendor_risk != 'high'``"). Running full customer code for that would be
overkill and would needlessly invoke the WASM sandbox. This module evaluates
such conditions safely WITHOUT any code execution:

  * the expression is parsed to an AST (``ast.parse(mode="eval")``);
  * a strict allowlist of node types is enforced — no attribute access, no
    arbitrary calls, no comprehensions, no lambdas, no f-strings;
  * names resolve only against the provided context dict (the box's fields);
  * a small set of pure builtins (len/abs/min/max/round/int/float/str/bool)
    is permitted, called by bare name only;
  * string/list repetition (``"x" * n``) is blocked to prevent memory blowups.

There is no ``eval``/``exec`` anywhere here. The worst a malformed or hostile
expression can do is raise, which the caller treats as "condition not met".
"""
from __future__ import annotations

import ast
import logging
from functools import lru_cache
from typing import Any, Dict

logger = logging.getLogger(__name__)

MAX_EXPRESSION_LEN = 1000
MAX_AST_NODES = 200

_SAFE_BUILTINS = {
    "len": len, "abs": abs, "min": min, "max": max, "round": round,
    "int": int, "float": float, "str": str, "bool": bool,
}

_ALLOWED_NODES = (
    ast.Expression, ast.BoolOp, ast.UnaryOp, ast.BinOp, ast.Compare,
    ast.Name, ast.Load, ast.Constant, ast.List, ast.Tuple, ast.Set,
    ast.Dict, ast.Subscript, ast.IfExp, ast.Call,
    ast.And, ast.Or, ast.Not, ast.USub, ast.UAdd,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.FloorDiv,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
)


class ExpressionError(ValueError):
    """Raised when an expression is structurally disallowed or invalid."""


@lru_cache(maxsize=512)
def _compile(expr: str) -> ast.Expression:
    if not isinstance(expr, str):
        raise ExpressionError("expression must be a string")
    if len(expr) > MAX_EXPRESSION_LEN:
        raise ExpressionError("expression too long")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"syntax error: {exc}")

    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_AST_NODES:
        raise ExpressionError("expression too complex")
    for node in nodes:
        if not isinstance(node, _ALLOWED_NODES):
            raise ExpressionError(
                f"disallowed expression element: {type(node).__name__}"
            )
        if isinstance(node, ast.Call):
            # Only bare-name calls to whitelisted builtins. No attributes,
            # no calling values resolved from context.
            if not isinstance(node.func, ast.Name) or node.func.id not in _SAFE_BUILTINS:
                raise ExpressionError("only whitelisted builtins may be called")
            if node.keywords:
                raise ExpressionError("keyword arguments are not allowed")
    return tree


def _eval(node: ast.AST, ctx: Dict[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _eval(node.body, ctx)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in _SAFE_BUILTINS:
            return _SAFE_BUILTINS[node.id]
        if node.id in ctx:
            return ctx[node.id]
        raise ExpressionError(f"unknown name: {node.id!r}")
    if isinstance(node, ast.BoolOp):
        values = node.values
        if isinstance(node.op, ast.And):
            result: Any = True
            for v in values:
                result = _eval(v, ctx)
                if not result:
                    return result
            return result
        # Or
        result = False
        for v in values:
            result = _eval(v, ctx)
            if result:
                return result
        return result
    if isinstance(node, ast.UnaryOp):
        operand = _eval(node.operand, ctx)
        if isinstance(node.op, ast.Not):
            return not operand
        if isinstance(node.op, ast.USub):
            return -operand
        return +operand
    if isinstance(node, ast.BinOp):
        left = _eval(node.left, ctx)
        right = _eval(node.right, ctx)
        if isinstance(node.op, ast.Mult):
            # Block string/list repetition (memory DoS); allow numeric mult.
            if isinstance(left, (str, bytes, list, tuple)) or isinstance(right, (str, bytes, list, tuple)):
                raise ExpressionError("sequence repetition is not allowed")
            return left * right
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.Mod):
            return left % right
        if isinstance(node.op, ast.FloorDiv):
            return left // right
        raise ExpressionError("disallowed operator")
    if isinstance(node, ast.Compare):
        left = _eval(node.left, ctx)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval(comparator, ctx)
            if not _compare(op, left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.IfExp):
        return _eval(node.body, ctx) if _eval(node.test, ctx) else _eval(node.orelse, ctx)
    if isinstance(node, ast.List):
        return [_eval(e, ctx) for e in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_eval(e, ctx) for e in node.elts)
    if isinstance(node, ast.Set):
        return {_eval(e, ctx) for e in node.elts}
    if isinstance(node, ast.Dict):
        return {_eval(k, ctx): _eval(v, ctx) for k, v in zip(node.keys, node.values)}
    if isinstance(node, ast.Subscript):
        value = _eval(node.value, ctx)
        key = _eval(node.slice, ctx)
        return value[key]
    if isinstance(node, ast.Call):
        func = _SAFE_BUILTINS[node.func.id]  # validated in _compile
        args = [_eval(a, ctx) for a in node.args]
        return func(*args)
    raise ExpressionError(f"disallowed expression element: {type(node).__name__}")


def _compare(op: ast.cmpop, left: Any, right: Any) -> bool:
    if isinstance(op, ast.Eq):
        return left == right
    if isinstance(op, ast.NotEq):
        return left != right
    if isinstance(op, ast.Lt):
        return left < right
    if isinstance(op, ast.LtE):
        return left <= right
    if isinstance(op, ast.Gt):
        return left > right
    if isinstance(op, ast.GtE):
        return left >= right
    if isinstance(op, ast.In):
        return left in right
    if isinstance(op, ast.NotIn):
        return left not in right
    raise ExpressionError("disallowed comparison")


def evaluate_expression(expr: str, context: Dict[str, Any]) -> Any:
    """Evaluate *expr* against *context*. Raises ExpressionError on bad input."""
    tree = _compile(expr)
    return _eval(tree, dict(context or {}))


def evaluate_condition(expr: str, context: Dict[str, Any]) -> bool:
    """Evaluate *expr* as a boolean guard.

    A structurally-invalid expression raises (caller's bug). A *runtime* error
    (e.g. comparing incompatible types, missing optional field) is treated as
    ``False`` — a guard that can't be evaluated does not fire.
    """
    tree = _compile(expr)  # structural errors surface to the author
    try:
        return bool(_eval(tree, dict(context or {})))
    except ExpressionError:
        raise
    except Exception:
        logger.debug("condition runtime error for %r; treating as False", expr)
        return False
