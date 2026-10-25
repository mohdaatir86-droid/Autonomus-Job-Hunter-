"""SQLite/PostgreSQL compatibility layer for Job Hunter.

Set DATABASE_URL to enable PostgreSQL. If absent, the existing SQLite path is retained.
"""
from __future__ import annotations

import os
import re
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def postgres_enabled() -> bool:
    return bool(os.getenv("DATABASE_URL", "").strip())


class HybridRow(Mapping):
    def __init__(self, names, values):
        self._data = dict(zip(names, values))

    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self._data.values())[key]
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    def keys(self):
        return self._data.keys()


class PgCursor:
    def __init__(self, cursor):
        self._cursor = cursor
        self._names = [d.name if hasattr(d, "name") else d[0] for d in (cursor.description or [])]

    def _row(self, row):
        return HybridRow(self._names, row) if row is not None else None

    def fetchone(self):
        return self._row(self._cursor.fetchone())

    def fetchall(self):
        return [self._row(row) for row in self._cursor.fetchall()]

    @property
    def rowcount(self):
        return self._cursor.rowcount


class PgConnection:
    def __init__(self, raw):
        self._raw = raw

    def __enter__(self):
        self._raw.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._raw.__exit__(exc_type, exc, tb)

    def close(self):
        self._raw.close()

    @staticmethod
    def _sql(sql):
        # Translate the small set of SQLite-specific syntax used by this app.
        sql = re.sub(r"(?i)INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", sql)
        if re.search(r"(?i)INSERT\s+OR\s+IGNORE", sql):
            sql = re.sub(r"(?i)INSERT\s+OR\s+IGNORE", "INSERT", sql)
            if "ON CONFLICT" not in sql.upper():
                sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
        sql = re.sub(
            r"(?i)date\(COALESCE\(posted_at, first_seen_at\)\)\s*>=\s*date\('now',\s*\?\)",
            "COALESCE(posted_at, first_seen_at)::date >= (CURRENT_DATE + (?)::interval)",
            sql,
        )
        return sql.replace("?", "%s")

    def execute(self, sql, params=()):
        import psycopg
        cursor = self._raw.cursor()
        cursor.execute(self._sql(sql), tuple(params or ()))
        return PgCursor(cursor)


def connect(db_path=None):
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        # psycopg 3 handles Supabase PostgreSQL. SSL is required for remote DBs.
        import psycopg
        from psycopg.rows import tuple_row
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        parts = urlsplit(url)
        if not parts.scheme.startswith("postgresql"):
            raise ValueError("DATABASE_URL must be a PostgreSQL connection string")
        if not parts.query:
            url = urlunsplit((parts.scheme, parts.netloc, parts.path, "sslmode=require", parts.fragment))
        elif "sslmode=" not in parts.query:
            url = url + ("&" if "?" in url else "?") + "sslmode=require"
        raw = psycopg.connect(url, connect_timeout=15, row_factory=tuple_row)
        return PgConnection(raw)

    path = str(db_path or os.getenv("DATABASE_PATH", "job_hunter.db"))
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
