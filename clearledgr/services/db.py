"""Lightweight DB helper that prefers Postgres when DATABASE_URL is set, falls back to SQLite."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterable, List, Tuple

try:
    import psycopg  # type: ignore
except ImportError:  # pragma: no cover
    psycopg = None


class DB:
    def __init__(self, sqlite_path: str = "state.sqlite3") -> None:
        self.dsn = os.getenv("DATABASE_URL")
        self.sqlite_path = sqlite_path
        self.use_postgres = bool(self.dsn and psycopg)

    @contextmanager
    def connect(self):
        if self.use_postgres:
            conn = psycopg.connect(self.dsn)  # type: ignore
            try:
                yield conn
            finally:
                conn.close()
        else:
            conn = sqlite3.connect(self.sqlite_path)
            try:
                yield conn
            finally:
                conn.close()

    def execute(self, sql: str, params: Tuple[Any, ...] = ()) -> None:
        sql = self._prepare(sql)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()

    def fetchall(self, sql: str, params: Tuple[Any, ...] = ()) -> List[Tuple]:
        sql = self._prepare(sql)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        return rows

    def fetchone(self, sql: str, params: Tuple[Any, ...] = ()) -> Tuple | None:
        sql = self._prepare(sql)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            row = cur.fetchone()
        return row

    def fetchall_dict(self, sql: str, params: Tuple[Any, ...] = ()) -> List[dict]:
        sql = self._prepare(sql)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            columns = [col[0] for col in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]

    def fetchone_dict(self, sql: str, params: Tuple[Any, ...] = ()) -> dict | None:
        sql = self._prepare(sql)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            row = cur.fetchone()
            if not row:
                return None
            columns = [col[0] for col in cur.description]
            return dict(zip(columns, row))

    def _prepare(self, sql: str) -> str:
        """Normalize placeholders between SQLite (?) and Postgres (%s)."""
        if self.use_postgres:
            return sql.replace("?", "%s")
        return sql
