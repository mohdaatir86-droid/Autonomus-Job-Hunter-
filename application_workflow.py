import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader

DB_PATH = os.getenv("DATABASE_PATH", "job_hunter.db")

DEFAULT_PROFILE = {
    "full_name": "Syed Aatir Hussain",
    "email": "mohdaatir86@gmail.com",
    "phone": "+91 8979743453",
    "linkedin_url": "https://linkedin.com/in/syed-aatir-hussain-b04520194",
    "headline": "Cluster Manager | QSR Expansion & Q-Commerce Operations | 8+ Years",
    "experience_summary": (
        "Operations-driven leader with 8+ years in multi-unit QSR operations, dark store management, "
        "Q-commerce fulfillment and supply chain optimization. Currently Cluster Manager at Swish leading "
        "rapid QSR expansion (12 stores launched in one month, 10+ in pipeline). Previously Senior Cluster "
        "Manager at ZeptoNow directing 18 dark stores, ~30,000 orders/day and 450+ frontline associates. "
        "Proven in greenfield launches, SLA excellence (90%+), shrinkage reduction, and building high-performance teams."
    ),
    "skills": [
        "Cluster Operations", "QSR Operations", "Dark Store Management", "Q-Commerce Fulfillment",
        "Multi-Unit Management", "Store Launch & Expansion", "SLA & KPI Management",
        "Inventory & Shrinkage Control", "Vendor Management", "3PL Coordination",
        "Last-Mile Logistics", "SOP Development", "Team Leadership (450+)", "Cost Optimization",
    ],
}


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
        row = conn.execute("SELECT full_name, email FROM candidate_profile WHERE id=1").fetchone()
        if row is None:
            conn.execute(
                """INSERT INTO candidate_profile
                   (id, full_name, email, phone, linkedin_url, headline, experience_summary, skills, updated_at)
                   VALUES (1,?,?,?,?,?,?,?,?)""",
                (
                    DEFAULT_PROFILE["full_name"], DEFAULT_PROFILE["email"], DEFAULT_PROFILE["phone"],
                    DEFAULT_PROFILE["linkedin_url"], DEFAULT_PROFILE["headline"], DEFAULT_PROFILE["experience_summary"],
                    json.dumps(DEFAULT_PROFILE["skills"]), _now(),
                ),
            )
        elif not (row["full_name"] or "").strip() and not (row["email"] or "").strip():
            conn.execute(
                """UPDATE candidate_profile SET full_name=?, email=?, phone=?, linkedin_url=?,
                   headline=?, experience_summary=?, skills=?, updated_at=? WHERE id=1""",
                (
                    DEFAULT_PROFILE["full_name"], DEFAULT_PROFILE["email"], DEFAULT_PROFILE["phone"],
                    DEFAULT_PROFILE["linkedin_url"], DEFAULT_PROFILE["headline"], DEFAULT_PROFILE["experience_summary"],
                    json.dumps(DEFAULT_PROFILE["skills"]), _now(),
                ),
            )


def get_profile():
    init_workflow_db()
    with _connect() as conn:
        row = dict(conn.execute("SELECT * FROM candidate_profile WHERE id=1").fetchone())
    row["skills"] = json.loads(row.get("skills") or "[]")
    row["has_resume"] = bool(row.get("resume_text"))
    row.pop("resume_text", None)
    return row


def get_profile_full():
    init_workflow_db()
    with _connect() as conn:
        row = dict(conn.execute("SELECT * FROM candidate_profile WHERE id=1").fetchone())
    row["skills"] = json.loads(row.get("skills") or "[]")
    return row


def save_profile(payload):
    init_workflow_db()
    allowed = {"full_name", "email", "phone", "linkedin_url", "headline", "experience_summary", "skills"}
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
        conn.execute(
            "UPDATE candidate_profile SET resume_text=?, resume_filename=?, updated_at=? WHERE id=1",
            (text, Path(filename).name[:180], _now()),
        )
    return {
        "uploaded": True,
        "filename": Path(filename).name[:180],
        "characters_extracted": len(text),
        "profile": get_profile(),
    }


def _keywords(description, title, skills):
    text = (title + " " + description).lower()
    candidates = [
        "multi-site operations", "cluster management", "city operations", "dark stores",
        "quick commerce", "last-mile delivery", "warehouse operations", "fulfilment",
        "inventory accuracy", "shrinkage", "SLA", "KPI", "fill rate", "customer experience",
        "cost control", "P&L", "team leadership", "vendor management", "capacity planning",
        "process improvement", "supply chain", "3PL", "forecasting", "workforce planning",
        "operations excellence", "on-time delivery", "program management", "store launches",
        "qsr operations", "multi-unit", "greenfield", "pre-launch", "food safety",
    ]
    found = [k for k in candidates if k.lower() in text]
    for skill in skills:
        if skill.lower() in text and skill not in found:
            found.append(skill)
    return found[:18]


