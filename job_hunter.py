import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

log = logging.getLogger("job-hunter")
DB_PATH = os.getenv("DATABASE_PATH", "job_hunter.db")
DEFAULT_COUNTRIES = ["India", "United Arab Emirates", "Saudi Arabia", "Qatar", "Kuwait", "Oman", "Canada"]
DEFAULT_TITLES = [
    "Senior Cluster Manager", "City Operations Manager", "Operations Manager",
    "Program Manager", "Head of Operations", "Warehouse Manager",
    "Fulfilment Manager", "Fulfillment Manager", "Regional Operations Manager",
    "Supply Chain Manager", "Dark Store Manager", "Last Mile Operations Manager"
]
DEFAULT_SKILLS = [
    "operations", "cluster management", "city operations", "warehouse", "fulfilment",
    "fulfillment", "supply chain", "quick commerce", "q-commerce", "dark store",
    "last mile", "logistics", "inventory", "sla", "kpi", "multi-unit", "team leadership",
    "store launch", "vendor management", "3pl", "process improvement", "program management"
]
COUNTRY_ALIASES = {
    "india": ["india", "new delhi", "delhi", "gurugram", "gurgaon", "noida", "mumbai", "bengaluru", "bangalore", "hyderabad", "pune", "chennai"],
    "united arab emirates": ["united arab emirates", "uae", "dubai", "abu dhabi", "sharjah"],
    "saudi arabia": ["saudi arabia", "riyadh", "jeddah", "dammam", "khobar"],
    "qatar": ["qatar", "doha"],
    "kuwait": ["kuwait", "kuwait city"],
    "oman": ["oman", "muscat"],
    "canada": ["canada", "toronto", "vancouver", "calgary", "montreal", "ottawa", "edmonton"]
}
DEFAULT_SETTINGS = {
    "target_countries": DEFAULT_COUNTRIES,
    "target_titles": DEFAULT_TITLES,
    "min_match_score": 35,
    "max_results_per_source": 100
}

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with connect() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            source_id TEXT,
            title TEXT NOT NULL,
            company TEXT,
            location TEXT,
            country TEXT,
            url TEXT NOT NULL UNIQUE,
            description TEXT,
            employment_type TEXT,
            salary TEXT,
            posted_at TEXT,
            match_score INTEGER NOT NULL DEFAULT 0,
            match_reasons TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'new',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            raw_json TEXT
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(match_score DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_country ON jobs(country)")
        conn.execute("CREATE TABLE IF NOT EXISTS hunt_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT, sources_checked INTEGER, fetched INTEGER, new_jobs INTEGER, errors TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (key, json.dumps(value)))

def get_settings():
    init_db()
    with connect() as conn:
        values = {row["key"]: json.loads(row["value"]) for row in conn.execute("SELECT key,value FROM settings")}
    return {**DEFAULT_SETTINGS, **values, "live_applications_enabled": False, "hunt_interval_minutes": max(15, int(os.getenv("HUNT_INTERVAL_MINUTES", "15")))}

def save_settings(values):
    init_db()
    allowed = set(DEFAULT_SETTINGS)
    with connect() as conn:
        for key in allowed:
            if key in values:
                conn.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(values[key])))
    return get_settings()

def normalize_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()

def detect_country(location, title="", description=""):
    haystack = " ".join([str(location or ""), str(title or ""), str(description or "")]).lower()
    # Do not infer a country from a remote/anywhere label alone.
    for country, aliases in COUNTRY_ALIASES.items():
        if any(alias in haystack for alias in aliases):
            return country.title() if country != "united arab emirates" else "United Arab Emirates"
    return "Remote / Unspecified" if any(x in haystack for x in ["remote", "worldwide", "anywhere"]) else "Unspecified"

