import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List
from clearledgr.state.db import db

def _now():
    return datetime.now(timezone.utc).isoformat()

def create_run(run_id: str, sheet_id: str, period_label: str, config: Dict[str, Any]):
    with db() as conn:
        conn.execute(
            "INSERT INTO runs(run_id,status,sheet_id,period_label,started_at,config_json) VALUES(?,?,?,?,?,?)",
            (run_id, "RUNNING", sheet_id, period_label, _now(), json.dumps(config)),
        )

def finish_run(run_id: str, summary: Dict[str, Any]):
    with db() as conn:
        conn.execute(
            "UPDATE runs SET status=?, finished_at=?, summary_json=? WHERE run_id=?",
            ("SUCCEEDED", _now(), json.dumps(summary), run_id),
        )

def fail_run(run_id: str, error: str):
    with db() as conn:
        conn.execute(
            "UPDATE runs SET status=?, finished_at=?, error=? WHERE run_id=?",
            ("FAILED", _now(), error, run_id),
        )

def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    with db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        for k in ("config_json", "summary_json"):
            if d.get(k):
                d[k] = json.loads(d[k])
        return d

def list_runs(limit: int = 50) -> List[Dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for k in ("config_json", "summary_json"):
                if d.get(k):
                    d[k] = json.loads(d[k])
            out.append(d)
        return out
