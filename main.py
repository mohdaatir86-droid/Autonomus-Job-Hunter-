import logging
import os
import hmac
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from job_hunter import get_settings, save_settings, init_db
from job_sources import hunt_jobs, get_stats, list_jobs, set_job_status
from application_workflow import (
    init_workflow_db,
    get_profile,
    save_profile,
    store_resume,
    create_application_draft,
    list_application_drafts,
    update_draft_status,
    get_draft_events,
)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("job-hunter")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    init_workflow_db()
    if os.getenv("ENABLE_SCHEDULER", "true").lower() == "true":
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler

            scheduler = AsyncIOScheduler(timezone="UTC")
            minutes = max(15, int(os.getenv("HUNT_INTERVAL_MINUTES", "15")))

            async def scheduled_hunt():
                await hunt_jobs()

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
    version="0.2.0",
    description="Job discovery, deduplication, candidate matching and application tracking. Submission is intentionally disabled by default.",
    lifespan=lifespan,
)
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
    protected = (
        path == "/api/profile"
        or path.startswith("/api/profile/")
        or path == "/api/applications"
        or path.startswith("/api/applications/")
    )
    if protected:
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
def health():
    return {
        "status": "ok",
        "service": "autonomous-job-hunter",
        "time": datetime.now(timezone.utc).isoformat(),
        "live_applications_enabled": False,
        "application_preparation_enabled": True,
        "resume_profile_configured": get_profile().get("has_resume", False),
    }


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
        )
    }


@app.post("/api/hunt")
async def hunt():
    return await hunt_jobs()


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
    allowed = {"saved", "applied_manually", "interview", "rejected", "offer", "not_relevant"}
    if status not in allowed:
        raise HTTPException(status_code=422, detail="status must be one of " + ", ".join(sorted(allowed)))
    result = set_job_status(job_id, status)
    if not result.get("updated"):
        raise HTTPException(status_code=404, detail="job not found")
    return result


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


@app.get("/api/applications")
def application_drafts():
    return {"items": list_application_drafts(), "automatic_submission_enabled": False}


@app.post("/api/applications/draft/{job_id}")
def prepare_application(job_id: int):
    draft = create_application_draft(job_id)
    if not draft:
        raise HTTPException(status_code=404, detail="job not found")
    return draft


@app.post("/api/applications/{draft_id}/status")
def update_application_status(draft_id: int, payload: dict[str, Any]):
    status = str(payload.get("status", "")).strip().lower()
    note = str(payload.get("note", ""))
    try:
        result = update_draft_status(draft_id, status, note)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if not result:
        raise HTTPException(status_code=404, detail="application draft not found")
    return result


@app.get("/api/applications/{draft_id}/events")
def application_events(draft_id: int):
    events = get_draft_events(draft_id)
    if events is None:
        raise HTTPException(status_code=404, detail="application draft not found")
    return {"items": events}


@app.get("/api/sources")
def sources():
    return {
        "sources": [
            {"name": "Arbeitnow", "type": "Public job API", "configured": True, "connection_mode": "API"},
            {"name": "Remotive", "type": "Public job API", "configured": True, "connection_mode": "API"},
            {"name": "Jobicy", "type": "Public job API", "configured": True, "connection_mode": "API"},
            {
                "name": "Google Jobs via SerpApi",
                "type": "Search API",
                "configured": bool(os.getenv("SERPAPI_API_KEY", "").strip()),
                "connection_mode": "API key required",
            },
            {
                "name": "Greenhouse company boards",
                "type": "Public company ATS API",
                "configured": bool(os.getenv("GREENHOUSE_BOARDS", "").strip()),
                "connection_mode": "Company board slugs",
            },
            {
                "name": "Lever company boards",
                "type": "Public company ATS API",
                "configured": bool(os.getenv("LEVER_SITES", "").strip()),
                "connection_mode": "Company board slugs",
            },
            {"name": "Naukri", "type": "Job board", "configured": False, "connection_mode": "Official integration or job-alert email required"},
            {"name": "Shine", "type": "Job board", "configured": False, "connection_mode": "Official integration or job-alert email required"},
            {"name": "Monster", "type": "Job board", "configured": False, "connection_mode": "Official integration or job-alert email required"},
            {"name": "LinkedIn", "type": "Job board", "configured": False, "connection_mode": "Authorized API/partner access required"},
        ],
        "note": "No portal passwords are stored. Naukri, Shine, Monster and LinkedIn are not connected until an authorized integration is configured. Direct application submission remains disabled.",
    }
