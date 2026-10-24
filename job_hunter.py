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
    "Senior Cluster Manager", "Cluster Manager", "City Operations Manager",
    "Operations Manager", "Head of Operations", "Regional Operations Manager",
    "Warehouse Manager", "Fulfilment Manager", "Fulfillment Manager",
    "Dark Store Manager", "Supply Chain Manager", "Last Mile Operations Manager",
    "QSR Operations Manager", "Multi-Unit Operations Manager", "Area Operations Manager"
]
DEFAULT_SKILLS = [
    "operations", "cluster management", "city operations", "warehouse", "fulfilment",
    "fulfillment", "supply chain", "quick commerce", "q-commerce", "dark store",
    "last mile", "logistics", "inventory", "sla", "kpi", "multi-unit", "team leadership",
    "store launch", "vendor management", "3pl", "process improvement", "qsr operations",
    "multi-unit management", "shrinkage control", "order fulfillment", "pre-launch readiness"
]
DEFAULT_SETTINGS = {
    "target_countries": DEFAULT_COUNTRIES,
    "target_titles": DEFAULT_TITLES,
    "min_match_score": 45,
    "max_results_per_source": 100,
    "min_india_salary_lpa": 20,
    "min_experience_years": 8,
    "work_arrangement": "all",
    "posted_within_days": 3,
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
        row = conn.execute("SELECT value FROM settings WHERE key='target_countries'").fetchone()
        if row:
            try:
                saved_countries = json.loads(row["value"])
            except (TypeError, json.JSONDecodeError):
                saved_countries = []
            legacy_defaults = ["India", "United Arab Emirates", "Saudi Arabia", "Qatar", "Kuwait", "Oman", "Canada"]
            if saved_countries == legacy_defaults or saved_countries == ["India", "United Arab Emirates", "Saudi Arabia", "Qatar", "Kuwait", "Oman", "Canada"]:
                conn.execute("UPDATE settings SET value=? WHERE key='target_countries'",
                             (json.dumps(DEFAULT_COUNTRIES),))

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
    loc = normalize_text(location).lower()
    if not loc or loc in {"remote", "worldwide", "anywhere", "remote worldwide", "global"}:
        return "Remote / Unspecified" if loc else "Unspecified"
    aliases = [
        ("United Arab Emirates", ["united arab emirates", "uae", "dubai", "abu dhabi", "sharjah", "ajman", "ras al khaimah"]),
        ("Saudi Arabia", ["saudi arabia", "riyadh", "jeddah", "dammam", "khobar", "mecca", "makkah", "medina", "madinah"]),
        ("Qatar", ["qatar", "doha"]),
        ("Kuwait", ["kuwait", "kuwait city"]),
        ("Oman", ["oman", "muscat", "salalah"]),
        ("Canada", ["canada", "toronto", "vancouver", "montreal", "calgary", "ottawa", "edmonton", "winnipeg", "quebec city"]),
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
    title = normalize_text(job.get("title")).lower()
    location = normalize_text(job.get("location")).lower()
    description = normalize_text(job.get("description")).lower()
    text = " ".join([title, description])
    score = 0
    reasons = []

    role_groups = [
        (["senior cluster manager", "cluster manager", "city operations manager", "city manager", "area operations manager"], 42, "Cluster/city/area operations leadership"),
        (["head of operations", "director of operations", "regional operations manager", "regional operations"], 40, "Regional or head-of-operations leadership"),
        (["qsr operations", "multi-unit operations", "multi unit operations", "restaurant operations manager"], 38, "QSR / multi-unit operations leadership"),
        (["operations manager", "general manager operations", "operations head"], 34, "Operations management"),
        (["warehouse manager", "fulfilment manager", "fulfillment manager", "fulfillment operations", "warehouse operations manager"], 34, "Warehouse/fulfilment leadership"),
        (["supply chain manager", "logistics manager", "last mile manager", "last-mile manager", "dark store manager", "store operations manager"], 34, "Supply-chain/logistics leadership"),
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
        (["multi-site", "multi site", "multi-unit", "multi unit", "multiple locations", "multiple stores", "cluster", "store launch", "greenfield"], 9, "Multi-site / expansion operations"),
        (["dark store", "quick commerce", "q-commerce", "last mile", "last-mile", "fulfilment", "fulfillment", "warehouse", "qsr"], 9, "Relevant fulfilment/QSR/logistics domain"),
        (["team of", "team management", "people management", "leadership", "managed a team", "staff", "450+", "workforce"], 7, "Large-team leadership"),
        (["sla", "service level", "kpi", "on-time", "on time", "operational excellence", "process improvement", "sop"], 6, "KPI/SLA and process ownership"),
        (["inventory", "shrinkage", "fill rate", "cost control", "vendor management", "3pl", "capacity planning", "food cost"], 6, "Inventory/cost/vendor operations"),
    ]
    for terms, points, reason in skill_groups:
        if any(term in text for term in terms):
            score += points
            reasons.append(reason)

    country = detect_country(location)
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
        "canada": {"canada"},
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
    if title_is_non_ops:
        score = min(score, 12)
    elif generic_program_manager:
        score = min(score, 24)
    if country in {"Unspecified", "Remote / Unspecified"}:
        score = min(score, 64)
    return score, reasons, country
