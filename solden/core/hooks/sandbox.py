"""Capability-gated, resource-limited WASM sandbox for customer code hooks.

Running customer-supplied code in a multi-tenant backend is, done naively,
arbitrary code execution. This module makes it safe by executing hooks inside a
WebAssembly sandbox (Wasmtime) with:

  * **No ambient capabilities** — the module is instantiated with NO imports.
    A guest that tries to import WASI / host functions fails to instantiate, so
    it has no syscalls, no filesystem, no network, no clock. Capabilities are
    added only by explicitly granting host functions (none, by default).
  * **CPU bound** — fuel metering traps the guest after a fixed instruction
    budget (infinite loops die).
  * **Wall-clock bound** — an epoch-interruption watchdog traps a guest that
    blocks past the timeout.
  * **Memory bound** — the store limiter caps linear-memory growth.
  * **Fail-closed** — any trap, limit, missing runtime, or malformed output is
    treated as DENY. A hook never fails *open*.

The hook ABI: the guest exports ``memory`` and ``hook(in_ptr, in_len) -> i64``;
the host writes the input JSON into guest memory, calls ``hook``, and reads the
returned ``(out_ptr << 32) | out_len`` slice back as a JSON :class:`HookResult`.

Gated behind ``FEATURE_WORKFLOW_HOOKS``. The live execution of customer
JavaScript (via a QuickJS-in-WASM guest) and external enablement require the
``wasmtime`` dependency, the guest artifact, and an adversarial security review
before the flag is flipped for any tenant. The isolation core here is proven
with inline WAT modules in the test suite.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

# Conservative defaults. Tunable per-tenant later (Phase 5 quotas).
DEFAULT_FUEL = 5_000_000
DEFAULT_MEMORY_BYTES = 16 * 1024 * 1024  # 16 MiB
DEFAULT_TIMEOUT_MS = 250
_INPUT_OFFSET = 1024  # where the host writes input JSON in guest memory


class SandboxError(Exception):
    """Sandbox could not run the hook (missing runtime, bad module, trap)."""


class SandboxDenied(SandboxError):
    """The guest hit a resource limit or trapped — treated as a hard deny."""


@dataclass
class SandboxLimits:
    fuel: int = DEFAULT_FUEL
    memory_bytes: int = DEFAULT_MEMORY_BYTES
    timeout_ms: int = DEFAULT_TIMEOUT_MS


@dataclass
class HookResult:
    """A hook's decision. Defaults are the safe/no-op outcome."""
    allow: bool = True
    deny_reason: str = ""
    data_patch: Dict[str, Any] = field(default_factory=dict)
    effects: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def deny(cls, reason: str) -> "HookResult":
        return cls(allow=False, deny_reason=reason)

    @classmethod
    def from_json(cls, raw: Union[str, bytes, Dict[str, Any]]) -> "HookResult":
        data = raw if isinstance(raw, dict) else json.loads(raw)
        if not isinstance(data, dict):
            raise SandboxError("hook output is not a JSON object")
        patch = data.get("data_patch") or {}
        effects = data.get("effects") or []
        if not isinstance(patch, dict):
            raise SandboxError("data_patch must be an object")
        if not isinstance(effects, list):
            raise SandboxError("effects must be a list")
        return cls(
            allow=bool(data.get("allow", True)),
            deny_reason=str(data.get("deny_reason") or ""),
            data_patch=patch,
            effects=effects,
        )


def runtime_available() -> bool:
    """True iff the WASM runtime dependency is importable."""
    try:
        import wasmtime  # noqa: F401
        return True
    except Exception:
        return False


