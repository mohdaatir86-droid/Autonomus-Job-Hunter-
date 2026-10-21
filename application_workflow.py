import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader

DB_PATH = os.getenv("DATABASE_PATH", "job_hunter.db")

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

def init_workflow_db():
    with _connect() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS candidate_profile (
            id INTEGER PRIMARY KEY CHECK(id=1), full_name TEXT DEFAULT '', email TEXT DEFAULT '',
            phone TEXT DEFAULT '', linkedin_url TEXT DEFAULT '', headline TEXT DEFAULT '',
            experience_summary TEXT DEFAULT '', skills TEXT DEFAULT '[]', resume_text TEXT DEFAULT '',
            resume_filename TEXT DEFAULT '', updated_at TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS application_drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'prepared', tailored_headline TEXT NOT NULL,
            keyword_checklist TEXT NOT NULL, resume_guidance TEXT NOT NULL,
            cover_letter TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS application_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, draft_id INTEGER NOT NULL,
            event TEXT NOT NULL, note TEXT DEFAULT '', created_at TEXT NOT NULL,
            FOREIGN KEY(draft_id) REFERENCES application_drafts(id) ON DELETE CASCADE
        )""")
        conn.execute("""INSERT OR IGNORE INTO candidate_profile
            (id, updated_at) VALUES (1, ?)""", (_now(),))

def get_profile():
    init_workflow_db()
    with _connect() as conn:
        row = dict(conn.execute("SELECT * FROM candidate_profile WHERE id=1").fetchone())
    row["skills"] = json.loads(row.get("skills") or "[]")
    row["has_resume"] = bool(row.get("resume_text"))
    row.pop("resume_text", None)
    return row

def save_profile(payload):
    init_workflow_db()
    allowed = {"full_name", "email", "phone", "linkedin_url", "headline", "experience_summary", "skills"}
    current = get_profile()
    values = {}
    for key in allowed:
        if key not in payload:
            continue
        value = payload[key]
        if key == "skills":
            if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
                raise ValueError("skills must be a list of strings")
            values[key] = json.dumps(list(dict.fromkeys(x.strip()[:80] for x in value if x.strip())))
        else:
            if not isinstance(value, str):
                raise ValueError(key + " must be text")
            if key == "email" and value and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value.strip()):
                raise ValueError("email address is invalid")
            if key == "linkedin_url" and value and not value.strip().startswith("https://"):
                raise ValueError("linkedin_url must start with https://")
            values[key] = value.strip()[:12000 if key == "experience_summary" else 500]
    if values:
        values["updated_at"] = _now()
        assignments = ", ".join(k + "=?" for k in values)
        with _connect() as conn:
            conn.execute("UPDATE candidate_profile SET " + assignments + " WHERE id=1", list(values.values()))
    return get_profile()

def store_resume(filename, raw_bytes):
    if len(raw_bytes) > 8 * 1024 * 1024:
        raise ValueError("Resume PDF must be 8 MB or smaller")
    if not filename.lower().endswith(".pdf") or not raw_bytes.startswith(b"%PDF"):
        raise ValueError("Upload a valid PDF resume")
    try:
        reader = PdfReader(__import__("io").BytesIO(raw_bytes))
        text = "\n".join((page.extract_text() or "") for page in reader.pages[:30]).strip()
    except Exception as exc:
        raise ValueError("Could not read this PDF. Please upload a text-based PDF.") from exc
    if not text:
        raise ValueError("No selectable text found. Upload a text-based PDF, not a scanned image.")
    if len(text) > 60000:
        text = text[:60000]
    init_workflow_db()
    with _connect() as conn:
        conn.execute("UPDATE candidate_profile SET resume_text=?, resume_filename=?, updated_at=? WHERE id=1",
                     (text, Path(filename).name[:180], _now()))
    return {"uploaded": True, "filename": Path(filename).name[:180], "characters_extracted": len(text), "profile": get_profile()}

def _keywords(description, title, skills):
    text = (title + " " + description).lower()
    candidates = [
        "multi-site operations", "cluster management", "city operations", "dark stores",
        "quick commerce", "last-mile delivery", "warehouse operations", "fulfilment",
        "inventory accuracy", "shrinkage", "SLA", "KPI", "fill rate", "customer experience",
        "cost control", "P&L", "team leadership", "vendor management", "capacity planning",
        "process improvement", "supply chain", "3PL", "forecasting", "workforce planning",
        "operations excellence", "on-time delivery", "program management", "store launches"
    ]
    found = [k for k in candidates if k.lower() in text]
    for skill in skills:
        if skill.lower() in text and skill not in found:
            found.append(skill)
    return found[:18]

def create_application_draft(job_id):
    init_workflow_db()
    with _connect() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            return None
        profile = dict(conn.execute("SELECT * FROM candidate_profile WHERE id=1").fetchone())
        skills = json.loads(profile.get("skills") or "[]")
        keywords = _keywords(job["description"] or "", job["title"] or "", skills)
        title = job["title"] or "the role"
        company = job["company"] or "your organization"
        name = profile.get("full_name") or "Candidate"
        headline = profile.get("headline") or "Operations and supply-chain leader"
        summary = profile.get("experience_summary") or "My experience in operations, logistics and team leadership"
        matched = [s for s in skills if s.lower() in ((job["description"] or "") + " " + title).lower()]
        guidance = [
            "Keep every claim consistent with your existing resume; do not add unverified metrics, tools, qualifications, or responsibilities.",
            "Prioritize relevant, already-supported experience for the role: " + title + ".",
            "Use these posting terms only where your actual experience supports them: " + (", ".join(keywords) if keywords else "Review the full job description and select accurate role-specific terms.") + ".",
            "Bring measurable outcomes, team/store scale, SLA, inventory, customer experience, and cost results to the top only if they appear in your source resume.",
        ]
        if not profile.get("resume_text"):
            guidance.insert(0, "Upload your PDF resume in Profile before treating this as a tailored application. This draft is based on profile fields only.")
        cover = (
            "Dear Hiring Team,\n\n"
            "I am writing to express my interest in the " + title + " opportunity at " + company + ". "
            + summary.rstrip(".") + ". My background aligns with the role's focus on operational execution, "
            "service performance, and building reliable teams.\n\n"
            + ("Relevant areas I would highlight include " + ", ".join(keywords[:6]) + ". " if keywords else "")
            + "I would welcome the opportunity to discuss how my experience can contribute to your team's operational goals. "
            "I have kept this draft general where specific achievements from my resume need to be confirmed.\n\n"
            + "Regards,\n" + name
        )
        now = _now()
        conn.execute("""INSERT INTO application_drafts(job_id,status,tailored_headline,keyword_checklist,resume_guidance,cover_letter,created_at,updated_at)
            VALUES (?, 'prepared', ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET status='prepared', tailored_headline=excluded.tailored_headline,
            keyword_checklist=excluded.keyword_checklist, resume_guidance=excluded.resume_guidance,
            cover_letter=excluded.cover_letter, updated_at=excluded.updated_at""",
            (job_id, title + " | " + headline, json.dumps(keywords), json.dumps(guidance), cover, now, now))
        draft = dict(conn.execute("SELECT * FROM application_drafts WHERE job_id=?", (job_id,)).fetchone())
        conn.execute("INSERT INTO application_events(draft_id,event,note,created_at) VALUES (?,?,?,?)",
                     (draft["id"], "draft_prepared", "Cover letter and resume tailoring checklist generated; no application submitted.", now))
    return _serialize_draft(draft, job)

def _serialize_draft(draft, job=None):
    out = dict(draft)
    out["keyword_checklist"] = json.loads(out.get("keyword_checklist") or "[]")
    out["resume_guidance"] = json.loads(out.get("resume_guidance") or "[]")
    if job:
        out["job"] = {k: job[k] for k in ("id", "title", "company", "location", "country", "url", "match_score") if k in job.keys()}
    return out

def list_application_drafts():
    init_workflow_db()
    with _connect() as conn:
        rows = conn.execute("""SELECT d.*, j.title, j.company, j.location, j.country, j.url, j.match_score
            FROM application_drafts d JOIN jobs j ON j.id=d.job_id ORDER BY d.updated_at DESC LIMIT 200""").fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["keyword_checklist"] = json.loads(item.get("keyword_checklist") or "[]")
        item["resume_guidance"] = json.loads(item.get("resume_guidance") or "[]")
        item["job"] = {k: item.pop(k) for k in ("title", "company", "location", "country", "url", "match_score")}
        result.append(item)
    return result

def update_draft_status(draft_id, status, note=""):
    allowed = {"prepared", "ready_for_review", "applied_manually", "interview", "rejected", "offer", "withdrawn"}
    if status not in allowed:
        raise ValueError("status must be one of " + ", ".join(sorted(allowed)))
    init_workflow_db()
    with _connect() as conn:
        row = conn.execute("SELECT id FROM application_drafts WHERE id=?", (draft_id,)).fetchone()
        if not row:
            return None
        now = _now()
        conn.execute("UPDATE application_drafts SET status=?, updated_at=? WHERE id=?", (status, now, draft_id))
        conn.execute("INSERT INTO application_events(draft_id,event,note,created_at) VALUES (?,?,?,?)",
                     (draft_id, status, note.strip()[:1000], now))
        return {"updated": True, "id": draft_id, "status": status}

def get_draft_events(draft_id):
    init_workflow_db()
    with _connect() as conn:
        exists = conn.execute("SELECT id FROM application_drafts WHERE id=?", (draft_id,)).fetchone()
        if not exists:
            return None
        return [dict(r) for r in conn.execute("SELECT event,note,created_at FROM application_events WHERE draft_id=? ORDER BY id DESC", (draft_id,)).fetchall()]
