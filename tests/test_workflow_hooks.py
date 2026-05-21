"""Phase 3 — customer logic on declarative Box types: conditions, the WASM
sandbox, the effect catalog, and the transition dispatcher.

The safe pure-Python layers (expression conditions, effect catalog, dispatcher
wiring) are always tested. The WASM isolation proofs run when ``wasmtime`` is
importable (it is in requirements), proving fuel/epoch limits, the capability
gate (no imports granted), the JSON hook ABI, and fail-closed behavior.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from solden.core import box_registry, database as db_module, workflow_spec  # noqa: E402
from solden.core.effects.catalog import EffectError, apply_effect  # noqa: E402
from solden.core.hooks import expressions as ex  # noqa: E402
from solden.core.hooks.dispatcher import HookDenied, run_transition_hooks  # noqa: E402
from solden.core.hooks.sandbox import (  # noqa: E402
    HookResult, SandboxDenied, SandboxError, WasmtimeSandbox, run_hook,
    runtime_available,
)
from solden.core.workflow_spec import WorkflowSpec  # noqa: E402

_wasm = pytest.mark.skipif(not runtime_available(), reason="wasmtime not installed")


# --------------------------------------------------------------------------
# 1. Expression condition language (safe, no sandbox)
# --------------------------------------------------------------------------

def test_expression_evaluates_safe_conditions():
    assert ex.evaluate_condition("amount > 5000", {"amount": 9000}) is True
    assert ex.evaluate_condition("amount <= 5000 and risk != 'high'",
                                 {"amount": 100, "risk": "low"}) is True
    assert ex.evaluate_condition("tier in ['gold', 'platinum']", {"tier": "gold"}) is True
    assert ex.evaluate_condition("len(items) >= 2", {"items": [1, 2, 3]}) is True


@pytest.mark.parametrize("expr", [
    "__import__('os').system('id')",   # call to non-whitelisted name
    "().__class__",                     # attribute access
    "[x for x in range(10)]",           # comprehension
    "(lambda: 1)()",                    # lambda
    "open('/etc/passwd')",              # non-whitelisted builtin
    "'a' * 999999999",                  # sequence repetition (memory DoS)
])
def test_expression_rejects_dangerous_constructs(expr):
    with pytest.raises(ex.ExpressionError):
        ex.evaluate_condition(expr, {})


def test_expression_runtime_error_is_false_not_raise():
    # comparing missing/typed values that error at runtime -> guard does not fire
    assert ex.evaluate_condition("amount > 5000", {"amount": "not_a_number"}) is False


# --------------------------------------------------------------------------
# 2. WASM sandbox isolation
# --------------------------------------------------------------------------

@_wasm
def test_sandbox_runs_bounded_computation():
    wat = '(module (func (export "run") (result i32) (i32.const 42)))'
    assert WasmtimeSandbox().run_numeric(wat) == 42


@_wasm
def test_sandbox_kills_infinite_loop():
    from solden.core.hooks.sandbox import SandboxLimits
    wat = '(module (func (export "run") (result i32) (loop $l (br $l)) (i32.const 0)))'
    with pytest.raises(SandboxDenied):
        WasmtimeSandbox(SandboxLimits(fuel=200_000)).run_numeric(wat)


@_wasm
def test_sandbox_capability_gate_blocks_imports():
    # A guest that imports anything (e.g. WASI / host funcs) cannot instantiate.
    wat = ('(module (import "env" "f" (func $f)) '
           '(func (export "run") (result i32) (call $f) (i32.const 0)))')
    with pytest.raises(SandboxError):
        WasmtimeSandbox().run_numeric(wat)


def _const_hook_wat(payload: dict) -> str:
    data = json.dumps(payload).encode("utf-8")
    esc = "".join(f"\\{b:02x}" for b in data)
    return (
        '(module (memory (export "memory") 1) '
        f'(data (i32.const 16) "{esc}") '
        '(func (export "hook") (param i32 i32) (result i64) '
        f'(i64.or (i64.shl (i64.const 16) (i64.const 32)) (i64.const {len(data)}))))'
    )


@_wasm
def test_sandbox_hook_abi_round_trip():
    wat = _const_hook_wat({"allow": True, "data_patch": {"reviewed": True}, "effects": []})
    result = run_hook(wat, {"amount": 100})
    assert result.allow is True
    assert result.data_patch == {"reviewed": True}


@_wasm
def test_sandbox_fail_closed_on_trap():
    bad = ('(module (memory (export "memory") 1) '
           '(func (export "hook") (param i32 i32) (result i64) (unreachable)))')
    r = run_hook(bad, {})
    assert r.allow is False and "sandbox_denied" in r.deny_reason


def test_run_hook_noop_when_no_module():
    assert run_hook(None, {}).allow is True


def test_run_hook_fail_closed_when_runtime_missing(monkeypatch):
    monkeypatch.setattr("solden.core.hooks.sandbox.runtime_available", lambda: False)
    r = run_hook("(module)", {})
    assert r.allow is False and r.deny_reason == "sandbox_runtime_unavailable"


# --------------------------------------------------------------------------
# 3. Effect catalog (SSRF guard + dispatch)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "http://127.0.0.1/x",
    "http://169.254.169.254/latest/meta-data/",   # AWS/GCP metadata
    "http://10.0.0.5/internal",
    "http://localhost:80/x",
    "ftp://example.com/x",                          # blocked scheme
    "http://example.com:8080/x",                    # blocked port
    "http://[::1]/x",                               # IPv6 loopback
    "http://[::ffff:169.254.169.254]/x",           # IPv4-mapped metadata (C2)
    "http://100.100.100.200/x",                     # CGNAT / Alibaba metadata (C2)
    "http://0.0.0.0/x",                             # unspecified
])
def test_webhook_effect_ssrf_guard_refuses(url):
    res = apply_effect({"type": "webhook", "url": url}, {"organization_id": "o"})
    assert res["status"] in ("refused", "error")


def test_webhook_effect_allows_public_host(monkeypatch):
    import socket

    from solden.core.effects import catalog
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    )
    calls = {}

    def _fake_post(scheme, host, family, ip, port, path, body):
        calls.update({"host": host, "ip": ip, "port": port, "path": path})
        return 200

    # Pin happens against the validated IP — assert we connect to THAT ip.
    monkeypatch.setattr(catalog, "_safe_post", _fake_post)
    res = apply_effect(
        {"type": "webhook", "url": "https://example.com/hook", "payload": {"a": 1}},
        {"organization_id": "o", "box_id": "b"},
    )
    assert res["status"] == "ok"
    assert calls["ip"] == "93.184.216.34" and calls["host"] == "example.com"
    assert calls["path"] == "/hook"


def test_webhook_rebinding_blocked_when_any_address_private(monkeypatch):
    # DNS returns a public AND a private address: the guard must refuse the
    # whole request (can't pick only the public one and risk a rebind).
    import socket

    from solden.core.effects import catalog
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443)),
        ],
    )
    monkeypatch.setattr(catalog, "_safe_post", lambda *a, **k: 200)
    res = apply_effect(
        {"type": "webhook", "url": "https://rebind.example/hook"},
        {"organization_id": "o"},
    )
    assert res["status"] == "refused"


def test_unknown_effect_is_error_not_raise():
    res = apply_effect({"type": "rm_rf"}, {})
    assert res["status"] == "error" and res["error"] == "unknown_effect"


def test_log_effect_ok():
    assert apply_effect({"type": "log", "message": "hi"}, {})["status"] == "ok"


# --------------------------------------------------------------------------
# 4. Dispatcher — conditions + hooks gate transitions (flag on)
# --------------------------------------------------------------------------

ORG = "orgWFHooks"


@pytest.fixture()
def hooks_on(monkeypatch):
    monkeypatch.setenv("FEATURE_WORKFLOW_HOOKS", "true")
    yield


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization(ORG, organization_name=ORG)
    return inst


def _spec(**over) -> WorkflowSpec:
    base = dict(
        box_type="loan_review",
        url_slug="loan-review",
        states=("draft", "submitted", "approved"),
        initial_state="draft",
        terminal_states=("approved",),
        transitions={"draft": {"submitted"}, "submitted": {"approved"}},
        action_states={"submit": "submitted", "approve": "approved"},
        fields=("amount",),
    )
    base.update(over)
    return WorkflowSpec(**base)


def test_condition_guard_denies_transition(hooks_on):
    spec = _spec(conditions={"draft->submitted": "amount <= 10000"})
    box_ok = {"id": "L1", "box_type": "loan_review", "organization_id": ORG, "data": {"amount": 5000}}
    box_no = {"id": "L2", "box_type": "loan_review", "organization_id": ORG, "data": {"amount": 50000}}
    assert run_transition_hooks(spec, box_ok, "draft", "submitted", actor="u").allow is True
    d = run_transition_hooks(spec, box_no, "draft", "submitted", actor="u")
    assert d.allow is False and "condition_failed" in d.deny_reason


def test_dispatcher_noop_when_flag_off():
    # No monkeypatch -> flag default off -> always allow regardless of conditions.
    spec = _spec(conditions={"draft->submitted": "amount <= 0"})
    box = {"id": "L3", "box_type": "loan_review", "organization_id": ORG, "data": {"amount": 999}}
    assert run_transition_hooks(spec, box, "draft", "submitted").allow is True


@pytest.fixture()
def registered_spec():
    spec = _spec(conditions={"draft->submitted": "amount <= 10000"})
    workflow_spec.register_spec(spec)
    try:
        yield spec
    finally:
        workflow_spec.unregister_spec("loan_review")


def test_store_blocks_transition_on_condition_deny(db, hooks_on, registered_spec):
    box_registry.create_box("loan_review", {
        "id": "LR-deny", "organization_id": ORG, "data": {"amount": 50000},
    }, db)
    with pytest.raises(HookDenied):
        box_registry.update_box("loan_review", "LR-deny", db, state="submitted", actor_id="u")
    # Box unchanged.
    assert box_registry.get_box("loan_review", "LR-deny", db)["state"] == "draft"


@_wasm
def test_store_applies_hook_data_patch(db, hooks_on):
    spec = _spec(hooks={"on_enter:submitted": {"wasm": _const_hook_wat(
        {"allow": True, "data_patch": {"reviewed": True}, "effects": []})}})
    workflow_spec.register_spec(spec)
    try:
        box_registry.create_box("loan_review", {
            "id": "LR-patch", "organization_id": ORG, "data": {"amount": 100},
        }, db)
        box_registry.update_box("loan_review", "LR-patch", db, state="submitted", actor_id="u")
        loaded = box_registry.get_box("loan_review", "LR-patch", db)
        assert loaded["state"] == "submitted"
        assert loaded["reviewed"] is True            # hook patch applied
        assert loaded["data"]["reviewed"] is True
    finally:
        workflow_spec.unregister_spec("loan_review")
