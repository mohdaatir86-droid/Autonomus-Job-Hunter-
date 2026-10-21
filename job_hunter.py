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
DEFAULT_COUNTRIES = ["India", "United Arab Emirates", "Saudi Arabia", "Qatar", "Kuwait", "Oman"]
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
}
DEFAULT_SETTINGS = {
    "target_countries": DEFAULT_COUNTRIES,
    "target_titles": DEFAULT_TITLES,
    "min_match_score": 45,
    "max_results_per_source": 100,
    "min_india_salary_lpa": 15,
    "min_experience_years": 8,
    "work_arrangement": "all",
    "posted_within_days": 1,
    "visa_preference": "any"
}

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def connect():
    db_path = Path(DB_PATH)
    if str(DB_PATH) != ":memory:":
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
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
        # Keep the saved profile aligned with the user's latest target-market decisions.
        # Replace old default lists containing Canada or Bahrain; preserve custom lists otherwise.
        row = conn.execute("SELECT value FROM settings WHERE key='target_countries'").fetchone()
        if row:
            try:
                saved_countries = json.loads(row["value"])
            except (TypeError, json.JSONDecodeError):
                saved_countries = []
            if any(str(country).lower() in {"canada", "bahrain"} for country in saved_countries):
                conn.execute("UPDATE settings SET value=? WHERE key='target_countries'",
                             (json.dumps(DEFAULT_COUNTRIES),))
        conn.execute("UPDATE settings SET value='15' WHERE key='min_india_salary_lpa' AND value='18'")

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
    """Classify from location fields only; never infer a job's country from its marketing copy."""
    loc = normalize_text(location).lower()
    if not loc or loc in {"remote", "worldwide", "anywhere", "remote worldwide", "global"}:
        return "Remote / Unspecified" if loc else "Unspecified"
    # Specific aliases first, and only against the location string.
    aliases = [
        ("United Arab Emirates", ["united arab emirates", "uae", "dubai", "abu dhabi", "sharjah", "ajman", "ras al khaimah"]),
        ("Saudi Arabia", ["saudi arabia", "riyadh", "jeddah", "dammam", "khobar", "mecca", "makkah", "medina", "madinah"]),
        ("Qatar", ["qatar", "doha"]),
        ("Kuwait", ["kuwait", "kuwait city"]),
        ("Oman", ["oman", "muscat", "salalah"]),
        ("Bahrain", ["bahrain", "manama"]),
        ("India", ["india", "new delhi", "delhi", "gurugram", "gurgaon", "noida", "mumbai", "bengaluru", "bangalore", "hyderabad", "pune", "chennai", "faridabad", "jaipur", "lucknow"]),
        ("United States", ["united states", "united states of america", "usa", "u.s.a.", "new york", "san francisco", "seattle", "austin", "boston", "los angeles", "chicago", "denver"]),
        ("United Kingdom", ["united kingdom", "uk", "london", "manchester", "birmingham", "edinburgh", "glasgow"]),
        ("Germany", ["germany", "deutschland", "berlin", "munich", "münchen", "hamburg", "frankfurt", "cologne", "köln", "aachen", "düsseldorf", "dusseldorf", "nuremberg"]),
        ("Australia", ["australia", "sydney", "melbourne", "brisbane", "perth", "adelaide"]),
        ("Singapore", ["singapore"]),
        ("Japan", ["japan", "tokyo", "osaka"]),
        ("France", ["france", "paris", "lyon", "marseille"]),
        ("Netherlands", ["netherlands", "amsterdam", "rotterdam", "eindhoven"]),
        ("Spain", ["spain", "madrid", "barcelona"]),
        ("Ireland", ["ireland", "dublin", "cork"]),
        ("Switzerland", ["switzerland", "zurich", "zürich", "geneva", "basel"]),
        ("Mexico", ["mexico", "mexico city"]),
        ("New Zealand", ["new zealand", "auckland", "wellington"]),
        ("Poland", ["poland", "warsaw", "krakow", "kraków"]),
        ("Brazil", ["brazil", "sao paulo", "são paulo", "rio de janeiro"]),
    ]
    padded = " " + re.sub(r"[^a-z0-9]+", " ", loc).strip() + " "
    for country, terms in aliases:
        for term in terms:
            needle = " " + re.sub(r"[^a-z0-9]+", " ", term).strip() + " "
            if needle in padded:
                return country
    if any(x in loc for x in ["remote", "worldwide", "anywhere", "work from home", "distributed"]):
        return "Remote / Unspecified"
    return "Unspecified"
