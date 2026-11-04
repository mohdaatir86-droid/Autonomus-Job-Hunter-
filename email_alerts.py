"""Email job digests over HTTPS using Resend; credentials stay in service settings."""
from __future__ import annotations

import logging
import os
import re
from email.utils import parseaddr

import httpx

log = logging.getLogger('job-hunter')


def _valid_address(value: str) -> bool:
    if '\r' in value or '\n' in value:
        return False
    _, address = parseaddr(value)
    return bool(re.fullmatch(r'[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+', address))


def email_status() -> dict:
    sender = os.getenv('EMAIL_FROM', 'onboarding@resend.dev').strip()
    recipient = os.getenv('EMAIL_TO', '').strip()
    missing = []
    if not os.getenv('RESEND_API_KEY', '').strip():
        missing.append('RESEND_API_KEY')
    if not recipient or not _valid_address(recipient):
        missing.append('EMAIL_TO (valid recipient address)')
    if not _valid_address(sender):
        missing.append('EMAIL_FROM (valid verified sender)')
    return {
        'configured': not missing,
        'provider': 'resend',
        'recipient_set': bool(recipient and _valid_address(recipient)),
        'sender_set': bool(sender and _valid_address(sender)),
        'missing': missing,
        'hint': 'Set RESEND_API_KEY and EMAIL_TO securely. The onboarding@resend.dev test sender can send only to your own Resend account email; use a verified sender domain for another recipient.',
    }


async def send_email(text: str, subject: str = 'Job Hunter: matching operations roles') -> dict:
    status = email_status()
    if not status['configured']:
        return {'sent': False, 'provider': 'resend', 'error': 'Email setup is incomplete', 'missing': status['missing']}
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.post(
                'https://api.resend.com/emails',
                headers={'Authorization': 'Bearer ' + os.environ['RESEND_API_KEY'].strip()},
                json={
                    'from': os.getenv('EMAIL_FROM', 'onboarding@resend.dev').strip(),
                    'to': [parseaddr(os.environ['EMAIL_TO'].strip())[1]],
                    'subject': subject,
                    'text': text[:20000],
                },
            )
        # Do not return provider response bodies, email addresses or credentials.
        return {'sent': 200 <= response.status_code < 300, 'provider': 'resend', 'status_code': response.status_code,
                'error': None if 200 <= response.status_code < 300 else 'Email provider rejected the request; check sender verification, recipient restrictions and quota.'}
    except httpx.HTTPError as exc:
        log.warning('Email delivery failed (%s)', type(exc).__name__)
        return {'sent': False, 'provider': 'resend', 'error': 'Email provider request failed'}
