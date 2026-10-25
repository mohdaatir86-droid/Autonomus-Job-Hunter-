"""Guarded browser worker for public Greenhouse/Lever application pages.

Requires Playwright and BROWSER_WS_ENDPOINT (remote Chromium) or local Chromium.
Only fills fields grounded in the candidate profile/draft; stops on CAPTCHA/OTP,
legal declarations, unknown required fields, login walls, and unsupported hosts.
"""
import asyncio
import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from application_workflow import get_profile_full, list_application_drafts, update_draft_status, get_draft_events
from database import connect as db_connect
from job_hunter import DB_PATH

ALLOWED_HOSTS = {"greenhouse.io", "boards.greenhouse.io", "jobs.lever.co", "lever.co"}
BLOCK_TERMS = re.compile(r"captcha|recaptcha|hcaptcha|one.?time.?password|verification code|\botp\b|work authorization|legally authorized|sponsorship|visa status|disability|veteran|race|ethnicity|gender|sexual orientation|criminal conviction|background check|terms and conditions|certify|attest", re.I)
CONFIRM_TERMS = re.compile(r"application (?:has been )?(?:submitted|received)|thank you for applying|successfully applied|your application is complete|we received your application", re.I)

def _now():
    return datetime.now(timezone.utc).isoformat()

def _allowed_url(url):
    try:
        p = urlparse(url)
        host = (p.hostname or "").lower()
        return p.scheme == "https" and any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)
    except Exception:
        return False

def _job_for_draft(draft_id):
    with db_connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT d.*, j.title, j.company, j.url, j.match_score, j.id AS job_id FROM application_drafts d JOIN jobs j ON j.id=d.job_id WHERE d.id=?",
            (draft_id,),
        ).fetchone()
    return dict(row) if row else None

def _event(draft_id, event, note):
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
        return (profile.get("full_name") or "").split(" ")[-1]
    if "name" in key and profile.get("full_name"):
        return profile["full_name"]
    if "email" in key:
        return profile.get("email", "")
    if any(x in key for x in ["phone", "mobile", "telephone"]):
        return profile.get("phone", "")
    if "linkedin" in key:
        return profile.get("linkedin_url", "")
    if any(x in key for x in ["resume", "cv", "cover letter", "website", "portfolio", "current company", "salary", "notice period"]):
        return None
    return None

async def _run(draft_id, dry_run=True):
    if os.getenv("BROWSER_WORKER_ENABLED", "false").lower() != "true":
        return {"status": "disabled", "detail": "Set BROWSER_WORKER_ENABLED=true only after configuring a secure browser runtime."}
    job = _job_for_draft(draft_id)
    if not job:
        return {"status": "not_found", "detail": "Application draft not found."}
    url = job.get("url") or ""
    if not _allowed_url(url):
        _event(draft_id, "browser_skipped", "Unsupported host; only Greenhouse/Lever public application URLs are allowed.")
        return {"status": "skipped", "reason": "unsupported_host", "url": url}
    profile = get_profile_full()
    if not profile.get("resume_text"):
        return {"status": "blocked", "reason": "resume_missing", "detail": "Upload a text-based PDF resume before browser applications."}
    try:
        from playwright.async_api import async_playwright
    except Exception:
        return {"status": "unavailable", "detail": "Playwright dependency is not installed."}

    ws = os.getenv("BROWSER_WS_ENDPOINT", "").strip()
    async with async_playwright() as p:
        browser = await (p.chromium.connect_over_cdp(ws) if ws else p.chromium.launch(headless=True))
        context = await browser.new_context(accept_downloads=False)
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1200)
            current_url = page.url
            if not _allowed_url(current_url):
                return {"status": "blocked", "reason": "redirected_outside_allowlist"}
            body = (await page.locator("body").inner_text(timeout=5000))[:20000]
            if re.search(r"captcha|verify you are human|one-time password|enter the code sent", body, re.I):
                _event(draft_id, "browser_blocked", "CAPTCHA/OTP or human verification detected; manual action required.")
                return {"status": "blocked", "reason": "captcha_or_otp"}
            if re.search(r"sign in|log in|create an account", body[:3000], re.I) and not page.locator("form").count():
                return {"status": "blocked", "reason": "login_required"}
            forms = page.locator("form")
            if await forms.count() == 0:
                return {"status": "blocked", "reason": "no_application_form"}
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
                    required = await el.get_attribute("required") is not None or (await el.get_attribute("aria-required") or "").lower() == "true"
                    combined = " ".join([label, name, placeholder])
                    if BLOCK_TERMS.search(combined):
                        if required:
                            blocked_fields.append(label[:120])
                        continue
                    if typ in {"hidden", "submit", "button", "password", "file", "checkbox", "radio"}:
                        if typ == "file" and required:
                            unknown_required.append(label[:120])
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
                _event(draft_id, "browser_blocked", "Required sensitive/legal fields require manual answers: " + ", ".join(blocked_fields[:8]))
                return {"status": "blocked", "reason": "legal_or_sensitive_required_fields", "fields": blocked_fields[:8], "filled": filled}
            if unknown_required:
                _event(draft_id, "browser_blocked", "Unknown required fields need manual review: " + ", ".join(unknown_required[:8]))
                return {"status": "blocked", "reason": "unknown_required_fields", "fields": unknown_required[:8], "filled": filled}
            if dry_run or os.getenv("BROWSER_ALLOW_SUBMIT", "false").lower() != "true":
                return {"status": "ready_to_submit", "filled_fields": filled, "detail": "Form filled in a temporary browser session; submission is disabled until explicitly enabled in Render."}
            submit = form.locator('button[type="submit"], input[type="submit"], button:has-text("Submit application"), button:has-text("Submit"), button:has-text("Apply")')
            if await submit.count() == 0:
                return {"status": "blocked", "reason": "submit_button_not_found", "filled_fields": filled}
            await submit.first.click(timeout=5000)
            await page.wait_for_timeout(2500)
            result_text = (await page.locator("body").inner_text(timeout=5000))[:20000]
            if CONFIRM_TERMS.search(result_text):
                update_draft_status(draft_id, "applied_manually", "Browser worker confirmed submission on ATS page at " + _now())
                _event(draft_id, "submission_confirmed", "ATS confirmation text detected. URL host: " + (urlparse(page.url).hostname or "unknown"))
                return {"status": "submitted_confirmed", "filled_fields": filled, "confirmation_detected": True}
            _event(draft_id, "submission_unconfirmed", "Submit action was attempted but no confirmation text was detected; verify manually.")
            return {"status": "submitted_unconfirmed", "filled_fields": filled, "confirmation_detected": False}
        finally:
            await context.close()
            await browser.close()

async def run_application_worker(draft_id, dry_run=True):
    return await _run(draft_id, dry_run=dry_run)
