import hashlib
from datetime import datetime, timezone
from clearledgr.state.db import db

def _now():
    return datetime.now(timezone.utc).isoformat()

def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def seen(key: str) -> bool:
    with db() as conn:
        row = conn.execute("SELECT key FROM idempotency_keys WHERE key=?", (key,)).fetchone()
        return row is not None

def record(run_id: str, key: str):
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO idempotency_keys(run_id,key,created_at) VALUES(?,?,?)",
            (run_id, key, _now()),
        )
