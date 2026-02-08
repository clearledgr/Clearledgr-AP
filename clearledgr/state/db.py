import os
import sqlite3
from contextlib import contextmanager
from clearledgr.services.db import DB

DB_PATH = os.getenv("STATE_DB_PATH", "state.sqlite3")
db_helper = DB(sqlite_path=DB_PATH)


def init_db():
    db_helper.execute(
        """
        CREATE TABLE IF NOT EXISTS runs(
            run_id TEXT PRIMARY KEY,
            status TEXT,
            sheet_id TEXT,
            period_label TEXT,
            started_at TEXT,
            finished_at TEXT,
            config_json TEXT,
            summary_json TEXT,
            error TEXT
        )"""
    )
    db_helper.execute(
        """
        CREATE TABLE IF NOT EXISTS run_steps(
            run_id TEXT,
            step_name TEXT,
            status TEXT,
            started_at TEXT,
            finished_at TEXT,
            checkpoint_json TEXT,
            PRIMARY KEY (run_id, step_name)
        )"""
    )
    db_helper.execute(
        """
        CREATE TABLE IF NOT EXISTS idempotency_keys(
            run_id TEXT,
            key TEXT PRIMARY KEY,
            created_at TEXT
        )"""
    )


@contextmanager
def db():
    with db_helper.connect() as conn:
        if isinstance(conn, sqlite3.Connection):
            conn.row_factory = sqlite3.Row
        yield conn