def score_job(job, settings):
    """Explainable role-fit score, separated from uncertain location evidence."""
    title = normalize_text(job.get("title")).lower()
    location = normalize_text(job.get("location")).lower()
    description = normalize_text(job.get("description")).lower()
    text = " ".join([title, description])
    score = 0
    reasons = []

    # Role relevance is weighted more heavily than incidental keyword mentions.
    role_groups = [
        (["senior cluster manager", "cluster manager", "city operations manager", "city manager"], 38, "Cluster/city operations leadership"),
        (["head of operations", "director of operations", "regional operations manager", "regional operations"], 38, "Regional or head-of-operations leadership"),
        (["operations manager", "program manager", "programme manager", "general manager operations"], 34, "Operations/program management"),
        (["warehouse manager", "fulfilment manager", "fulfillment manager", "fulfillment operations", "warehouse operations manager"], 34, "Warehouse/fulfilment leadership"),
        (["supply chain manager", "logistics manager", "last mile manager", "last-mile manager", "dark store manager", "store operations manager"], 32, "Supply-chain/logistics leadership"),
        (["operations lead", "logistics lead", "warehouse lead", "fulfilment lead", "fulfillment lead"], 27, "Operations team leadership"),
    ]
    role_score = 5
    for terms, points, reason in role_groups:
        if any(term in title for term in terms):
            role_score = max(role_score, points)
            reasons.append("Target role: " + reason)
            break
    if role_score == 5 and any(term in title for term in ["operations", "logistics", "warehouse", "fulfil", "fulfill", "supply chain", "cluster", "distribution", "inventory"]):
        role_score = 20
        reasons.append("Adjacent operations role")
    # Generic technical, sales, finance, HR and marketing roles are not equivalent to operations leadership.
    domain_terms = ["warehouse", "fulfil", "fulfill", "supply chain", "logistics", "last mile", "last-mile", "dark store", "cluster", "distribution", "city operations", "store operations", "fleet", "transport", "delivery", "inventory", "quick commerce", "q-commerce"]
    non_ops_terms = ["software", "engineer", "engineering", "security", "developer", "data science", "machine learning", "finance", "accounting", "sales", "marketing", "talent acquisition", "human resources", "hr ", "social strategy", "customer evidence", "business development"]
    title_is_non_ops = any(term in title for term in non_ops_terms) and not any(term in title for term in domain_terms)
    generic_program_manager = any(term in title for term in ["program manager", "programme manager"]) and not any(term in title for term in domain_terms + ["operations"])
    if title_is_non_ops:
        role_score = min(role_score, 8)
        reasons = ["Title appears outside the target operations domain"]
    elif generic_program_manager:
        role_score = min(role_score, 16)
        reasons = ["Program-management title lacks operations/supply-chain context"]
    score += role_score

    skill_groups = [
        (["multi-site", "multi site", "multi-unit", "multi unit", "multiple locations", "multiple stores", "cluster"], 8, "Multi-site operations"),
        (["dark store", "quick commerce", "q-commerce", "last mile", "last-mile", "fulfilment", "fulfillment", "warehouse"], 8, "Relevant fulfilment/logistics domain"),
        (["team of", "team management", "people management", "leadership", "managed a team", "staff"], 6, "People leadership"),
        (["sla", "service level", "kpi", "on-time", "on time", "operational excellence", "process improvement"], 5, "KPI/SLA and process ownership"),
        (["inventory", "shrinkage", "fill rate", "cost control", "vendor management", "3pl", "capacity planning"], 5, "Inventory/cost/vendor operations"),
    ]
    for terms, points, reason in skill_groups:
        if any(term in text for term in terms):
            score += points
            reasons.append(reason)

    country = job.get("country") or detect_country(location)
    country = detect_country(location)  # authoritative location-only classification
    countries = [str(c).lower() for c in settings.get("target_countries", DEFAULT_COUNTRIES)]
    normalized_targets = set(countries)
    aliases = {
        "united arab emirates": {"united arab emirates", "uae"},
        "saudi arabia": {"saudi arabia"},
        "india": {"india"},
        "bahrain": {"bahrain"},
        "qatar": {"qatar"},
        "kuwait": {"kuwait"},
        "oman": {"oman"},
    }
    if any(country.lower() in aliases.get(target, {target}) for target in normalized_targets):
        score += 18
        reasons.append("Confirmed target-country location")
    elif country == "Remote / Unspecified":
        reasons.append("Location needs verification")
    else:
        score -= 8
        reasons.append("Location not confirmed in target markets")

    if any(x in title for x in ["senior", "lead", "head", "regional", "director", "manager", "chief"]):
        score += 8
        reasons.append("Leadership seniority")
    if any(x in title for x in ["intern", "internship", "junior", "entry level", "graduate trainee", "assistant"]):
        score -= 18
        reasons.append("Seniority mismatch")

    score = max(0, min(100, score))
    # Enforce role-domain caps AFTER every bonus so generic keywords/location cannot
    # turn an unrelated role into a strong match.
    if title_is_non_ops:
        score = min(score, 12)
    elif generic_program_manager:
        score = min(score, 24)
    # Avoid false confidence: an unknown location cannot be classified as a top match.
    if country in {"Unspecified", "Remote / Unspecified"}:
        score = min(score, 64)
    return score, reasons, country

def _parse_iso(value):
    if value is None or value == "":
        return None
    # Some public APIs return Unix timestamps instead of ISO dates.
    # Normalize seconds/milliseconds to UTC ISO strings so SQLite freshness
    # filters compare dates rather than raw epoch digits.
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

def list_jobs(country=None, opportunity=None, min_score=0, status=None, posted_within_days=None, limit=100, offset=0, q=None):
    init_db()
    clauses = ["match_score >= ?"]
    params: list[Any] = [min_score]
    if country:
        clauses.append("lower(country) = lower(?)")
        params.append(country)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if posted_within_days is not None:
        clauses.append("COALESCE(posted_at, first_seen_at) >= datetime('now', ?)")
        params.append("-" + str(int(posted_within_days)) + " days")
    if q and normalize_text(q):
        terms = [term.lower() for term in normalize_text(q).split() if len(term) > 1][:8]
        for term in terms:
            clauses.append("(lower(title) LIKE ? OR lower(company) LIKE ? OR lower(location) LIKE ?)")
            like = "%" + term.replace("%", "\\%").replace("_", "\\_") + "%"
            params.extend([like, like, like])
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
        try:
            row["match_reasons"] = json.loads(row.get("match_reasons") or "[]")
        except (TypeError, json.JSONDecodeError):
            row["match_reasons"] = []
        row.pop("raw_json", None)
        row.pop("description", None)
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
