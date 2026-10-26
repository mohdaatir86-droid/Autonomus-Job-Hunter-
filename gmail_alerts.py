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
import secrets
import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from job_hunter import DB_PATH, normalize_text, detect_country
from database import connect as db_connect, postgres_enabled

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
    return db_connect(DB_PATH)


def init_gmail_db():
    if postgres_enabled():
        return  # PostgreSQL tables are created by the Supabase schema migration.
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
    missing = []
    if not os.getenv("GOOGLE_CLIENT_ID", "").strip():
        missing.append("GOOGLE_CLIENT_ID")
    if not os.getenv("GOOGLE_CLIENT_SECRET", "").strip():
        missing.append("GOOGLE_CLIENT_SECRET")
    if not _oauth_state_secret():
        missing.append("OAUTH_STATE_SECRET or PORTAL_ACCESS_TOKEN")
    hint = (
        "Create a Google Cloud OAuth client (Web application) with redirect URI "
        + _redirect_uri()
        + ". Enable the Gmail API. Set the missing variable names in Render, then open /api/gmail/auth. "
        "Create job alerts on Naukri, LinkedIn, Shine, Indeed, Bayt and GulfTalent to that Gmail."
    )
    if gmail_configured() and not missing and not connected:
        hint = "OAuth credentials are set. Open /api/gmail/auth and approve read-only Gmail access. No password is requested."
    elif connected:
        hint = "Connected. New job-alert emails are imported on the next hunt and are not imported twice."
    return {
        "oauth_configured": gmail_configured(),
        "connected": connected,
        "email": (row["email"] if row else "") or "",
        "callback_url": _redirect_uri(),
        "missing": missing,
        "setup_required": bool(missing) or not connected,
        "hint": hint,
    }


def _redirect_uri() -> str:
    return os.getenv(
        "GOOGLE_REDIRECT_URI",
        "https://autonomous-job-hunter-docker.onrender.com/api/gmail/callback",
    ).strip()


def _oauth_state_secret() -> str:
    return (os.getenv("OAUTH_STATE_SECRET") or os.getenv("PORTAL_ACCESS_TOKEN") or "").strip()


def build_auth_url(state: str | None = None) -> str:
    if not gmail_configured():
        raise ValueError("Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in environment")
    secret = _oauth_state_secret()
    if not secret:
        raise ValueError("Set OAUTH_STATE_SECRET in Render before connecting Gmail")
    nonce = secrets.token_urlsafe(24)
    issued = str(int(datetime.now(timezone.utc).timestamp()))
    payload = f"{issued}.{nonce}"
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    signed_state = f"{payload}.{signature}"
    params = {
        "client_id": os.getenv("GOOGLE_CLIENT_ID").strip(),
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/gmail.readonly",
        "access_type": "offline",
        "prompt": "consent",
        "state": signed_state,
    }
    return GOOGLE_AUTH + "?" + urlencode(params)


def verify_oauth_state(state: str | None) -> bool:
    secret = _oauth_state_secret()
    if not secret or not state:
        return False
    try:
        issued, nonce, supplied = state.split(".", 2)
        age = int(datetime.now(timezone.utc).timestamp()) - int(issued)
        if age < 0 or age > 600 or len(nonce) < 16:
            return False
        payload = f"{issued}.{nonce}"
        expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, supplied)
    except (ValueError, TypeError):
        return False


async def exchange_code(code: str) -> dict[str, Any]:
    if not gmail_configured():
        return {"ok": False, "error": "Google OAuth is not configured"}
    if not code:
        return {"ok": False, "error": "Missing authorization code"}
    try:
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
            if r.status_code >= 400:
                log.warning("Gmail code exchange failed with HTTP %s", r.status_code)
                return {"ok": False, "error": f"Google token exchange failed: HTTP {r.status_code}"}
            data = r.json()
    except httpx.TimeoutException:
        return {"ok": False, "error": "Google token exchange timed out"}
    except httpx.RequestError:
        return {"ok": False, "error": "Google token exchange network error"}
    except (ValueError, json.JSONDecodeError):
        return {"ok": False, "error": "Google token exchange returned invalid JSON"}
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
    return {"ok": True, "connected": True, "email": email}


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
            log.warning("Gmail token refresh failed with HTTP %s", r.status_code)
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
        return [], "GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET not set"
    token = await _access_token()
    if not token:
        return [], "Gmail not connected — open /api/gmail/auth after setting Google OAuth env vars"
    init_gmail_db()
    from_q = " OR ".join(f"from:{d}" for d in ALERT_SENDERS[:8])
    q = f"({from_q}) OR subject:(job alert OR jobs for you OR recommended jobs)"
    async with httpx.AsyncClient(timeout=40) as client:
        r = await client.get(
            GMAIL_API + "/messages",
            headers={"Authorization": f"Bearer {token}"},
            params={"q": q, "maxResults": 25},
        )
        if r.status_code in (401, 403):
            return [], f"Gmail list failed: HTTP {r.status_code}"
        if r.status_code == 429:
            return [], "Gmail list failed: HTTP 429"
        if r.status_code >= 400:
            return [], f"Gmail list failed: HTTP {r.status_code}"
        ids = [m["id"] for m in (r.json().get("messages") or [])]
        jobs: list[dict] = []
        for mid in ids:
            with _connect() as conn:
                seen = conn.execute("SELECT 1 FROM gmail_seen_messages WHERE message_id=?", (mid,)).fetchone()
            if seen:
                continue
            try:
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
            except Exception:
                log.warning("Skipped malformed Gmail message %s", mid[:16])
            with _connect() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO gmail_seen_messages(message_id, seen_at) VALUES (?,?)",
                    (mid, _now()),
                )
    return jobs, None
