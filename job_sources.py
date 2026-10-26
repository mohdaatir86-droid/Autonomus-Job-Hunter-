"""Fetch public job sources and orchestrate hunt runs."""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

import httpx

from job_hunter import (
    connect,
    init_db,
    get_settings,
    score_job,
    normalize_text,
    detect_country,
    now_iso,
    DEFAULT_COUNTRIES,
)

log = logging.getLogger("job-hunter")

GULF_COUNTRIES = ["United Arab Emirates", "Saudi Arabia", "Qatar", "Kuwait", "Oman"]
LISTABLE_COUNTRIES = ["India"] + GULF_COUNTRIES + ["Canada"]

INDIA_TARGET_LOCATION_TERMS = (
    "delhi", "ncr", "gurgaon", "gurugram", "noida", "greater noida",
    "dehradun", "uttar pradesh", "uttarakhand", "chandigarh",
    "ghaziabad", "faridabad", "manesar",
)

def is_target_market_job(job, country=None):
    location = normalize_text(job.get("location")).lower()
    market = country or detect_country(location, job.get("title", ""), job.get("description", ""))
    if market in GULF_COUNTRIES or market == "Canada":
        return True
    if market != "India":
        return False
    if any(term in location for term in INDIA_TARGET_LOCATION_TERMS):
        return True
    import re
    return bool(re.search(r"(^|[^a-z])u\.?p\.?([^a-z]|$)", location))

SERPAPI_QUERIES = [
    "Cluster Manager India",
    "Operations Manager QSR India",
    "Dark Store Manager India",
    "City Operations Manager India OR Noida OR Gurugram",
    "Warehouse Manager OR Fulfilment Manager India",
    "Regional Operations Manager India",
    "Multi Unit Operations Manager India",
    "Q-commerce OR quick commerce Operations Manager India",
    "Cluster Manager UAE OR Dubai OR Riyadh OR Qatar",
    "Operations Manager Gulf OR UAE OR Saudi Arabia",
    "Operations Manager Qatar OR Kuwait OR Oman",
    "Senior Cluster Manager Canada OR Toronto OR Vancouver",
    "City Operations Manager Canada OR Toronto OR Calgary",
    "Program Manager Operations Supply Chain Canada OR UAE OR Saudi Arabia",
]


def _parse_iso(value):
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
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return str(value)[:80]


def _job_url(job):
    url = normalize_text(job.get("url") or job.get("job_url") or job.get("apply_url") or job.get("link"))
    if not url.startswith(("http://", "https://")):
        return ""
    return url


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


async def fetch_arbeitnow(client, limit):
    data = await _get_json(client, "https://www.arbeitnow.com/api/job-board-api")
    out = []
    for raw in (data.get("data") or data.get("jobs") or [])[:limit]:
        job = _clean_job(raw if isinstance(raw, dict) else {}, "Arbeitnow")
        if job:
            out.append(job)
    return out


