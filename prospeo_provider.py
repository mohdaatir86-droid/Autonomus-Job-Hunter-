"""Prospeo search/reveal adapter. Runtime secrets, durable credit reservations, no sends."""
import hashlib
import json
import os
import re
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
import outreach
import outreach_engine as engine

ERRORS = {
    'INVALID_API_KEY': 'Prospeo rejected the API key. Update PROSPEO_API_KEY in secure Render settings.',
    'INSUFFICIENT_CREDITS': 'Prospeo credits are exhausted. No further lookup will run automatically.',
    'PLAN_REQUIRED': 'This Prospeo search needs a different plan. No upgrade has been purchased.',
    'INVALID_FILTERS': 'Prospeo rejected the search filters. Contact support before retrying.',
    'SERVICE_TEMPORARILY_UNAVAILABLE': 'Prospeo is temporarily unavailable. Check usage before retrying.',
}
ROLE = re.compile(r'recruit|talent acquisition|human resources|\bhr\b|operations.*(?:head|director|manager)|(?:head|director|manager).*operations', re.I)


def configured():
    return bool(os.getenv('PROSPEO_API_KEY', '').strip())


async def request(path, payload):
    key = os.getenv('PROSPEO_API_KEY', '').strip()
    if not key:
        raise HTTPException(503, 'Connect Prospeo: add PROSPEO_API_KEY securely in Render. Never paste it in chat.')
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post('https://api.prospeo.io/' + path,
                headers={'X-KEY': key, 'Content-Type': 'application/json'}, json=payload)
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, 'Prospeo response is uncertain. Check provider usage; this request will not be retried automatically.')
    code = str(data.get('error_code') or '')
    if code == 'NO_RESULTS' and response.status_code < 500:
        return {'results': [], 'person': {}, 'company': {}}
    if response.status_code == 429:
        raise HTTPException(429, 'Prospeo rate limit reached. No automatic retry will consume credits.')
    if response.status_code >= 400 or data.get('error'):
        raise HTTPException(502, ERRORS.get(code, 'Prospeo denied this request. Check account API access and usage. Provider error details are not exposed.'))
    return data


def domain_of(value):
    value = str(value or '').strip().lower()
    return (urlsplit(value if '://' in value else 'https://' + value).hostname or '').removeprefix('www.')


def company_matches(company, domain):
    return domain in {domain_of(company.get('domain')), domain_of(company.get('website'))}


def set_result(key, state, result=None):
    with outreach.connect() as db:
        db.execute('UPDATE outreach_provider_cache SET state=?,result=?,updated_at=? WHERE request_key=?',
                   (state, json.dumps(result or {}), engine.stamp(), key))
        db.execute('UPDATE outreach_enrichment SET state=?,detail=?,updated_at=? WHERE apollo_id=?',
                   (state, 'Prospeo lookup; at most 1 credit. Check actual provider usage.', engine.stamp(), key))


def reserve(key, domain):
    if not configured():
        raise HTTPException(503, 'Add PROSPEO_API_KEY securely in Render before using Prospeo.')
    with outreach.connect() as db:
        db.execute('INSERT INTO outreach_leads(apollo_id,domain,searched_at) VALUES (?,?,?) ON CONFLICT(apollo_id) DO NOTHING',
                   (key, domain, engine.stamp()))
    engine.claim_enrichment(key)
    with outreach.connect() as db:
        db.execute("INSERT INTO outreach_provider_cache(request_key,state,result,updated_at) VALUES (?,'pending','{}',?)",
                   (key, engine.stamp()))


