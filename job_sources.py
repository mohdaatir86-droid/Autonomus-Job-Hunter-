"""Fetch public job sources and orchestrate hunt runs."""
from __future__ import annotations

import json
import logging
import os
import re
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote_plus, urlsplit, urlunsplit, parse_qsl, urlencode

import httpx
from database import postgres_initialization_once

from job_hunter import (
    connect,
    init_db,
    get_settings,
    score_job,
    is_relevant_role,
    normalize_text,
    detect_country,
    now_iso,
    DEFAULT_COUNTRIES,
    INDIA_REGION_ALIASES,
)

log = logging.getLogger("job-hunter")

PORTAL_SOURCE_MATCHES = (
    ("naukri_gulf", "Naukri Gulf", ("naukri gulf", "naukrigulf"), ("naukrigulf.com",)),
    ("naukri", "Naukri", ("naukri",), ("naukri.com",)),
    ("linkedin", "LinkedIn Jobs", ("linkedin",), ("linkedin.com",)),
    ("indeed", "Indeed", ("indeed",), ("indeed.com",)),
    ("foundit", "Foundit (Monster)", ("foundit", "monster"), ("foundit.in", "monster.com", "monsterindia.com")),
    ("gulftalent", "GulfTalent", ("gulftalent",), ("gulftalent.com",)),
    ("bayt", "Bayt", ("bayt",), ("bayt.com",)),
    ("shine", "Shine", ("shine",), ("shine.com",)),
    ("hirist", "Hirist", ("hirist",), ("hirist.com",)),
    ("iimjobs", "IIMJobs", ("iimjobs",), ("iimjobs.com",)),
    ("timesjobs", "TimesJobs", ("timesjobs",), ("timesjobs.com",)),
    ("catererglobal", "CatererGlobal", ("catererglobal",), ("catererglobal.com",)),
    ("gulfjobs", "GulfJobs", ("gulfjobs",), ("gulfjobs.com",)),
)

GULF_COUNTRIES = ["United Arab Emirates", "Saudi Arabia", "Qatar", "Kuwait", "Oman", "Bahrain"]
LISTABLE_COUNTRIES = ["India"] + GULF_COUNTRIES + ["Canada"]

INDIA_TARGET_LOCATION_TERMS = (
    "delhi", "ncr", "gurgaon", "gurugram", "noida", "greater noida",
    "dehradun", "uttar pradesh", "uttarakhand", "chandigarh",
    "ghaziabad", "faridabad", "manesar",
)

def is_target_market_job(job, country=None):
    """Keep jobs in the seven target countries. City preference is a score boost, not a hard reject."""
    market = country or detect_country(job.get("location"), job.get("title", ""), job.get("description", ""))
    return market in LISTABLE_COUNTRIES

