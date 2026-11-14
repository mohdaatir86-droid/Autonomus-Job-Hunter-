import logging
import os
import hmac
import base64
import hashlib
import json
import secrets
import time
import asyncio
from contextlib import asynccontextmanager
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from job_hunter import get_settings, save_settings, init_db
from job_sources import hunt_jobs, get_stats, list_jobs, set_job_status, LISTABLE_COUNTRIES
from gmail_alerts import build_auth_url, exchange_code, get_gmail_status, init_gmail_db, verify_oauth_state
from ai_assist import ai_status, generate_form_answers
from notify import notify_status, send_notification
from database import postgres_enabled, database_diagnostics, initialize_database_schema
from automation_settings import init_automation_settings, get_automation_settings, save_automation_settings
from browser_worker import run_application_worker, browser_health_check
from automation_routes import router as automation_router
from application_state import init_application_state_db
from outreach_routes import router as outreach_router
from outreach import init_outreach_db
from outreach_engine import tick as outreach_tick

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


PORTAL_SESSION_COOKIE = "jh_portal_session"
PORTAL_CSRF_COOKIE = "jh_portal_csrf"
PORTAL_SESSION_SECONDS = 7 * 24 * 60 * 60


def _secure_equal(left: str, right: str) -> bool:
    # compare_digest(str, str) rejects non-ASCII input. Request headers, cookies
    # and query parameters are untrusted Unicode and should fail closed, not 500.
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _portal_session_key() -> bytes | None:
    access_token = os.getenv("PORTAL_ACCESS_TOKEN", "").strip()
    if len(access_token) < 32:
        return None
    return hashlib.sha256(("job-hunter-google-session:" + access_token).encode("utf-8")).digest()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _issue_portal_session(email: str, now: int | None = None) -> str | None:
    key = _portal_session_key()
    if not key:
        return None
    issued = int(time.time()) if now is None else int(now)
    payload = _b64url(json.dumps({"email": email.strip().lower(), "iat": issued, "exp": issued + PORTAL_SESSION_SECONDS}, separators=(",", ":")).encode("utf-8"))
    signature = _b64url(hmac.new(key, payload.encode("ascii"), hashlib.sha256).digest())
    return payload + "." + signature


