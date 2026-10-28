import logging
import os
import hmac
from contextlib import asynccontextmanager
from urllib.parse import urlparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from job_hunter import get_settings, save_settings, init_db
from job_sources import hunt_jobs, get_stats, list_jobs, set_job_status
from gmail_alerts import build_auth_url, exchange_code, get_gmail_status, init_gmail_db, verify_oauth_state
from ai_assist import ai_status, generate_form_answers
from notify import notify_status, send_whatsapp
from database import postgres_enabled, database_diagnostics
from automation_settings import init_automation_settings, get_automation_settings, save_automation_settings
from browser_worker import run_application_worker, browser_health_check
from automation_routes import router as automation_router
from application_state import init_application_state_db

from application_workflow import (
    init_workflow_db,
    get_profile,
    get_profile_full,
    save_profile,
    store_resume,
    create_application_draft,
    list_application_drafts,
    update_draft_status,
    get_draft_events,
)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("job-hunter")


def internal_hunt_scheduler_enabled() -> bool:
    configured = os.getenv("ENABLE_INTERNAL_HUNT_SCHEDULER")
    if configured is None:
        configured = os.getenv("ENABLE_SCHEDULER", "true")
    return configured.strip().lower() == "true"


def application_profile_readiness() -> tuple[bool, list[str]]:
    profile = get_profile()
    missing = []
    if not (profile.get("full_name") or "").strip():
        missing.append("full name")
    if not (profile.get("email") or "").strip():
        missing.append("email")
    if not profile.get("has_resume"):
        missing.append("PDF resume")
    return not missing, missing


