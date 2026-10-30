"""Explicit portal capability registry.

Distinguishes fully supported, partially supported, and unsupported portals.
Never claim a portal is supported until discovery + application flow has been tested.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# Capability flags (all default False unless proven)
PORTAL_REGISTRY: dict[str, dict[str, Any]] = {
    "arbeitnow": {
        "display_name": "Arbeitnow",
        "discovery": True,
        "job_details": True,
        "application_form": False,
        "resume_upload": False,
        "submission": False,
        "confirmation_verification": False,
        "auth_required": False,
        "auth_status": "n/a",
        "integration_health": "healthy",
        "last_successful_test": None,
        "notes": "Public job API only. No application flow.",
    },
    "remotive": {
        "display_name": "Remotive",
        "discovery": True,
        "job_details": True,
        "application_form": False,
        "resume_upload": False,
        "submission": False,
        "confirmation_verification": False,
        "auth_required": False,
        "auth_status": "n/a",
        "integration_health": "healthy",
        "last_successful_test": None,
        "notes": "Public remote-jobs API. No application flow.",
    },
    "jobicy": {
        "display_name": "Jobicy",
        "discovery": True,
        "job_details": True,
        "application_form": False,
        "resume_upload": False,
        "submission": False,
        "confirmation_verification": False,
        "auth_required": False,
        "auth_status": "n/a",
        "integration_health": "healthy",
        "last_successful_test": None,
        "notes": "Public remote-jobs API. No application flow.",
    },
    "serpapi_google_jobs": {
        "display_name": "Google Jobs (SerpApi)",
        "market_focus": "India + Gulf",
        "discovery": True,
        "job_details": True,
        "application_form": False,
        "resume_upload": False,
        "submission": False,
        "confirmation_verification": False,
        "auth_required": False,
        "auth_status": "n/a",
        "integration_health": "configured_if_key_set",
        "last_successful_test": None,
        "notes": "Discovery only. Surfaces Naukri/LinkedIn/Indeed/Bayt/GulfTalent links when present.",
    },
    "greenhouse": {
        "display_name": "Greenhouse (public boards)",
        "discovery": True,
        "job_details": True,
        "application_form": True,
        "resume_upload": False,
        "submission": False,
        "confirmation_verification": True,
        "auth_required": False,
        "auth_status": "n/a",
        "integration_health": "partial",
        "last_successful_test": None,
        "notes": "Public ATS forms via browser worker (dry-run default). CAPTCHA/OTP/legal fields stop the run.",
    },
    "lever": {
        "display_name": "Lever (public boards)",
        "discovery": True,
        "job_details": True,
        "application_form": True,
        "resume_upload": False,
        "submission": False,
        "confirmation_verification": True,
        "auth_required": False,
        "auth_status": "n/a",
        "integration_health": "partial",
        "last_successful_test": None,
        "notes": "Public ATS forms via browser worker (dry-run default). CAPTCHA/OTP/legal fields stop the run.",
    },
    "naukri": {
        "display_name": "Naukri",
        "market_focus": "India",
        "discovery": True,
        "job_details": False,
        "application_form": False,
        "resume_upload": False,
        "submission": False,
        "confirmation_verification": False,
        "auth_required": True,
        "auth_status": "not_configured",
        "integration_health": "unsupported_application",
        "last_successful_test": None,
        "notes": "Discovery only (SerpApi/Gmail). Authenticated application not implemented. Do not store passwords.",
    },
    "linkedin": {
        "display_name": "LinkedIn Jobs",
        "market_focus": "India + Gulf",
        "discovery": True,
        "job_details": False,
        "application_form": False,
        "resume_upload": False,
        "submission": False,
        "confirmation_verification": False,
        "auth_required": True,
        "auth_status": "not_configured",
        "integration_health": "unsupported_application",
        "last_successful_test": None,
        "notes": "Discovery only. Easy Apply / authenticated apply not implemented.",
    },
    "indeed": {
        "display_name": "Indeed",
        "market_focus": "India + Gulf",
        "discovery": True,
        "job_details": False,
        "application_form": False,
        "resume_upload": False,
        "submission": False,
        "confirmation_verification": False,
        "auth_required": True,
        "auth_status": "not_configured",
        "integration_health": "unsupported_application",
        "last_successful_test": None,
        "notes": "Discovery only via SerpApi/Gmail. No automated apply.",
    },
    "foundit": {
        "display_name": "Foundit (Monster)",
        "market_focus": "India",
        "discovery": True,
        "job_details": False,
        "application_form": False,
        "resume_upload": False,
        "submission": False,
        "confirmation_verification": False,
        "auth_required": True,
        "auth_status": "not_configured",
        "integration_health": "via_google_jobs",
        "last_successful_test": None,
        "notes": "Discovery through Google Jobs/SerpApi results and job-alert emails when connected. No direct portal login or application.",
    },
    "gulftalent": {
        "display_name": "GulfTalent",
        "market_focus": "Gulf",
        "discovery": True,
        "job_details": False,
        "application_form": False,
        "resume_upload": False,
        "submission": False,
        "confirmation_verification": False,
        "auth_required": True,
        "auth_status": "not_configured",
        "integration_health": "unsupported_application",
        "last_successful_test": None,
        "notes": "Discovery only. Gulf market focus.",
    },
    "bayt": {
        "display_name": "Bayt",
        "market_focus": "Gulf",
        "discovery": True,
        "job_details": False,
        "application_form": False,
        "resume_upload": False,
        "submission": False,
        "confirmation_verification": False,
        "auth_required": True,
        "auth_status": "not_configured",
        "integration_health": "unsupported_application",
        "last_successful_test": None,
        "notes": "Discovery only. Gulf market focus.",
    },
    "naukri_gulf": {
        "display_name": "Naukri Gulf",
        "market_focus": "Gulf",
        "discovery": True,
        "job_details": False,
        "application_form": False,
        "resume_upload": False,
        "submission": False,
        "confirmation_verification": False,
        "auth_required": True,
        "auth_status": "not_configured",
        "integration_health": "via_google_jobs",
        "last_successful_test": None,
        "notes": "Discovery through Google Jobs/SerpApi results and job-alert emails when connected. No direct portal login or application.",
    },
    "shine": {
        "display_name": "Shine",
        "market_focus": "India",
        "discovery": True,
        "job_details": False,
        "application_form": False,
        "resume_upload": False,
        "submission": False,
        "confirmation_verification": False,
        "auth_required": True,
        "auth_status": "not_configured",
        "integration_health": "via_google_jobs",
        "last_successful_test": None,
        "notes": "Discovery through Google Jobs/SerpApi results and job-alert emails when connected. No direct portal login or application.",
    },
    "hirist": {
        "display_name": "Hirist",
        "market_focus": "India",
        "discovery": True,
        "job_details": False,
        "application_form": False,
        "resume_upload": False,
        "submission": False,
        "confirmation_verification": False,
        "auth_required": True,
        "auth_status": "not_configured",
        "integration_health": "via_google_jobs",
        "last_successful_test": None,
        "notes": "Discovery through Google Jobs/SerpApi results and job-alert emails when connected. No direct portal login or application.",
    },
    "iimjobs": {
        "display_name": "IIMJobs",
        "market_focus": "India",
        "discovery": True,
        "job_details": False,
        "application_form": False,
        "resume_upload": False,
        "submission": False,
        "confirmation_verification": False,
        "auth_required": True,
        "auth_status": "not_configured",
        "integration_health": "via_google_jobs",
        "last_successful_test": None,
        "notes": "Discovery through Google Jobs/SerpApi results and job-alert emails when connected. No direct portal login or application.",
    },
    "timesjobs": {
        "display_name": "TimesJobs",
        "market_focus": "India",
        "discovery": True,
        "job_details": False,
        "application_form": False,
        "resume_upload": False,
        "submission": False,
        "confirmation_verification": False,
        "auth_required": True,
        "auth_status": "not_configured",
        "integration_health": "via_google_jobs",
        "last_successful_test": None,
        "notes": "Discovery through Google Jobs/SerpApi results and job-alert emails when connected. No direct portal login or application.",
    },
    "catererglobal": {
        "display_name": "CatererGlobal",
        "market_focus": "Gulf",
        "discovery": True,
        "job_details": False,
        "application_form": False,
        "resume_upload": False,
        "submission": False,
        "confirmation_verification": False,
        "auth_required": True,
        "auth_status": "not_configured",
        "integration_health": "via_google_jobs",
        "last_successful_test": None,
        "notes": "Discovery through Google Jobs/SerpApi results. No direct portal login or application.",
    },
    "gulfjobs": {
        "display_name": "GulfJobs",
        "market_focus": "Gulf",
        "discovery": True,
        "job_details": False,
        "application_form": False,
        "resume_upload": False,
        "submission": False,
        "confirmation_verification": False,
        "auth_required": True,
        "auth_status": "not_configured",
        "integration_health": "via_google_jobs",
        "last_successful_test": None,
        "notes": "Discovery through Google Jobs/SerpApi results. No direct portal login or application.",
    },
}


def get_portal_registry() -> dict[str, Any]:
    """Return current capability map for dashboard / health."""
    import os

    registry = {k: dict(v) for k, v in PORTAL_REGISTRY.items()}
    if not os.getenv("SERPAPI_API_KEY", "").strip():
        registry["serpapi_google_jobs"]["integration_health"] = "key_missing"
        registry["serpapi_google_jobs"]["discovery"] = False
        for key, portal in registry.items():
            if portal.get("integration_health") == "via_google_jobs":
                portal["integration_health"] = "key_missing"
                portal["discovery"] = False
    browser_ready = os.getenv("BROWSER_WORKER_ENABLED", "false").lower() == "true"
    for key, portal in registry.items():
        if portal.get("auth_required") and portal.get("discovery") and not portal.get("application_form"):
            if portal.get("integration_health") != "key_missing":
                portal["integration_health"] = "discovery_only"
    for key in ("greenhouse", "lever"):
        if browser_ready:
            registry[key]["integration_health"] = "partial_browser_ready"
        else:
            registry[key]["integration_health"] = "partial_no_browser"
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "portals": registry,
        "summary": {
            "fully_supported_application": [],
            "partial_application": [
                k for k, v in registry.items() if v.get("application_form") and not v.get("submission")
            ],
            "discovery_only": [
                k for k, v in registry.items() if v.get("discovery") and not v.get("application_form")
            ],
            "unsupported": [
                k for k, v in registry.items() if not v.get("discovery") and not v.get("application_form")
            ],
        },
        "safety": {
            "live_submission_enabled": False,
            "browser_allow_submit": os.getenv("BROWSER_ALLOW_SUBMIT", "false").lower() == "true",
            "note": "No portal is marked fully supported for automatic submission until end-to-end confirmation is verified in the deployed environment.",
        },
    }


def portal_support_level(portal_key: str) -> str:
    reg = PORTAL_REGISTRY.get(portal_key.lower(), {})
    if reg.get("submission") and reg.get("confirmation_verification"):
        return "full"
    if reg.get("application_form"):
        return "partial"
    if reg.get("discovery"):
        return "discovery_only"
    return "unsupported"
