"""Guarded browser worker for public Greenhouse/Lever application pages.

Requires Playwright + Docker Chromium (or BROWSER_WS_ENDPOINT).
Only fills fields grounded in the candidate profile; stops on CAPTCHA/OTP,
legal declarations, unknown required fields, login walls, unsupported hosts.
Integrates with application_state machine for idempotent attempts.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from application_workflow import get_profile_full, update_draft_status
from application_state import (
    get_or_create_attempt,
    transition,
    already_applied,
    stable_job_key,
)
from database import connect as db_connect
from job_hunter import DB_PATH

ALLOWED_HOSTS = {
    "greenhouse.io",
    "boards.greenhouse.io",
    "jobs.lever.co",
    "lever.co",
}

BLOCK_TERMS = re.compile(
    r"captcha|recaptcha|hcaptcha|one.?time.?password|verification code|\botp\b|"
    r"work authorization|legally authorized|sponsorship|visa status|disability|"
    r"veteran|race|ethnicity|gender|sexual orientation|criminal conviction|"
    r"background check|terms and conditions|certify|attest|i agree|i certify",
    re.I,
)
CONFIRM_TERMS = re.compile(
    r"application (?:has been )?(?:submitted|received)|thank you for applying|"
    r"successfully applied|your application is complete|we received your application|"
    r"application received|thanks for applying",
    re.I,
)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _allowed_url(url: str) -> bool:
    try:
        p = urlparse(url)
        host = (p.hostname or "").lower()
        return p.scheme == "https" and any(
            host == h or host.endswith("." + h) for h in ALLOWED_HOSTS
        )
    except Exception:
        return False


def _job_for_draft(draft_id: int):
    with db_connect(DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT d.*, j.title, j.company, j.url, j.match_score, j.id AS job_id,
                   j.source, j.source_id
            FROM application_drafts d
            JOIN jobs j ON j.id = d.job_id
            WHERE d.id = ?
            """,
            (draft_id,),
        ).fetchone()
    return dict(row) if row else None


def _event(draft_id: int, event: str, note: str):
    with db_connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO application_events(draft_id,event,note,created_at) VALUES (?,?,?,?)",
            (draft_id, event, note[:1000], _now()),
        )


def _profile_value(profile, label, name, placeholder):
    key = " ".join([label or "", name or "", placeholder or ""]).lower()
    if any(x in key for x in ["first name", "firstname", "given name"]):
        return (profile.get("full_name") or "").split(" ", 1)[0]
    if any(x in key for x in ["last name", "lastname", "family name", "surname"]):
        parts = (profile.get("full_name") or "").split()
        return parts[-1] if parts else ""
    if "name" in key and "company" not in key and profile.get("full_name"):
        return profile["full_name"]
    if "email" in key:
        return profile.get("email", "")
    if any(x in key for x in ["phone", "mobile", "telephone"]):
        return profile.get("phone", "")
    if "linkedin" in key:
        return profile.get("linkedin_url", "")
    if any(
        x in key
        for x in [
            "resume",
            "cv",
            "cover letter",
            "website",
            "portfolio",
            "current company",
            "salary",
            "notice period",
            "expected",
            "current ctc",
            "preferred",
        ]
    ):
        return None
    return None


