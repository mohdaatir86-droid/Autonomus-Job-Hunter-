"""Idempotent application state machine for Job Hunter.

States (ordered lifecycle):
  discovered -> scored -> skipped | queued -> opening_job -> form_detected
  -> filling_form -> validation_failed | waiting_for_user | ready_to_submit
  -> submitting -> submitted_unverified | submitted | failed | expired_session

Rules:
- Never apply twice to the same job (stable key = portal + job_id or canonical URL).
- Ambiguous network timeout after click => submitted_unverified (do not retry blindly).
- Concurrent workers use DB unique constraint / lock on application_attempts.
- Submission remains gated by env + policy; this module only tracks state.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from database import connect as db_connect, postgres_enabled
from job_hunter import DB_PATH

VALID_STATES = frozenset({
    "discovered",
    "scored",
    "skipped",
    "queued",
    "opening_job",
    "form_detected",
    "filling_form",
    "validation_failed",
    "waiting_for_user",
    "ready_to_submit",
    "submitting",
    "submitted_unverified",
    "submitted",
    "failed",
    "expired_session",
})

ALLOWED_TRANSITIONS = {
    "discovered": {"scored", "skipped"},
    "scored": {"queued", "skipped"},
    "skipped": set(),
    "queued": {"opening_job", "skipped", "failed"},
    "opening_job": {"form_detected", "failed", "expired_session", "waiting_for_user"},
    "form_detected": {"filling_form", "failed", "waiting_for_user"},
    "filling_form": {
        "validation_failed",
        "waiting_for_user",
        "ready_to_submit",
        "failed",
    },
    "validation_failed": {"waiting_for_user", "filling_form", "failed", "skipped"},
    "waiting_for_user": {"queued", "filling_form", "skipped", "failed"},
    "ready_to_submit": {"submitting", "waiting_for_user", "skipped"},
    "submitting": {"submitted", "submitted_unverified", "failed", "expired_session"},
    "submitted_unverified": {"submitted", "failed", "waiting_for_user"},
    "submitted": set(),
    "failed": {"queued", "skipped"},
    "expired_session": {"queued", "waiting_for_user", "failed"},
}

TERMINAL_SUCCESS = frozenset({"submitted"})
TERMINAL_STOP = frozenset({"skipped", "submitted"})
NO_AUTO_RETRY = frozenset({"submitted", "submitted_unverified", "waiting_for_user", "expired_session"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_job_key(portal: str, source_id: str | None, url: str) -> str:
    portal = (portal or "unknown").strip().lower()[:80]
    sid = (source_id or "").strip()[:200]
    if sid:
        raw = f"{portal}|{sid}"
    else:
        raw = f"{portal}|url|{(url or '').strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def init_application_state_db() -> None:
    with db_connect(DB_PATH) as conn:
        if postgres_enabled():
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS application_attempts (
                    id SERIAL PRIMARY KEY,
                    job_key TEXT NOT NULL UNIQUE,
                    job_id INTEGER,
                    draft_id INTEGER,
                    portal TEXT NOT NULL DEFAULT '',
                    job_url TEXT NOT NULL DEFAULT '',
                    title TEXT DEFAULT '',
                    company TEXT DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'discovered',
                    reason TEXT DEFAULT '',
                    confirmation_evidence TEXT DEFAULT '',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    locked_at TEXT,
                    locked_by TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS application_state_events (
                    id SERIAL PRIMARY KEY,
                    attempt_id INTEGER NOT NULL,
                    from_state TEXT,
                    to_state TEXT NOT NULL,
                    note TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
        else:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS application_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_key TEXT NOT NULL UNIQUE,
                    job_id INTEGER,
                    draft_id INTEGER,
                    portal TEXT NOT NULL DEFAULT '',
                    job_url TEXT NOT NULL DEFAULT '',
                    title TEXT DEFAULT '',
                    company TEXT DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'discovered',
                    reason TEXT DEFAULT '',
                    confirmation_evidence TEXT DEFAULT '',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    locked_at TEXT,
                    locked_by TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS application_state_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    attempt_id INTEGER NOT NULL,
                    from_state TEXT,
                    to_state TEXT NOT NULL,
                    note TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(attempt_id) REFERENCES application_attempts(id) ON DELETE CASCADE
                )
                """
            )


