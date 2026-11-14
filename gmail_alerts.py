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
from html.parser import HTMLParser
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from job_hunter import DB_PATH, normalize_text, detect_country
from database import connect as db_connect, postgres_enabled, postgres_initialization_once

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
    "naukrigulf.com",
    "catererglobal.com",
    "gulfjobs.com",
]

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _connect():
    return db_connect(DB_PATH)


@postgres_initialization_once
def init_gmail_db():
    with _connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS gmail_oauth_states (state_hash TEXT PRIMARY KEY, expires_at BIGINT NOT NULL)")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS gmail_tokens (
            id INTEGER PRIMARY KEY CHECK(id=1),
            access_token TEXT, refresh_token TEXT, token_expiry TEXT,
            email TEXT DEFAULT '', updated_at TEXT NOT NULL
        )"""
        )
        if not postgres_enabled():
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(gmail_tokens)")}
            if "granted_scopes" not in columns:
                conn.execute("ALTER TABLE gmail_tokens ADD COLUMN granted_scopes TEXT NOT NULL DEFAULT ''")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS gmail_seen_messages (
            message_id TEXT PRIMARY KEY, seen_at TEXT NOT NULL
        )"""
        )


def gmail_configured() -> bool:
    return bool(os.getenv("GOOGLE_CLIENT_ID", "").strip() and os.getenv("GOOGLE_CLIENT_SECRET", "").strip())


class GmailAuthError(ValueError):
    """An actionable, credential-free Gmail authorization failure."""


def _valid_access_token(row, *, margin: int = 0) -> bool:
    if not row or not row["access_token"] or not row["token_expiry"]:
        return False
    try:
        expiry = datetime.fromisoformat(row["token_expiry"])
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return expiry.timestamp() > datetime.now(timezone.utc).timestamp() + margin
    except (TypeError, ValueError):
        return False


def _token_error(response) -> str:
    try:
        data = response.json()
        code = data.get("error") if isinstance(data, dict) else None
    except ValueError:
        code = None
    return code if code in {"invalid_grant", "invalid_client", "unauthorized_client", "access_denied"} else ""


def _authorization_failure(code: str, status_code: int) -> str:
    if code in {"invalid_grant", "access_denied"}:
        return "Gmail authorization expired or was revoked. Reconnect Gmail job alerts."
    if code in {"invalid_client", "unauthorized_client"}:
        return "Google OAuth client configuration was rejected. Check the matching Client ID and Client secret in Render."
    return f"Google token service failed: HTTP {status_code}. Try again later."


def _token_payload(data) -> tuple[str, str]:
    if not isinstance(data, dict) or not isinstance(data.get("access_token"), str) or not data["access_token"].strip():
        raise GmailAuthError("Google token service returned no access token. Try again later.")
    if data.get("refresh_token") is not None and not isinstance(data["refresh_token"], str):
        raise GmailAuthError("Google token service returned an invalid refresh grant. Try again later.")
    expiry = ""
    if data.get("expires_in") is not None:
        try:
            lifetime = int(data["expires_in"])
            if isinstance(data["expires_in"], bool) or not 0 < lifetime <= 31 * 86400:
                raise ValueError()
        except (ValueError, TypeError, OverflowError):
            raise GmailAuthError("Google token service returned an invalid token lifetime. Try again later.") from None
        expiry = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + lifetime, tz=timezone.utc).isoformat()
    return data["access_token"], expiry


def get_gmail_status() -> dict[str, Any]:
    init_gmail_db()
    with _connect() as conn:
        row = conn.execute("SELECT email, access_token, token_expiry, refresh_token, granted_scopes FROM gmail_tokens WHERE id=1").fetchone()
    connected = bool(row and (row["refresh_token"] or _valid_access_token(row)))
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
        + ". Enable the Gmail API. Set the missing variable names in Render, then sign into the private dashboard and click Connect Gmail job alerts. "
        "Create job alerts on Naukri, Naukri Gulf, LinkedIn, Indeed, Foundit, Shine, Hirist, IIMJobs, TimesJobs, GulfTalent, Bayt, CatererGlobal and GulfJobs to that Gmail."
    )
    if gmail_configured() and not missing and not connected:
        hint = "OAuth credentials are set. Sign into the private dashboard, click Connect Gmail job alerts and approve read-only Gmail access. No password is requested."
    elif connected:
        hint = "Connected. New job-alert emails are imported on the next hunt and are not imported twice."
    return {
        "send_authorized": bool(connected and row and "https://www.googleapis.com/auth/gmail.send" in (row["granted_scopes"] or "").split()),
        "oauth_configured": gmail_configured(),
        "connected": connected,
        "email": (row["email"] if row else "") or "",
        "callback_url": _redirect_uri(),
        "missing": missing,
        "setup_required": bool(missing) or not connected,
        "hint": hint,
    }