def score_job(job, settings):
    title = normalize_text(job.get("title")).lower()
    company = normalize_text(job.get("company")).lower()
    location = normalize_text(job.get("location")).lower()
    description = normalize_text(job.get("description")).lower()
    text = " ".join([title, company, location, description])
    score = 0
    reasons = []
    titles = [str(x).lower() for x in settings.get("target_titles", DEFAULT_TITLES)]
    title_hits = [t for t in titles if t in title]
    if title_hits:
        score += 42
        reasons.append("Target role: " + title_hits[0])
    elif any(term in title for term in ["operations", "logistics", "warehouse", "fulfil", "fulfill", "supply chain", "cluster", "program manager"]):
        score += 25
        reasons.append("Related operations leadership title")
    else:
        score += 5
    skill_hits = [s for s in DEFAULT_SKILLS if s in text]
    skill_points = min(30, len(skill_hits) * 3)
    score += skill_points
    if skill_hits:
        reasons.append("Relevant skills: " + ", ".join(skill_hits[:5]))
    country = job.get("country") or detect_country(location, job.get("title"), description)
    countries = [str(c).lower() for c in settings.get("target_countries", DEFAULT_COUNTRIES)]
    country_match = country.lower() in countries
    if country_match:
        score += 20
        reasons.append("Target country")
    elif country == "Remote / Unspecified":
        score += 8
        reasons.append("Remote or location not clearly specified")
    if any(x in text for x in ["senior", "lead", "head of", "regional", "director", "manager"]):
        score += 8
        reasons.append("Leadership-level signal")
    if any(x in text for x in ["intern", "internship", "junior", "entry level", "graduate trainee"]):
        score -= 25
        reasons.append("Seniority mismatch")
    return max(0, min(100, score)), reasons, country

def _parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).isoformat()
    except Exception:
        return str(value)[:80]

def _job_url(job):
    url = normalize_text(job.get("url") or job.get("job_url") or job.get("apply_url"))
    if not url.startswith(("http://", "https://")):
        return ""
    return url

def _clean_job(raw, source):
    title = normalize_text(raw.get("title") or raw.get("position"))
    company_obj = raw.get("company")
    company = normalize_text(company_obj.get("name") if isinstance(company_obj, dict) else company_obj or raw.get("company_name") or raw.get("companyName"))
    location_obj = raw.get("location")
    if isinstance(location_obj, dict):
        location = normalize_text(", ".join(str(v) for v in location_obj.values() if v))
    elif isinstance(location_obj, list):
        location = normalize_text(", ".join(str(v) for v in location_obj if v))
    else:
        location = normalize_text(location_obj or raw.get("candidate_required_location") or raw.get("jobLocation") or raw.get("workplace"))
    description = normalize_text(re.sub(r"<[^>]+>", " ", str(raw.get("description") or raw.get("descriptionPlain") or raw.get("jobDescription") or raw.get("snippet") or "")))
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
        "employment_type": normalize_text(raw.get("job_type") or raw.get("employment_type") or raw.get("contractType") or raw.get("type"))[:100],
        "salary": normalize_text(raw.get("salary") or raw.get("salaryRange") or raw.get("salary_range"))[:200],
        "posted_at": _parse_iso(raw.get("publication_date") or raw.get("published_at") or raw.get("createdAt") or raw.get("date")),
        "raw_json": json.dumps(raw, ensure_ascii=False)[:20000]
    }

async def _get_json(client, url, params=None):
    response = await client.get(url, params=params, timeout=25, follow_redirects=True)
    response.raise_for_status()
    return response.json()

