import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from job_hunter import hunt_jobs, get_stats, list_jobs, get_settings, save_settings, init_db, set_job_status

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("job-hunter")

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if os.getenv("ENABLE_SCHEDULER", "true").lower() == "true":
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            scheduler = AsyncIOScheduler(timezone="UTC")
            minutes = max(15, int(os.getenv("HUNT_INTERVAL_MINUTES", "15")))
            async def scheduled_hunt():
                await hunt_jobs()
            scheduler.add_job(scheduled_hunt, "interval", minutes=minutes, id="job-hunt", replace_existing=True, max_instances=1, coalesce=True, next_run_time=datetime.now(timezone.utc))
            scheduler.start()
            app.state.scheduler = scheduler
            log.info("Job discovery scheduler started; interval=%s minutes", minutes)
        except Exception:
            log.exception("Scheduler could not start; manual hunt endpoint remains available")
    yield
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler:
        scheduler.shutdown(wait=False)

app = FastAPI(title="Autonomous Job Hunter", version="0.1.0", description="Job discovery, deduplication, candidate matching and application tracking. Submission is intentionally disabled by default.", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "*").split(","), allow_credentials=False, allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["*"])

@app.get("/")
def root():
    dashboard = Path(__file__).with_name("dashboard.html")
    if dashboard.exists():
        return FileResponse(dashboard)
    return {"name": "Autonomous Job Hunter", "status": "ready", "version": app.version, "docs": "/docs", "health": "/health"}

@app.get("/health")
def health():
    return {"status": "ok", "service": "autonomous-job-hunter", "time": datetime.now(timezone.utc).isoformat(), "live_applications_enabled": False}

@app.get("/api/stats")
def stats():
    return get_stats()

@app.get("/api/jobs")
def jobs(country: str | None = None, opportunity: str | None = None, min_score: int = Query(default=0, ge=0, le=100), status: str | None = None, limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0)):
    return {"items": list_jobs(country=country, opportunity=opportunity, min_score=min_score, status=status, limit=limit, offset=offset)}

@app.post("/api/hunt")
async def hunt():
    return await hunt_jobs()

@app.get("/api/settings")
def settings():
    return get_settings()

@app.post("/api/settings")
def update_settings(payload: dict[str, Any]):
    allowed = {"target_countries", "target_titles", "min_match_score", "max_results_per_source"}
    current = get_settings()
    for key, value in payload.items():
        if key in allowed:
            current[key] = value
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

@app.get("/api/sources")
def sources():
    return {"sources": [
        {"name": "Arbeitnow", "type": "public API", "configured": True},
        {"name": "Remotive", "type": "public API", "configured": True},
        {"name": "Jobicy", "type": "public API", "configured": True},
        {"name": "Greenhouse", "type": "public job boards", "configured": bool(os.getenv("GREENHOUSE_BOARDS", ""))},
        {"name": "Lever", "type": "public job boards", "configured": bool(os.getenv("LEVER_SITES", ""))}
    ], "note": "Results depend on what each provider exposes. Search frequency respects provider limits; this is not exhaustive coverage of every job board."}
