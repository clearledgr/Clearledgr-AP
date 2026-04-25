"""Runtime metrics collection with durable storage on production-like profiles."""
from __future__ import annotations

import logging
import os
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Dict

logger = logging.getLogger(__name__)

_PRODUCTION_ENVS = {"production", "prod", "staging", "stage"}

# Background executor for persistent metric writes. Calling
# ``record_request``/``record_error`` from inside a request middleware
# previously did the INSERT inline — synchronously, on the asyncio
# event-loop thread (BaseHTTPMiddleware runs sync code on the loop).
# That blocked the response on a DB roundtrip per request and made
# /health p50 trend toward seconds. Submitting to a single-worker
# executor moves the write off the hot path: the request returns
# immediately and the write completes in the background.
#
# Single worker (not a pool) by design — INSERTs serialize anyway on
# the underlying connection pool, and a single worker gives FIFO
# semantics that ``flush_pending`` can rely on without tracking
# pending futures by hand.
_METRICS_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cl-metrics")


def _submit_persist(fn, /, *args, **kwargs) -> None:
    """Fire-and-forget metric persistence. Logs and swallows on failure
    — metrics dropping a record is preferable to a request blocking
    or raising on an audit-side write.
    """
    try:
        future = _METRICS_EXECUTOR.submit(fn, *args, **kwargs)
        # Attach a logger callback so unhandled exceptions in the
        # background work surface in the worker logs instead of being
        # silently swallowed by Python's GC of unawaited futures.
        future.add_done_callback(_log_persist_failure)
    except RuntimeError:
        # Executor is shutting down (process termination). Best-effort:
        # try the write inline so we don't lose the very last metrics.
        try:
            fn(*args, **kwargs)
        except Exception:  # noqa: BLE001
            pass


def _log_persist_failure(future) -> None:
    exc = future.exception()
    if exc is not None:
        logger.warning("metrics: background persist failed: %s", exc)


def flush_pending(timeout: float = 5.0) -> bool:
    """Block until all in-flight metric writes complete, or timeout
    elapses. For tests + graceful shutdown — production code paths
    should not call this.

    Returns True if the executor drained, False on timeout.
    """
    # Single-worker executor → FIFO semantics: a sentinel submitted
    # now runs strictly after every task that was already queued.
    # Waiting on the sentinel waits on every prior task transitively.
    try:
        sentinel = _METRICS_EXECUTOR.submit(lambda: None)
        sentinel.result(timeout=timeout)
        return True
    except Exception:  # noqa: BLE001
        return False


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(minimum, int(str(raw).strip()))
    except (TypeError, ValueError):
        return default


def _is_production_like() -> bool:
    env_name = str(os.getenv("ENV", "dev")).strip().lower()
    return env_name in _PRODUCTION_ENVS


_PERSISTENT_MODE = _is_production_like()
_METRICS_RETENTION_DAYS = _env_int("API_METRICS_RETENTION_DAYS", 30, minimum=1)
_METRICS_PRUNE_INTERVAL_SECONDS = _env_int("API_METRICS_PRUNE_INTERVAL_SECONDS", 300, minimum=5)
_SCHEMA_LOCK = Lock()
_PRUNE_LOCK = Lock()
_SCHEMA_READY = False
_LAST_PRUNE_MONOTONIC = 0.0

# Dev-mode fallback and safety net if persistent writes fail.
_metrics: Dict[str, Any] = {
    "requests": defaultdict(int),
    "errors": defaultdict(int),
    "reconciliation_runs": defaultdict(int),
    "response_times": [],
    "start_time": datetime.now(timezone.utc).isoformat(),
}