async def fetch_public_sources(settings):
    results = []
    errors = []
    limit = max(10, min(250, int(settings.get("max_results_per_source", 100))))
    async with httpx.AsyncClient(headers={"User-Agent": "AutonomousJobHunter/0.1 (job discovery; contact configured by operator)"}) as client:
        # Arbeitnow public job API
        try:
            data = await _get_json(client, "https://www.arbeitnow.com/api/job-board-api")
            rows = data.get("data", []) if isinstance(data, dict) else []
            for row in rows[:limit]:
                item = _clean_job({
                    "id": row.get("slug"), "title": row.get("title"), "company": row.get("company_name"),
                    "location": row.get("location"), "url": row.get("url"), "description": row.get("description"),
                    "job_type": ", ".join(row.get("job_types", []) if isinstance(row.get("job_types"), list) else []),
                    "publication_date": row.get("created_at")
                }, "Arbeitnow")
                if item: results.append(item)
        except Exception as exc:
            errors.append({"source": "Arbeitnow", "error": str(exc)[:300]})
        # Remotive public API
        try:
            data = await _get_json(client, "https://remotive.com/api/remote-jobs", {"limit": limit})
            rows = data.get("jobs", []) if isinstance(data, dict) else []
            for row in rows:
                item = _clean_job({
                    "id": row.get("id"), "title": row.get("title"), "company": row.get("company_name"),
                    "location": row.get("candidate_required_location") or "Remote", "url": row.get("url"),
                    "description": row.get("description"), "job_type": row.get("job_type"),
                    "publication_date": row.get("publication_date"), "salary": row.get("salary")
                }, "Remotive")
                if item: results.append(item)
        except Exception as exc:
            errors.append({"source": "Remotive", "error": str(exc)[:300]})
        # Jobicy public API. Use one page; avoid aggressive polling.
        try:
            data = await _get_json(client, "https://jobicy.com/api/v2/remote-jobs", {"count": min(50, limit)})
            rows = data.get("jobs", []) if isinstance(data, dict) else []
            for row in rows:
                item = _clean_job({
                    "id": row.get("id"), "title": row.get("jobTitle"), "company": row.get("companyName"),
                    "location": row.get("jobGeo") or "Remote", "url": row.get("url"),
                    "description": row.get("jobDescription"), "job_type": row.get("jobType"),
                    "publication_date": row.get("pubDate"), "salary": row.get("annualSalary")
                }, "Jobicy")
                if item: results.append(item)
        except Exception as exc:
            errors.append({"source": "Jobicy", "error": str(exc)[:300]})
        # Public company job boards configured by the operator.
        greenhouse_boards = [x.strip() for x in os.getenv("GREENHOUSE_BOARDS", "").split(",") if x.strip()]
        for board in greenhouse_boards[:30]:
            try:
                data = await _get_json(client, "https://boards-api.greenhouse.io/v1/boards/" + board + "/jobs", {"content": "true"})
                for row in data.get("jobs", [])[:limit]:
                    loc = (row.get("location") or {}).get("name", "")
                    item = _clean_job({"id": row.get("id"), "title": row.get("title"), "company": board,
                        "location": loc, "url": row.get("absolute_url"), "description": row.get("content"),
                        "publication_date": row.get("updated_at")}, "Greenhouse:" + board)
                    if item: results.append(item)
            except Exception as exc:
                errors.append({"source": "Greenhouse:" + board, "error": str(exc)[:300]})
        lever_sites = [x.strip() for x in os.getenv("LEVER_SITES", "").split(",") if x.strip()]
        for site in lever_sites[:30]:
            try:
                data = await _get_json(client, "https://api.lever.co/v0/postings/" + site, {"mode": "json"})
                for row in data[:limit] if isinstance(data, list) else []:
                    cats = row.get("categories") or {}
                    loc = cats.get("location", "") if isinstance(cats, dict) else ""
                    item = _clean_job({"id": row.get("id"), "title": row.get("text"), "company": site,
                        "location": loc, "url": row.get("hostedUrl") or row.get("applyUrl"),
                        "description": row.get("descriptionPlain") or row.get("description"),
                        "employment_type": cats.get("commitment", "") if isinstance(cats, dict) else "",
                        "publication_date": row.get("createdAt")}, "Lever:" + site)
                    if item: results.append(item)
            except Exception as exc:
                errors.append({"source": "Lever:" + site, "error": str(exc)[:300]})
    return results, errors