class WasmtimeSandbox:
    """A single-shot WASM executor with hard CPU/memory/wall-clock limits."""

    def __init__(self, limits: Optional[SandboxLimits] = None):
        self.limits = limits or SandboxLimits()

    def _engine_store(self):
        import wasmtime
        config = wasmtime.Config()
        config.consume_fuel = True
        config.epoch_interruption = True
        engine = wasmtime.Engine(config)
        store = wasmtime.Store(engine)
        # CPU budget. Fail closed: if neither fuel API works, do NOT run an
        # unmetered guest — raise so the hook is denied rather than uncapped.
        if hasattr(store, "set_fuel"):
            store.set_fuel(self.limits.fuel)
        elif hasattr(store, "add_fuel"):
            store.add_fuel(self.limits.fuel)
        else:
            raise SandboxError("fuel metering unavailable; refusing to run unmetered")
        # Memory budget.
        store.set_limits(memory_size=self.limits.memory_bytes)
        # Wall-clock budget: trip the epoch after timeout_ms.
        store.set_epoch_deadline(1)
        return engine, store

    def _instantiate(self, store, engine, module_source: Union[str, bytes]):
        import wasmtime
        module = wasmtime.Module(engine, module_source)
        # NO imports granted: a guest requiring any import fails here. This is
        # the capability gate — no syscalls, no WASI, no host functions.
        if module.imports:
            raise SandboxError(
                "hook module requests imports; none are granted (capability gate)"
            )
        return wasmtime.Instance(store, module, [])

    def run_numeric(self, module_source: Union[str, bytes], entry: str = "run") -> int:
        """Run a no-arg exported function returning i32. Used to prove limits."""
        engine, store = self._engine_store()
        watchdog = _Watchdog(engine, self.limits.timeout_ms)
        try:
            instance = self._instantiate(store, engine, module_source)
            fn = instance.exports(store).get(entry)
            if fn is None:
                raise SandboxError(f"hook module has no export {entry!r}")
            watchdog.start()
            return int(fn(store))
        except SandboxError:
            raise
        except Exception as exc:  # traps: fuel, epoch, memory, anything
            raise SandboxDenied(f"guest trapped: {exc}")
        finally:
            watchdog.cancel()

    def run_hook_module(
        self,
        module_source: Union[str, bytes],
        input_json: str,
        entry: str = "hook",
    ) -> str:
        """Run the JSON-ABI hook and return its output JSON string."""
        engine, store = self._engine_store()
        watchdog = _Watchdog(engine, self.limits.timeout_ms)
        try:
            instance = self._instantiate(store, engine, module_source)
            exports = instance.exports(store)
            memory = exports.get("memory")
            hook = exports.get(entry)
            if memory is None or hook is None:
                raise SandboxError("hook module must export 'memory' and 'hook'")
            data = input_json.encode("utf-8")
            self._write(store, memory, _INPUT_OFFSET, data)
            watchdog.start()
            packed = int(hook(store, _INPUT_OFFSET, len(data)))
            out_ptr = (packed >> 32) & 0xFFFFFFFF
            out_len = packed & 0xFFFFFFFF
            return self._read(store, memory, out_ptr, out_len).decode("utf-8")
        except SandboxError:
            raise
        except Exception as exc:
            raise SandboxDenied(f"guest trapped: {exc}")
        finally:
            watchdog.cancel()

    @staticmethod
    def _write(store, memory, offset: int, data: bytes) -> None:
        buf = memory.data_ptr(store)
        size = memory.data_len(store)
        if offset + len(data) > size:
            raise SandboxDenied("input exceeds guest memory")
        for i, b in enumerate(data):
            buf[offset + i] = b

    @staticmethod
    def _read(store, memory, offset: int, length: int) -> bytes:
        buf = memory.data_ptr(store)
        size = memory.data_len(store)
        if length < 0 or offset < 0 or offset + length > size:
            raise SandboxDenied("hook output pointer out of bounds")
        return bytes(buf[offset:offset + length])


class _Watchdog:
    """Trip the engine's epoch after a timeout so a blocked guest is killed."""

    def __init__(self, engine, timeout_ms: int):
        self._engine = engine
        self._timeout_s = max(timeout_ms, 1) / 1000.0
        self._timer: Optional[threading.Timer] = None

    def start(self) -> None:
        self._timer = threading.Timer(self._timeout_s, self._engine.increment_epoch)
        self._timer.daemon = True
        self._timer.start()

    def cancel(self) -> None:
        if self._timer is not None:
            self._timer.cancel()


def run_hook(
    module_source: Optional[Union[str, bytes]],
    context: Dict[str, Any],
    *,
    limits: Optional[SandboxLimits] = None,
) -> HookResult:
    """Run a customer hook, fail-closed.

    ``module_source`` None → no hook configured → no-op allow. Any failure
    (runtime missing, bad module, trap, limit, malformed output) → DENY.
    """
    if not module_source:
        return HookResult()  # no hook -> allow
    if not runtime_available():
        logger.warning("workflow hook configured but WASM runtime unavailable")
        return HookResult.deny("sandbox_runtime_unavailable")
    try:
        sandbox = WasmtimeSandbox(limits)
        out = sandbox.run_hook_module(module_source, json.dumps(context or {}))
        return HookResult.from_json(out)
    except SandboxDenied as exc:
        return HookResult.deny(f"sandbox_denied:{exc}")
    except Exception as exc:
        logger.exception("hook execution failed")
        return HookResult.deny(f"sandbox_error:{exc}")