def _redirect_uri() -> str:
    configured = os.getenv("GOOGLE_REDIRECT_URI", "").strip()
    origin = os.getenv("RENDER_EXTERNAL_URL", "https://autonomous-job-hunter-docker.onrender.com").strip().rstrip("/")
    return configured or origin + "/api/gmail/callback"


def _oauth_state_secret() -> str:
    return (os.getenv("OAUTH_STATE_SECRET") or os.getenv("PORTAL_ACCESS_TOKEN") or "").strip()


def build_auth_url(state: str | None = None, *, allow_send: bool = False) -> str:
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
    init_gmail_db()
    with _connect() as conn:
        conn.execute("DELETE FROM gmail_oauth_states WHERE expires_at < ?", (int(issued),))
        conn.execute("INSERT INTO gmail_oauth_states(state_hash,expires_at) VALUES (?,?)", (hashlib.sha256(signed_state.encode()).hexdigest(), int(issued) + 600))
    params = {
        "client_id": os.getenv("GOOGLE_CLIENT_ID").strip(),
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/gmail.readonly" + (" https://www.googleapis.com/auth/gmail.send" if allow_send else ""),
        "access_type": "offline",
        "prompt": "consent",
        "state": signed_state,
    }
    return GOOGLE_AUTH + "?" + urlencode(params)


