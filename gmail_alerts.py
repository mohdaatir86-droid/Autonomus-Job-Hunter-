"""Gmail OAuth + job-alert email ingestion (Naukri, LinkedIn, Shine, Monster, Bayt, etc.).

Legal path: user creates job alerts on those sites → emails arrive in Gmail → we parse links.
No password scraping of job boards.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from job_hunter import DB_PATH, normalize_text, detect_country

log = logging.getLogger("job-hunter")

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"

ALERT_SENDERS = [
    "naukri.com",
    "jobsearch.naukri.com",
    "linkedin.com",
    "jobs-listings.linkedin.com",
    "shine.com",
    "monsterindia.com",
    "monster.com",
    "bayt.com",
    "gulftalent.com",
    "indeed.com",
    "hirist.com",
    "iimjobs.com",
    "foundit.in",
    "timesjobs.com",
]

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _connect():
    path = Path(DB_PATH)
    if str(DB_PATH) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_gmail_db():
    with _connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS gmail_tokens (
            id INTEGER PRIMARY KEY CHECK(id=1),
            access_token TEXT, refresh_token TEXT, token_expiry TEXT,
            email TEXT DEFAULT '', updated_at TEXT NOT NULL
        )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS gmail_seen_messages (
            message_id TEXT PRIMARY KEY, seen_at TEXT NOT NULL
        )"""
        )


def gmail_configured() -> bool:
    return bool(os.getenv("GOOGLE_CLIENT_ID", "").strip() and os.getenv("GOOGLE_CLIENT_SECRET", "").strip())


def get_gmail_status() -> dict[str, Any]:
    init_gmail_db()
    with _connect() as conn:
        row = conn.execute("SELECT email, token_expiry, refresh_token FROM gmail_tokens WHERE id=1").fetchone()
    connected = bool(row and (row["refresh_token"] or row["email"]))
    return {
        "oauth_configured": gmail_configured(),
        "connected": connected,
        "email": (row["email"] if row else "") or "",
        "callback_url": _redirect_uri(),
        "hint": "Create job alerts on Naukri / LinkedIn / Shine / Monster / Bayt to this Gmail. Connect OAuth, then hunt will ingest new alert emails.",
    }


def _redirect_uri() -> str:
    return os.getenv(
        "GOOGLE_REDIRECT_URI",
        "https://autonomous-job-hunter.onrender.com/api/gmail/callback",
    ).strip()