def get_or_create_attempt(
    *,
    portal: str,
    source_id: str | None,
    url: str,
    job_id: int | None = None,
    draft_id: int | None = None,
    title: str = "",
    company: str = "",
) -> dict[str, Any]:
    init_application_state_db()
    key = stable_job_key(portal, source_id, url)
    now = _now()
    with db_connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM application_attempts WHERE job_key=?", (key,)
        ).fetchone()
        if row:
            return dict(row)
        conn.execute(
            """
            INSERT INTO application_attempts
            (job_key, job_id, draft_id, portal, job_url, title, company, state,
             attempt_count, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'discovered', 0, ?, ?)
            """,
            (
                key,
                job_id,
                draft_id,
                (portal or "")[:80],
                (url or "")[:1500],
                (title or "")[:300],
                (company or "")[:300],
                now,
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM application_attempts WHERE job_key=?", (key,)
        ).fetchone()
        attempt = dict(row)
        conn.execute(
            """
            INSERT INTO application_state_events
            (attempt_id, from_state, to_state, note, created_at)
            VALUES (?, NULL, 'discovered', 'attempt created', ?)
            """,
            (attempt["id"], now),
        )
        return attempt


def transition(
    attempt_id: int,
    to_state: str,
    *,
    note: str = "",
    confirmation_evidence: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    if to_state not in VALID_STATES:
        raise ValueError(f"invalid state: {to_state}")
    init_application_state_db()
    now = _now()
    with db_connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM application_attempts WHERE id=?", (attempt_id,)
        ).fetchone()
        if not row:
            raise ValueError("attempt not found")
        current = dict(row)
        from_state = current["state"]
        allowed = ALLOWED_TRANSITIONS.get(from_state, set())
        if to_state != from_state and to_state not in allowed:
            raise ValueError(f"illegal transition {from_state} -> {to_state}")
        if from_state in TERMINAL_SUCCESS and to_state != from_state:
            raise ValueError("cannot transition away from submitted")
        fields = ["state=?", "updated_at=?"]
        params: list[Any] = [to_state, now]
        if confirmation_evidence is not None:
            fields.append("confirmation_evidence=?")
            params.append(confirmation_evidence[:2000])
        if reason is not None:
            fields.append("reason=?")
            params.append(reason[:1000])
        if to_state == "submitting":
            fields.append("attempt_count=attempt_count+1")
        params.append(attempt_id)
        conn.execute(
            f"UPDATE application_attempts SET {', '.join(fields)} WHERE id=?",
            params,
        )
        conn.execute(
            """
            INSERT INTO application_state_events
            (attempt_id, from_state, to_state, note, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (attempt_id, from_state, to_state, (note or "")[:1000], now),
        )
        return dict(
            conn.execute(
                "SELECT * FROM application_attempts WHERE id=?", (attempt_id,)
            ).fetchone()
        )


def already_applied(job_key: str) -> bool:
    init_application_state_db()
    with db_connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT state FROM application_attempts WHERE job_key=?",
            (job_key,),
        ).fetchone()
    if not row:
        return False
    return row["state"] in {"submitted", "submitted_unverified", "submitting"}


def list_attempts(limit: int = 100, state: str | None = None) -> list[dict]:
    init_application_state_db()
    limit = max(1, min(500, int(limit)))
    with db_connect(DB_PATH) as conn:
        if state:
            rows = conn.execute(
                """
                SELECT * FROM application_attempts
                WHERE state=? ORDER BY updated_at DESC LIMIT ?
                """,
                (state, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM application_attempts
                ORDER BY updated_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def attempt_events(attempt_id: int) -> list[dict] | None:
    init_application_state_db()
    with db_connect(DB_PATH) as conn:
        exists = conn.execute(
            "SELECT id FROM application_attempts WHERE id=?", (attempt_id,)
        ).fetchone()
        if not exists:
            return None
        rows = conn.execute(
            """
            SELECT from_state, to_state, note, created_at
            FROM application_state_events
            WHERE attempt_id=? ORDER BY id DESC LIMIT 100
            """,
            (attempt_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def state_machine_summary() -> dict[str, Any]:
    init_application_state_db()
    with db_connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT state, COUNT(*) AS c FROM application_attempts GROUP BY state"
        ).fetchall()
    counts = {r["state"]: r["c"] for r in rows}
    return {
        "counts": counts,
        "valid_states": sorted(VALID_STATES),
        "no_auto_retry_states": sorted(NO_AUTO_RETRY),
        "safety": {
            "duplicate_submit_guard": True,
            "ambiguous_timeout_policy": "submitted_unverified",
            "live_submit_requires_env": True,
        },
    }
