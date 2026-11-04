"""Email-first job digests, with explicit opt-in for legacy WhatsApp delivery.

Options (first configured wins):
1) CallMeBot — free personal: https://www.callmebot.com/blog/free-api-whatsapp-messages/
   WHATSAPP_PHONE=9198xxxxxxxx  WHATSAPP_CALLMEBOT_APIKEY=...
2) Twilio WhatsApp
   TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_WHATSAPP_FROM / WHATSAPP_PHONE
"""
from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import quote

import httpx

log = logging.getLogger("job-hunter")


def _whatsapp_configured() -> bool:
    phone = os.getenv("WHATSAPP_PHONE", "").strip().lstrip("+")
    if not phone:
        return False
    if os.getenv("WHATSAPP_CALLMEBOT_APIKEY", "").strip():
        return True
    if os.getenv("TWILIO_ACCOUNT_SID", "").strip() and os.getenv("TWILIO_AUTH_TOKEN", "").strip() and os.getenv("TWILIO_WHATSAPP_FROM", "").strip():
        return True
    return False


def notification_channel() -> str:
    return os.getenv("ALERT_CHANNEL", "email").strip().lower()


def notification_min_score() -> int:
    try:
        return max(0, min(100, int(os.getenv("ALERT_MIN_SCORE", os.getenv("WHATSAPP_MIN_SCORE", "70")))))
    except ValueError:
        return 70


def notify_configured() -> bool:
    if notification_channel() == "email":
        from email_alerts import email_status
        return email_status()["configured"]
    return notification_channel() == "whatsapp" and _whatsapp_configured()


def notify_status() -> dict[str, Any]:
    channel = notification_channel()
    if channel == "email":
        from email_alerts import email_status
        return {**email_status(), "channel": "email", "min_score": notification_min_score()}
    return {"configured": notify_configured(), "channel": channel,
            "provider": ("callmebot" if os.getenv("WHATSAPP_CALLMEBOT_APIKEY", "").strip() else "twilio") if channel == "whatsapp" else None,
            "min_score": notification_min_score(), "hint": "Email is the default. Set ALERT_CHANNEL=whatsapp only to opt into legacy WhatsApp delivery."}


async def send_notification(text: str) -> dict[str, Any]:
    if notification_channel() == "email":
        from email_alerts import send_email
        return await send_email(text)
    if notification_channel() == "whatsapp":
        return await send_whatsapp(text)
    return {"sent": False, "error": "Unsupported ALERT_CHANNEL; select email or whatsapp"}


async def send_whatsapp(text: str) -> dict[str, Any]:
    phone = os.getenv("WHATSAPP_PHONE", "").strip().lstrip("+")
    if not phone:
        return {"sent": False, "error": "WHATSAPP_PHONE not set"}
    text = text[:1500]
    apikey = os.getenv("WHATSAPP_CALLMEBOT_APIKEY", "").strip()
    if apikey:
        url = (
            "https://api.callmebot.com/whatsapp.php"
            f"?phone={phone}&text={quote(text)}&apikey={apikey}"
        )
        try:
            async with httpx.AsyncClient(timeout=25) as client:
                r = await client.get(url)
            ok = r.status_code < 400
            return {"sent": ok, "provider": "callmebot", "status_code": r.status_code, "body": "accepted" if ok else "provider rejected request"}
        except Exception as e:
            log.warning("CallMeBot failed (%s)", type(e).__name__)
            return {"sent": False, "provider": "callmebot", "error": "provider request failed"}

    sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    from_wa = os.getenv("TWILIO_WHATSAPP_FROM", "").strip()
    if sid and token and from_wa:
        to = phone if phone.startswith("whatsapp:") else f"whatsapp:+{phone}"
        try:
            async with httpx.AsyncClient(timeout=25) as client:
                r = await client.post(
                    f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                    auth=(sid, token),
                    data={"From": from_wa, "To": to, "Body": text},
                )
            return {"sent": r.status_code < 300, "provider": "twilio", "status_code": r.status_code, "body": "accepted" if r.status_code < 300 else "provider rejected request"}
        except Exception as e:
            log.warning("Twilio failed (%s)", type(e).__name__)
            return {"sent": False, "provider": "twilio", "error": "provider request failed"}

    return {"sent": False, "error": "No WhatsApp provider configured"}


async def notify_high_match_jobs(jobs: list[dict], portal_base: str | None = None) -> dict[str, Any]:
    if not notify_configured() or not jobs:
        return {"sent": False, "skipped": True, "count": 0}
    min_score = notification_min_score()
    hits = [j for j in jobs if int(j.get("match_score") or 0) >= min_score]
    if not hits:
        return {"sent": False, "skipped": True, "count": 0, "reason": "no jobs above min score"}
    hits = sorted(hits, key=lambda x: -int(x.get("match_score") or 0))[:5]
    base = (portal_base or os.getenv("PORTAL_PUBLIC_URL") or "https://autonomous-job-hunter-docker.onrender.com").rstrip("/")
    lines = [f"Job Hunter: {len(hits)} high-match role(s) for you"]
    for j in hits:
        lines.append(
            f"• {j.get('match_score')}/100 | {j.get('title') or '?'} @ {j.get('company') or '?'} "
            f"({j.get('location') or j.get('country') or ''})"
        )
        if j.get("url"):
            lines.append(f"  {j['url']}")
    lines.append(f"Open portal: {base}")
    lines.append("Draft cover letter on portal, then apply yourself.")
    result = await send_notification("\n".join(lines))
    result["count"] = len(hits)
    return result
