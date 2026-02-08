"""
Run History Tracking for Clearledgr v1

Uses the shared DB helper (Postgres-first, SQLite fallback) for audit/debug.
"""
import json
from datetime import datetime, timezone
from typing import Optional, List, Dict
import os
from clearledgr.services.db import DB

DB_PATH = os.getenv("RUN_HISTORY_DB_PATH", os.path.join(os.path.dirname(__file__), "run_history.db"))
db = DB(sqlite_path=DB_PATH)


def init_run_history_db():
    """Initialize the run history database."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            period_start TEXT,
            period_end TEXT,
            status TEXT DEFAULT 'RUNNING',
            started_at TEXT NOT NULL,
            completed_at TEXT,
            total_groups INTEGER DEFAULT 0,
            total_exceptions INTEGER DEFAULT 0,
            match_rate REAL DEFAULT 0,
            config_json TEXT,
            summary_json TEXT,
            error_message TEXT
        )
    """)
    
    db.execute("CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at DESC)")


def create_run(
    run_id: str,
    source_type: str,
    period_start: str,
    period_end: str,
    config: Optional[Dict] = None
) -> Dict:
    """Create a new run record."""
    now = datetime.now(timezone.utc).isoformat()
    
    db.execute("""
        INSERT INTO runs (run_id, source_type, period_start, period_end, started_at, config_json)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (run_id, source_type, period_start, period_end, now, json.dumps(config) if config else None))
    
    return {"run_id": run_id, "status": "RUNNING", "started_at": now}


def complete_run(
    run_id: str,
    total_groups: int = 0,
    total_exceptions: int = 0,
    match_rate: float = 0,
    summary: Optional[Dict] = None
) -> Dict:
    """Mark a run as completed successfully."""
    now = datetime.now(timezone.utc).isoformat()
    
    db.execute("""
        UPDATE runs 
        SET status = 'SUCCEEDED',
            completed_at = ?,
            total_groups = ?,
            total_exceptions = ?,
            match_rate = ?,
            summary_json = ?
        WHERE run_id = ?
    """, (now, total_groups, total_exceptions, match_rate, json.dumps(summary) if summary else None, run_id))
    
    return {"run_id": run_id, "status": "SUCCEEDED", "completed_at": now}


def fail_run(run_id: str, error_message: str) -> Dict:
    """Mark a run as failed."""
    now = datetime.now(timezone.utc).isoformat()
    
    db.execute("""
        UPDATE runs 
        SET status = 'FAILED',
            completed_at = ?,
            error_message = ?
        WHERE run_id = ?
    """, (now, error_message, run_id))
    
    return {"run_id": run_id, "status": "FAILED", "error_message": error_message}


def get_run(run_id: str) -> Optional[Dict]:
    """Get a run by ID."""
    row = db.fetchone_dict("SELECT * FROM runs WHERE run_id = ?", (run_id,))
    return dict(row) if row else None


def list_runs(
    limit: int = 50,
    source_type: Optional[str] = None,
    status: Optional[str] = None
) -> List[Dict]:
    """List recent runs with optional filters."""
    query = "SELECT * FROM runs WHERE 1=1"
    params = []
    
    if source_type:
        query += " AND source_type = ?"
        params.append(source_type)
    
    if status:
        query += " AND status = ?"
        params.append(status)
    
    query += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)
    
    rows = db.fetchall_dict(query, tuple(params))
    return [dict(row) for row in rows]


def get_run_stats() -> Dict:
    """Get aggregate run statistics."""
    row = db.fetchone("""
        SELECT 
            COUNT(*) as total_runs,
            SUM(CASE WHEN status = 'SUCCEEDED' THEN 1 ELSE 0 END) as succeeded,
            SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) as failed,
            AVG(match_rate) as avg_match_rate,
            SUM(total_groups) as total_groups_all,
            SUM(total_exceptions) as total_exceptions_all
        FROM runs
    """)
    
    return {
        "total_runs": row[0] or 0,
        "succeeded": row[1] or 0,
        "failed": row[2] or 0,
        "avg_match_rate": round(row[3] or 0, 2),
        "total_groups_processed": row[4] or 0,
        "total_exceptions_found": row[5] or 0
    }


# Initialize on import
init_run_history_db()
