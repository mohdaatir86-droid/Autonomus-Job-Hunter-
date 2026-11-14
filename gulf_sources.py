"""Public Gulf employer discovery and evidence-based operations shortlist.

No credentials, enrichment, application submission or messaging. Active feeds
are distinguished from external search links; publication dates are untouched.
"""
import asyncio
import html
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

COUNTRIES = {'ae': 'United Arab Emirates', 'sa': 'Saudi Arabia', 'qa': 'Qatar',
             'kw': 'Kuwait', 'om': 'Oman', 'bh': 'Bahrain'}
FEED_NAME = 'Gulf employer feeds'
CATALOG = [
    ('Talabat / Delivery Hero', 'https://careers.deliveryhero.com/talabat', 'direct', 'Published SmartRecruiters vacancies; six Gulf countries'),
    ('Careem', 'https://careers.careem.com/', 'direct', 'Public Greenhouse employer feed'),
    ('Lalamove', 'https://jobs.lever.co/lalamove', 'direct', 'Public Lever employer feed'),
    ('Noon', 'https://www.linkedin.com/company/nooncom/jobs', 'external', 'Official company jobs on LinkedIn; access may require login'),
    ('Snoonu', 'https://snoonu.keka.com/careers', 'external', 'Official employer career board'),
    ('Kitopi', 'https://www.kitopi.com/careers', 'external', 'Employer career page; linked Lever board returned 404, not an active feed'),
    ('DP World', 'https://ehpv.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs', 'external', 'Official employer Oracle career board'),
    ('Landmark Group', 'https://efhi.fa.em3.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs', 'external', 'Official employer Oracle career board'),
    ('Aramex', 'https://careers.aramex.com/search/?q=operations', 'direct', 'Employer RSS; bounded published selection, not every vacancy'),
    ('DHL', 'https://careers.dhl.com/global/en/search-results', 'external', 'Official employer career search'),
    ('Alshaya', 'https://www.alshaya.com/english/careers/vacancies/', 'external', 'Official employer vacancies'),
    ('Al-Futtaim', 'https://www.alfuttaim.com/en/careers/', 'external', 'Official employer careers'),
    ('Majid Al Futtaim', 'https://careers.majidalfuttaim.com/global/en/search-results', 'external', 'Official employer career search'),
    ('GMG', 'https://careers.gmg.com/jobs', 'direct', 'Employer RSS with published job descriptions and explicit job locations'),
    ('Amazon Operations', 'https://www.amazon.jobs/content/en/teams/fulfillment-and-operations/fulfillment-center-management', 'external', 'Official operations career hub; select Gulf location'),
    ('NaukriGulf', 'https://www.naukrigulf.com/operations-manager-jobs', 'indexed', 'Google Jobs / connected job alerts; partial indexed coverage'),
    ('GulfTalent', 'https://www.gulftalent.com/jobs/title/operations-manager', 'indexed', 'Google Jobs / connected job alerts; partial indexed coverage'),
    ('Bayt', 'https://www.bayt.com/en/international/jobs/operations-manager-jobs/', 'indexed', 'Google Jobs / connected job alerts; partial indexed coverage'),
    ('LinkedIn Jobs', 'https://www.linkedin.com/jobs/search/?keywords=operations%20manager&location=United%20Arab%20Emirates', 'indexed', 'Google Jobs / connected job alerts; direct search may require login'),
    ('Indeed UAE', 'https://ae.indeed.com/jobs?q=operations+manager', 'indexed', 'Google Jobs / connected job alerts; partial indexed coverage'),
    ('CatererGlobal', 'https://www.catererglobal.com/jobs/operations-manager', 'indexed', 'Google Jobs / connected job alerts; hospitality roles need fit review'),
    ('GulfJobs', 'https://www.gulfjobs.com/', 'indexed', 'Google Jobs / connected job alerts; partial indexed coverage'),
]


def catalog():
    return [{'name': n, 'url': u, 'mode': m, 'note': d} for n, u, m, d in CATALOG]