async def fetch_remotive(client, limit):
    data = await _get_json(client, "https://remotive.com/api/remote-jobs")
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
    # Balance scheduled searches across India and Gulf target markets.
    # The old first-three slice only searched India, excluding Gulf roles.
    queries = SERPAPI_QUERIES[:3] + SERPAPI_QUERIES[8:10] + SERPAPI_QUERIES[5:6]
    per_q = max(5, min(20, limit // max(1, len(queries))))
    for q in queries:
        try:
            data = await _get_json(
                client,
                "https://serpapi.com/search.json",
                params={"engine": "google_jobs", "q": q, "hl": "en", "api_key": api_key},
            )
            for raw in (data.get("jobs_results") or [])[:per_q]:
                apply_options = raw.get("apply_options") or []
                link = raw.get("share_link") or raw.get("link") or ""
                if not link and apply_options:
                    link = apply_options[0].get("link") or ""
                for opt in apply_options:
                    l = (opt.get("link") or "").lower()
                    if any(x in l for x in ("naukri", "linkedin", "shine", "monster", "bayt", "gulftalent")):
                        link = opt.get("link") or link
                        break
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
                    via = normalize_text(raw.get("via") or "")
                    if via:
                        job["source"] = f"Google Jobs / {via}"[:80]
                    out.append(job)
        except Exception as e:
            summary = _safe_source_error(e)
            log.warning("SerpApi query failed (%s): %s", q[:40], summary)
            errors.append(f"SerpApi[{q[:40]}]: {summary}")
    return out, ("; ".join(errors) if errors else None)


async def fetch_greenhouse(client, limit):
    boards = [b.strip() for b in os.getenv("GREENHOUSE_BOARDS", "").split(",") if b.strip()]
    if not boards:
        return [], None
    out, errors = [], []
    for board in boards[:15]:
        try:
            data = await _get_json(
                client,
                f"https://boards-api.greenhouse.io/v1/boards/{quote_plus(board)}/jobs",
                params={"content": "true"},
            )
            for raw in (data.get("jobs") or [])[:limit]:
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
                    "date": raw.get("updated_at") or raw.get("created_at"),
                }
                job = _clean_job(normalized, f"Greenhouse/{board}")
                if job:
                    out.append(job)
        except Exception as e:
            summary = _safe_source_error(e)
            log.warning("Greenhouse board %s failed: %s", board, summary)
            errors.append(f"Greenhouse/{board}: {summary}")
    return out, ("; ".join(errors) if errors else None)


async def fetch_lever(client, limit):
    sites = [s.strip() for s in os.getenv("LEVER_SITES", "").split(",") if s.strip()]
    if not sites:
        return [], None
    out, errors = [], []
    for site in sites[:15]:
        try:
            data = await _get_json(client, f"https://api.lever.co/v0/postings/{quote_plus(site)}")
            rows = data if isinstance(data, list) else data.get("data") or []
            for raw in rows[:limit]:
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
                    out.append(job)
        except Exception as e:
            summary = _safe_source_error(e)
            log.warning("Lever board %s failed: %s", site, summary)
            errors.append(f"Lever/{site}: {summary}")
    return out, ("; ".join(errors) if errors else None)


async def fetch_public_sources(settings):
    results, errors = [], []
    limit = max(10, min(250, int(settings.get("max_results_per_source", 100))))
    async with httpx.AsyncClient(headers={"User-Agent": "AutonomousJobHunter/0.4"}) as client:
        for name, coro in [
            ("Arbeitnow", fetch_arbeitnow(client, limit)),
            ("Remotive", fetch_remotive(client, limit)),
            ("Jobicy", fetch_jobicy(client, limit)),
        ]:
            try:
                results.extend(await coro)
            except Exception as e:
                summary = _safe_source_error(e)
                log.warning("%s source failed: %s", name, summary)
                errors.append(f"{name}: {summary}")

        serps, serr = await fetch_serpapi_google_jobs(client, limit)
        results.extend(serps)
        if serr:
            errors.append(serr)

        gh, gerr = await fetch_greenhouse(client, limit)
        results.extend(gh)
        if gerr:
            errors.append(gerr)

        lv, lerr = await fetch_lever(client, limit)
        results.extend(lv)
        if lerr:
            errors.append(lerr)

        try:
            from gmail_alerts import fetch_jobs_from_gmail

            gmail_jobs, gmail_err = await fetch_jobs_from_gmail()
            results.extend(gmail_jobs)
            if gmail_err:
                errors.append(gmail_err)
        except Exception as e:
            summary = _safe_source_error(e)
            log.warning("Gmail alerts failed: %s", summary)
            errors.append("Gmail: " + summary)

    return results, errors


async def hunt_jobs():
    started = now_iso()
    settings = get_settings()
    init_db()
    fetched, errors = await fetch_public_sources(settings)
    inserted = updated = 0
    sources_touched = set()
    notify_jobs = []
    with connect() as conn:
        for job in fetched:
            score, reasons, country = score_job(job, settings)
            if country not in LISTABLE_COUNTRIES or not is_target_market_job(job, country):
                continue
            sources_touched.add(job.get("source") or "?")
            job["match_score"] = score
            job["match_reasons"] = json.dumps(reasons, ensure_ascii=False)
            job["country"] = country
            existing = conn.execute("SELECT id FROM jobs WHERE url=?", (job["url"],)).fetchone()
            if existing:
                conn.execute(
                    """UPDATE jobs SET title=?,company=?,location=?,country=?,description=?,
                    employment_type=?,salary=?,posted_at=?,match_score=?,match_reasons=?,
                    last_seen_at=?,raw_json=?,source=? WHERE id=?""",
                    (
                        job["title"], job["company"], job["location"], country, job["description"],
                        job["employment_type"], job["salary"], job["posted_at"], score,
                        job["match_reasons"], now_iso(), job["raw_json"], job["source"], existing["id"],
                    ),
                )
                updated += 1
            else:
                conn.execute(
                    """INSERT INTO jobs(source,source_id,title,company,location,country,url,description,
                    employment_type,salary,posted_at,match_score,match_reasons,status,first_seen_at,last_seen_at,raw_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'new',?,?,?)""",
                    (
                        job["source"], job["source_id"], job["title"], job["company"], job["location"],
                        country, job["url"], job["description"], job["employment_type"], job["salary"],
                        job["posted_at"], score, job["match_reasons"], now_iso(), now_iso(), job["raw_json"],
                    ),
                )
                inserted += 1
                min_wa = int(os.getenv("WHATSAPP_MIN_SCORE", "70"))
                if score >= min_wa:
                    notify_jobs.append({
                        "title": job["title"], "company": job["company"], "location": job["location"],
                        "country": country, "url": job["url"], "match_score": score,
                    })
        conn.execute(
            "INSERT INTO hunt_runs(started_at,finished_at,sources_checked,fetched,new_jobs,errors) VALUES (?,?,?,?,?,?)",
            (started, now_iso(), len(sources_touched), len(fetched), inserted, json.dumps(errors)),
        )
    notify_result = {"sent": False, "skipped": True}
    try:
        from notify import notify_high_match_jobs
        notify_result = await notify_high_match_jobs(notify_jobs)
    except Exception as e:
        summary = _safe_source_error(e)
        log.warning("WhatsApp notify failed: %s", summary)
        notify_result = {"sent": False, "error": summary}
    return {
        "status": "completed",
        "started_at": started,
        "finished_at": now_iso(),
        "fetched": len(fetched),
        "new_jobs": inserted,
        "updated_jobs": updated,
        "sources": sorted(sources_touched),
        "errors": errors,
        "whatsapp_notify": notify_result,
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
):
    init_db()
    if country:
        allowed = [country]
    else:
        allowed = LISTABLE_COUNTRIES
    placeholders = ",".join("?" for _ in allowed)
    india_location_clause = "(" + " OR ".join(["lower(location) LIKE ?" for _ in INDIA_TARGET_LOCATION_TERMS] + ["lower(location) LIKE ?"]) + ")"
    clauses = ["match_score >= ?", "country IN (" + placeholders + ")", "(country != 'India' OR " + india_location_clause + ")"]
    params: list[Any] = [min_score, *allowed, *["%" + term + "%" for term in INDIA_TARGET_LOCATION_TERMS], "%up%"]
    if status:
        clauses.append("status = ?")
        params.append(status)
    if posted_within_days:
        clauses.append("date(COALESCE(posted_at, first_seen_at)) >= date('now', ?)")
        params.append("-" + str(int(posted_within_days)) + " days")
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
        + " ORDER BY match_score DESC, COALESCE(posted_at, first_seen_at) DESC LIMIT ? OFFSET ?"
    )
    params.extend([limit, offset])
    with connect() as conn:
        rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
    for row in rows:
        try:
            row["match_reasons"] = json.loads(row.get("match_reasons") or "[]")
        except (TypeError, json.JSONDecodeError):
            row["match_reasons"] = []
        row.pop("raw_json", None)
        row.pop("description", None)
    return rows