def build_search_queries(settings, reference_time=None):
    # Cover all saved roles over scheduled ticks without increasing lookup count.
    saved = list(dict.fromkeys(str(role).strip() for role in settings.get("target_titles", []) if str(role).strip()))
    saved = saved or ["Operations Manager", "Cluster Manager", "Area Manager"]
    moment = reference_time or datetime.now(timezone.utc)
    start = (int(moment.timestamp()) // (6 * 3600) * 3) % len(saved)
    roles = [saved[(start + index) % len(saved)] for index in range(min(3, len(saved)))]
    countries = settings.get("target_countries", DEFAULT_COUNTRIES)
    queries = []
    if "India" in countries:
        locations = settings.get("preferred_locations") or ["India"]
        for role in roles:
            queries.append(f"{role} (" + " OR ".join(locations) + ") India")
    gulf = [country for country in countries if country in GULF_COUNTRIES]
    if gulf:
        for role in roles:
            queries.append(role + " (" + " OR ".join(gulf) + ")")
    for country in countries:
        if country != "India" and country not in GULF_COUNTRIES:
            queries.append(f"{roles[0]} {country}")
    return queries


def _parse_iso(value, reference_time=None):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        try:
            stamp = float(value)
            if stamp > 10_000_000_000:
                stamp /= 1000.0
            return datetime.fromtimestamp(stamp, tz=timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            return None

    text = str(value).strip()
    relative = re.fullmatch(
        r"(?:posted\s+)?(?P<count>\d+(?:\.\d+)?|a|an)\s*(?P<unit>minutes?|mins?|m|hours?|hrs?|h|days?|d)\s+ago",
        text,
        flags=re.IGNORECASE,
    )
    if relative:
        count = 1.0 if relative.group("count").lower() in {"a", "an"} else float(relative.group("count"))
        unit = relative.group("unit").lower()
        if unit.startswith("m"):
            delta = timedelta(minutes=count)
        elif unit.startswith("d"):
            delta = timedelta(days=count)
        else:
            delta = timedelta(hours=count)
        return ((reference_time or datetime.now(timezone.utc)) - delta).isoformat()

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        # Do not treat unknown or human-readable dates as verified posting timestamps.
        return None


def _job_url(job):
    url = normalize_text(job.get("url") or job.get("job_url") or job.get("apply_url") or job.get("link"))
    if not url.startswith(("http://", "https://")):
        return ""
    return url


def identify_portal_source(via="", urls=()):
    """Return a known board key only when Google/Gmail identifies that board."""
    via_text = normalize_text(via).lower()
    candidates = [str(url) for url in (urls or ()) if url]
    for key, _display_name, aliases, _domains in PORTAL_SOURCE_MATCHES:
        if any(alias in via_text for alias in aliases):
            return key
    for candidate in candidates:
        try:
            hostname = (urlsplit(candidate).hostname or "").lower().removeprefix("www.")
        except ValueError:
            continue
        for key, _display_name, _aliases, domains in PORTAL_SOURCE_MATCHES:
            if any(hostname == domain or hostname.endswith("." + domain) for domain in domains):
                return key
    return None


def portal_discovery_counts(jobs):
    """Count unique, attributed listings from the latest aggregator/alert fetch."""
    counts = {key: 0 for key, *_ in PORTAL_SOURCE_MATCHES}
    counts["serpapi_google_jobs"] = 0
    seen = set()
    for job in jobs or []:
        source = str(job.get("source") or "")
        url = normalize_job_url(job.get("url") or "")
        if "google jobs" in source.lower():
            identity = ("serpapi_google_jobs", url or str(job.get("source_id") or job.get("title") or ""))
            if identity not in seen:
                seen.add(identity)
                counts["serpapi_google_jobs"] += 1
        key = identify_portal_source(source, [job.get("url")])
        if not key:
            continue
        identity = (key, url or str(job.get("source_id") or job.get("title") or ""))
        if identity in seen:
            continue
        seen.add(identity)
        counts[key] += 1
    return counts


def _extract_posted(raw):
    ext = raw.get("detected_extensions")
    if isinstance(ext, dict) and ext.get("posted_at"):
        return ext.get("posted_at")
    return (
        raw.get("publication_date")
        or raw.get("published_at")
        or raw.get("createdAt")
        or raw.get("date")
    )


def _clean_job(raw, source):
    title = normalize_text(raw.get("title") or raw.get("position") or raw.get("name"))
    company_obj = raw.get("company")
    company = normalize_text(
        company_obj.get("name")
        if isinstance(company_obj, dict)
        else company_obj or raw.get("company_name") or raw.get("companyName")
    )
    location_obj = raw.get("location")
    if isinstance(location_obj, dict):
        location = normalize_text(", ".join(str(v) for v in location_obj.values() if v))
    elif isinstance(location_obj, list):
        location = normalize_text(", ".join(str(v) for v in location_obj if v))
    else:
        location = normalize_text(
            location_obj
            or raw.get("candidate_required_location")
            or raw.get("jobLocation")
            or raw.get("workplace")
        )
    description = normalize_text(
        re.sub(
            r"<[^>]+>",
            " ",
            str(
                raw.get("description")
                or raw.get("descriptionPlain")
                or raw.get("jobDescription")
                or raw.get("snippet")
                or raw.get("description_text")
                or ""
            ),
        )
    )
    url = _job_url(raw)
    if not title or not url:
        return None
    return {
        "source": source,
        "source_id": str(raw.get("id") or raw.get("slug") or raw.get("job_id") or "")[:120],
        "title": title[:300],
        "company": company[:300],
        "location": location[:300],
        "country": detect_country(location, title, description),
        "url": url,
        "description": description[:12000],
        "employment_type": normalize_text(
            raw.get("job_type") or raw.get("employment_type") or raw.get("contractType") or raw.get("type")
        )[:100],
        "salary": normalize_text(raw.get("salary") or raw.get("salaryRange") or raw.get("salary_range"))[:200],
        "posted_at": _parse_iso(_extract_posted(raw)),
        "raw_json": json.dumps(raw, ensure_ascii=False)[:20000],
    }


async def _get_json(client, url, params=None):
    response = await client.get(url, params=params, timeout=20, follow_redirects=True)
    response.raise_for_status()
    return response.json()


def _safe_source_error(exc):
    """Summarize provider failures without leaking request URLs or query-string API keys."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "request timed out"
    if isinstance(exc, httpx.RequestError):
        return "network/request error"
    if isinstance(exc, (ValueError, json.JSONDecodeError)):
        return "invalid JSON response"
    return type(exc).__name__


def status_from_error_text(detail: str | None) -> str:
    text = (detail or "").lower()
    if not text:
        return "working"
    if "not set" in text or "not configured" in text or "not connected" in text:
        return "missing_configuration"
    if "http 401" in text or "http 403" in text or "unauthorized" in text or "invalid api" in text:
        return "unauthorized"
    if "http 429" in text or "rate limit" in text or "rate-limited" in text:
        return "rate_limited"
    if "timed out" in text or "network/request" in text or "http 5" in text:
        return "unavailable"
    return "degraded"


_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "gclid", "fbclid", "mc_cid", "mc_eid", "igshid", "si", "trk",
}


def normalize_job_url(url: str) -> str:
    raw = normalize_text(url)
    if not raw:
        return ""
    parts = urlsplit(raw)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return raw
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key.lower() not in _TRACKING_PARAMS
        )
    )
    path = parts.path.rstrip("/") or "/"
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return urlunsplit((parts.scheme.lower(), host, path, query, ""))


@postgres_initialization_once
def ensure_job_columns():
    init_db()
    from database import postgres_enabled
    with connect() as conn:
        if postgres_enabled():
            conn.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS normalized_url TEXT")
        else:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
            if "normalized_url" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN normalized_url TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_normalized_url ON jobs(normalized_url)")
        conn.execute("CREATE TABLE IF NOT EXISTS discovery_leases(name TEXT PRIMARY KEY, owner TEXT NOT NULL, expires_at TEXT NOT NULL)")


def persist_source_report(reports, portal_counts=None):
    payload = {
        "checked_at": now_iso(),
        "sources": reports,
        "portal_counts": portal_counts or {},
        "portal_fetch_method": "Google Jobs aggregator and connected job-alert emails",
    }
    with connect() as conn:
        conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("last_source_report", json.dumps(payload)),
        )


def get_source_report():
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key='last_source_report'").fetchone()
    if not row:
        return None
    try:
        return json.loads(row["value"])
    except (TypeError, json.JSONDecodeError):
        return None


_hunt_lock = asyncio.Lock()


async def fetch_arbeitnow(client, limit):
    data = await _get_json(client, "https://www.arbeitnow.com/api/job-board-api")
    out = []
    for raw in (data.get("data") or data.get("jobs") or [])[:limit]:
        job = _clean_job(raw if isinstance(raw, dict) else {}, "Arbeitnow")
        if job:
            out.append(job)
    return out


async def fetch_remotive(client, limit):
    data = await _get_json(client, "https://remotive.com/api/remote-jobs", params={"category": "operations"})
    out = []
    for raw in (data.get("jobs") or [])[:limit]:
        job = _clean_job(raw if isinstance(raw, dict) else {}, "Remotive")
        if job:
            out.append(job)
    return out


async def fetch_jobicy(client, limit):
    data = await _get_json(client, "https://jobicy.com/api/v2/remote-jobs", params={"count": limit})
    out = []
    for raw in (data.get("jobs") or [])[:limit]:
        job = _clean_job(raw if isinstance(raw, dict) else {}, "Jobicy")
        if job:
            out.append(job)
    return out


async def fetch_serpapi_google_jobs(client, limit):
    api_key = os.getenv("SERPAPI_API_KEY", "").strip()
    if not api_key:
        return [], "SERPAPI_API_KEY not set"
    out, errors = [], []
    queries = build_search_queries(get_settings())
    per_q = max(5, min(20, limit // max(1, len(queries))))
    for q in queries:
        try:
            data = await _get_json(
                client,
                "https://serpapi.com/search.json",
                params={"engine": "google_jobs", "q": q, "hl": "en", "api_key": api_key},
            )
            for raw in (data.get("jobs_results") or [])[:per_q]:
                apply_options = [opt for opt in (raw.get("apply_options") or []) if isinstance(opt, dict)]
                option_links = [opt.get("link") or "" for opt in apply_options]
                via = normalize_text(raw.get("via") or "")
                portal_key = identify_portal_source(via, option_links + [raw.get("link"), raw.get("share_link")])
                link = ""
                if portal_key:
                    link = next(
                        (candidate for candidate in option_links if identify_portal_source(urls=[candidate]) == portal_key),
                        "",
                    )
                link = link or raw.get("share_link") or raw.get("link") or ""
                if not link and option_links:
                    link = option_links[0]
                company = raw.get("company_name") if isinstance(raw.get("company_name"), str) else ""
                loc = raw.get("location") or ""
                ext = raw.get("detected_extensions") or {}
                posted = ext.get("posted_at") or ext.get("posted") or ""
                desc_parts = [raw.get("description") or raw.get("snippet") or ""]
                if raw.get("job_highlights"):
                    for h in raw["job_highlights"]:
                        items = h.get("items") or []
                        desc_parts.append(" ".join(items))
                normalized = {
                    "title": raw.get("title"),
                    "company": company,
                    "location": loc,
                    "url": link,
                    "description": " ".join(str(x) for x in desc_parts if x),
                    "id": raw.get("job_id") or link,
                    "date": posted,
                    "salary": (ext.get("salary") if isinstance(ext, dict) else None) or raw.get("salary"),
                }
                job = _clean_job(normalized, "Google Jobs (SerpApi)")
                if job:
                    if portal_key:
                        display_name = next(name for key, name, *_ in PORTAL_SOURCE_MATCHES if key == portal_key)
                        job["source"] = f"{display_name} via Google Jobs"[:80]
                    elif via:
                        job["source"] = f"Google Jobs / {via}"[:80]
                    out.append(job)
        except Exception as e:
            summary = _safe_source_error(e)
            log.warning("SerpApi query failed (%s): %s", q[:40], summary)
            errors.append(f"SerpApi[{q[:40]}]: {summary}")
            if isinstance(e, httpx.HTTPStatusError) and e.response.status_code in (401, 403, 429):
                # Rate limits apply to the key, not this query: avoid wasting
                # quota and flooding the provider with subsequent requests.
                break
    if not out and errors and status_from_error_text("; ".join(errors)) == "unauthorized":
        return out, "SerpApi unauthorized: HTTP 401. Rotate SERPAPI_API_KEY in Render; do not paste it into chat."
    return out, ("; ".join(errors) if errors else None)


BUILTIN_GREENHOUSE_BOARDS = ("careem", "flexport", "instacart")


async def fetch_greenhouse(client, limit):
    configured = [b.strip() for b in os.getenv("GREENHOUSE_BOARDS", "").split(",") if b.strip()]
    boards = configured or list(BUILTIN_GREENHOUSE_BOARDS)
    settings = get_settings()
    out, errors = [], []
    per_board = max(10, min(40, limit))
    for board in boards[:15]:
        try:
            data = await _get_json(
                client,
                f"https://boards-api.greenhouse.io/v1/boards/{quote_plus(board)}/jobs",
                params={"content": "true"},
            )
            jobs = data.get("jobs") or []
            candidates = []
            for raw in jobs:
                loc = ""
                if isinstance(raw.get("location"), dict):
                    loc = raw["location"].get("name") or ""
                normalized = {
                    "title": raw.get("title"),
                    "company": board,
                    "location": loc,
                    "url": raw.get("absolute_url") or "",
                    "description": raw.get("content") or "",
                    "id": str(raw.get("id") or ""),
                    "date": raw.get("first_published") or raw.get("created_at"),
                }
                job = _clean_job(normalized, f"Greenhouse/{board}")
                if job:
                    job["_board_complete"] = len(jobs) <= per_board
                    candidates.append(job)
            candidates.sort(key=lambda job: (
                job["country"] in settings["target_countries"] and matches_preferences(job, settings),
                score_job(job, settings)[0],
                job.get("posted_at") or "",
            ), reverse=True)
            out.extend(candidates[:per_board])
        except Exception as e:
            summary = _safe_source_error(e)
            log.warning("Greenhouse board %s failed: %s", board, summary)
            errors.append(f"Greenhouse/{board}: {summary}")
    return out, ("; ".join(errors) if errors else None)


BUILTIN_LEVER_SITES = ("meesho", "lalamove")


async def fetch_lever(client, limit):
    configured = [s.strip() for s in os.getenv("LEVER_SITES", "").split(",") if s.strip()]
    sites = configured or list(BUILTIN_LEVER_SITES)
    settings = get_settings()
    out, errors = [], []
    for site in sites[:15]:
        try:
            data = await _get_json(client, f"https://api.lever.co/v0/postings/{quote_plus(site)}")
            rows = data if isinstance(data, list) else data.get("data") or []
            candidates = []
            for raw in rows:
                cats = raw.get("categories") or {}
                loc = cats.get("location") or raw.get("location") or ""
                normalized = {
                    "title": raw.get("text") or raw.get("title"),
                    "company": site,
                    "location": loc,
                    "url": raw.get("hostedUrl") or raw.get("applyUrl") or "",
                    "description": raw.get("descriptionPlain") or raw.get("description") or "",
                    "id": str(raw.get("id") or ""),
                    "date": raw.get("createdAt"),
                }
                job = _clean_job(normalized, f"Lever/{site}")
                if job:
                    candidates.append(job)
            # Relevance comes before the per-board cap: Gulf listings may be
            # beyond the first hundred entries on a global company board.
            candidates.sort(key=lambda job: (
                job["country"] in settings["target_countries"] and matches_preferences(job, settings),
                score_job(job, settings)[0],
                job.get("posted_at") or "",
            ), reverse=True)
            out.extend(candidates[:limit])
        except Exception as e:
            summary = _safe_source_error(e)
            log.warning("Lever board %s failed: %s", site, summary)
            errors.append(f"Lever/{site}: {summary}")
    return out, ("; ".join(errors) if errors else None)


async def fetch_public_sources(settings):
    results, errors, reports = [], [], []
    limit = max(10, min(250, int(settings.get("max_results_per_source", 100))))

    def add_report(name, jobs, detail):
        status = "working" if not detail else status_from_error_text(detail)
        if jobs and status not in {"working", "degraded"}:
            status = "degraded"
        reports.append({
            "name": name,
            "status": status,
            "fetched": len(jobs),
            "detail": detail or "",
            "configured": status != "missing_configuration",
        })
        if detail and status not in {"working", "missing_configuration"}:
            errors.append(name + ": " + detail)

    async with httpx.AsyncClient(headers={"User-Agent": "AutonomousJobHunter/0.6"}) as client:
        for name, fetcher in [
            ("Arbeitnow", fetch_arbeitnow),
            ("Remotive", fetch_remotive),
            ("Jobicy", fetch_jobicy),
        ]:
            try:
                jobs = await fetcher(client, limit)
                results.extend(jobs)
                add_report(name, jobs, None)
            except Exception as e:
                summary = _safe_source_error(e)
                log.warning("%s source failed: %s", name, summary)
                add_report(name, [], summary)

        try:
            serps, serr = await fetch_serpapi_google_jobs(client, limit)
            results.extend(serps)
            add_report("Google Jobs via SerpApi", serps, serr)
        except Exception as e:
            summary = _safe_source_error(e)
            add_report("Google Jobs via SerpApi", [], summary)

        try:
            gh, gerr = await fetch_greenhouse(client, limit)
            results.extend(gh)
            if not os.getenv("GREENHOUSE_BOARDS", "").strip() and not gerr:
                gerr = None
            add_report("Greenhouse company boards", gh, gerr)
        except Exception as e:
            add_report("Greenhouse company boards", [], _safe_source_error(e))

        try:
            lv, lerr = await fetch_lever(client, limit)
            results.extend(lv)
            add_report("Lever company boards", lv, lerr)
        except Exception as e:
            add_report("Lever company boards", [], _safe_source_error(e))

        try:
            from gulf_sources import fetch_gulf_sources, FEED_NAME
            gulf_jobs, gulf_error = await fetch_gulf_sources(client, settings)
            results.extend(gulf_jobs)
            add_report(FEED_NAME, gulf_jobs, gulf_error)
        except Exception as e:
            add_report("Gulf employer feeds", [], _safe_source_error(e))

        try:
            from gmail_alerts import fetch_jobs_from_gmail

            gmail_jobs, gmail_err = await fetch_jobs_from_gmail()
            results.extend(gmail_jobs)
            add_report("Gmail job alerts", gmail_jobs, gmail_err)
        except Exception as e:
            summary = _safe_source_error(e)
            log.warning("Gmail alerts failed: %s", summary)
            add_report("Gmail job alerts", [], summary)

    return results, errors, reports, portal_discovery_counts(results)


def _find_existing_job(conn, job, normalized_url):
    existing = conn.execute(
        "SELECT id, url FROM jobs WHERE url=? OR (normalized_url IS NOT NULL AND normalized_url<>'' AND normalized_url=?)",
        (job["url"], normalized_url),
    ).fetchone()
    if existing:
        return existing
    source = normalize_text(job.get("source"))
    source_id = normalize_text(job.get("source_id"))
    if not source or not source_id:
        return None
    # Employers can publish several requisitions with the same title and city.
    # Only provider identity or the canonical URL establishes a duplicate.
    return conn.execute(
        "SELECT id, url FROM jobs WHERE source=? AND source_id=?",
        (source, source_id),
    ).fetchone()


def _choose_url(existing_url, new_url):
    old = existing_url or ""
    if "google.com/search" in old and "google.com/search" not in (new_url or ""):
        return new_url
    return old or new_url


def matches_preferences(job, settings):
    """Reject explicit mismatches; retain undisclosed requirements for review."""
    country = job.get("country") or detect_country(job.get("location"))
    if country == "India" and settings.get("preferred_locations"):
        location = normalize_text(job.get("location")).lower()
        groups = INDIA_REGION_ALIASES
        terms = [term for place in settings["preferred_locations"] for term in groups.get(place.lower(), [place.lower()])]
        # Country-only/remote locations cannot establish a city mismatch.
        if location and location not in {"india", "remote", "remote, india", "india (remote)"} and not any(term in location for term in terms):
            return False
    text = normalize_text(" ".join(str(job.get(k) or "") for k in ("title", "description", "employment_type", "location"))).lower()
    desired = settings.get("work_arrangement", "all")
    actual = "hybrid" if "hybrid" in text else "remote" if re.search(r"\bremote\b|work from home", text) else "onsite" if re.search(r"on[ -]?site|in[ -]?office", text) else None
    if desired != "all" and actual and desired != actual:
        return False
    if settings.get("visa_preference") == "sponsor" and re.search(r"no (?:visa )?sponsorship|(?:visa )?sponsorship (?:is )?not (?:available|provided|offered)|must (?:already )?have (?:the )?right to work", text):
        return False
    experience = re.search(r"(?:minimum |at least )?(\d+)(?:\s*[-–]\s*(\d+))?\+?\s*years?(?: of)? experience", text)
    if experience and int(experience.group(2) or experience.group(1)) < int(settings.get("min_experience_years", 0)):
        return False
    if country == "India":
        salary = normalize_text(job.get("salary")).lower().replace(",", "")
        match = re.search(r"(\d+(?:\.\d+)?)(?:\s*[-–]\s*(\d+(?:\.\d+)?))?\s*(?:lpa|lakhs?(?: per annum)?|lacs?)\b", salary)
        if match and float(match.group(2) or match.group(1)) < float(settings.get("min_india_salary_lpa", 0)):
            return False
    return True


async def hunt_jobs(gulf_only=False):
    if _hunt_lock.locked():
        return {
            "status": "already_running",
            "fetched": 0,
            "new_jobs": 0,
            "updated_jobs": 0,
            "sources": [],
            "errors": ["A hunt is already running"],
            "live_applications_enabled": False,
        }
    await _hunt_lock.acquire()
    try:
        import secrets
        ensure_job_columns()
        owner = secrets.token_hex(16)
        expiry = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        with connect() as conn:
            conn.execute("INSERT INTO discovery_leases(name,owner,expires_at) VALUES ('hunt',?,?) ON CONFLICT(name) DO NOTHING", (owner, expiry))
            claim = conn.execute("UPDATE discovery_leases SET owner=?,expires_at=? WHERE name='hunt' AND (owner=? OR expires_at<?)", (owner, expiry, owner, now_iso()))
            acquired = claim.rowcount == 1
        if not acquired:
            return {"status": "already_running", "fetched": 0, "new_jobs": 0, "errors": []}
        try:
            return await _hunt_jobs_locked(gulf_only=gulf_only)
        finally:
            with connect() as conn:
                conn.execute("DELETE FROM discovery_leases WHERE name='hunt' AND owner=?", (owner,))
    finally:
        _hunt_lock.release()


async def _hunt_jobs_locked(gulf_only=False):
    started = now_iso()
    settings = get_settings()
    ensure_job_columns()
    if gulf_only:
        from gulf_sources import fetch_gulf_sources, FEED_NAME
        async with httpx.AsyncClient(headers={"User-Agent": "AutonomousJobHunter/0.6"}) as client:
            fetched, detail = await fetch_gulf_sources(client, settings)
        errors = [detail] if detail else []
        reports = [{"name": FEED_NAME, "status": "degraded" if detail else "working", "fetched": len(fetched), "detail": detail or "", "configured": True}]
        portal_counts = None
    else:
        fetched, errors, reports, portal_counts = await fetch_public_sources(settings)
    inserted = updated = closed = 0
    sources_touched = set()
    notify_jobs = []
    notification_now = datetime.now(timezone.utc)
    notification_cutoff = (notification_now - timedelta(days=settings.get("posted_within_days", 3))).isoformat()
    from application_workflow import get_profile_full
    gulf_profile = get_profile_full()
    full_board_urls = {}
    for job in fetched:
        if job.get("_board_complete"):
            full_board_urls.setdefault(job.get("source") or "", set()).add(job.get("url"))
    with connect() as conn:
        for job in fetched:
            score, reasons, country = score_job(job, settings)
            from gulf_sources import current_evidence, fit
            gulf_verified = current_evidence(job)
            if gulf_verified:
                if not fit(job, gulf_profile, settings)[0]:
                    continue
                # The explicitly requested Gulf view includes all six countries,
                # independently of older saved-country preferences.
                gulf_scoring = {**settings, "target_countries": list(dict.fromkeys(settings.get("target_countries", []) + GULF_COUNTRIES))}
                score, reasons, country = score_job(job, gulf_scoring)
            elif not is_relevant_role(job) or country not in settings.get("target_countries", DEFAULT_COUNTRIES) or not is_target_market_job(job, country) or not matches_preferences({**job, "country": country}, settings):
                continue
            sources_touched.add(job.get("source") or "?")
            normalized_url = normalize_job_url(job["url"])
            job["match_score"] = score
            job["match_reasons"] = json.dumps(reasons, ensure_ascii=False)
            job["country"] = country
            existing = _find_existing_job(conn, job, normalized_url)
            if existing:
                chosen_url = _choose_url(existing["url"], job["url"])
                if chosen_url != (existing["url"] or ""):
                    clash = conn.execute(
                        "SELECT id FROM jobs WHERE url=? AND id<>?",
                        (chosen_url, existing["id"]),
                    ).fetchone()
                    if clash:
                        chosen_url = existing["url"]
                conn.execute(
                    """UPDATE jobs SET title=?,company=?,location=?,country=?,description=?,
                    employment_type=?,salary=?,posted_at=?,match_score=?,match_reasons=?,
                    last_seen_at=?,raw_json=?,source=?,url=?,normalized_url=? WHERE id=?""",
                    (
                        job["title"], job["company"], job["location"], country, job["description"],
                        job["employment_type"], job["salary"], job["posted_at"], score,
                        job["match_reasons"], now_iso(), job["raw_json"], job["source"],
                        chosen_url, normalize_job_url(chosen_url), existing["id"],
                    ),
                )
                updated += 1
            else:
                conn.execute(
                    """INSERT INTO jobs(source,source_id,title,company,location,country,url,description,
                    employment_type,salary,posted_at,match_score,match_reasons,status,first_seen_at,last_seen_at,raw_json,normalized_url)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'new',?,?,?,?)""",
                    (
                        job["source"], job["source_id"], job["title"], job["company"], job["location"],
                        country, job["url"], job["description"], job["employment_type"], job["salary"],
                        job["posted_at"], score, job["match_reasons"], now_iso(), now_iso(), job["raw_json"],
                        normalized_url,
                    ),
                )
                inserted += 1
                from notify import notification_min_score
                min_alert_score = notification_min_score()
                posted_at = _parse_iso(job.get("posted_at"))
                if score >= min_alert_score and posted_at and notification_cutoff <= posted_at <= notification_now.isoformat():
                    notify_jobs.append({
                        "title": job["title"], "company": job["company"], "location": job["location"],
                        "country": country, "url": job["url"], "match_score": score,
                    })
        for source, urls in full_board_urls.items():
            if not source or not urls:
                continue
            rows = conn.execute("SELECT id, url, status FROM jobs WHERE source=?", (source,)).fetchall()
            for row in rows:
                if row["url"] in urls:
                    continue
                if row["status"] in {"new", "saved", "shortlisted"}:
                    conn.execute(
                        "UPDATE jobs SET status='closed', last_seen_at=? WHERE id=?",
                        (now_iso(), row["id"]),
                    )
                    closed += 1
        # Acknowledge imported Gmail messages in the same transaction as their
        # jobs. A failed write must leave the messages eligible for a retry.
        for message_id in {job["_gmail_message_id"] for job in fetched if job.get("_gmail_message_id")}:
            conn.execute(
                "INSERT OR IGNORE INTO gmail_seen_messages(message_id, seen_at) VALUES (?,?)",
                (message_id, now_iso()),
            )
        conn.execute(
            "INSERT INTO hunt_runs(started_at,finished_at,sources_checked,fetched,new_jobs,errors) VALUES (?,?,?,?,?,?)",
            (started, now_iso(), len(reports), len(fetched), inserted, json.dumps(errors)),
        )
    try:
        if gulf_only:
            previous = get_source_report() or {}
            reports = [r for r in previous.get("sources", []) if r.get("name") != "Gulf employer feeds"] + reports
            portal_counts = previous.get("portal_counts", {})
        persist_source_report(reports, portal_counts)
    except Exception:
        log.exception("Could not persist source report")
    notify_result = {"sent": False, "skipped": True}
    try:
        from notify import notify_high_match_jobs
        if not gulf_only:
            notify_result = await notify_high_match_jobs(notify_jobs)
    except Exception as e:
        summary = _safe_source_error(e)
        log.warning("Job notification failed: %s", summary)
        notify_result = {"sent": False, "error": summary}
    return {
        "status": "completed",
        "started_at": started,
        "finished_at": now_iso(),
        "fetched": len(fetched),
        "new_jobs": inserted,
        "updated_jobs": updated,
        "closed_jobs": closed,
        "sources": sorted(sources_touched),
        "source_report": reports,
        "errors": errors,
        "notification": notify_result,
        "live_applications_enabled": False,
    }


def list_jobs(
    country=None,
    opportunity=None,
    min_score=0,
    status=None,
    posted_within_days=None,
    limit=100,
    offset=0,
    q=None,
    source=None,
    draft_candidates=False,
    gulf_open=False,
):
    init_db()
    settings = get_settings()
    tracked = status in {"saved", "shortlisted", "draft_ready", "applied_manually", "interview", "offer", "rejected", "not_relevant", "closed"}
    if gulf_open:
        allowed = [country] if country else GULF_COUNTRIES
    elif country:
        allowed = [country]
    else:
        allowed = LISTABLE_COUNTRIES if tracked else settings["target_countries"]
    placeholders = ",".join("?" for _ in allowed)
    clauses = ["match_score >= ?", "country IN (" + placeholders + ")"]
    params: list[Any] = [min_score, *allowed]
    if draft_candidates:
        clauses.append("status IN ('new','saved','shortlisted')")
        clauses.append("NOT EXISTS (SELECT 1 FROM application_drafts WHERE application_drafts.job_id=jobs.id)")
    if status:
        clauses.append("status = ?")
        params.append(status)
    if source:
        clauses.append("lower(source) LIKE ?")
        params.append("%" + source.strip().lower() + "%")
    if posted_within_days is None:
        posted_within_days = 0 if status in {"saved", "shortlisted", "draft_ready", "applied_manually", "interview", "offer", "rejected", "not_relevant", "closed"} else settings.get("posted_within_days", 3)
    if gulf_open:
        posted_within_days = 0
        if not status:
            clauses.append("status NOT IN ('closed','not_relevant','rejected')")
    if posted_within_days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(posted_within_days))).isoformat()
        clauses.append("posted_at IS NOT NULL AND posted_at >= ?")
        params.append(cutoff)
    if q:
        for term in q.split():
            term = term.strip()
            if not term:
                continue
            clauses.append(
                "(lower(title) LIKE ? OR lower(company) LIKE ? OR lower(location) LIKE ? OR lower(source) LIKE ?)"
            )
            like = "%" + term.lower() + "%"
            params.extend([like, like, like, like])
    if opportunity:
        opp = opportunity.lower()
        if opp == "high demand":
            clauses.append("match_score >= 65")
        elif opp == "rising":
            clauses.append("match_score >= 45 AND match_score < 65")
    sql = (
        "SELECT * FROM jobs WHERE "
        + " AND ".join(clauses)
        + " ORDER BY match_score DESC, COALESCE(posted_at, first_seen_at) DESC"
    )
    rows, eligible = [], 0
    if gulf_open:
        from gulf_sources import current_evidence, fit, evidence
        from application_workflow import get_profile_full
        profile = get_profile_full()
    with connect() as conn:
        for record in conn.execute(sql, params):
            row = dict(record)
            if gulf_open:
                suitable, notes = fit(row, profile, settings)
                if not current_evidence(row) or not suitable:
                    continue
                row["gulf_evidence"] = evidence(row)
                row["fit_notes"] = notes
            elif not tracked and (not is_relevant_role(row) or not matches_preferences(row, settings)):
                continue
            if eligible >= offset:
                rows.append(row)
            eligible += 1
            if limit is not None and len(rows) >= limit:
                break
    for row in rows:
        try:
            row["match_reasons"] = json.loads(row.get("match_reasons") or "[]")
        except (TypeError, json.JSONDecodeError):
            row["match_reasons"] = []
        row.pop("raw_json", None)
        row.pop("description", None)
    return rows


def get_stats(**filters):
    from collections import Counter
    settings = get_settings()
    countries, sources = Counter(), Counter()
    total = new_count = high = 0
    with connect() as conn:
        # Share the list's eligibility, evidence expiry and selected filters.
        # No pagination: summary counts include every matching row.
        for job in list_jobs(limit=None, **filters):
            total += 1
            new_count += job["status"] == "new"
            high += job["match_score"] >= 65
            countries[job["country"]] += 1
            sources[job["source"]] += 1
        last = conn.execute("SELECT * FROM hunt_runs ORDER BY id DESC LIMIT 1").fetchone()
    return {
        "total_jobs": total, "new_jobs": new_count, "high_match_jobs": high,
        "last_run": dict(last) if last else None,
        "by_country": [{"country": name, "count": count} for name, count in countries.most_common()],
        "by_source": [{"source": name, "count": count} for name, count in sources.most_common()],
        "posted_within_days": filters.get("posted_within_days") or settings.get("posted_within_days", 3),
        "freshness_scope": "published_checked_36h" if filters.get("gulf_open") else "posting_window",
        "live_applications_enabled": False,
    }


def set_job_status(job_id, status):
    init_db()
    from application_workflow import init_workflow_db
    init_workflow_db()
    with connect() as conn:
        cur = conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
        if cur.rowcount == 0:
            return {"updated": False, "error": "job not found"}
        draft_status = {"draft_ready": "prepared", "applied_manually": "applied_manually", "interview": "interview", "rejected": "rejected", "offer": "offer", "not_relevant": "withdrawn", "closed": "withdrawn"}.get(status)
        if draft_status:
            conn.execute("UPDATE application_drafts SET status=?, updated_at=? WHERE job_id=?", (draft_status, now_iso(), job_id))
    return {"updated": True, "id": job_id, "status": status}