def candidate_years(profile):
    # Explicit profile statement only. Never add up overlapping employment dates.
    text = str(profile.get('experience_summary') or '')
    match = re.search(r'\b(\d{1,2})(?:\.\d+)?\+?\s*years?\b', text, re.I)
    return int(match[1]) if match else None


def role_candidate(title):
    text = title.lower()
    if re.search(r'\b(junior|intern|trainee|picker|packer|driver|chef|coordinator|associate|specialist|supervisor|assistant)\b', text) and not re.search(r'\b(?:associate|assistant) director\b', text):
        return False
    if re.search(r'\b(sales|account|category|procurement|purchasing|marketing|engineering|engineer|software|product|data|finance|real estate|communications|customer care|customer experience|retail media|acquisition)\b', text):
        return False
    return bool(re.search(r'\b(manager|head|director|lead|leadership)\b', text) and re.search(r'\b(operations|logistics|warehouse|warehousing|fulfilment|fulfillment|supply chain|distribution|inventory|store|stores|rider|workforce|3pl|city|area|cluster|regional|country|program|programme|restaurant|qsr)\b', text))


def fit(job, profile, settings):
    from job_hunter import role_context
    title, desc = job.get('title', ''), job.get('description', '')
    unrelated, generic, physical = role_context(job)
    if job.get('country') not in COUNTRIES.values() or not role_candidate(title) or unrelated or generic or not physical:
        return False, []
    text = (title + ' ' + desc).lower()
    years = candidate_years(profile)
    # These are total-experience clauses, not e.g. "two years managing riders".
    requirements = list(re.finditer(r'(?:(?:minimum|at least|over|more than)\s+)?(\d{1,2})\s*(?:[-–to]+\s*(\d{1,2}))?\s*\+?\s*years?\s+(?:of\s+)?(?:professional\s+|relevant\s+|operational\s+|operations\s+|work\s+)?experience', text))
    notes = ['Operations leadership scope matches your multi-store / fulfilment background.']
    for requirement in requirements:
        low, high = int(requirement[1]), int(requirement[2]) if requirement[2] else None
        if years is not None and (low > years or (high is not None and high < years)):
            return False, []
    if requirements and years is not None:
        notes.append(f'Saved profile: {years}+ years; published total-experience requirement checked. Minimum is not a maximum.')
    else:
        notes.append('Review experience requirements: no comparable total-years requirement or saved profile years found.')
    notes.append('Salary, qualifications and work authorisation require your review; eligibility is not confirmed.')
    if re.search(r'\b(?:arabic|russian|speaker|national|citizen|visa|right to work)\b', text):
        notes.append('Check the original nationality, language and visa conditions before applying.')
    # Keep other explicit preferences; replace the old lower-bound experience rule.
    from job_sources import matches_preferences
    if not matches_preferences(job, {**settings, 'min_experience_years': 0}):
        return False, []
    return True, notes


def evidence(job):
    try:
        return json.loads(job.get('raw_json') or '{}').get('_gulf', {})
    except (ValueError, TypeError, AttributeError):
        return {}


def current_evidence(job, now=None):
    meta = evidence(job)
    try:
        checked = datetime.fromisoformat(meta['checked_at'])
        age = (now or datetime.now(timezone.utc)) - checked
        return (meta.get('state') == 'published' and timedelta(0) <= age < timedelta(hours=36)
                and safe_apply_url(meta.get('apply_url', '')))
    except (KeyError, TypeError, ValueError):
        return False


def safe_apply_url(url):
    try:
        p = urlsplit(url)
        return p.scheme == 'https' and not p.username and not p.password and (p.port in (None, 443)) and p.hostname in {
            'jobs.smartrecruiters.com', 'careers.smartrecruiters.com', 'job-boards.greenhouse.io',
            'boards.greenhouse.io', 'boards.eu.greenhouse.io', 'jobs.lever.co', 'careers.careem.com',
            'careers.gmg.com', 'careers.aramex.com'}
    except (ValueError, TypeError):
        return False