async def hunt_jobs():
    init_db()
    settings = get_settings()
    started = now_iso()
    try:
        fetched, errors = await fetch_public_sources(settings)
    except Exception as exc:
        fetched, errors = [], [{"source": "system", "error": str(exc)[:300]}]
    inserted = 0
    updated = 0
    with connect() as conn:
        for job in fetched:
            score, reasons, country = score_job(job, settings)
            job["match_score"] = score
            job["match_reasons"] = json.dumps(reasons, ensure_ascii=False)
            job["country"] = country
            existing = conn.execute("SELECT id, status FROM jobs WHERE url=?", (job["url"],)).fetchone()
            if existing:
                conn.execute("""UPDATE jobs SET title=?,company=?,location=?,country=?,description=?,employment_type=?,salary=?,posted_at=?,match_score=?,match_reasons=?,last_seen_at=?,raw_json=? WHERE id=?""",
                    (job["title"], job["company"], job["location"], country, job["description"], job["employment_type"], job["salary"], job["posted_at"], score, job["match_reasons"], now_iso(), job["raw_json"], existing["id"]))
                updated += 1
            else:
                conn.execute("""INSERT INTO jobs(source,source_id,title,company,location,country,url,description,employment_type,salary,posted_at,match_score,match_reasons,status,first_seen_at,last_seen_at,raw_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'new',?,?,?)""",
                    (job["source"], job["source_id"], job["title"], job["company"], job["location"], country, job["url"], job["description"], job["employment_type"], job["salary"], job["posted_at"], score, job["match_reasons"], now_iso(), now_iso(), job["raw_json"]))
                inserted += 1
        conn.execute("INSERT INTO hunt_runs(started_at,finished_at,sources_checked,fetched,new_jobs,errors) VALUES (?,?,?,?,?,?)",
            (started, now_iso(), 3 + len([x for x in os.getenv("GREENHOUSE_BOARDS", "").split(",") if x.strip()]) + len([x for x in os.getenv("LEVER_SITES", "").split(",") if x.strip()]), len(fetched), inserted, json.dumps(errors)))
    return {"status": "completed", "started_at": started, "finished_at": now_iso(), "fetched": len(fetched), "new_jobs": inserted, "updated_jobs": updated, "errors": errors, "live_applications_enabled": False}

def list_jobs(country=None, opportunity=None, min_score=0, status=None, limit=100, offset=0):
    init_db()
    clauses = ["match_score >= ?"]
    params: list[Any] = [min_score]
    if country:
        clauses.append("lower(country) = lower(?)")
        params.append(country)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if opportunity and opportunity.lower() != "all":
        if opportunity.lower() == "high demand":
            clauses.append("match_score >= 65")
        elif opportunity.lower() == "rising":
            clauses.append("match_score >= 45 AND match_score < 65")
        elif opportunity.lower() == "early viral":
            # No trustworthy historical velocity data yet; do not mislabel as viral.
            clauses.append("1=0")
    sql = "SELECT * FROM jobs WHERE " + " AND ".join(clauses) + " ORDER BY match_score DESC, COALESCE(posted_at, first_seen_at) DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    with connect() as conn:
        rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
    for row in rows:
        row["match_reasons"] = json.loads(row.get("match_reasons") or "[]")
        row.pop("raw_json", None)
        row.pop("description", None) if not row.get("description") else None
    return rows

def get_stats():
    init_db()
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        new_count = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='new'").fetchone()[0]
        high = conn.execute("SELECT COUNT(*) FROM jobs WHERE match_score >= 65").fetchone()[0]
        last = conn.execute("SELECT * FROM hunt_runs ORDER BY id DESC LIMIT 1").fetchone()
        by_country = [dict(row) for row in conn.execute("SELECT country, COUNT(*) AS count FROM jobs GROUP BY country ORDER BY count DESC LIMIT 12")]
        by_source = [dict(row) for row in conn.execute("SELECT source, COUNT(*) AS count FROM jobs GROUP BY source ORDER BY count DESC")]
    return {"total_jobs": total, "new_jobs": new_count, "high_match_jobs": high, "last_run": dict(last) if last else None, "by_country": by_country, "by_source": by_source, "live_applications_enabled": False}

def set_job_status(job_id, status):
    init_db()
    with connect() as conn:
        cur = conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
        if cur.rowcount == 0:
            return {"updated": False, "error": "job not found"}
    return {"updated": True, "id": job_id, "status": status}