def _read_portal_session(token: str | None, now: int | None = None) -> dict[str, Any] | None:
    key = _portal_session_key()
    if not key or not token:
        return None
    try:
        payload_part, signature = token.split(".", 1)
        expected = _b64url(hmac.new(key, payload_part.encode("ascii"), hashlib.sha256).digest())
        if not _secure_equal(signature, expected):
            return None
        padded = payload_part + "=" * (-len(payload_part) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        current = int(time.time()) if now is None else int(now)
        if not isinstance(payload, dict) or not isinstance(payload.get("email"), str):
            return None
        if int(payload.get("exp", 0)) <= current or int(payload.get("iat", current + 1)) > current:
            return None
        if int(payload.get("exp", 0)) - int(payload.get("iat", current)) > PORTAL_SESSION_SECONDS:
            return None
        return payload
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _portal_session_matches_profile(session: dict[str, Any] | None) -> bool:
    if not session:
        return False
    profile_email = str(get_profile().get("email") or "").strip().lower()
    session_email = str(session.get("email") or "").strip().lower()
    return bool(profile_email and session_email and _secure_equal(profile_email, session_email))


def _verify_google_credential(credential: str) -> dict[str, Any]:
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    if not client_id:
        raise ValueError("Google sign-in is not configured")
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2 import id_token

    return id_token.verify_oauth2_token(credential, GoogleRequest(), audience=client_id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.scheduler = None
    initialize_database_schema()
    init_db()
    init_workflow_db()
    init_gmail_db()
    init_automation_settings()
    init_application_state_db()
    init_outreach_db()
    from outreach_engine import readiness_summary
    log.info("Outreach readiness (configuration only): %s", readiness_summary())
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
                        draft_candidates=True,
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
            scheduler.add_job(outreach_tick,"interval",minutes=1,id="approved-outreach",replace_existing=True,max_instances=1,coalesce=True)
            scheduler.start()
            app.state.scheduler = scheduler
            log.info("Job discovery scheduler started; interval=%s minutes", minutes)
        except Exception:
            log.exception("Scheduler could not start; manual hunt endpoint remains available")
    yield
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(
    title="Autonomous Job Hunter",
    version="0.6.0",
    description="Job Hunter: discovery, drafts, guarded Greenhouse/Lever browser apply (opt-in). Authenticated boards discovery-only.",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=Path(__file__).with_name("static")), name="static")
app.include_router(automation_router)
app.include_router(outreach_router)

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
    github_discovery = path == "/api/integrations/github/hunt" and request.method == "POST"
    if github_discovery:
        from github_discovery import verify_github_discovery_token

        supplied = request.headers.get("authorization", "")
        token = supplied[7:] if supplied.lower().startswith("bearer ") else ""
        if not token:
            return JSONResponse(status_code=401, content={"detail": "Unauthorized."})
        try:
            await verify_github_discovery_token(token)
        except ValueError:
            return JSONResponse(status_code=401, content={"detail": "Unauthorized."})
        except RuntimeError:
            return JSONResponse(status_code=503, content={"detail": "GitHub discovery authentication is temporarily unavailable."})
    if path == "/api/integrations/n8n/hunt" and request.method != "OPTIONS":
        expected = os.getenv("N8N_AUTOMATION_TOKEN", "").strip()
        if len(expected) < 32:
            return JSONResponse(
                status_code=503,
                content={"detail": "n8n discovery integration is not configured with a strong bearer token."},
            )
        supplied = request.headers.get("authorization", "")
        token = supplied[7:] if supplied.lower().startswith("bearer ") else ""
        if not token or not _secure_equal(token, expected):
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized."},
            )
    protected = (
        (request.method not in {"GET", "HEAD", "OPTIONS"} and path.startswith("/api/")
         and path not in {"/api/integrations/n8n/hunt", "/api/auth/google/session", "/api/auth/logout"}
         and not github_discovery)
        or path == "/api/gmail/auth"
        or path == "/api/gmail/status"
        or path == "/api/setup"
        or path in {"/api/jobs", "/api/stats", "/api/settings", "/api/sources", "/api/operational-readiness", "/health/db"}
        or path.startswith("/api/jobs/")
        or path.startswith("/api/gulf")
        or path.startswith("/api/outreach")
        or path == "/health/browser"
        or path == "/api/profile"
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
        if len(expected) < 32:
            return JSONResponse(
                status_code=503,
                content={"detail": "Private profile features are locked until PORTAL_ACCESS_TOKEN is configured in Render."},
            )
        supplied = request.headers.get("authorization", "")
        token = supplied[7:] if supplied.lower().startswith("bearer ") else ""
        if token and _secure_equal(token, expected):
            return await call_next(request)

        session = _read_portal_session(request.cookies.get(PORTAL_SESSION_COOKIE))
        if not _portal_session_matches_profile(session):
            return JSONResponse(
                status_code=401,
                content={"detail": "Sign in with the Google account saved in your profile, or enter your private access key."},
            )
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            csrf_cookie = request.cookies.get(PORTAL_CSRF_COOKIE, "")
            csrf_header = request.headers.get("x-jh-csrf", "")
            if not csrf_cookie or not csrf_header or not _secure_equal(csrf_cookie, csrf_header):
                return JSONResponse(status_code=403, content={"detail": "The request could not be verified. Refresh and try again."})
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
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/health/db")
def health_db():
    result = database_diagnostics()
    return JSONResponse(status_code=200 if result.get("connected") else 503, content=result)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "service": "autonomous-job-hunter"}


def _google_sign_in_settings():
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    portal_key_ready = bool(_portal_session_key())
    enabled = bool(client_id and portal_key_ready)
    if not client_id:
        reason = "Google sign-in needs GOOGLE_CLIENT_ID in Render and the portal callback added to the Google OAuth client."
    elif not portal_key_ready:
        reason = "The private portal access key must be configured before Google sign-in can be enabled."
    else:
        reason = "Sign in with the same Google email saved in your Job Hunter profile."
    return {"enabled": enabled, "client_id": client_id if enabled else None, "reason": reason}


GOOGLE_LOGIN_COOKIE = "jh_google_login_nonce"