def mark(job, apply_url, checked_at):
    if job and safe_apply_url(apply_url):
        job['raw_json'] = json.dumps({'_gulf': {'state': 'published', 'checked_at': checked_at,
            'apply_url': apply_url, 'basis': 'Employer public published-vacancy feed. Availability can change; check the application page.'}})
    return job


async def fetch_smartrecruiters(client, profile, settings):
    from job_sources import _clean_job
    checked = datetime.now(timezone.utc).isoformat()
    semaphore = asyncio.Semaphore(4)
    errors = []

    async def get(url, params=None):
        async with semaphore:
            response = await client.get(url, params=params, timeout=15, follow_redirects=False)
            response.raise_for_status()
            return response.json()

    async def country_jobs(cc, country):
        candidates = []
        try:
            for offset in (0, 100, 200):
                payload = await get('https://api.smartrecruiters.com/v1/companies/DeliveryHero/postings',
                                    {'country': cc, 'limit': 100, 'offset': offset})
                content = payload['content']
                candidates.extend(j for j in content if role_candidate(str(j.get('name') or '')) and (j.get('location') or {}).get('country') == cc)
                if offset + len(content) >= int(payload.get('totalFound', 0)) or not content:
                    break
            else:
                errors.append(f'SmartRecruiters/{cc}: first 300 published roles checked; coverage partial')
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            errors.append(f'SmartRecruiters/{cc}: public feed unavailable; previous evidence expires after 36 hours')
        return [(item, country) for item in candidates[:25]]

    import httpx
    groups = await asyncio.gather(*(country_jobs(cc, name) for cc, name in COUNTRIES.items()))
    async def detail(item, country):
        identifier = str(item.get('id') or '')
        if not identifier.isdigit():
            return None
        try:
            data = await get(f'https://api.smartrecruiters.com/v1/companies/DeliveryHero/postings/{identifier}')
            sections = (data.get('jobAd') or {}).get('sections') or {}
            description = '\n'.join(str(v.get('text') or '') for v in sections.values() if isinstance(v, dict))
            if not description or not safe_apply_url(data.get('applyUrl', '')):
                return None
            brand = next((f.get('valueLabel') for f in (data.get('customField') or item.get('customField') or []) if f.get('fieldLabel') == 'Brands'), None)
            # Use the publisher's brand label, never infer an employer from the country.
            job = _clean_job({'id': identifier, 'title': data.get('name'), 'company': brand or 'Delivery Hero',
                'location': (data.get('location') or {}).get('city', '') + ', ' + country,
                'description': html.unescape(description), 'date': data.get('releasedDate') or item.get('releasedDate'),
                'url': f'https://jobs.smartrecruiters.com/DeliveryHero/{identifier}',
                'employment_type': (data.get('typeOfEmployment') or {}).get('label', '')}, 'SmartRecruiters/DeliveryHero')
            if job and fit(job, profile, settings)[0]:
                return mark(job, data['applyUrl'], checked)
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            errors.append('SmartRecruiters: a shortlisted detail could not be verified')
        return None
    jobs = await asyncio.gather(*(detail(item, country) for group in groups for item, country in group))
    return [job for job in jobs if job], errors


