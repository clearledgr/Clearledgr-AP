from __future__ import annotations

import importlib
from pathlib import Path
from datetime import datetime, timedelta, timezone

from clearledgr.core import database as db_module


def test_metrics_use_durable_store_in_staging(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ENV", "staging")
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-token-encryption-key")
    db = db_module.get_db()
    db.initialize()

    metrics_module = importlib.import_module("clearledgr.services.metrics")
    metrics = importlib.reload(metrics_module)
    metrics.reset_metrics()
    metrics.record_request("GET", "/health", 200, 12.5)
    metrics.record_error("http_500", "/api/test")
    metrics.record_reconciliation_run("gmail", "success", 50.0)
    # record_request / record_error are fire-and-forget post-fix —
    # wait for the executor to drain so the assertions below see the
    # rows.
    assert metrics.flush_pending(timeout=5.0)

    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS cnt FROM api_request_metrics")
        request_count = int(dict(cur.fetchone())["cnt"])
        cur.execute("SELECT COUNT(*) AS cnt FROM api_error_metrics")
        error_count = int(dict(cur.fetchone())["cnt"])

    payload = metrics.get_metrics()
    assert request_count == 1
    assert error_count == 1
    assert payload["requests"]["total"] >= 1
    assert payload["errors"]["total"] >= 1
    assert payload["backend"]["mode"] == "durable_db"
    assert int(payload["backend"]["retention_days"]) >= 1


def test_metrics_prunes_rows_older_than_retention(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ENV", "staging")
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-token-encryption-key")
    monkeypatch.setenv("API_METRICS_RETENTION_DAYS", "1")
    monkeypatch.setenv("API_METRICS_PRUNE_INTERVAL_SECONDS", "5")
    db = db_module.get_db()
    db.initialize()

    metrics_module = importlib.import_module("clearledgr.services.metrics")
    metrics = importlib.reload(metrics_module)
    metrics.reset_metrics()
    metrics.record_request("GET", "/health", 200, 10.0)
    # Wait for the first persist (which also runs the schema-creation
    # + first prune in the background executor) to land before we
    # insert the stale row + reset the prune marker. Otherwise that
    # in-flight task would overwrite our forced reset and the second
    # record_request would skip the prune step.
    assert metrics.flush_pending(timeout=5.0)

    stale_ts = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            (
                """
                INSERT INTO api_request_metrics
                (id, ts, method, path, status_code, duration_ms)
                VALUES (%s, %s, %s, %s, %s, %s)
                """
            ),
            ("req_stale", stale_ts, "GET", "/stale", 200, 1.0),
        )
        conn.commit()

    # Force prune on next write by resetting the in-process monotonic marker.
    metrics._LAST_PRUNE_MONOTONIC = 0.0
    metrics.record_request("GET", "/healthz", 200, 11.0)
    # record_request is fire-and-forget via the metrics executor — wait
    # for the in-flight prune+insert to land before reading the table.
    assert metrics.flush_pending(timeout=5.0)

    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS cnt FROM api_request_metrics WHERE path = %s", ("/stale",))
        stale_count = int(dict(cur.fetchone())["cnt"])

    assert stale_count == 0