def verify_oauth_state(state: str | None, *, consume: bool = False) -> bool:
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
        if not hmac.compare_digest(expected, supplied):
            return False
        if consume:
            init_gmail_db()
            with _connect() as conn:
                deleted = conn.execute("DELETE FROM gmail_oauth_states WHERE state_hash=? AND expires_at>=?", (hashlib.sha256(state.encode()).hexdigest(), int(datetime.now(timezone.utc).timestamp())))
                return deleted.rowcount == 1
        return True
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
                return {"ok": False, "error": _authorization_failure(_token_error(r), r.status_code)}
            data = r.json()
    except httpx.TimeoutException:
        return {"ok": False, "error": "Google token exchange timed out"}
    except httpx.RequestError:
        return {"ok": False, "error": "Google token exchange network error"}
    except (ValueError, json.JSONDecodeError):
        return {"ok": False, "error": "Google token exchange returned invalid JSON"}
    try:
        access_token, expiry = _token_payload(data)
    except GmailAuthError as exc:
        return {"ok": False, "error": str(exc)}
    init_gmail_db()
    email = await _fetch_profile_email(access_token)
    from application_workflow import get_profile
    expected_email = str(get_profile().get("email") or "").strip().lower()
    if not email or not expected_email or not hmac.compare_digest(email.lower().encode("utf-8"), expected_email.encode("utf-8")):
        return {"ok": False, "error": "Connect the Gmail account matching your private profile email."}
    with _connect() as conn:
        conn.execute(
            """INSERT INTO gmail_tokens(id, access_token, refresh_token, token_expiry, email, updated_at, granted_scopes)
               VALUES (1,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 access_token=excluded.access_token,
                 refresh_token=COALESCE(excluded.refresh_token, gmail_tokens.refresh_token),
                 token_expiry=excluded.token_expiry,
                 email=excluded.email,
                 granted_scopes=excluded.granted_scopes,
                 updated_at=excluded.updated_at""",
            (access_token, data.get("refresh_token"), expiry, email, _now(), str(data.get("scope") or "")),
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
    if _valid_access_token(row, margin=60):
        return token
    if not refresh:
        # A short-lived token without a refresh grant cannot remain connected
        # indefinitely simply because the mailbox email is stored.
        return token if _valid_access_token(row) else None
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
            code = _token_error(r)
            if code in {"invalid_grant", "access_denied"}:
                with _connect() as conn:
                    # Do not erase a newer grant saved by a concurrent reconnect.
                    conn.execute(
                        "UPDATE gmail_tokens SET access_token=NULL, refresh_token=NULL, token_expiry='', granted_scopes='', updated_at=? WHERE id=1 AND refresh_token=?",
                        (_now(), refresh),
                    )
            raise GmailAuthError(_authorization_failure(code, r.status_code))
        data = r.json()
    new_token, expiry = _token_payload(data)
    with _connect() as conn:
        conn.execute(
            "UPDATE gmail_tokens SET access_token=?, token_expiry=?, updated_at=? WHERE id=1 AND refresh_token=?",
            (new_token, expiry, _now(), refresh),
        )
    return new_token


class _AlertHTMLText(HTMLParser):
    """Keep anchor destinations beside their visible text in HTML alerts."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.anchors = []
        self.hidden = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden.append(tag)
        if self.hidden:
            return
        if tag == "a":
            self.anchors.append(dict(attrs).get("href") or "")
        if tag in {"br", "p", "div", "td", "tr", "li"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
            return
        if tag == "a" and self.anchors:
            href = self.anchors.pop().strip()
            if href.lower().startswith(("https://", "http://")):
                self.parts.append(" " + href + " ")
        if tag in {"p", "div", "td", "tr", "li"}:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _decode_body(payload: dict) -> str:
    parts = []

    def walk(p):
        if not p:
            return
        body = p.get("body") or {}
        data = body.get("data")
        if data:
            try:
                decoded = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
                if p.get("mimeType") == "text/html" or (not p.get("mimeType") and "<" in decoded):
                    parser = _AlertHTMLText()
                    parser.feed(decoded)
                    parser.close()
                    decoded = "".join(parser.parts)
                parts.append(decoded)
            except Exception:
                pass
        for child in p.get("parts") or []:
            walk(child)

    walk(payload)
    text = "\n".join(parts)
    return normalize_text(text)


def _guess_source(from_header: str, subject: str) -> str:
    blob = (from_header + " " + subject).lower()
    mapping = [
        ("naukrigulf", "Naukri Gulf (email alert)"),
        ("catererglobal", "CatererGlobal (email alert)"),
        ("gulfjobs", "GulfJobs (email alert)"),
        ("gulftalent", "GulfTalent (email alert)"),
        ("naukri", "Naukri (email alert)"),
        ("linkedin", "LinkedIn (email alert)"),
        ("shine", "Shine (email alert)"),
        ("monster", "Monster (email alert)"),
        ("bayt", "Bayt (email alert)"),
        ("indeed", "Indeed (email alert)"),
        ("foundit", "Foundit (email alert)"),
        ("timesjobs", "TimesJobs (email alert)"),
        ("hirist", "Hirist (email alert)"),
        ("iimjobs", "IIMJobs (email alert)"),
    ]
    for key, label in mapping:
        if key in blob:
            return label
    return "Job alert email"


def _extract_jobs_from_email(subject: str, from_header: str, body: str, message_id: str, received_at: datetime | None = None) -> list[dict]:
    source = _guess_source(from_header, subject)
    urls = []
    content = body + " " + subject
    for m in URL_RE.finditer(content):
        u = m.group(0).rstrip(").,;\"'")
        low = u.lower()
        try:
            parsed = urlsplit(u)
            host = (parsed.hostname or "").lower()
        except ValueError:
            continue
        if parsed.username is not None or parsed.password is not None:
            continue
        if any(
            host == domain or host.endswith("." + domain)
            for domain in (
                "naukri.com",
                "naukrigulf.com",
                "linkedin.com",
                "shine.com",
                "monster.com",
                "monsterindia.com",
                "bayt.com",
                "gulftalent.com",
                "indeed.com",
                "foundit.in",
                "timesjobs.com",
                "hirist.com",
                "iimjobs.com",
                "catererglobal.com",
                "gulfjobs.com",
                "greenhouse.io",
                "lever.co",
            )
        ):
            if "unsubscribe" in low or "mailto:" in low:
                continue
            urls.append((u, m.start(), m.end()))
    seen, uniq = set(), []
    for u, start, end in urls:
        if u not in seen:
            seen.add(u)
            uniq.append((u, start, end))
    jobs = []
    title_guess = subject
    for noise in ("job alert", "new jobs", "recommended jobs", "jobs for you", "|", "-"):
        title_guess = re.sub(re.escape(noise), " ", title_guess, flags=re.I)
    title_guess = normalize_text(title_guess)[:200] or "Role from job alert"
    for i, (url, start, end) in enumerate(uniq[:8]):
        previous_end = uniq[i - 1][2] if i else 0
        context = content[max(previous_end, start - 240):start]
        loc = ""
        city_matches = []
        cities = (
            "dubai", "abu dhabi", "riyadh", "doha", "noida", "gurgaon", "gurugram",
            "mumbai", "bengaluru", "bangalore", "hyderabad", "pune", "delhi",
            "sharjah", "jeddah", "dammam", "kuwait", "muscat", "manama",
            "lucknow", "dehradun", "ghaziabad", "faridabad",
        )
        for city in cities:
            city_matches.extend((match.start(), city.title()) for match in re.finditer(r"\b" + re.escape(city) + r"\b", context, re.I))
        if city_matches:
            loc = max(city_matches)[1]
        else:
            # Only a subject-level location can apply to the whole alert. Do
            # not borrow the city of a different listing elsewhere in its body.
            subject_cities = [city.title() for city in cities if re.search(r"\b" + re.escape(city) + r"\b", subject, re.I)]
            subject_country = detect_country(subject)
            loc = subject_cities[0] if len(subject_cities) == 1 else subject_country if subject_country not in {"Unspecified", "Remote / Unspecified"} else ""
        # Only use a nearby pre-link age label; a wider window can accidentally borrow
        # the posting age from another listing in the same alert email.
        posted_at = None
        relative_dates = list(re.finditer(
            r"\b(?:posted\s+)?(?:\d+(?:\.\d+)?|a|an)\s*(?:minutes?|mins?|m|hours?|hrs?|h|days?|d)\s+ago\b",
            context,
            flags=re.IGNORECASE,
        ))
        if relative_dates:
            from job_sources import _parse_iso

            posted_at = _parse_iso(relative_dates[-1].group(0), reference_time=received_at)
        jobs.append(
            {
                "source": source,
                "source_id": f"{message_id}:{i}",
                "title": title_guess if i == 0 else f"{title_guess} ({i+1})",
                "company": "",
                "location": loc,
                "country": detect_country(loc),
                "url": url[:500],
                "description": body[:4000],
                "employment_type": "",
                "salary": "",
                # Email arrival time is not a verified posting date. Keep unknown dates hidden
                # from the portal's strict last-24-hours view.
                "posted_at": posted_at,
                "raw_json": json.dumps({"message_id": message_id, "from": from_header, "subject": subject})[:5000],
                "_gmail_message_id": message_id,
            }
        )
    return jobs


async def fetch_jobs_from_gmail() -> tuple[list[dict], str | None]:
    """Pull recent job-alert messages and convert to job dicts."""
    if not gmail_configured():
        return [], "GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET not set"
    try:
        token = await _access_token()
    except GmailAuthError as exc:
        return [], str(exc)
    if not token:
        return [], "Gmail not connected — use Connect Gmail job alerts in the private dashboard after setting Google OAuth env vars"
    init_gmail_db()
    from_q = " OR ".join(f"from:{d}" for d in ALERT_SENDERS)
    from job_hunter import get_settings
    lookback_days = max(7, min(31, int(get_settings().get("posted_within_days", 3)) + 1))
    q = f"newer_than:{lookback_days}d (({from_q}) OR subject:(job alert OR jobs for you OR recommended jobs))"
    async with httpx.AsyncClient(timeout=40) as client:
        ids, page_token = [], None
        for _page in range(5):
            params = {"q": q, "maxResults": 25}
            if page_token:
                params["pageToken"] = page_token
            r = await client.get(GMAIL_API + "/messages", headers={"Authorization": f"Bearer {token}"}, params=params)
            if r.status_code >= 400:
                return [], f"Gmail list failed: HTTP {r.status_code}"
            page = r.json()
            ids.extend(m["id"] for m in (page.get("messages") or []) if isinstance(m, dict) and m.get("id"))
            page_token = page.get("nextPageToken")
            if not page_token:
                break
        errors = ["Gmail scan limited to 125 recent messages; narrow job-alert subscriptions if this repeats."] if page_token else []
        jobs: list[dict] = []
        for mid in dict.fromkeys(ids):
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
                    errors.append(f"Gmail message fetch failed: HTTP {mr.status_code}")
                    continue
                msg = mr.json()
                headers = {h["name"].lower(): h["value"] for h in (msg.get("payload") or {}).get("headers") or []}
                subject = headers.get("subject", "")
                from_h = headers.get("from", "")
                body = _decode_body(msg.get("payload") or {})
                from job_sources import _parse_iso
                received = _parse_iso(msg.get("internalDate"))
                if not received:
                    raise ValueError("Missing message timestamp")
                extracted = _extract_jobs_from_email(subject, from_h, body, mid, received_at=datetime.fromisoformat(received))
                jobs.extend(extracted)
            except Exception:
                log.warning("Skipped malformed Gmail message %s", mid[:16])
                errors.append("Gmail message could not be parsed; it will be retried.")
                continue
            if not extracted:
                with _connect() as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO gmail_seen_messages(message_id, seen_at) VALUES (?,?)",
                        (mid, _now()),
                    )
    return jobs, "; ".join(dict.fromkeys(errors)) or None