def _decode_row(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    if hasattr(row, "keys"):
        return dict(row)
    return {}


def _db() -> Any | None:
    try:
        from clearledgr.core.database import get_db

        return get_db()
    except Exception:
        return None


def _ensure_schema(db: Any) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        if not db or not hasattr(db, "connect"):
            return
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                (
                    """
                    CREATE TABLE IF NOT EXISTS api_request_metrics (
                        id TEXT PRIMARY KEY,
                        ts TEXT NOT NULL,
                        method TEXT NOT NULL,
                        path TEXT NOT NULL,
                        status_code INTEGER NOT NULL,
                        duration_ms REAL NOT NULL
                    )
                    """)
            )
            cur.execute(
                (
                    """
                    CREATE TABLE IF NOT EXISTS api_error_metrics (
                        id TEXT PRIMARY KEY,
                        ts TEXT NOT NULL,
                        error_type TEXT NOT NULL,
                        path TEXT
                    )
                    """)
            )
            cur.execute(
                (
                    """
                    CREATE TABLE IF NOT EXISTS api_reconciliation_metrics (
                        id TEXT PRIMARY KEY,
                        ts TEXT NOT NULL,
                        source_type TEXT NOT NULL,
                        status TEXT NOT NULL,
                        duration_ms REAL NOT NULL
                    )
                    """)
            )
            cur.execute(
                (
                    """
                    CREATE TABLE IF NOT EXISTS api_metrics_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                    """)
            )
            cur.execute(
                (
                    "CREATE INDEX IF NOT EXISTS idx_api_request_metrics_ts ON api_request_metrics(ts)")
            )
            cur.execute(
                (
                    "CREATE INDEX IF NOT EXISTS idx_api_request_metrics_path ON api_request_metrics(path)")
            )
            cur.execute(
                (
                    "CREATE INDEX IF NOT EXISTS idx_api_error_metrics_ts ON api_error_metrics(ts)")
            )
            cur.execute(
                (
                    "CREATE INDEX IF NOT EXISTS idx_api_recon_metrics_ts ON api_reconciliation_metrics(ts)")
            )
            conn.commit()
        _SCHEMA_READY = True


def _persist_meta_value(db: Any, key: str, value: str) -> None:
    sql = (
        "INSERT INTO api_metrics_meta (key, value) VALUES (%s, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value")
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (str(key), str(value)))
        conn.commit()


def _maybe_prune_old_metrics(db: Any) -> None:
    global _LAST_PRUNE_MONOTONIC
    if not _PERSISTENT_MODE:
        return
    now = time.monotonic()
    if (now - _LAST_PRUNE_MONOTONIC) < _METRICS_PRUNE_INTERVAL_SECONDS:
        return
    with _PRUNE_LOCK:
        now = time.monotonic()
        if (now - _LAST_PRUNE_MONOTONIC) < _METRICS_PRUNE_INTERVAL_SECONDS:
            return
        cutoff = (datetime.now(timezone.utc) - timedelta(days=_METRICS_RETENTION_DAYS)).isoformat()
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM api_request_metrics WHERE ts < %s",
                (cutoff,),
            )
            cur.execute(
                "DELETE FROM api_error_metrics WHERE ts < %s",
                (cutoff,),
            )
            cur.execute(
                "DELETE FROM api_reconciliation_metrics WHERE ts < %s",
                (cutoff,),
            )
            conn.commit()
        _persist_meta_value(db, "last_prune_at", datetime.now(timezone.utc).isoformat())
        _LAST_PRUNE_MONOTONIC = now


def _record_in_memory_request(method: str, path: str, status_code: int, duration_ms: float) -> None:
    _metrics["requests"][f"{method} {path}"] += 1
    _metrics["requests"][f"status_{status_code}"] += 1
    _metrics["response_times"].append(duration_ms)
    if len(_metrics["response_times"]) > 1000:
        _metrics["response_times"] = _metrics["response_times"][-1000:]


def _record_in_memory_error(error_type: str, path: str = "") -> None:
    _metrics["errors"][error_type] += 1
    if path:
        _metrics["errors"][f"{error_type}:{path}"] += 1


def _record_in_memory_reconciliation(source_type: str, status: str, _duration_ms: float) -> None:
    _metrics["reconciliation_runs"][f"{source_type}:{status}"] += 1


