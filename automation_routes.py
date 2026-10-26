"""Automation browser routes - mounted from main."""
import os
import logging
from typing import Any
from fastapi import APIRouter, HTTPException, Query

from browser_worker import run_application_worker, browser_health_check
from portal_registry import get_portal_registry
from application_state import (
    list_attempts,
    attempt_events,
    state_machine_summary,
)
from automation_settings import get_automation_settings
from application_workflow import list_application_drafts

log = logging.getLogger("job-hunter")
router = APIRouter()


@router.get("/health/browser")
async def health_browser():
    return await browser_health_check()


@router.get("/api/portals")
def portals():
    return get_portal_registry()


@router.get("/api/application-state")
def application_state_status():
    return state_machine_summary()


@router.get("/api/application-attempts")
def application_attempts(state: str | None = None, limit: int = Query(default=50, ge=1, le=200)):
    return {"items": list_attempts(limit=limit, state=state)}


@router.get("/api/application-attempts/{attempt_id}/events")
def application_attempt_events(attempt_id: int):
    events = attempt_events(attempt_id)
    if events is None:
        raise HTTPException(status_code=404, detail="attempt not found")
    return {"items": events}


@router.post("/api/automation/run-browser")
async def automation_run_browser(payload: dict[str, Any] | None = None):
    payload = payload or {}
    dry_run = bool(payload.get("dry_run", True))
    if os.getenv("BROWSER_WORKER_ENABLED", "false").lower() != "true":
        raise HTTPException(
            status_code=503,
            detail="Browser worker disabled. Deploy Docker runtime and set BROWSER_WORKER_ENABLED=true.",
        )
    health = await browser_health_check()
    if not health.get("chromium_launchable"):
        raise HTTPException(
            status_code=503,
            detail={"message": "Chromium not launchable on this host", "browser_health": health},
        )
    policy = get_automation_settings()
    limit = min(int(policy.get("max_per_run", 5)), 10)
    drafts = list_application_drafts()
    results = []
    processed = 0
    for draft in drafts:
        if processed >= limit:
            break
        job = draft.get("job") or {}
        url = (job.get("url") or draft.get("url") or "").lower()
        if not any(h in url for h in ("greenhouse.io", "lever.co", "jobs.lever.co")):
            results.append({"draft_id": draft.get("id"), "status": "skipped", "reason": "not_greenhouse_or_lever"})
            continue
        if draft.get("status") in {"applied_manually", "interview", "offer", "rejected", "withdrawn"}:
            results.append({"draft_id": draft.get("id"), "status": "skipped", "reason": "already_actioned"})
            continue
        try:
            out = await run_application_worker(int(draft["id"]), dry_run=dry_run)
            results.append({"draft_id": draft.get("id"), "job": job.get("title"), "result": out})
            processed += 1
        except Exception as exc:
            log.exception("browser run failed draft=%s", draft.get("id"))
            results.append({"draft_id": draft.get("id"), "status": "error", "error": str(exc)[:180]})
    return {
        "dry_run": dry_run,
        "allow_submit_env": os.getenv("BROWSER_ALLOW_SUBMIT", "false").lower() == "true",
        "processed": processed,
        "results": results,
        "message": (
            "Dry-run only; no submit clicked."
            if dry_run or os.getenv("BROWSER_ALLOW_SUBMIT", "false").lower() != "true"
            else "Live submit permitted by env — verify confirmations carefully."
        ),
    }


@router.get("/api/portals/authenticated-status")
def authenticated_portals_status():
    return {
        "apply_supported": False,
        "portals": ["naukri", "linkedin", "indeed", "foundit", "bayt", "gulftalent"],
        "reason": (
            "These boards require user login sessions (storage_state). "
            "Passwords must never be stored in the app. Provision a Playwright "
            "storage_state file via BROWSER_STORAGE_STATE_PATH after manual login "
            "on a trusted machine, then each portal must be tested independently."
        ),
        "discovery_only": True,
        "next_step": "Complete Docker Chromium for Greenhouse/Lever first; then optional storage_state for one board.",
    }
