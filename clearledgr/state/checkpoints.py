import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from clearledgr.state.db import db

def _now():
    return datetime.now(timezone.utc).isoformat()

def step_get(run_id: str, step_name: str) -> Optional[Dict[str, Any]]:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM run_steps WHERE run_id=? AND step_name=?",
            (run_id, step_name),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("checkpoint_json"):
            d["checkpoint_json"] = json.loads(d["checkpoint_json"])
        return d

def step_start(run_id: str, step_name: str):
    with db() as conn:
        conn.execute("""
        INSERT INTO run_steps(run_id,step_name,status,started_at,checkpoint_json)
        VALUES(?,?,?,?,?)
        ON CONFLICT(run_id,step_name) DO UPDATE SET status=excluded.status, started_at=excluded.started_at
        """, (run_id, step_name, "RUNNING", _now(), json.dumps({})))

def step_checkpoint(run_id: str, step_name: str, checkpoint: Dict[str, Any]):
    with db() as conn:
        conn.execute(
            "UPDATE run_steps SET checkpoint_json=? WHERE run_id=? AND step_name=?",
            (json.dumps(checkpoint), run_id, step_name),
        )

def step_finish(run_id: str, step_name: str, checkpoint: Dict[str, Any] | None = None):
    if checkpoint is None:
        checkpoint = {}
    with db() as conn:
        conn.execute(
            "UPDATE run_steps SET status=?, finished_at=?, checkpoint_json=? WHERE run_id=? AND step_name=?",
            ("SUCCEEDED", _now(), json.dumps(checkpoint), run_id, step_name),
        )