def _persist_request_metric(method: str, path: str, status_code: int, duration_ms: float, ts: str) -> None:
    """The blocking part of record_request — runs in the background
    executor so request handlers don't pay for the DB roundtrip."""
    db = _db()
    if not db:
        _record_in_memory_request(method, path, status_code, duration_ms)
        return
    try:
        _ensure_schema(db)
        _maybe_prune_old_metrics(db)
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                (
                    """
                    INSERT INTO api_request_metrics
                    (id, ts, method, path, status_code, duration_ms)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """),
                (
                    f"req_{uuid.uuid4().hex}",
                    ts,
                    str(method or "GET"),
                    str(path or "/"),
                    int(status_code),
                    float(duration_ms),
                ),
            )
            conn.commit()
    except Exception:
        _record_in_memory_request(method, path, status_code, duration_ms)


def record_request(method: str, path: str, status_code: int, duration_ms: float) -> None:
    """Record HTTP request metrics. Non-blocking — the DB write goes
    through the metrics executor."""
    if not _PERSISTENT_MODE:
        _record_in_memory_request(method, path, status_code, duration_ms)
        return
    _submit_persist(
        _persist_request_metric,
        method,
        path,
        status_code,
        duration_ms,
        datetime.now(timezone.utc).isoformat(),
    )


def _persist_error_metric(error_type: str, path: str, ts: str) -> None:
    """Background half of record_error."""
    db = _db()
    if not db:
        _record_in_memory_error(error_type, path)
        return
    try:
        _ensure_schema(db)
        _maybe_prune_old_metrics(db)
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                (
                    """
                    INSERT INTO api_error_metrics
                    (id, ts, error_type, path)
                    VALUES (%s, %s, %s, %s)
                    """),
                (
                    f"err_{uuid.uuid4().hex}",
                    ts,
                    str(error_type or "unknown_error"),
                    str(path or ""),
                ),
            )
            conn.commit()
    except Exception:
        _record_in_memory_error(error_type, path)


def record_error(error_type: str, path: str = "") -> None:
    """Record error metrics. Non-blocking — see :func:`record_request`."""
    if not _PERSISTENT_MODE:
        _record_in_memory_error(error_type, path)
        return
    _submit_persist(
        _persist_error_metric,
        error_type,
        path,
        datetime.now(timezone.utc).isoformat(),
    )


def record_reconciliation_run(source_type: str, status: str, duration_ms: float) -> None:
    """Record reconciliation run metrics."""
    if not _PERSISTENT_MODE:
        _record_in_memory_reconciliation(source_type, status, duration_ms)
        return

    db = _db()
    if not db:
        _record_in_memory_reconciliation(source_type, status, duration_ms)
        return

    try:
        _ensure_schema(db)
        _maybe_prune_old_metrics(db)
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                (
                    """
                    INSERT INTO api_reconciliation_metrics
                    (id, ts, source_type, status, duration_ms)
                    VALUES (%s, %s, %s, %s, %s)
                    """),
                (
                    f"rec_{uuid.uuid4().hex}",
                    datetime.now(timezone.utc).isoformat(),
                    str(source_type or "unknown"),
                    str(status or "unknown"),
                    float(duration_ms),
                ),
            )
            conn.commit()
    except Exception:
        _record_in_memory_reconciliation(source_type, status, duration_ms)


def _format_uptime(seconds: float) -> str:
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if secs > 0 or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