def require_application_profile() -> None:
    ready, missing = application_profile_readiness()
    if not ready:
        raise HTTPException(
            status_code=409,
            detail="Complete your private profile and upload your PDF resume before preparing drafts. Missing: " + ", ".join(missing) + ".",
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    init_workflow_db()
    init_gmail_db()
    init_automation_settings()
    init_application_state_db()
    if os.getenv("ENABLE_SCHEDULER", "true").lower() == "true":
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler

            scheduler = AsyncIOScheduler(timezone="UTC")
            minutes = max(10, int(os.getenv("HUNT_INTERVAL_MINUTES", "15")))

            async def scheduled_hunt():
                try:
                    result = await hunt_jobs()
                    errors = result.get("errors") or []
                    log.info(
                        "Scheduled hunt completed: fetched=%s new=%s updated=%s sources=%s errors=%s",
                        result.get("fetched", 0),
                        result.get("new_jobs", 0),
                        result.get("updated_jobs", 0),
                        len(result.get("sources") or []),
                        len(errors),
                    )
                    if errors:
                        log.warning("Scheduled hunt source errors: %s", errors[:10])
                except Exception:
                    log.exception("Scheduled hunt failed")
                    raise

            if internal_hunt_scheduler_enabled():
                scheduler.add_job(
                    scheduled_hunt,
                    "interval",
                    minutes=minutes,
                    id="job-hunt",
                    replace_existing=True,
                    max_instances=1,
                    coalesce=True,
                    next_run_time=datetime.now(timezone.utc),
                )
            else:
                log.info("Internal discovery schedule disabled; external orchestration owns hunt cadence")

            async def scheduled_application_preparation():
                try:
                    policy = get_automation_settings()
                    if not policy.get("enabled"):
                        return
                    profile_ready, missing_profile = application_profile_readiness()
                    if not profile_ready:
                        log.info("Scheduled application preparation skipped; private profile missing: %s", ", ".join(missing_profile))
                        return
                    candidates = list_jobs(
                        min_score=policy["min_score"],
                        status=None,
                        limit=policy["max_per_run"],
                    )
                    prepared_count = 0
                    skipped_count = 0
                    for job in candidates:
                        if job.get("status") in {"applied_manually", "interview", "offer", "rejected", "not_relevant", "closed"}:
                            skipped_count += 1
                            continue
                        try:
                            draft = await create_application_draft(int(job["id"]))
                            if draft:
                                prepared_count += 1
                        except Exception:
                            log.exception("Scheduled application draft failed for job %s", job.get("id"))
                    log.info(
                        "Scheduled application preparation finished: enabled=%s candidates=%s prepared=%s skipped=%s submission_enabled=false",
                        policy.get("enabled"), len(candidates), prepared_count, skipped_count,
                    )
                except Exception:
                    log.exception("Scheduled application preparation failed")

            scheduler.add_job(
                scheduled_application_preparation,
                "interval",
                minutes=max(15, int(os.getenv("APPLICATION_PREP_INTERVAL_MINUTES", "30"))),
                id="application-preparation",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            scheduler.start()
            app.state.scheduler = scheduler
            log.info("Job discovery scheduler started; interval=%s minutes", minutes)
        except Exception:
            log.exception("Scheduler could not start; manual hunt endpoint remains available")
    yield
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(
    title="Autonomous Job Hunter",
    version="0.6.0",
    description="Job Hunter: discovery, drafts, guarded Greenhouse/Lever browser apply (opt-in). Authenticated boards discovery-only.",
    lifespan=lifespan,
)
app.include_router(automation_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def protect_private_candidate_data(request, call_next):
    path = request.url.path
    if path == "/api/integrations/n8n/hunt" and request.method != "OPTIONS":
        expected = os.getenv("N8N_AUTOMATION_TOKEN", "").strip()
        if len(expected) < 32:
            return JSONResponse(
                status_code=503,
                content={"detail": "n8n discovery integration is not configured with a strong bearer token."},
            )
        supplied = request.headers.get("authorization", "")
        token = supplied[7:] if supplied.lower().startswith("bearer ") else ""
        if not token or not hmac.compare_digest(token, expected):
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized."},
            )
    protected = (
        path == "/api/profile"
        or path.startswith("/api/profile/")
        or path == "/api/applications"
        or path.startswith("/api/applications/")
        or path == "/api/auto-apply"
        or path.startswith("/api/auto-apply/")
        or path == "/api/auto-fill"
        or path.startswith("/api/automation/")
        or path.startswith("/api/application-attempts")
        or path == "/api/application-state"
        or path.startswith("/api/portals")
    )
    if protected and request.method != "OPTIONS":
        expected = os.getenv("PORTAL_ACCESS_TOKEN", "").strip()
        if not expected:
            return JSONResponse(
                status_code=503,
                content={"detail": "Private profile features are locked until PORTAL_ACCESS_TOKEN is configured in Render."},
            )
        supplied = request.headers.get("authorization", "")
        token = supplied[7:] if supplied.lower().startswith("bearer ") else ""
        if not token or not hmac.compare_digest(token, expected):
            return JSONResponse(
                status_code=401,
                content={"detail": "Enter the private portal access token to use profile and application drafts."},
            )
    return await call_next(request)


@app.api_route("/", methods=["GET", "HEAD"])
def root():
    dashboard = Path(__file__).with_name("dashboard.html")
    if dashboard.exists():
        return FileResponse(dashboard)
    return {
        "name": "Autonomous Job Hunter",
        "status": "ready",
        "version": app.version,
        "docs": "/docs",
        "health": "/health",
    }


@app.api_route("/health", methods=["GET", "HEAD"])
@app.api_route("/health.", methods=["GET", "HEAD"])
def health():
    return {
        "status": "ok",
        "service": "autonomous-job-hunter",
        "version": app.version,
        "database_mode": "postgresql" if postgres_enabled() else "sqlite",
        "time": datetime.now(timezone.utc).isoformat(),
        "live_applications_enabled": False,
        "browser_submit_enabled": os.getenv("BROWSER_ALLOW_SUBMIT", "false").lower() == "true",
        "admin_endpoints_locked": bool(os.getenv("PORTAL_ACCESS_TOKEN", "").strip()),
        "application_preparation_enabled": True,
        "resume_profile_configured": get_profile().get("has_resume", False),
        "browser_worker_enabled": os.getenv("BROWSER_WORKER_ENABLED", "false").lower() == "true",
        "gmail": get_gmail_status(),
        "ai": ai_status(),
        "whatsapp": notify_status(),
    }


@app.get("/health/db")
def health_db():
    result = database_diagnostics()
    return JSONResponse(status_code=200 if result.get("connected") else 503, content=result)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "service": "autonomous-job-hunter"}


@app.get("/api/stats")
def stats():
    return get_stats()


@app.get("/api/jobs")
def jobs(
    country: str | None = None,
    opportunity: str | None = None,
    min_score: int = Query(default=0, ge=0, le=100),
    status: str | None = None,
    posted_within_days: int | None = Query(default=None, ge=1, le=365),
    q: str | None = Query(default=None, max_length=120),
    source: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    return {
        "items": list_jobs(
            country=country,
            opportunity=opportunity,
            min_score=min_score,
            status=status,
            posted_within_days=posted_within_days,
            limit=limit,
            offset=offset,
            q=q,
            source=source,
        )
    }


@app.post("/api/hunt")
@app.post("/api/integrations/n8n/hunt")
async def hunt():
    result = await hunt_jobs()
    if "whatsapp_notify" not in result:
        result["whatsapp_notify"] = {
            "sent": False,
            "note": "Update job_sources for auto-notify on new high matches",
        }
    return result


@app.get("/api/settings")
def settings():
    return get_settings()


@app.post("/api/settings")
def update_settings(payload: dict[str, Any]):
    allowed = {
        "target_countries",
        "target_titles",
        "min_match_score",
        "max_results_per_source",
        "min_india_salary_lpa",
        "min_experience_years",
        "work_arrangement",
        "posted_within_days",
        "visa_preference",
    }
    current = get_settings()
    for key, value in payload.items():
        if key not in allowed:
            continue
        if key in {"target_countries", "target_titles"}:
            if not isinstance(value, list) or not value or not all(isinstance(item, str) and item.strip() for item in value):
                raise HTTPException(status_code=422, detail=key + " must be a non-empty list of strings")
            current[key] = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        elif key == "min_match_score":
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 100:
                raise HTTPException(status_code=422, detail="min_match_score must be an integer from 0 to 100")
            current[key] = value
        elif key == "max_results_per_source":
            if not isinstance(value, int) or isinstance(value, bool) or not 10 <= value <= 250:
                raise HTTPException(status_code=422, detail="max_results_per_source must be an integer from 10 to 250")
            current[key] = value
    preference_rules = {
        "min_india_salary_lpa": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 500,
        "min_experience_years": lambda v: isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 60,
        "work_arrangement": lambda v: v in {"all", "onsite", "hybrid", "remote"},
        "posted_within_days": lambda v: isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 365,
        "visa_preference": lambda v: v in {"any", "sponsor"},
    }
    for key, validator in preference_rules.items():
        if key in payload:
            if not validator(payload[key]):
                raise HTTPException(status_code=422, detail="Invalid value for " + key)
            current[key] = payload[key]
    return save_settings(current)


@app.post("/api/jobs/{job_id}/status")
def update_job_status(job_id: int, payload: dict[str, Any]):
    status = str(payload.get("status", "")).strip().lower()
    allowed = {"new", "shortlisted", "saved", "draft_ready", "applied_manually", "interview", "rejected", "offer", "not_relevant", "closed"}
    if status not in allowed:
        raise HTTPException(status_code=422, detail="status must be one of " + ", ".join(sorted(allowed)))
    result = set_job_status(job_id, status)
    if not result.get("updated"):
        raise HTTPException(status_code=404, detail="job not found")
    return result


@app.get("/api/automation/settings")
def automation_settings():
    return get_automation_settings()


@app.post("/api/automation/settings")
def update_automation_settings(payload: dict[str, Any]):
    if payload.get("enabled") is True:
        require_application_profile()
    try:
        return save_automation_settings(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/api/automation/prepare")
async def automation_prepare():
    require_application_profile()
    policy = get_automation_settings()
    if not policy.get("enabled"):
        raise HTTPException(status_code=409, detail="Automation mode is OFF. Enable it in the dashboard first.")
    candidates = list_jobs(min_score=policy["min_score"], status=None, limit=policy["max_per_run"])
    prepared, skipped, errors = [], [], []
    for job in candidates:
        if job.get("status") in {"applied_manually", "interview", "offer", "rejected", "not_relevant", "closed"}:
            skipped.append({"job_id": job.get("id"), "reason": "job already actioned"})
            continue
        try:
            draft = await create_application_draft(int(job["id"]))
            if draft:
                prepared.append({
                    "draft_id": draft.get("id"), "job_id": job.get("id"),
                    "title": job.get("title"), "company": job.get("company"),
                    "match_score": job.get("match_score"), "status": draft.get("status", "prepared"),
                    "url": job.get("url"),
                })
        except Exception as exc:
            log.exception("Automation preparation failed for job %s", job.get("id"))
            errors.append({"job_id": job.get("id"), "error": str(exc)[:180]})
    return {
        "status": "prepared_only", "enabled": True,
        "threshold": policy["min_score"], "candidate_count": len(candidates),
        "prepared_count": len(prepared), "prepared": prepared, "skipped": skipped, "errors": errors,
        "auto_submit_enabled": False,
        "message": "Drafts only; every application submission stays a user-reviewed step.",
    }


@app.post("/api/auto-fill")
async def ai_auto_fill(payload: dict[str, Any]):
    page_url = str(payload.get("page_url", ""))[:1500]
    parsed_page_url = urlparse(page_url)
    page_host = (parsed_page_url.hostname or "").lower()
    allowed_form_host = any(
        page_host == domain or page_host.endswith("." + domain)
        for domain in ("greenhouse.io", "lever.co")
    )
    if parsed_page_url.scheme != "https" or not allowed_form_host:
        raise HTTPException(
            status_code=422,
            detail="Form help supports HTTPS Greenhouse and Lever forms only. LinkedIn, Naukri and Shine are discovery-only.",
        )
    page_title = str(payload.get("page_title", ""))[:300]
    fields = payload.get("fields", [])
    if not isinstance(fields, list) or not fields or len(fields) > 80:
        raise HTTPException(status_code=422, detail="fields must contain 1 to 80 visible form fields")
    cleaned = []
    for i, field in enumerate(fields):
        if not isinstance(field, dict):
            continue
        cleaned.append({
            "id": str(field.get("id", i))[:80],
            "label": str(field.get("label", ""))[:250],
            "name": str(field.get("name", ""))[:150],
            "type": str(field.get("type", "text"))[:40],
            "placeholder": str(field.get("placeholder", ""))[:200],
            "required": bool(field.get("required", False)),
            "options": [str(x)[:160] for x in field.get("options", [])[:40]] if isinstance(field.get("options", []), list) else [],
        })
    if not cleaned:
        raise HTTPException(status_code=422, detail="No supported form fields found")
    require_application_profile()
    profile = get_profile_full()
    answers = await generate_form_answers(
        {"title": page_title, "url": page_url, "fields": cleaned},
        profile,
    )
    if answers is None:
        raise HTTPException(status_code=503, detail="AI form assistance is unavailable. Check AI_API_KEY in Render.")
    return {
        "status": "suggestions_ready",
        "page_title": page_title,
        "answers": answers,
        "filled_count": sum(1 for x in answers.values() if isinstance(x, str) and x.strip()),
        "note": "Suggested values only. Review every field. The assistant never submits the application.",
    }


@app.get("/api/profile")
def candidate_profile():
    return get_profile()


@app.post("/api/profile")
def update_candidate_profile(payload: dict[str, Any]):
    try:
        return save_profile(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/api/profile/resume")
async def upload_resume(file: UploadFile = File(...)):
    try:
        content = await file.read(8 * 1024 * 1024 + 1)
        result = store_resume(file.filename or "resume.pdf", content)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    finally:
        await file.close()


@app.post("/api/auto-apply/queue")
async def auto_apply_queue():
    require_application_profile()
    policy = get_automation_settings()
    candidates = list_jobs(min_score=policy["min_score"], status=None, limit=policy["max_per_run"])
    prepared, skipped, errors = [], [], []
    for job in candidates:
        if job.get("status") in {"applied_manually", "interview", "offer", "rejected", "not_relevant", "closed"}:
            skipped.append({"job_id": job.get("id"), "reason": "job already actioned"})
            continue
        try:
            draft = await create_application_draft(int(job["id"]))
            if draft:
                prepared.append({
                    "draft_id": draft.get("id"),
                    "job_id": job.get("id"),
                    "title": job.get("title"),
                    "company": job.get("company"),
                    "match_score": job.get("match_score"),
                    "ai_assisted": draft.get("ai_assisted", False),
                    "status": draft.get("status", "prepared"),
                    "url": job.get("url"),
                })
        except Exception as exc:
            log.exception("Auto-apply queue preparation failed for job %s", job.get("id"))
            errors.append({"job_id": job.get("id"), "error": str(exc)[:180]})
    return {
        "status": "prepared_for_review",
        "threshold": policy["min_score"],
        "candidate_count": len(candidates),
        "prepared_count": len(prepared),
        "prepared": prepared,
        "skipped": skipped,
        "errors": errors,
        "automatic_submission_enabled": False,
        "automation_policy_enabled": policy["enabled"],
        "message": "Drafts only. Greenhouse/Lever form help requires your review; no application is submitted automatically.",
    }


@app.post("/api/applications/draft/{job_id}")
async def draft_application(job_id: int):
    require_application_profile()
    draft = await create_application_draft(job_id)
    if not draft:
        raise HTTPException(status_code=404, detail="job not found")
    return draft


@app.get("/api/applications")
def applications():
    return {"items": list_application_drafts()}


@app.post("/api/applications/{draft_id}/status")
def application_status(draft_id: int, payload: dict[str, Any]):
    status = str(payload.get("status", "")).strip().lower()
    note = str(payload.get("note", ""))[:1000]
    try:
        result = update_draft_status(draft_id, status, note=note)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if not result:
        raise HTTPException(status_code=404, detail="draft not found")
    return result


@app.get("/api/applications/{draft_id}/events")
def application_events(draft_id: int):
    events = get_draft_events(draft_id)
    if events is None:
        raise HTTPException(status_code=404, detail="draft not found")
    return {"items": events}


@app.get("/api/gmail/status")
def gmail_status():
    return get_gmail_status()


@app.get("/api/gmail/auth")
def gmail_auth():
    try:
        url = build_auth_url()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return RedirectResponse(url)


@app.get("/api/gmail/callback")
async def gmail_callback(code: str = "", state: str = ""):
    if not verify_oauth_state(state):
        raise HTTPException(status_code=400, detail="Invalid OAuth state")
    result = await exchange_code(code)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "oauth failed"))
    return RedirectResponse("/")


@app.post("/api/notify/test")
async def notify_test():
    result = await send_whatsapp("Job Hunter test: WhatsApp push is working.")
    if not result.get("sent"):
        raise HTTPException(status_code=400, detail=result)
    return result


@app.get("/api/sources")
def sources():
    from job_sources import get_source_report, BUILTIN_GREENHOUSE_BOARDS

    gmail = get_gmail_status()
    report = get_source_report() or {}
    by_name = {item.get("name"): item for item in (report.get("sources") or [])}
    greenhouse_env = bool(os.getenv("GREENHOUSE_BOARDS", "").strip())
    catalog = [
        {"name": "Arbeitnow", "type": "Public job API", "configured": True, "connection_mode": "Public API, no key"},
        {"name": "Remotive", "type": "Public job API", "configured": True, "connection_mode": "Public API, operations category"},
        {"name": "Jobicy", "type": "Public job API", "configured": True, "connection_mode": "Public API, no key"},
        {
            "name": "Google Jobs via SerpApi",
            "type": "Search API",
            "configured": bool(os.getenv("SERPAPI_API_KEY", "").strip()),
            "connection_mode": "SERPAPI_API_KEY in Render. A rejected key is Unauthorized, not Ready.",
        },
        {
            "name": "Greenhouse company boards",
            "type": "Public company ATS API",
            "configured": True,
            "connection_mode": "GREENHOUSE_BOARDS" if greenhouse_env else "Built-in public boards: " + ", ".join(BUILTIN_GREENHOUSE_BOARDS),
        },
        {
            "name": "Lever company boards",
            "type": "Public company ATS API",
            "configured": bool(os.getenv("LEVER_SITES", "").strip()),
            "connection_mode": "LEVER_SITES slugs",
        },
        {
            "name": "Gmail job alerts",
            "type": "Job-alert email",
            "configured": bool(gmail.get("connected")),
            "connection_mode": "Google OAuth read-only. Redirect: " + str(gmail.get("callback_url") or ""),
            "oauth_configured": gmail.get("oauth_configured", False),
            "gmail_email": gmail.get("email") or None,
            "auth_url": "/api/gmail/auth",
            "missing": gmail.get("missing") or [],
        },
    ]
    for item in catalog:
        live = by_name.get(item["name"]) or {}
        if live:
            item["status"] = live.get("status") or "unavailable"
            item["detail"] = (live.get("detail") or "")[:300]
            item["fetched"] = live.get("fetched", 0)
        elif item["configured"]:
            item["status"] = "unknown"
            item["detail"] = "No hunt has recorded this source yet"
            item["fetched"] = 0
        else:
            item["status"] = "missing_configuration"
            item["detail"] = item["connection_mode"]
            item["fetched"] = 0
        item["healthy"] = item["status"] == "working"
    return {
        "sources": catalog,
        "checked_at": report.get("checked_at"),
        "note": "Submission stays off unless BROWSER_ALLOW_SUBMIT is explicitly true. Ready means the last hunt succeeded for that source.",
        "gmail": gmail,
        "ai": ai_status(),
        "whatsapp": notify_status(),
        "live_applications_enabled": False,
        "admin_locked": bool(os.getenv("PORTAL_ACCESS_TOKEN", "").strip()),
    }
