"""Transition hook dispatcher — conditions, code hooks, and effects.

Called by the generic Box store on every declarative-type transition. Entirely
gated behind ``FEATURE_WORKFLOW_HOOKS``: when off (the default), this is a
no-op returning ALLOW with no patch and no effects, so the transition path is
byte-for-byte unchanged.

When on, for a transition ``from_state -> to_state`` it:

  1. evaluates any matching *condition* guard (safe expression layer) — a
     False guard denies the transition;
  2. runs any matching *hook* (expression or WASM sandbox) — a deny result
     denies the transition; an allow result may carry a whitelisted
     ``data_patch`` and a list of ``effects``;
  3. applies the requested effects (best-effort, via the built-in catalog).

Hook/condition keys on the spec, most specific first:
  ``"{from_state}->{to_state}"`` then ``"on_enter:{to_state}"``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from solden.core.feature_flags import is_workflow_hooks_enabled

logger = logging.getLogger(__name__)


class HookDenied(Exception):
    """A condition or hook vetoed the transition."""


@dataclass
class HookDecision:
    allow: bool = True
    deny_reason: str = ""
    data_patch: Dict[str, Any] = field(default_factory=dict)
    effects_applied: List[Dict[str, Any]] = field(default_factory=list)


def _hook_keys(from_state: str, to_state: str) -> List[str]:
    return [f"{from_state}->{to_state}", f"on_enter:{to_state}"]


def _build_context(box: Dict[str, Any], from_state: str, to_state: str,
                   actor: str) -> Dict[str, Any]:
    data = box.get("data") if isinstance(box.get("data"), dict) else {}
    ctx = dict(data)
    ctx.update({
        "box_id": box.get("id"),
        "box_type": box.get("box_type"),
        "organization_id": box.get("organization_id"),
        "from_state": from_state,
        "to_state": to_state,
        "actor": actor,
    })
    return ctx


def run_transition_hooks(
    spec: Any,
    box: Dict[str, Any],
    from_state: str,
    to_state: str,
    *,
    actor: str = "",
) -> HookDecision:
    """Evaluate guards + hooks for a transition and apply effects.

    No-op (ALLOW) unless ``FEATURE_WORKFLOW_HOOKS`` is on. Raises nothing; the
    caller inspects ``decision.allow``.
    """
    if not is_workflow_hooks_enabled():
        return HookDecision()

    conditions = getattr(spec, "conditions", None) or {}
    hooks = getattr(spec, "hooks", None) or {}
    if not conditions and not hooks:
        return HookDecision()

    import time
    started = time.monotonic()
    ctx = _build_context(box, from_state, to_state, actor)
    keys = _hook_keys(from_state, to_state)
    decision = _evaluate(conditions, hooks, keys, ctx, box)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    _record(box, keys, decision, elapsed_ms)
    return decision


def _evaluate(conditions, hooks, keys, ctx, box) -> HookDecision:
    # 1. Condition guards (safe expression layer).
    from solden.core.hooks.expressions import ExpressionError, evaluate_condition
    for key in keys:
        expr = conditions.get(key)
        if not expr:
            continue
        try:
            if not evaluate_condition(str(expr), ctx):
                return HookDecision(allow=False, deny_reason=f"condition_failed:{key}")
        except ExpressionError as exc:
            logger.warning("[hooks] invalid condition %r: %s", key, exc)
            return HookDecision(allow=False, deny_reason=f"condition_invalid:{key}")

    # 2. Code hooks (expression result or WASM sandbox).
    from solden.core.effects.catalog import apply_effects
    patch: Dict[str, Any] = {}
    effects: List[Dict[str, Any]] = []
    for key in keys:
        cfg = hooks.get(key)
        if not cfg or not isinstance(cfg, dict):
            continue
        result = _run_one_hook(cfg, ctx)
        if not result.allow:
            return HookDecision(allow=False, deny_reason=result.deny_reason or f"hook_denied:{key}")
        if result.data_patch:
            patch.update(result.data_patch)
        if result.effects:
            effects.extend(result.effects)

    applied: List[Dict[str, Any]] = []
    if effects:
        effect_ctx = {
            "organization_id": box.get("organization_id"),
            "box_type": box.get("box_type"),
            "box_id": box.get("id"),
        }
        applied = apply_effects(effects, effect_ctx)

    return HookDecision(allow=True, data_patch=patch, effects_applied=applied)


def _record(box, keys, decision: HookDecision, elapsed_ms: int) -> None:
    """Best-effort audit of a hook run. Never raises into the transition path."""
    try:
        from solden.core.database import get_db
        get_db().record_hook_run(
            organization_id=str(box.get("organization_id") or ""),
            box_type=str(box.get("box_type") or ""),
            box_id=str(box.get("id") or ""),
            hook_key=",".join(keys),
            outcome="allow" if decision.allow else "deny",
            deny_reason=decision.deny_reason,
            duration_ms=elapsed_ms,
        )
    except Exception:
        logger.debug("[hooks] failed to record hook run", exc_info=True)


def _run_one_hook(cfg: Dict[str, Any], ctx: Dict[str, Any]):
    from solden.core.hooks.sandbox import HookResult, run_hook
    # Expression-tier hook: a boolean guard expressed inline.
    if "expr" in cfg:
        from solden.core.hooks.expressions import ExpressionError, evaluate_condition
        try:
            ok = evaluate_condition(str(cfg["expr"]), ctx)
        except ExpressionError:
            return HookResult.deny("hook_expr_invalid")
        return HookResult(allow=bool(ok), deny_reason="" if ok else "hook_expr_false")
    # Code-tier hook: customer WASM module, run in the sandbox (fail-closed).
    module = cfg.get("wasm")
    return run_hook(module, ctx)