def _in_memory_metrics_payload() -> Dict[str, Any]:
    response_times = _metrics["response_times"]
    avg_response_time = sum(response_times) / len(response_times) if response_times else 0
    p95_response_time = (
        sorted(response_times)[int(len(response_times) * 0.95)]
        if len(response_times) >= 20 else 0
    )
    p99_response_time = (
        sorted(response_times)[int(len(response_times) * 0.99)]
        if len(response_times) >= 100 else 0
    )

    total_requests = sum(v for k, v in _metrics["requests"].items() if not k.startswith("status_"))
    total_errors = sum(_metrics["errors"].values())
    total_runs = sum(_metrics["reconciliation_runs"].values())

    uptime_seconds = (
        datetime.now(timezone.utc) - datetime.fromisoformat(_metrics["start_time"])
    ).total_seconds()

    return {
        "uptime_seconds": int(uptime_seconds),
        "uptime_human": _format_uptime(uptime_seconds),
        "backend": {
            "mode": "in_memory",
            "retention_days": None,
            "prune_interval_seconds": None,
            "last_prune_at": None,
        },
        "requests": {
            "total": total_requests,
            "by_endpoint": {
                k: v for k, v in _metrics["requests"].items() if not k.startswith("status_")
            },
            "by_status": {
                k: v for k, v in _metrics["requests"].items() if k.startswith("status_")
            },
        },
        "errors": {
            "total": total_errors,
            "by_type": dict(_metrics["errors"]),
        },
        "reconciliation_runs": {
            "total": total_runs,
            "by_type_and_status": dict(_metrics["reconciliation_runs"]),
        },
        "performance": {
            "avg_response_time_ms": round(avg_response_time, 2),
            "p95_response_time_ms": round(p95_response_time, 2),
            "p99_response_time_ms": round(p99_response_time, 2),
            "requests_per_second": round(total_requests / uptime_seconds, 2) if uptime_seconds > 0 else 0,
        },
    }