def build_auth_url(state: str = "jobhunter") -> str:
    if not gmail_configured():
        raise ValueError("Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in environment")
    params = {
        "client_id": os.getenv("GOOGLE_CLIENT_ID").strip(),
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/gmail.readonly",
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return GOOGLE_AUTH + "?" + urlencode(params)


async def exchange_code(code: str) -> dict[str, Any]:
    if not gmail_configured():
        raise ValueError("Google OAuth not configured")
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            GOOGLE_TOKEN,
            data={
                "code": code,
                "client_id": os.getenv("GOOGLE_CLIENT_ID").strip(),
                "client_secret": os.getenv("GOOGLE_CLIENT_SECRET").strip(),
                "redirect_uri": _redirect_uri(),
                "grant_type": "authorization_code",
            },
        )
        r.raise_for_status()
        data = r.json()
    init_gmail_db()
    email = await _fetch_profile_email(data.get("access_token", ""))
    expiry = ""
    if data.get("expires_in"):
        expiry = datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() + int(data["expires_in"]),
            tz=timezone.utc,
        ).isoformat()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO gmail_tokens(id, access_token, refresh_token, token_expiry, email, updated_at)
               VALUES (1,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 access_token=excluded.access_token,
                 refresh_token=COALESCE(excluded.refresh_token, gmail_tokens.refresh_token),
                 token_expiry=excluded.token_expiry,
                 email=excluded.email,
                 updated_at=excluded.updated_at""",
            (data.get("access_token"), data.get("refresh_token"), expiry, email, _now()),
        )
    return {"connected": True, "email": email}


async def _fetch_profile_email(access_token: str) -> str:
    if not access_token:
        return ""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(
                GMAIL_API + "/profile",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if r.status_code == 200:
                return (r.json().get("emailAddress") or "")[:200]
    except Exception:
        log.exception("profile email fetch failed")
    return ""


async def _access_token() -> str | None:
    init_gmail_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM gmail_tokens WHERE id=1").fetchone()
    if not row:
        return None
    token = row["access_token"] or ""
    refresh = row["refresh_token"]
    if not refresh:
        return token or None
    need_refresh = True
    if row["token_expiry"]:
        try:
            exp = datetime.fromisoformat(row["token_expiry"])
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            need_refresh = exp.timestamp() < datetime.now(timezone.utc).timestamp() + 60
        except Exception:
            need_refresh = True
    if not need_refresh and token:
        return token
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            GOOGLE_TOKEN,
            data={
                "client_id": os.getenv("GOOGLE_CLIENT_ID", "").strip(),
                "client_secret": os.getenv("GOOGLE_CLIENT_SECRET", "").strip(),
                "refresh_token": refresh,
                "grant_type": "refresh_token",
            },
        )
        if r.status_code >= 400:
            log.error("token refresh failed: %s", r.text[:200])
            return token or None
        data = r.json()
    new_token = data.get("access_token") or token
    expiry = ""
    if data.get("expires_in"):
        expiry = datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() + int(data["expires_in"]),
            tz=timezone.utc,
        ).isoformat()
    with _connect() as conn:
        conn.execute(
            "UPDATE gmail_tokens SET access_token=?, token_expiry=?, updated_at=? WHERE id=1",
            (new_token, expiry, _now()),
        )
    return new_token


def _decode_body(payload: dict) -> str:
    parts = []

    def walk(p):
        if not p:
            return
        body = p.get("body") or {}
        data = body.get("data")
        if data:
            try:
                parts.append(base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore"))
            except Exception:
                pass
        for child in p.get("parts") or []:
            walk(child)

    walk(payload)
    text = "\n".join(parts)
    text = re.sub(r"<[^>]+>", " ", text)
    return normalize_text(text)


def _guess_source(from_header: str, subject: str) -> str:
    blob = (from_header + " " + subject).lower()
    mapping = [
        ("naukri", "Naukri (email alert)"),
        ("linkedin", "LinkedIn (email alert)"),
        ("shine", "Shine (email alert)"),
        ("monster", "Monster (email alert)"),
        ("bayt", "Bayt (email alert)"),
        ("gulftalent", "GulfTalent (email alert)"),
        ("indeed", "Indeed (email alert)"),
        ("foundit", "Foundit (email alert)"),
        ("timesjobs", "TimesJobs (email alert)"),
        ("hirist", "Hirist (email alert)"),
    ]
    for key, label in mapping:
        if key in blob:
            return label
    return "Job alert email"


def _extract_jobs_from_email(subject: str, from_header: str, body: str, message_id: str) -> list[dict]:
    source = _guess_source(from_header, subject)
    urls = []
    for m in URL_RE.findall(body + " " + subject):
        u = m.rstrip(").,;\"'")
        low = u.lower()
        if any(
            x in low
            for x in (
                "naukri.com",
                "linkedin.com/jobs",
                "shine.com",
                "monster",
                "bayt.com",
                "gulftalent",
                "indeed.com",
                "foundit.in",
                "timesjobs",
                "hirist.com",
                "greenhouse.io",
                "lever.co",
            )
        ):
            if "unsubscribe" in low or "mailto:" in low:
                continue
            urls.append(u)
    seen, uniq = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    jobs = []
    title_guess = subject
    for noise in ("job alert", "new jobs", "recommended jobs", "jobs for you", "|", "-"):
        title_guess = re.sub(re.escape(noise), " ", title_guess, flags=re.I)
    title_guess = normalize_text(title_guess)[:200] or "Role from job alert"
    for i, url in enumerate(uniq[:8]):
        loc = ""
        for city in (
            "dubai", "abu dhabi", "riyadh", "doha", "noida", "gurgaon", "gurugram",
            "mumbai", "bengaluru", "bangalore", "hyderabad", "pune", "delhi",
        ):
            if city in body.lower() or city in subject.lower():
                loc = city.title()
                break
        jobs.append(
            {
                "source": source,
                "source_id": f"{message_id}:{i}",
                "title": title_guess if i == 0 else f"{title_guess} ({i+1})",
                "company": "",
                "location": loc,
                "country": detect_country(loc or body[:200]),
                "url": url[:500],
                "description": body[:4000],
                "employment_type": "",
                "salary": "",
                "posted_at": _now(),
                "raw_json": json.dumps({"message_id": message_id, "from": from_header, "subject": subject})[:5000],
            }
        )
    return jobs


async def fetch_jobs_from_gmail() -> tuple[list[dict], str | None]:
    """Pull recent job-alert messages and convert to job dicts."""
    if not gmail_configured():
        return [], None
    token = await _access_token()
    if not token:
        return [], "Gmail not connected — visit /api/gmail/auth after setting Google OAuth env vars"
    init_gmail_db()
    from_q = " OR ".join(f"from:{d}" for d in ALERT_SENDERS[:8])
    q = f"({from_q}) OR subject:(job alert OR jobs for you OR recommended jobs)"
    async with httpx.AsyncClient(timeout=40) as client:
        r = await client.get(
            GMAIL_API + "/messages",
            headers={"Authorization": f"Bearer {token}"},
            params={"q": q, "maxResults": 25},
        )
        if r.status_code >= 400:
            return [], f"Gmail list failed: {r.status_code} {r.text[:150]}"
        ids = [m["id"] for m in (r.json().get("messages") or [])]
        jobs: list[dict] = []
        for mid in ids:
            with _connect() as conn:
                seen = conn.execute("SELECT 1 FROM gmail_seen_messages WHERE message_id=?", (mid,)).fetchone()
            if seen:
                continue
            mr = await client.get(
                GMAIL_API + f"/messages/{mid}",
                headers={"Authorization": f"Bearer {token}"},
                params={"format": "full"},
            )
            if mr.status_code >= 400:
                continue
            msg = mr.json()
            headers = {h["name"].lower(): h["value"] for h in (msg.get("payload") or {}).get("headers") or []}
            subject = headers.get("subject", "")
            from_h = headers.get("from", "")
            body = _decode_body(msg.get("payload") or {})
            extracted = _extract_jobs_from_email(subject, from_h, body, mid)
            jobs.extend(extracted)
            with _connect() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO gmail_seen_messages(message_id, seen_at) VALUES (?,?)",
                    (mid, _now()),
                )
    return jobs, None