async def search(payload):
    domain = str(payload.get('company_domain') or '').strip().lower()
    if len(domain) > 253 or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', domain) or domain in {'gmail.com','yahoo.com','outlook.com','hotmail.com'}:
        raise HTTPException(422, 'Enter the verified employer website domain.')
    key = 'prospeo:search:' + hashlib.sha256(domain.encode()).hexdigest()
    with outreach.connect() as db:
        cache = db.execute('SELECT * FROM outreach_provider_cache WHERE request_key=?', (key,)).fetchone()
    if cache and cache['state'] == 'saved':
        return {**json.loads(cache['result']), 'cached': True, 'searched_at': cache['updated_at']}
    if payload.get('confirm_credit_use') is not True:
        raise HTTPException(409, 'Confirm search credit use: Prospeo can charge 1 credit per results page, before any email reveal.')
    reserve(key, domain)
    try:
        data = await request('search-person', {'page': 1, 'filters': {
            'company': {'websites': {'include': [domain]}},
            'person_job_title': {'include': ['recruiter','talent acquisition','human resources','HR manager','head of operations','operations director'], 'match_mode': 'CONTAINS'},
        }})
        items = []
        for row in (data.get('results') or [])[:25]:
            person, company = row.get('person') or {}, row.get('company') or {}
            identifier = str(person.get('person_id') or '')
            title = str(person.get('current_job_title') or '')
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', identifier) or not company_matches(company, domain) or not ROLE.search(title):
                continue
            with outreach.connect() as db:
                db.execute('INSERT INTO outreach_leads(apollo_id,domain,searched_at) VALUES (?,?,?) ON CONFLICT(apollo_id) DO UPDATE SET domain=excluded.domain,searched_at=excluded.searched_at',
                           ('prospeo:person:' + identifier, domain, engine.stamp()))
            items.append({'person_id': identifier, 'name': str(person.get('full_name') or person.get('first_name') or '')[:200],
                          'title': title[:200], 'company': str(company.get('name') or '')[:200], 'provider': 'Prospeo'})
        result = {'items': items, 'cached': False, 'provider': 'Prospeo', 'searched_at': engine.stamp(),
                  'credit_cost': 0 if data.get('free') or not data.get('results') else 1,
                  'note': 'Search: up to 1 credit. Email reveal: up to 1 additional credit each. Cached results may be stale; verify current employer and hiring responsibility.'}
        set_result(key, 'saved', result)
        return result
    except Exception:
        set_result(key, 'uncertain')
        raise


async def enrich(payload):
    if payload.get('confirm_credit_use') is not True:
        raise HTTPException(409, 'Confirm Prospeo credit use before revealing a work email.')
    identifier = str(payload.get('person_id') or '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', identifier):
        raise HTTPException(422, 'A valid shortlisted Prospeo person ID is required.')
    key = 'prospeo:person:' + identifier
    with outreach.connect() as db:
        cached = db.execute('SELECT * FROM outreach_contacts WHERE candidate_id=? AND apollo_id=?', (outreach.CANDIDATE_ID, key)).fetchone()
        lead = db.execute('SELECT domain FROM outreach_leads WHERE apollo_id=?', (key,)).fetchone()
    if cached:
        return outreach._serialize(cached)
    if not lead:
        raise HTTPException(409, 'Shortlist this person using an employer-domain Prospeo search first.')
    domain = lead['domain']
    reserve(key, domain)
    try:
        data = await request('enrich-person', {'only_verified_email': True, 'enrich_mobile': False, 'data': {'person_id': identifier}})
        person, company = data.get('person') or {}, data.get('company') or {}
        email = person.get('email') or {}
        if (person.get('person_id') != identifier or not company_matches(company, domain)
                or not ROLE.search(str(person.get('current_job_title') or ''))
                or not isinstance(email, dict) or email.get('status') != 'VERIFIED' or email.get('revealed') is not True):
            raise HTTPException(422, 'No verified work email matching this shortlisted person and current employer. Nothing was saved.')
        address = outreach._email(email.get('email'))
        if address.split('@')[1] != domain:
            raise HTTPException(422, 'Work-email domain differs from the shortlisted employer. No address was guessed or saved.')
        result = outreach.create_contact({'email': address, 'name': person.get('full_name') or person.get('first_name'),
            'title': person.get('current_job_title'), 'company': company.get('name'), 'company_domain': domain,
            'source_url': person.get('linkedin_url') or 'https://' + domain, 'apollo_id': key, 'email_status': 'verified (Prospeo)'})
        set_result(key, 'saved', {'contact_id': result['id'], 'credit_cost': 0 if data.get('free_enrichment') else 1})
        return result
    except Exception:
        set_result(key, 'uncertain')
        raise