def get_metrics() -> Dict[str, Any]:
    """Get current metrics."""
    if not _PERSISTENT_MODE:
        return _in_memory_metrics_payload()

    db = _db()
    if not db:
        return _in_memory_metrics_payload()

    try:
        _ensure_schema(db)
        _maybe_prune_old_metrics(db)
        with db.connect() as conn:
            cur = conn.cursor()

            cur.execute("SELECT method, path, COUNT(*) AS cnt FROM api_request_metrics GROUP BY method, path")
            request_rows = [_decode_row(row) for row in cur.fetchall()]

            cur.execute("SELECT status_code, COUNT(*) AS cnt FROM api_request_metrics GROUP BY status_code")
            status_rows = [_decode_row(row) for row in cur.fetchall()]

            cur.execute("SELECT duration_ms FROM api_request_metrics ORDER BY ts DESC LIMIT 1000")
            response_rows = [_decode_row(row) for row in cur.fetchall()]

            cur.execute("SELECT error_type, path, COUNT(*) AS cnt FROM api_error_metrics GROUP BY error_type, path")
            error_rows = [_decode_row(row) for row in cur.fetchall()]

            cur.execute(
                (
                    "SELECT source_type, status, COUNT(*) AS cnt FROM api_reconciliation_metrics GROUP BY source_type, status")
            )
            run_rows = [_decode_row(row) for row in cur.fetchall()]

            cur.execute("SELECT MIN(ts) AS min_ts FROM api_request_metrics")
            req_min = _decode_row(cur.fetchone()).get("min_ts")
            cur.execute("SELECT MIN(ts) AS min_ts FROM api_error_metrics")
            err_min = _decode_row(cur.fetchone()).get("min_ts")
            cur.execute("SELECT MIN(ts) AS min_ts FROM api_reconciliation_metrics")
            run_min = _decode_row(cur.fetchone()).get("min_ts")
            cur.execute(
                (
                    "SELECT value FROM api_metrics_meta WHERE key = 'last_prune_at'")
            )
            prune_row = _decode_row(cur.fetchone())
            last_prune_at = prune_row.get("value")

        by_endpoint: Dict[str, int] = {}
        for row in request_rows:
            method = str(row.get("method") or "GET")
            path = str(row.get("path") or "/")
            by_endpoint[f"{method} {path}"] = int(row.get("cnt") or 0)
        total_requests = sum(by_endpoint.values())

        by_status: Dict[str, int] = {}
        for row in status_rows:
            key = f"status_{int(row.get('status_code') or 0)}"
            by_status[key] = int(row.get("cnt") or 0)

        response_times = [float(row.get("duration_ms") or 0.0) for row in response_rows]
        avg_response_time = sum(response_times) / len(response_times) if response_times else 0.0
        p95_response_time = (
            sorted(response_times)[int(len(response_times) * 0.95)]
            if len(response_times) >= 20 else 0.0
        )
        p99_response_time = (
            sorted(response_times)[int(len(response_times) * 0.99)]
            if len(response_times) >= 100 else 0.0
        )

        error_map: Dict[str, int] = {}
        for row in error_rows:
            error_type = str(row.get("error_type") or "unknown_error")
            path = str(row.get("path") or "")
            count = int(row.get("cnt") or 0)
            error_map[error_type] = error_map.get(error_type, 0) + count
            if path:
                scoped = f"{error_type}:{path}"
                error_map[scoped] = error_map.get(scoped, 0) + count
        total_errors = sum(
            count for key, count in error_map.items() if ":" not in key
        )

        runs_map: Dict[str, int] = {}
        for row in run_rows:
            source_type = str(row.get("source_type") or "unknown")
            status = str(row.get("status") or "unknown")
            runs_map[f"{source_type}:{status}"] = int(row.get("cnt") or 0)
        total_runs = sum(runs_map.values())

        min_candidates = []
        for candidate in (req_min, err_min, run_min):
            if isinstance(candidate, str) and candidate.strip():
                try:
                    min_candidates.append(
                        datetime.fromisoformat(candidate.replace("Z", "+00:00"))
                    )
                except Exception:
                    continue
        start_at = min(min_candidates) if min_candidates else datetime.now(timezone.utc)
        if start_at.tzinfo is None:
            start_at = start_at.replace(tzinfo=timezone.utc)
        else:
            start_at = start_at.astimezone(timezone.utc)
        uptime_seconds = max(0.0, (datetime.now(timezone.utc) - start_at).total_seconds())

        return {
            "uptime_seconds": int(uptime_seconds),
            "uptime_human": _format_uptime(uptime_seconds),
            "backend": {
                "mode": "durable_db",
                "retention_days": _METRICS_RETENTION_DAYS,
                "prune_interval_seconds": _METRICS_PRUNE_INTERVAL_SECONDS,
                "last_prune_at": last_prune_at,
            },
            "requests": {
                "total": total_requests,
                "by_endpoint": by_endpoint,
                "by_status": by_status,
            },
            "errors": {
                "total": total_errors,
                "by_type": error_map,
            },
            "reconciliation_runs": {
                "total": total_runs,
                "by_type_and_status": runs_map,
            },
            "performance": {
                "avg_response_time_ms": round(avg_response_time, 2),
                "p95_response_time_ms": round(p95_response_time, 2),
                "p99_response_time_ms": round(p99_response_time, 2),
                "requests_per_second": round(total_requests / uptime_seconds, 2) if uptime_seconds > 0 else 0,
            },
        }
    except Exception:
        return _in_memory_metrics_payload()


def reset_metrics() -> None:
    """Reset all metrics (for tests/dev utility)."""
    global _metrics, _LAST_PRUNE_MONOTONIC
    _metrics = {
        "requests": defaultdict(int),
        "errors": defaultdict(int),
        "reconciliation_runs": defaultdict(int),
        "response_times": [],
        "start_time": datetime.now(timezone.utc).isoformat(),
    }
    _LAST_PRUNE_MONOTONIC = 0.0

    db = _db()
    if not db:
        return
    try:
        _ensure_schema(db)
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM api_request_metrics")
            cur.execute("DELETE FROM api_error_metrics")
            cur.execute("DELETE FROM api_reconciliation_metrics")
            cur.execute("DELETE FROM api_metrics_meta")
            conn.commit()
    except Exception:
        # Keep reset best-effort for tests and local diagnostics.
        return
