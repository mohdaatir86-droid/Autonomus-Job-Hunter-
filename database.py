"""Small SQLite/PostgreSQL compatibility layer used by Job Hunter.

Set DATABASE_URL in the hosting environment to enable PostgreSQL. Without it,
the app uses SQLite at DATABASE_PATH (default: ./job_hunter.db).
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
    """Row supporting both sqlite-style row['name']/row[0] and dict(row)."""
    def __init__(self, names, values):
        self._data = dict(zip(names, values))
        self._values = tuple(values)

    def __getitem__(self, key):
        return self._values[key] if isinstance(key, int) else self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    def keys(self):
        return self._data.keys()


class PgCursor:
    def __init__(self, cursor):
        self._cursor = cursor
        self._names = [
            d.name if hasattr(d, "name") else d[0]
            for d in (cursor.description or [])
        ]

    def _row(self, row):
        return HybridRow(self._names, row) if row is not None else None

    def fetchone(self):
        return self._row(self._cursor.fetchone())

    def fetchall(self):
        return [self._row(row) for row in self._cursor.fetchall()]

    def __iter__(self):
        """Permit direct iteration over execute() results like sqlite cursors."""
        while True:
            row = self.fetchone()
            if row is None:
                break
            yield row

    def __iter__(self):
        """Allow SQLite-style iteration over execute() results."""
        while True:
            row = self.fetchone()
            if row is None:
                break
            yield row

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def lastrowid(self):
        # PostgreSQL does not expose SQLite's implicit lastrowid.
        return None


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
        # Translate SQLite's INSERT OR IGNORE idiom to PostgreSQL.
        ignore_insert = bool(re.search(r"(?i)\bINSERT\s+OR\s+IGNORE\s+INTO\b", sql))
        if ignore_insert:
            sql = re.sub(r"(?i)\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", sql)
            if not re.search(r"(?i)\bON\s+CONFLICT\b", sql):
                sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

        # Convert SQLite relative-date filtering to PostgreSQL interval syntax.
        sql = re.sub(
            r"(?i)date\(COALESCE\(posted_at,\s*first_seen_at\)\)\s*>=\s*date\('now',\s*\?\)",
            "COALESCE(posted_at, first_seen_at)::date >= (CURRENT_DATE + (%s)::interval)",
            sql,
        )
        # Translate ? placeholders while preserving string literals and identifiers.
        out, i, quote = [], 0, None
        while i < len(sql):
            ch = sql[i]
            if quote:
                out.append(ch)
                if ch == quote:
                    if i + 1 < len(sql) and sql[i + 1] == quote:
                        out.append(sql[i + 1])
                        i += 1
                    else:
                        quote = None
            elif ch in ("'", '"'):
                quote = ch
                out.append(ch)
            elif ch == "?":
                out.append("%s")
            else:
                out.append(ch)
            i += 1
        return "".join(out)

    def execute(self, sql, params=()):
        import psycopg
        cursor = self._raw.cursor()
        cursor.execute(self._sql(sql), tuple(params or ()))
        return PgCursor(cursor)


def _postgres_url():
    url = os.getenv("DATABASE_URL", "").strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    parts = urlsplit(url)
    if not parts.scheme.startswith("postgresql"):
        raise ValueError("DATABASE_URL must be a PostgreSQL connection string")
    query = parts.query
    if not query:
        query = "sslmode=require"
    elif not re.search(r"(?:^|&)sslmode=", query, flags=re.I):
        query += "&sslmode=require"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def connect(db_path=None):
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        import psycopg
        from psycopg.rows import tuple_row
        return PgConnection(
            psycopg.connect(_postgres_url(), connect_timeout=15, row_factory=tuple_row)
        )

    path = str(db_path or os.getenv("DATABASE_PATH", "job_hunter.db"))
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def database_diagnostics():
    """Return non-secret connectivity/schema diagnostics for /health/db."""
    expected_tables = (
        "jobs", "hunt_runs", "settings", "candidate_profile",
        "application_drafts", "application_events", "gmail_tokens",
        "gmail_seen_messages",
    )
    mode = "postgresql" if postgres_enabled() else "sqlite"
    try:
        with connect() as conn:
            if mode == "postgresql":
                rows = conn.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
                ).fetchall()
                existing = {row["table_name"] for row in rows}
            else:
                rows = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
                existing = {row["name"] for row in rows}
            missing = [name for name in expected_tables if name not in existing]
            counts = {}
            for table in expected_tables:
                if table in existing:
                    counts[table] = conn.execute(
                        "SELECT COUNT(*) FROM " + table
                    ).fetchone()[0]
        return {
            "ok": not missing,
            "mode": mode,
            "connected": True,
            "expected_tables": len(expected_tables),
            "missing_tables": missing,
            "row_counts": counts,
        }
    except Exception as exc:
        return {
            "ok": False,
            "mode": mode,
            "connected": False,
            "expected_tables": len(expected_tables),
            "error_type": type(exc).__name__,
        }