def _google_login_nonce() -> str:
    value = str(int(time.time())) + "." + secrets.token_urlsafe(32)
    signature = _b64url(hmac.new(_portal_session_key(), ("google-login:" + value).encode(), hashlib.sha256).digest())
    return value + "." + signature


def _valid_google_login_nonce(nonce: str) -> bool:
    try:
        issued, random_value, signature = nonce.split(".")
        if not 0 <= time.time() - int(issued) <= 600 or len(random_value) < 32:
            return False
        expected = _b64url(hmac.new(_portal_session_key(), ("google-login:" + issued + "." + random_value).encode(), hashlib.sha256).digest())
        return _secure_equal(signature, expected)
    except (ValueError, TypeError, AttributeError):
        return False


@app.get("/api/auth/google/config")
def google_sign_in_config(request: Request):
    config = _google_sign_in_settings()
    if config["enabled"]:
        config["nonce"] = _google_login_nonce()
    response = JSONResponse(config, headers={"Cache-Control": "no-store"})
    if config["enabled"]:
        secure = request.url.scheme == "https" or os.getenv("RENDER_EXTERNAL_URL", "").startswith("https://")
        response.set_cookie(GOOGLE_LOGIN_COOKIE, config["nonce"], max_age=600, httponly=True,
                            secure=secure, samesite="strict", path="/api/auth/google/session")
    return response


@app.get("/api/auth/session")
def google_sign_in_session(request: Request):
    session = _read_portal_session(request.cookies.get(PORTAL_SESSION_COOKIE))
    if not _portal_session_matches_profile(session):
        return {"authenticated": False}
    return {"authenticated": True, "email": str(session.get("email", ""))}


@app.post("/api/auth/google/session")
async def google_sign_in(request: Request):
    """Verify Google's ID token and issue a short-lived, HttpOnly portal session."""
    is_json = request.headers.get("content-type", "").split(";", 1)[0].strip().lower() == "application/json"
    def failure(reason, status=401):
        # Fixed reason codes only; never log credentials, cookies or token payloads.
        log.warning("Google portal sign-in rejected: %s", reason)
        if is_json:
            messages = {"email_mismatch":"Use the same Google email saved in your Job Hunter profile.",
                        "browser_binding":"This sign-in page expired. Refresh Job Hunter before signing in again.",
                        "token_nonce_mismatch":"This Google response belongs to a different sign-in page. Refresh Job Hunter.",
                        "not_configured":"Google sign-in is not configured on the server. Use your private access key.",
                        "origin_mismatch":"Open Job Hunter directly before signing in."}
            return JSONResponse({"detail": messages.get(reason,"Google sign-in could not be verified. Refresh Job Hunter before trying again.")}, status_code=status)
        return RedirectResponse("/?google_signin=" + ("email_mismatch" if reason=="email_mismatch" else "not_configured" if reason=="not_configured" else "failed") + "#profilePanel", status_code=303)
    if not _google_sign_in_settings().get("enabled"):
        return failure("not_configured", 503)
    try:
        login_nonce = ""
        if is_json:
            expected_origin = (os.getenv("RENDER_EXTERNAL_URL") or os.getenv("PORTAL_PUBLIC_URL") or str(request.base_url)).rstrip("/")
            if request.headers.get("origin", "").rstrip("/") != expected_origin:
                return failure("origin_mismatch", 403)
            form = await request.json()
            if not isinstance(form, dict):return failure("invalid_body", 422)
            login_nonce = str(form.get("login_nonce") or "")
            cookie_nonce = request.cookies.get(GOOGLE_LOGIN_COOKIE, "")
            if not login_nonce or len(login_nonce)>400 or not cookie_nonce or not _secure_equal(login_nonce,cookie_nonce) or not _valid_google_login_nonce(login_nonce):
                return failure("browser_binding", 403)
        else:
            form = await request.form()
            form_csrf = str(form.get("g_csrf_token") or "")
            cookie_csrf = request.cookies.get("g_csrf_token", "")
            if not form_csrf or not cookie_csrf or not _secure_equal(form_csrf,cookie_csrf):
                return failure("csrf_mismatch", 403)
        credential = str(form.get("credential") or "")
        if not credential or len(credential) > 16384:
            return failure("missing_credential", 422)
        # google-auth fetches verification certificates through synchronous HTTP.
        # Keep login latency from blocking every request and scheduled job.
        claims = await asyncio.to_thread(_verify_google_credential, credential)
        if is_json and not _secure_equal(str(claims.get("nonce") or ""), login_nonce):
            return failure("token_nonce_mismatch",403)
        email = str(claims.get("email") or "").strip().lower()
        if claims.get("email_verified") is not True or not claims.get("sub") or not email:
            return failure("unverified_identity")
        profile_email = str(get_profile().get("email") or "").strip().lower()
        if not profile_email or not _secure_equal(profile_email, email):
            return failure("email_mismatch")
        session_cookie = _issue_portal_session(email)
        if not session_cookie:
            return failure("not_configured",503)
        response = JSONResponse({"authenticated":True}) if is_json else RedirectResponse("/?google_signin=success#profilePanel", status_code=303)
        public_origin = os.getenv("RENDER_EXTERNAL_URL", "").lower()
        secure_cookie = request.url.scheme.lower() == "https" or public_origin.startswith("https://")
        csrf_cookie = secrets.token_urlsafe(32)
        response.set_cookie(
            PORTAL_SESSION_COOKIE,
            session_cookie,
            max_age=PORTAL_SESSION_SECONDS,
            httponly=True,
            secure=secure_cookie,
            samesite="lax",
            path="/",
        )
        response.set_cookie(
            PORTAL_CSRF_COOKIE,
            csrf_cookie,
            max_age=PORTAL_SESSION_SECONDS,
            httponly=False,
            secure=secure_cookie,
            samesite="lax",
            path="/",
        )
        response.delete_cookie(GOOGLE_LOGIN_COOKIE,path="/api/auth/google/session")
        return response
    except Exception as exc:
        log.warning("Google sign-in failed (%s)", type(exc).__name__)
        return failure("verification_failed")