def detect_portal(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "greenhouse" in host:
        return "greenhouse"
    if "lever" in host:
        return "lever"
    return "unknown"


async def _run(draft_id: int, dry_run: bool = True) -> dict:
    if os.getenv("BROWSER_WORKER_ENABLED", "false").lower() != "true":
        return {
            "status": "disabled",
            "detail": "Set BROWSER_WORKER_ENABLED=true only after Docker Chromium is verified via /health/browser.",
        }

    job = _job_for_draft(draft_id)
    if not job:
        return {"status": "not_found", "detail": "Application draft not found."}

    url = job.get("url") or ""
    portal = detect_portal(url)
    source_id = str(job.get("source_id") or "") or None

    attempt = get_or_create_attempt(
        portal=portal,
        source_id=source_id,
        url=url,
        job_id=job.get("job_id"),
        draft_id=draft_id,
        title=job.get("title") or "",
        company=job.get("company") or "",
    )
    job_key = attempt["job_key"]
    if already_applied(job_key):
        return {
            "status": "skipped",
            "reason": "already_applied_or_in_flight",
            "attempt_id": attempt["id"],
            "state": attempt["state"],
        }

    if not _allowed_url(url):
        _event(draft_id, "browser_skipped", "Unsupported host; only Greenhouse/Lever public HTTPS URLs allowed.")
        try:
            transition(attempt["id"], "skipped", note="unsupported_host", reason="unsupported_host")
        except ValueError:
            pass
        return {"status": "skipped", "reason": "unsupported_host", "url": url, "attempt_id": attempt["id"]}

    profile = get_profile_full()
    if not profile.get("resume_text"):
        try:
            transition(attempt["id"], "waiting_for_user", note="resume_missing", reason="resume_missing")
        except ValueError:
            pass
        return {
            "status": "blocked",
            "reason": "resume_missing",
            "detail": "Upload a text-based PDF resume before browser applications.",
            "attempt_id": attempt["id"],
        }

    try:
        from playwright.async_api import async_playwright
    except Exception:
        return {"status": "unavailable", "detail": "Playwright dependency is not installed."}

    try:
        transition(attempt["id"], "scored", note="matched policy")
        transition(attempt["id"], "queued", note="browser worker started")
        transition(attempt["id"], "opening_job", note=url[:200])
    except ValueError:
        pass

    ws = os.getenv("BROWSER_WS_ENDPOINT", "").strip()
    async with async_playwright() as p:
        browser = await (
            p.chromium.connect_over_cdp(ws) if ws else p.chromium.launch(headless=True)
        )
        context = await browser.new_context(accept_downloads=False)
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1200)
            current_url = page.url
            if not _allowed_url(current_url):
                transition(attempt["id"], "failed", note="redirected_outside_allowlist", reason="redirect")
                return {"status": "blocked", "reason": "redirected_outside_allowlist", "attempt_id": attempt["id"]}

            body = (await page.locator("body").inner_text(timeout=5000))[:20000]
            if re.search(r"captcha|verify you are human|one-time password|enter the code sent", body, re.I):
                _event(draft_id, "browser_blocked", "CAPTCHA/OTP detected; manual action required.")
                transition(attempt["id"], "waiting_for_user", note="captcha_or_otp", reason="captcha_or_otp")
                return {"status": "blocked", "reason": "captcha_or_otp", "attempt_id": attempt["id"]}

            if re.search(r"sign in|log in|create an account", body[:3000], re.I) and await page.locator("form").count() == 0:
                transition(attempt["id"], "expired_session", note="login_required", reason="login_required")
                return {"status": "blocked", "reason": "login_required", "attempt_id": attempt["id"]}

            forms = page.locator("form")
            if await forms.count() == 0:
                transition(attempt["id"], "failed", note="no_application_form", reason="no_form")
                return {"status": "blocked", "reason": "no_application_form", "attempt_id": attempt["id"]}

            try:
                transition(attempt["id"], "form_detected", note="form present")
                transition(attempt["id"], "filling_form", note="mapping profile fields")
            except ValueError:
                pass

            form = forms.first
            inputs = form.locator("input, textarea, select")
            count = min(await inputs.count(), 100)
            filled, unknown_required, blocked_fields = [], [], []

            for i in range(count):
                el = inputs.nth(i)
                try:
                    if not await el.is_visible() or not await el.is_enabled():
                        continue
                    typ = (await el.get_attribute("type") or "").lower()
                    name = await el.get_attribute("name") or ""
                    ident = await el.get_attribute("id") or ""
                    placeholder = await el.get_attribute("placeholder") or ""
                    label = ""
                    if ident:
                        lab = page.locator(f'label[for="{ident}"]')
                        if await lab.count():
                            label = (await lab.first.inner_text())[:200]
                    if not label:
                        label = name or ident or placeholder
                    required = (
                        await el.get_attribute("required") is not None
                        or (await el.get_attribute("aria-required") or "").lower() == "true"
                    )
                    combined = " ".join([label, name, placeholder])
                    if BLOCK_TERMS.search(combined):
                        if required:
                            blocked_fields.append(label[:120])
                        continue
                    if typ in {"hidden", "submit", "button", "password", "file", "checkbox", "radio"}:
                        if typ == "file" and required:
                            unknown_required.append(label[:120] or "file_upload")
                        continue
                    value = _profile_value(profile, label, name, placeholder)
                    if value is not None and str(value).strip():
                        if typ == "email":
                            value = profile.get("email", "")
                        elif typ == "tel":
                            value = profile.get("phone", "")
                        await el.fill(str(value), timeout=2000)
                        filled.append(label[:120])
                    elif required and not (await el.input_value()).strip():
                        unknown_required.append(label[:120])
                except Exception:
                    continue

            if blocked_fields:
                _event(
                    draft_id,
                    "browser_blocked",
                    "Required sensitive/legal fields need manual answers: " + ", ".join(blocked_fields[:8]),
                )
                transition(
                    attempt["id"],
                    "waiting_for_user",
                    note="legal_or_sensitive",
                    reason="legal_or_sensitive_required_fields",
                )
                return {
                    "status": "blocked",
                    "reason": "legal_or_sensitive_required_fields",
                    "fields": blocked_fields[:8],
                    "filled": filled,
                    "attempt_id": attempt["id"],
                }

            if unknown_required:
                _event(
                    draft_id,
                    "browser_blocked",
                    "Unknown required fields need manual review: " + ", ".join(unknown_required[:8]),
                )
                transition(
                    attempt["id"],
                    "validation_failed",
                    note="unknown_required",
                    reason="unknown_required_fields",
                )
                return {
                    "status": "blocked",
                    "reason": "unknown_required_fields",
                    "fields": unknown_required[:8],
                    "filled": filled,
                    "attempt_id": attempt["id"],
                }

            try:
                transition(attempt["id"], "ready_to_submit", note=f"filled={len(filled)}")
            except ValueError:
                pass

            allow_submit = os.getenv("BROWSER_ALLOW_SUBMIT", "false").lower() == "true"
            if dry_run or not allow_submit:
                return {
                    "status": "ready_to_submit",
                    "filled_fields": filled,
                    "attempt_id": attempt["id"],
                    "detail": "Form filled; submission disabled until BROWSER_ALLOW_SUBMIT=true and dry_run=false.",
                }

            transition(attempt["id"], "submitting", note="click submit")
            submit = form.locator(
                'button[type="submit"], input[type="submit"], '
                'button:has-text("Submit application"), button:has-text("Submit"), button:has-text("Apply")'
            )
            if await submit.count() == 0:
                transition(attempt["id"], "failed", note="submit_button_not_found", reason="no_submit")
                return {
                    "status": "blocked",
                    "reason": "submit_button_not_found",
                    "filled_fields": filled,
                    "attempt_id": attempt["id"],
                }

            await submit.first.click(timeout=5000)
            await page.wait_for_timeout(2500)
            result_text = (await page.locator("body").inner_text(timeout=5000))[:20000]
            if CONFIRM_TERMS.search(result_text):
                evidence = CONFIRM_TERMS.search(result_text).group(0)[:200]
                update_draft_status(
                    draft_id,
                    "applied_manually",
                    "Browser worker confirmed submission at " + _now(),
                )
                transition(
                    attempt["id"],
                    "submitted",
                    note="confirmation detected",
                    confirmation_evidence=evidence,
                )
                _event(
                    draft_id,
                    "submission_confirmed",
                    "ATS confirmation: " + evidence + " host=" + (urlparse(page.url).hostname or ""),
                )
                return {
                    "status": "submitted_confirmed",
                    "filled_fields": filled,
                    "confirmation_detected": True,
                    "confirmation_evidence": evidence,
                    "attempt_id": attempt["id"],
                }

            transition(
                attempt["id"],
                "submitted_unverified",
                note="clicked submit but no confirmation text",
                reason="no_confirmation",
            )
            _event(
                draft_id,
                "submission_unconfirmed",
                "Submit clicked; no confirmation text — verify manually before retry.",
            )
            return {
                "status": "submitted_unverified",
                "filled_fields": filled,
                "confirmation_detected": False,
                "attempt_id": attempt["id"],
            }
        finally:
            await context.close()
            await browser.close()


