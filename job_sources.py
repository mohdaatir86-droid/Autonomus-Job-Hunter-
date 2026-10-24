"""Fetch public job sources and orchestrate hunt runs."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

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

# Countries allowed in list/stats by default (target markets + unspecified for review)
LISTABLE_COUNTRIES = list(DEFAULT_COUNTRIES) + ["Remote / Unspecified", "Unspecified"]


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
    url = normalize_text(job.get("url") or job.get("job_url") or job.get("apply_url"))
    if not url.startswith(("http://", "https://")):
        return ""
    return url


def _clean_job(raw, source):
    title = normalize_text(raw.get("title") or raw.get("position"))
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
                or ""
            ),
        )
    )
    url = _job_url(raw)
    if not title or not url:
        return None
    return {
        "source": source,
        "source_id": str(raw.get("id") or raw.get("slug") or ""),
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
        "posted_at": _parse_iso(
            raw.get("publication_date") or raw.get("published_at") or raw.get("createdAt") or raw.get("date")
        ),
        "raw_json": json.dumps(raw, ensure_ascii=False)[:20000],
    }


async def _get_json(client, url, params=None):
    response = await client.get(url, params=params, timeout=25, follow_redirects=True)
    response.raise_for_status()
    return response.json()


async def fetch_public_sources(settings):
    results, errors = [], []
    limit = max(10, min(250, int(settings.get("max_results_per_source", 100))))
    async with httpx.AsyncClient(headers={"User-Agent": "AutonomousJobHunter/0.2"}) as client:
        try:
            data = await _get_json(client, "https://www.arbeitnow.com/api/job-board-api")
            for raw in (data.get("data") or data.get("jobs") or [])[:limit]:
                job = _clean_job(raw if isinstance(raw, dict) else {}, "Arbeitnow")
                if job:
                    results.append(job)
        except Exception as e:
            log.exception("Arbeitnow failed")
            errors.append("Arbeitnow: " + str(e)[:200])
        try:
            data = await _get_json(client, "https://remotive.com/api/remote-jobs")
            for raw in (data.get("jobs") or [])[:limit]:
                job = _clean_job(raw if isinstance(raw, dict) else {}, "Remotive")
                if job:
                    results.append(job)
        except Exception as e:
            log.exception("Remotive failed")
            errors.append("Remotive: " + str(e)[:200])
        try:
            data = await _get_json(client, "https://jobicy.com/api/v2/remote-jobs", params={"count": limit})
            for raw in (data.get("jobs") or [])[:limit]:
                job = _clean_job(raw if isinstance(raw, dict) else {}, "Jobicy")
                if job:
                    results.append(job)
        except Exception as e:
            log.exception("Jobicy failed")
            errors.append("Jobicy: " + str(e)[:200])
    return results, errors


async def hunt_jobs():
    started = now_iso()
    settings = get_settings()
    init_db()
    fetched, errors = await fetch_public_sources(settings)
    inserted = updated = 0
    with connect() as conn:
        for job in fetched:
            score, reasons, country = score_job(job, settings)
            if country not in LISTABLE_COUNTRIES:
                continue
            job["match_score"] = score
            job["match_reasons"] = json.dumps(reasons, ensure_ascii=False)
            job["country"] = country
            existing = conn.execute("SELECT id FROM jobs WHERE url=?", (job["url"],)).fetchone()
            if existing:
                conn.execute(
                    """UPDATE jobs SET title=?,company=?,location=?,country=?,description=?,
                    employment_type=?,salary=?,posted_at=?,match_score=?,match_reasons=?,
                    last_seen_at=?,raw_json=? WHERE id=?""",
                    (
                        job["title"], job["company"], job["location"], country, job["description"],
                        job["employment_type"], job["salary"], job["posted_at"], score,
                        job["match_reasons"], now_iso(), job["raw_json"], existing["id"],
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
        conn.execute(
            "INSERT INTO hunt_runs(started_at,finished_at,sources_checked,fetched,new_jobs,errors) VALUES (?,?,?,?,?,?)",
            (started, now_iso(), 3, len(fetched), inserted, json.dumps(errors)),
        )
    return {
        "status": "completed",
        "started_at": started,
        "finished_at": now_iso(),
        "fetched": len(fetched),
        "new_jobs": inserted,
        "updated_jobs": updated,
        "errors": errors,
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
    clauses = ["match_score >= ?", "country IN (" + placeholders + ")"]
    params: list[Any] = [min_score, *allowed]
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
            clauses.append("(lower(title) LIKE ? OR lower(company) LIKE ? OR lower(location) LIKE ?)")
            like = "%" + term.lower() + "%"
            params.extend([like, like, like])
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
            )
        ]
        by_source = [
            dict(row)
            for row in conn.execute(
                "SELECT source, COUNT(*) AS count FROM jobs WHERE country IN ("
                + placeholders
                + ") GROUP BY source ORDER BY count DESC",
                allowed,
            )
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