@app.post("/api/auth/logout")
def google_sign_out():
    response = JSONResponse({"signed_out": True})
    response.delete_cookie(PORTAL_SESSION_COOKIE, path="/")
    response.delete_cookie(PORTAL_CSRF_COOKIE, path="/")
    return response


@app.get("/api/stats")
def stats():
    return get_stats()


@app.get("/api/operational-readiness")
def operational_readiness():
    """Report actual automation readiness without implying a successful application."""
    from job_sources import get_source_report
    from automation_settings import get_automation_settings

    stats_snapshot = get_stats()
    source_report = get_source_report()
    profile = get_profile()
    policy = get_automation_settings()
    gmail = get_gmail_status()
    return {
        "status": "discovery_and_draft_only",
        "jobs_in_current_window": stats_snapshot.get("total_jobs", 0),
        "last_hunt": stats_snapshot.get("last_run"),
        "source_report_available": source_report is not None,
        "resume_uploaded": bool(profile.get("has_resume")),
        "gmail_connected": bool(gmail.get("connected")),
        "application_drafts_supported": True,
        "automatic_submission_enabled": False,
        "application_policy_enabled": bool(policy.get("enabled")),
        "blockers": [
            *([] if source_report is not None else ["No persisted source run report; run discovery and verify source responses."]),
            *([] if profile.get("has_resume") else ["Upload a PDF resume to prepare grounded applications."]),
            "Automatic third-party submission is intentionally disabled pending verified authorized ATS flow.",
        ],
    }


@app.get("/api/jobs")
def jobs(
    country: str | None = None,
    opportunity: str | None = None,
    min_score: int = Query(default=0, ge=0, le=100),
    status: str | None = None,
    posted_within_days: int | None = Query(default=None, ge=1, le=365),
    q: str | None = Query(default=None, max_length=120),
    source: str | None = Query(default=None, max_length=80),
    gulf_open: bool = False,
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
            gulf_open=gulf_open,
        )
    }


@app.get("/api/gulf/sources")
def gulf_source_catalog():
    from gulf_sources import catalog, COUNTRIES
    from job_sources import get_source_report
    report = get_source_report() or {}
    feed = next((r for r in report.get("sources", []) if r.get("name") == "Gulf employer feeds"), {})
    return {"items": catalog(), "countries": list(COUNTRIES.values()), "feed": feed,
            "checked_at": report.get("checked_at"), "note": "Direct feeds refresh on the existing discovery schedule. External links and indexed boards are not direct integrations. No source list is exhaustive."}