def get_stats():
    init_db()
    allowed = tuple(LISTABLE_COUNTRIES)
    placeholders = ",".join("?" for _ in allowed)
    with connect() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE country IN (" + placeholders + ")", allowed
        ).fetchone()[0]
        new_count = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status='new' AND country IN (" + placeholders + ")", allowed
        ).fetchone()[0]
        high = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE match_score >= 65 AND country IN (" + placeholders + ")", allowed
        ).fetchone()[0]
        last = conn.execute("SELECT * FROM hunt_runs ORDER BY id DESC LIMIT 1").fetchone()
        by_country = [
            dict(row)
            for row in conn.execute(
                "SELECT country, COUNT(*) AS count FROM jobs WHERE country IN ("
                + placeholders
                + ") GROUP BY country ORDER BY count DESC",
                allowed,
            ).fetchall()
        ]
        by_source = [
            dict(row)
            for row in conn.execute(
                "SELECT source, COUNT(*) AS count FROM jobs WHERE country IN ("
                + placeholders
                + ") GROUP BY source ORDER BY count DESC",
                allowed,
            ).fetchall()
        ]
    return {
        "total_jobs": total,
        "new_jobs": new_count,
        "high_match_jobs": high,
        "last_run": dict(last) if last else None,
        "by_country": by_country,
        "by_source": by_source,
        "live_applications_enabled": False,
    }


def set_job_status(job_id, status):
    init_db()
    with connect() as conn:
        cur = conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
        if cur.rowcount == 0:
            return {"updated": False, "error": "job not found"}
    return {"updated": True, "id": job_id, "status": status}