async def run_application_worker(draft_id: int, dry_run: bool = True) -> dict:
    return await _run(draft_id, dry_run=dry_run)


async def browser_health_check() -> dict:
    """Launch Chromium briefly and report version. Never exposes secrets."""
    result = {
        "browser_worker_enabled": os.getenv("BROWSER_WORKER_ENABLED", "false").lower() == "true",
        "allow_submit": os.getenv("BROWSER_ALLOW_SUBMIT", "false").lower() == "true",
        "playwright_available": False,
        "chromium_launchable": False,
        "chromium_version": None,
        "error": None,
        "checked_at": _now(),
    }
    try:
        from playwright.async_api import async_playwright

        result["playwright_available"] = True
    except Exception as exc:
        result["error"] = f"playwright_import: {type(exc).__name__}"
        return result

    ws = os.getenv("BROWSER_WS_ENDPOINT", "").strip()
    try:
        async with async_playwright() as p:
            if ws:
                browser = await p.chromium.connect_over_cdp(ws)
            else:
                browser = await p.chromium.launch(headless=True)
            try:
                result["chromium_launchable"] = True
                result["chromium_version"] = browser.version
            finally:
                await browser.close()
    except Exception as exc:
        result["error"] = f"chromium_launch: {type(exc).__name__}: {str(exc)[:200]}"
    return result