@app.post("/api/gulf/refresh")
async def refresh_gulf_jobs():
    # Public employer feeds only: no paid search, mailbox read, outreach tick or notification.
    return await hunt_jobs(gulf_only=True)


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: int):
    from job_hunter import connect
    from gulf_sources import evidence, current_evidence, fit
    with connect() as conn:
        record = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not record:
        raise HTTPException(404, "Job not found")
    row = dict(record)
    meta = evidence(row)
    recently_checked = bool(current_evidence(row))
    suitable, notes = fit(row, get_profile_full(), get_settings())
    row.pop("raw_json", None)
    row["gulf_evidence"] = meta
    row["fit_notes"] = notes if suitable else ["Review role requirements against your profile before applying."]
    row["published_recently_checked"] = recently_checked and row["status"] != "closed"
    row["apply_url"] = meta.get("apply_url") if row["published_recently_checked"] else None
    return row


@app.post("/api/hunt")
@app.post("/api/integrations/n8n/hunt")
@app.post("/api/integrations/github/hunt")
async def hunt(background_tasks: BackgroundTasks):
    result = await hunt_jobs()
    background_tasks.add_task(_bounded_outreach_tick)
    return result


async def _bounded_outreach_tick():
    try:
        await asyncio.wait_for(outreach_tick(),timeout=90)
    except Exception as exc:
        log.warning("Outreach queue tick deferred: %s",type(exc).__name__)


@app.get("/api/settings")
def settings():
    return get_settings()


@app.post("/api/settings")
def update_settings(payload: dict[str, Any]):
    allowed = {
        "target_countries",
        "target_titles",
        "preferred_locations",
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
        if key in {"target_countries", "target_titles", "preferred_locations"}:
            if not isinstance(value, list) or (key != "preferred_locations" and not value) or len(value) > 100 or not all(isinstance(item, str) and item.strip() and len(item) <= 150 for item in value):
                raise HTTPException(status_code=422, detail=key + " must be a non-empty list of strings")
            if key == "target_countries" and any(item.strip() not in LISTABLE_COUNTRIES for item in value):
                raise HTTPException(status_code=422, detail="Choose supported countries: India, Gulf countries, or Canada.")
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
        "work_arrangement": lambda v: isinstance(v, str) and v in {"all", "onsite", "hybrid", "remote"},
        "posted_within_days": lambda v: isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 365,
        "visa_preference": lambda v: isinstance(v, str) and v in {"any", "sponsor"},
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
    candidates = list_jobs(min_score=policy["min_score"], status=None, limit=policy["max_per_run"], draft_candidates=True)
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
    candidates = list_jobs(min_score=policy["min_score"], status=None, limit=policy["max_per_run"], draft_candidates=True)
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


@app.post("/api/gmail/auth")
def gmail_auth(request: Request):
    return _gmail_connect_response(request)


@app.post("/api/outreach/gmail/connect")
def outreach_gmail_connect(request: Request):
    return _gmail_connect_response(request, allow_send=True)


def _gmail_connect_response(request: Request, *, allow_send: bool = False):
    if not (get_profile().get("email") or "").strip():
        raise HTTPException(status_code=409, detail="Save your profile email before connecting Gmail.")
    try:
        url = build_auth_url(allow_send=True) if allow_send else build_auth_url()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    state = parse_qs(urlparse(url).query)["state"][0]
    response = JSONResponse({"url": url})
    secure = request.url.scheme == "https" or os.getenv("RENDER_EXTERNAL_URL", "").startswith("https://")
    response.set_cookie("jh_gmail_state", state, max_age=600, httponly=True, secure=secure, samesite="lax", path="/api/gmail")
    return response


@app.get("/api/gmail/callback")
async def gmail_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    cookie_state = request.cookies.get("jh_gmail_state", "")
    if not cookie_state or not state or not _secure_equal(cookie_state, state) or not verify_oauth_state(state, consume=True):
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state. Connect Gmail again from your private profile.")
    if error:
        response = RedirectResponse("/?gmail=denied#sources", status_code=303)
        response.delete_cookie("jh_gmail_state", path="/api/gmail")
        return response
    result = await exchange_code(code)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "oauth failed"))
    response = RedirectResponse("/")
    response.delete_cookie("jh_gmail_state", path="/api/gmail")
    return response