def _build_cover_letter(job, profile, keywords):
    title = job["title"] or "the role"
    company = job["company"] or "your organization"
    name = profile.get("full_name") or "Syed Aatir Hussain"
    summary = (profile.get("experience_summary") or "").strip()
    if not summary:
        summary = "I bring 8+ years of progressive experience in multi-unit QSR and Q-commerce operations"
    highlights = []
    text = (title + " " + (job.get("description") or "")).lower()
    if any(k in text for k in ["cluster", "multi", "dark store", "store", "qsr", "expansion", "launch"]):
        highlights.append(
            "At Swish I am leading rapid QSR expansion — 12 stores launched in one month with 10+ more in the active pipeline, while building standardized SOPs and high-performing teams across the cluster."
        )
    if any(k in text for k in ["dark store", "q-commerce", "quick commerce", "fulfilment", "fulfillment", "sla", "last mile"]):
        highlights.append(
            "As Senior Cluster Manager at ZeptoNow I directed 18 dark stores processing ~30,000 orders daily with 450+ associates, sustaining 90% SLA and reducing inventory shrinkage by 29%."
        )
    if any(k in text for k in ["warehouse", "logistics", "supply chain", "3pl", "inventory"]):
        highlights.append(
            "Earlier roles at Supr Daily, Ninjacart and Foodpanda gave me hands-on warehouse, logistics coordination and city-level delivery operations experience."
        )
    if not highlights:
        highlights.append(
            "My background covers end-to-end operations leadership from greenfield launch through stabilization, with measurable improvements in SLA, shrinkage, fill rate and team capability."
        )
    keyword_line = (
        ("I would particularly highlight experience aligned with: " + ", ".join(keywords[:6]) + ".\n\n") if keywords else ""
    )
    cover = (
        f"Dear Hiring Team,\n\nI am writing to express my interest in the {title} role at {company}.\n\n"
        f"{summary.rstrip('.')}.\n\n{highlights[0]}\n\n"
    )
    if len(highlights) > 1:
        cover += highlights[1] + "\n\n"
    cover += keyword_line
    cover += f"I would welcome the opportunity to discuss how this experience can support your operational goals.\n\nRegards,\n{name}"
    return cover


def _default_guidance(title, keywords, has_resume):
    guidance = [
        "Keep every claim consistent with your existing resume; do not invent metrics or responsibilities.",
        f"Prioritize the most relevant experience for: {title}.",
        "Lead with Swish (current QSR expansion) and ZeptoNow (18 dark stores, 30k orders/day, 450+ people) when the role involves multi-unit, dark store, QSR or cluster operations.",
        "Use these posting terms only where your actual experience supports them: "
        + (", ".join(keywords) if keywords else "Review the full job description.")
        + ".",
        "Quantified outcomes: 12 stores launched in 1 month, 90% SLA, 29% shrinkage reduction, 95% fill rate, 96% audit compliance — use only if relevant.",
    ]
    if not has_resume:
        guidance.insert(0, "Upload your PDF resume in Profile so AI drafts can match your exact wording.")
    return guidance


async def create_application_draft(job_id):
    init_workflow_db()
    with _connect() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            return None
        job = dict(job)
        profile = get_profile_full()
        skills = profile.get("skills") or []
        keywords = _keywords(job.get("description") or "", job.get("title") or "", skills)
        title = job.get("title") or "the role"
        headline = profile.get("headline") or "Operations and supply-chain leader"
        guidance = _default_guidance(title, keywords, bool(profile.get("resume_text")))
        cover = _build_cover_letter(job, profile, keywords)
        ai_used = False
        try:
            from ai_assist import generate_cover_letter, generate_resume_guidance, ai_enabled

            if ai_enabled():
                ai_cover = await generate_cover_letter(job, profile, keywords)
                if ai_cover and len(ai_cover.strip()) > 80:
                    cover = ai_cover.strip()
                    ai_used = True
                ai_guide = await generate_resume_guidance(job, profile, keywords)
                if ai_guide:
                    guidance = ai_guide
                    ai_used = True
        except Exception:
            pass
        now = _now()
        conn.execute(
            """INSERT INTO application_drafts(job_id,status,tailored_headline,keyword_checklist,resume_guidance,cover_letter,created_at,updated_at)
            VALUES (?, 'prepared', ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET status='prepared', tailored_headline=excluded.tailored_headline,
            keyword_checklist=excluded.keyword_checklist, resume_guidance=excluded.resume_guidance,
            cover_letter=excluded.cover_letter, updated_at=excluded.updated_at""",
            (job_id, title + " | " + headline, json.dumps(keywords), json.dumps(guidance), cover, now, now),
        )
        draft = dict(conn.execute("SELECT * FROM application_drafts WHERE job_id=?", (job_id,)).fetchone())
        note = "AI-assisted draft" if ai_used else "Template draft; set AI_API_KEY for smarter letters"
        conn.execute(
            "INSERT INTO application_events(draft_id,event,note,created_at) VALUES (?,?,?,?)",
            (draft["id"], "draft_prepared", note + "; no application submitted.", now),
        )
    out = _serialize_draft(draft, job)
    out["ai_assisted"] = ai_used
    return out


def _serialize_draft(draft, job=None):
    out = dict(draft)
    out["keyword_checklist"] = json.loads(out.get("keyword_checklist") or "[]")
    out["resume_guidance"] = json.loads(out.get("resume_guidance") or "[]")
    if job:
        out["job"] = {
            k: job[k]
            for k in ("id", "title", "company", "location", "country", "url", "match_score")
            if k in job.keys()
        }
    return out


def list_application_drafts():
    init_workflow_db()
    with _connect() as conn:
        rows = conn.execute(
            """SELECT d.*, j.title, j.company, j.location, j.country, j.url, j.match_score
            FROM application_drafts d JOIN jobs j ON j.id=d.job_id ORDER BY d.updated_at DESC LIMIT 200"""
        ).fetchall()
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
        conn.execute(
            "INSERT INTO application_events(draft_id,event,note,created_at) VALUES (?,?,?,?)",
            (draft_id, status, note.strip()[:1000], now),
        )
        return {"updated": True, "id": draft_id, "status": status}


def get_draft_events(draft_id):
    init_workflow_db()
    with _connect() as conn:
        exists = conn.execute("SELECT id FROM application_drafts WHERE id=?", (draft_id,)).fetchone()
        if not exists:
            return None
        return [
            dict(r)
            for r in conn.execute(
                "SELECT event,note,created_at FROM application_events WHERE draft_id=? ORDER BY id DESC",
                (draft_id,),
            ).fetchall()
        ]