async def fetch_gulf_sources(client, settings):
    from application_workflow import get_profile_full
    from job_sources import _clean_job
    profile = get_profile_full()
    jobs, errors = await fetch_smartrecruiters(client, profile, settings)
    checked = datetime.now(timezone.utc).isoformat()

    async def employer(name, url):
        found = []
        try:
            response = await client.get(url, timeout=20, follow_redirects=False)
            response.raise_for_status()
            data = response.json()
            rows = data.get('jobs', []) if name == 'Careem' else data
            for row in rows:
                if name == 'Careem':
                    raw = {'id': row['id'], 'title': row['title'], 'company': name, 'location': (row.get('location') or {}).get('name'),
                           'description': row.get('content'), 'url': row.get('absolute_url'), 'date': row.get('first_published')}
                else:
                    lists = '\n'.join(str(v.get('content') or '') for v in row.get('lists', []))
                    raw = {'id': row['id'], 'title': row.get('text'), 'company': name, 'location': (row.get('categories') or {}).get('location'),
                           'description': str(row.get('description') or '') + lists, 'url': row.get('hostedUrl'), 'date': row.get('createdAt')}
                job = _clean_job(raw, 'Greenhouse/careem' if name == 'Careem' else 'Lever/lalamove')
                if job and fit(job, profile, settings)[0] and safe_apply_url(job['url']):
                    found.append(mark(job, job['url'], checked))
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            errors.append(f'{name}: public feed unavailable; no new verified roles imported')
        return found
    import httpx
    for result in await asyncio.gather(employer('Careem', 'https://boards-api.greenhouse.io/v1/boards/careem/jobs?content=true'),
                                      employer('Lalamove', 'https://api.lever.co/v0/postings/lalamove')):
        jobs.extend(result)
    rss_jobs, rss_errors = await fetch_rss_employers(client, profile, settings)
    jobs.extend(rss_jobs)
    errors.extend(rss_errors)
    return jobs, '; '.join(dict.fromkeys(errors)) or None


async def fetch_rss_employers(client, profile, settings):
    """Parse public employer-published RSS; never infer location from body text."""
    import hashlib
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    from job_sources import _clean_job
    import httpx
    checked = datetime.now(timezone.utc).isoformat()
    feeds = [('GMG', 'https://careers.gmg.com/jobs.rss', 'careers.gmg.com'),
             ('Aramex', 'https://careers.aramex.com/services/rss/job/?locale=en_US&keywords=(operations)', 'careers.aramex.com')]
    errors = []
    async def fetch(name, url, host):
        jobs = []
        try:
            response = await client.get(url, timeout=20, follow_redirects=False)
            response.raise_for_status()
            # Bounds also prevent XML entity expansion / oversized feed processing.
            if len(response.content) > 3_000_000 or re.search(r'<!DOCTYPE|<!ENTITY', response.text, re.I):
                raise ValueError('Unsupported XML')
            root = ET.fromstring(response.content)
            items = root.findall('./channel/item')
            for item in items[:300]:
                title, url = item.findtext('title', ''), item.findtext('link', '')
                if not role_candidate(title) or urlsplit(url).hostname != host or not safe_apply_url(url):
                    continue
                if name == 'GMG':
                    namespace = '{https://teamtailor.com/locations}'
                    locations = item.findall(f'{namespace}locations/{namespace}location')
                    # Multiple-location advertisements must include a verified Gulf address.
                    loc = next((l for l in locations if l.findtext(namespace+'country') in COUNTRIES.values()), None)
                    if loc is None:
                        continue
                    location = ', '.join(filter(None, [loc.findtext(namespace+'city'), loc.findtext(namespace+'country')]))
                else:
                    match = re.search(r'\(([^()]+),\s*(AE|SA|QA|KW|OM|BH)\)\s*$', title)
                    if not match:
                        continue
                    location = match[1] + ', ' + COUNTRIES[match[2].lower()]
                posted = None
                if item.findtext('pubDate'):
                    try:
                        posted = parsedate_to_datetime(item.findtext('pubDate')).astimezone(timezone.utc).isoformat()
                    except (ValueError, TypeError):
                        pass
                row = _clean_job({'id': hashlib.sha256(url.encode()).hexdigest(), 'title': title, 'company': name,
                    'location': location, 'description': html.unescape(item.findtext('description', '')), 'url': url, 'date': posted}, 'Employer RSS/'+name)
                if row and fit(row, profile, settings)[0]:
                    jobs.append(mark(row, url, checked))
            if len(items) >= (20 if name == 'Aramex' else 300):
                errors.append(f'{name}: RSS publishes a bounded selection; coverage is partial')
        except (httpx.HTTPError, ValueError, ET.ParseError, TypeError):
            errors.append(f'{name}: RSS unavailable; no new verified roles imported')
        return jobs
    groups = await asyncio.gather(*(fetch(*feed) for feed in feeds))
    return [row for rows in groups for row in rows], errors