@app.post("/api/notify/test")
async def notify_test():
    result = await send_notification("Job Hunter test: your job alerts are connected. New matching operations roles will appear here after discovery.")
    if not result.get("sent"):
        raise HTTPException(status_code=400, detail=result)
    return result


@app.get("/api/sources")
def sources():
    from job_sources import get_source_report, BUILTIN_GREENHOUSE_BOARDS, BUILTIN_LEVER_SITES
    from portal_registry import get_portal_registry

    gmail = dict(get_gmail_status())
    gmail.pop("email", None)
    report = get_source_report() or {}
    by_name = {item.get("name"): item for item in (report.get("sources") or [])}
    greenhouse_env = bool(os.getenv("GREENHOUSE_BOARDS", "").strip())
    catalog = [
        {"name": "Gulf employer feeds", "type": "Public employer ATS", "configured": True,
         "connection_mode": "Delivery Hero / Talabat, Careem, Lalamove, GMG and Aramex; six Gulf countries; no key"},
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
            "configured": True,
            "connection_mode": "LEVER_SITES slugs" if os.getenv("LEVER_SITES", "").strip() else "Built-in public boards: " + ", ".join(BUILTIN_LEVER_SITES),
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
        if item["name"] == "Lever company boards" and item["status"] == "missing_configuration" and "LEVER_SITES not set" in item.get("detail", ""):
            item.update(status="unknown", detail="Public Lever boards enabled; run discovery to check them.", fetched=0)
        if item["name"] == "Gmail job alerts" and gmail.get("connected") and item["status"] == "missing_configuration":
            item.update(status="unknown", detail="Gmail connected; run discovery to import job alerts.", fetched=0)
        item["healthy"] = item["status"] == "working"
    portal_coverage = {
        key: portal
        for key, portal in get_portal_registry()["portals"].items()
        if portal.get("market_focus")
    }
    google_jobs_status = (by_name.get("Google Jobs via SerpApi") or {}).get("status", "unknown")
    portal_counts = report.get("portal_counts") or {}
    portal_counts_available = isinstance(report.get("portal_counts"), dict)
    for key, portal in portal_coverage.items():
        portal["fetch_channel"] = (
            "Google Jobs + connected Gmail alerts"
            if gmail.get("connected")
            else "Google Jobs; Gmail alerts need connection"
        )
        portal["source_search_status"] = google_jobs_status
        portal["last_hunt_fetched"] = int(portal_counts.get(key, 0) or 0) if portal_counts_available else None
        portal["last_hunt_checked_at"] = report.get("checked_at") if portal_counts_available else None
        if not portal.get("discovery"):
            portal["integration_health"] = "key_missing"
        elif google_jobs_status in {"unauthorized", "rate_limited", "unavailable"}:
            portal["integration_health"] = google_jobs_status
        elif google_jobs_status in {"working", "degraded"}:
            portal["integration_health"] = "aggregated_search"
    return {
        "sources": catalog,
        "checked_at": report.get("checked_at"),
        "note": "Portal results come from Google Jobs indexing and, after Gmail read-only access is connected, job-alert emails. Direct portal APIs and logins are not configured. Coverage depends on provider results; applications are not submitted.",
        "gmail": gmail,
        "ai": ai_status(),
        "notifications": notify_status(),
        "portal_coverage": portal_coverage,
        "live_applications_enabled": False,
        "admin_locked": bool(os.getenv("PORTAL_ACCESS_TOKEN", "").strip()),
    }


@app.get("/api/setup")
def setup_status():
    ready, missing = application_profile_readiness()
    return {
        "profile": {"ready": ready, "missing": missing},
        "discovery": {"public_sources": True, "google_jobs_configured": bool(os.getenv("SERPAPI_API_KEY", "").strip())},
        "notifications": notify_status(),
        "scheduler": {"enabled": os.getenv("ENABLE_SCHEDULER", "true").lower() == "true", "internal_discovery": internal_hunt_scheduler_enabled()},
        "submission_enabled": False,
        "settings": get_settings(),
    }
