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
    ('Snoonu', 'https://snoonu.keka.com/careers', 'direct', 'Official Keka public active-job feed; full descriptions and explicit locations'),
    ('Kitopi', 'https://www.kitopi.com/careers', 'external', 'Employer career page; linked Lever board returned 404, not an active feed'),
    ('DP World', 'https://ehpv.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs', 'direct', 'Public employer Oracle career feed; operations search with published detail checks'),
    ('Landmark Group', 'https://efhi.fa.em3.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs', 'direct', 'Public employer Oracle career feed; operations search with published detail checks'),
    ('Aramex', 'https://careers.aramex.com/search/?q=operations', 'direct', 'Employer RSS; bounded published selection, not every vacancy'),
    ('DHL', 'https://careers.dhl.com/global/en/search-results', 'direct', 'Public embedded search data; bounded global scan with Gulf filters, partial coverage'),
    ('Alshaya', 'https://jobsearch.alshaya.com/cau/en/job', 'direct', 'Official published vacancies and Apply now details; bounded pages'),
    ('Al-Futtaim', 'https://www.afuturewithus.com/search/?q=operations', 'direct', 'Official employer RSS; bounded operations search selection'),
    ('Majid Al Futtaim', 'https://careers.majidalfuttaim.com/global/en/search-results', 'direct', 'Public embedded search and active details; bounded global scan, partial coverage'),
    ('GMG', 'https://careers.gmg.com/jobs', 'direct', 'Employer RSS with published job descriptions and explicit job locations'),
    ('Amazon Operations', 'https://www.amazon.jobs/content/en/teams/fulfillment-and-operations/fulfillment-center-management', 'direct', 'Public Amazon Jobs operations search restricted to six Gulf country codes'),
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
    if re.search(r'\b(tech|technical|technology|systems|architect|architecture|devops|sales|account|category|procurement|purchasing|marketing|engineering|engineer|software|product|data|finance|real estate|communications|customer care|customer experience|retail media|acquisition)\b', text):
        return False
    return bool(re.search(r'\b(manager|head|director|lead|leadership)\b', text) and re.search(r'\b(operations|logistics|warehouse|warehousing|fulfilment|fulfillment|supply chain|distribution|inventory|store|stores|rider|workforce|3pl|city|area|cluster|regional|country|program|programme|restaurant|kitchen|qsr)\b', text))


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
        data = json.loads(job.get('raw_json') or '{}').get('_gulf', {})
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError, AttributeError):
        return {}


def current_evidence(job, now=None):
    meta = evidence(job)
    try:
        checked = datetime.fromisoformat(meta['checked_at'])
        age = (now or datetime.now(timezone.utc)) - checked
        return (meta.get('state') == 'published' and timedelta(0) <= age < timedelta(hours=36)
                and (not meta.get('valid_until') or (now or datetime.now(timezone.utc)) < datetime.fromisoformat(meta['valid_until']))
                and safe_apply_url(meta.get('apply_url', '')))
    except (KeyError, TypeError, ValueError):
        return False


def safe_apply_url(url):
    try:
        p = urlsplit(url)
        return p.scheme == 'https' and not p.username and not p.password and (p.port in (None, 443)) and p.hostname in {
            'jobs.smartrecruiters.com', 'careers.smartrecruiters.com', 'job-boards.greenhouse.io',
            'boards.greenhouse.io', 'boards.eu.greenhouse.io', 'jobs.lever.co', 'careers.careem.com',
            'careers.gmg.com', 'careers.aramex.com', 'www.afuturewithus.com', 'snoonu.keka.com', 'www.amazon.jobs', 'amazon.jobs', 'jobsearch.alshaya.com',
            'careers.dhl.com', 'careers.majidalfuttaim.com',
            'ehpv.fa.em2.oraclecloud.com', 'efhi.fa.em3.oraclecloud.com'}
    except (ValueError, TypeError):
        return False


def mark(job, apply_url, checked_at, valid_until=None):
    if job and safe_apply_url(apply_url):
        job['raw_json'] = json.dumps({'_gulf': {'state': 'published', 'checked_at': checked_at,
            'apply_url': apply_url, 'valid_until': valid_until, 'basis': 'Employer public published-vacancy feed. Availability can change; check the application page.'}})
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
    jobs, errors = [], []
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
    from gulf_web_feeds import fetch_amazon, fetch_alshaya, fetch_phenom
    async def bounded(name, operation):
        try:
            return await asyncio.wait_for(operation, timeout=60)
        except (TimeoutError, httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError, IndexError):
            return [], [f'{name}: provider time budget or response validation failed; coverage is partial']

    smart, oracle, rss, snoonu, amazon, alshaya, dhl, maf, careem, lalamove = await asyncio.gather(
        bounded('SmartRecruiters', fetch_smartrecruiters(client, profile, settings)),
        bounded('Oracle careers', fetch_oracle_employers(client, profile, settings)),
        bounded('Employer RSS', fetch_rss_employers(client, profile, settings)),
        bounded('Snoonu', fetch_snoonu(client, profile, settings)),
        bounded('Amazon', fetch_amazon(client, profile, settings)),
        bounded('Alshaya', fetch_alshaya(client, profile, settings)),
        bounded('DHL', fetch_phenom(client, profile, settings, 'DHL', 'careers.dhl.com')),
        bounded('Majid Al Futtaim', fetch_phenom(client, profile, settings, 'Majid Al Futtaim', 'careers.majidalfuttaim.com')),
        employer('Careem', 'https://boards-api.greenhouse.io/v1/boards/careem/jobs?content=true'),
        employer('Lalamove', 'https://api.lever.co/v0/postings/lalamove'))
    for rows, issues in (smart, oracle, rss, snoonu, amazon, alshaya, dhl, maf):
        jobs.extend(rows)
        errors.extend(issues)
    jobs.extend(careem)
    jobs.extend(lalamove)
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
             ('Aramex', 'https://careers.aramex.com/services/rss/job/?locale=en_US&keywords=(operations)', 'careers.aramex.com'),
             ('Al-Futtaim', 'https://www.afuturewithus.com/services/rss/job/?locale=en_GB&keywords=(operations)', 'www.afuturewithus.com')]
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
                    match = re.search(r'\((?:([^()]+),\s*)?(AE|SA|QA|KW|OM|BH)\)\s*$', title)
                    if not match:
                        continue
                    location = ((match[1] + ', ') if match[1] else '') + COUNTRIES[match[2].lower()]
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
            if len(items) >= (20 if name != 'GMG' else 300):
                errors.append(f'{name}: RSS publishes a bounded selection; coverage is partial')
        except (httpx.HTTPError, ValueError, ET.ParseError, TypeError):
            errors.append(f'{name}: RSS unavailable; no new verified roles imported')
        return jobs
    groups = await asyncio.gather(*(fetch(*feed) for feed in feeds))
    return [row for rows in groups for row in rows], errors


ORACLE_EMPLOYERS = (
    ('DP World', 'ehpv.fa.em2.oraclecloud.com'),
    ('Landmark Group', 'efhi.fa.em3.oraclecloud.com'),
)


async def fetch_oracle_employers(client, profile, settings):
    """Only externally published IDs from fixed employer career sites are read.

    Details use documented CE ById, not internal candidate/recruiter resources.
    Dates and country must agree; a posting's explicit closing time overrides TTL.
    """
    import httpx
    from job_sources import _clean_job, _parse_iso
    moment = datetime.now(timezone.utc)
    checked = moment.isoformat()
    semaphore = asyncio.Semaphore(4)
    errors = []

    async def request(host, resource, finder, expand=None):
        params = {'onlyData': 'true', 'finder': finder}
        if expand:
            params['expand'] = expand
        async with semaphore:
            response = await client.get(f'https://{host}/hcmRestApi/resources/11.13.18.05/{resource}',
                                        params=params, timeout=15, follow_redirects=False)
            response.raise_for_status()
            return response.json()

    def valid_dates(start, end):
        parsed_start, parsed_end = _parse_iso(start), _parse_iso(end)
        if start and not parsed_start or end and not parsed_end:
            return False
        return not (parsed_start and parsed_start > checked or parsed_end and parsed_end <= checked)

    async def employer(name, host):
        async def page(offset):
            try:
                data = await request(host, 'recruitingCEJobRequisitions',
                    f'findReqs;siteNumber=CX_1,keyword=operations,limit=100,offset={offset}', 'requisitionList')
                return data['items'][0]
            except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
                errors.append(f'{name}: a published-vacancy page is unavailable; coverage is partial')
                return {}
        first = await page(0)
        count = int(first.get('TotalJobsCount', 0))
        pages = [first] + await asyncio.gather(*(page(i) for i in range(100, min(count, 400), 100)))
        if count > 400:
            errors.append(f'{name}: first 400 operations search results checked; coverage is partial')
        candidates = {}
        for p in pages:
            for row in p.get('requisitionList') or []:
                code = str(row.get('PrimaryLocationCountry') or '').lower()
                identifier = str(row.get('Id') or '')
                if code in COUNTRIES and identifier.isdigit() and role_candidate(str(row.get('Title') or '')) and valid_dates(row.get('PostedDate'), row.get('PostingEndDate')):
                    candidates[identifier] = row
        if len(candidates) > 30:
            errors.append(f'{name}: first 30 relevant details checked; coverage is partial')

        async def detail(identifier, listing):
            try:
                data = await request(host, 'recruitingCEJobRequisitionDetails', f'ById;Id={identifier},siteNumber=CX_1')
                detail = next((r for r in data.get('items', []) if str(r.get('Id')) == identifier), None)
                if not detail or not valid_dates(detail.get('ExternalPostedStartDate'), detail.get('ExternalPostedEndDate')):
                    return None
                code = str(detail.get('PrimaryLocationCountry') or '').lower()
                if code not in COUNTRIES or code != str(listing.get('PrimaryLocationCountry') or '').lower():
                    return None
                description = '\n'.join(str(detail.get(field) or '') for field in
                    ('ExternalDescriptionStr', 'ExternalResponsibilitiesStr', 'ExternalQualificationsStr'))
                # Corporate boilerplate alone is not evidence of this job's scope.
                if not description.strip():
                    return None
                url = f'https://{host}/hcmUI/CandidateExperience/en/sites/CX_1/job/{identifier}'
                job = _clean_job({'id': identifier, 'title': detail.get('Title'), 'company': name,
                    'location': str(detail.get('PrimaryLocation') or '') + ', ' + COUNTRIES[code],
                    'description': html.unescape(description), 'url': url,
                    'date': detail.get('ExternalPostedStartDate') or listing.get('PostedDate'),
                    'employment_type': detail.get('JobSchedule') or ''}, 'Oracle Careers/'+name)
                if job and fit(job, profile, settings)[0]:
                    return mark(job, url, checked, _parse_iso(detail.get('ExternalPostedEndDate') or listing.get('PostingEndDate')))
            except (httpx.HTTPError, ValueError, TypeError, KeyError):
                errors.append(f'{name}: a shortlisted detail could not be verified')
            return None
        rows = await asyncio.gather(*(detail(identifier, row) for identifier, row in list(candidates.items())[:30]))
        return [row for row in rows if row]
    groups = await asyncio.gather(*(employer(*employer_site) for employer_site in ORACLE_EMPLOYERS))
    return [row for group in groups for row in group], errors


async def fetch_snoonu(client, profile, settings):
    """Read the active public feed used by Snoonu's official Keka embed widget.

    Endpoint and tenant come from the employer-published embed script. They are
    fixed here; no tokens, candidates, internal jobs or recruiter APIs are read.
    """
    import httpx
    from job_sources import _clean_job, _parse_iso
    errors, jobs = [], []
    checked = datetime.now(timezone.utc).isoformat()
    try:
        response = await client.get('https://snoonu.keka.com/careers/api/embedjobs/default/active/80eaceed-f9ad-465b-a21c-b5515f6624c8',
                                    timeout=15, follow_redirects=False)
        response.raise_for_status()
        if len(response.content) > 3_000_000:
            raise ValueError('Oversized public feed')
        rows = response.json()
        if not isinstance(rows, list):
            raise ValueError('Active feed format changed')
        if len(rows) > 500:
            errors.append('Snoonu: first 500 published jobs checked; coverage is partial')
        for row in rows[:500]:
            if not isinstance(row, dict):
                raise ValueError('Invalid public job')
            identifier = str(row.get('id') or '')
            title = str(row.get('title') or '')
            if not identifier.isdigit() or not role_candidate(title):
                continue
            locations = row.get('jobLocations') or []
            location = next((l for l in locations if str(l.get('countryCode') or '').lower() in COUNTRIES), None)
            if not location or not row.get('description'):
                continue
            posted = _parse_iso(row.get('publishedOn'))
            if row.get('publishedOn') and (not posted or posted > checked):
                continue
            code = location['countryCode'].lower()
            city = str(location.get('city') or '')
            url = f'https://snoonu.keka.com/careers/jobdetails/{identifier}'
            # Structured experience is retained alongside the JD, not silently
            # replaced by a conflicting lower minimum in the prose.
            experience = str(row.get('experience') or '').strip()
            if re.fullmatch(r'\d{1,2}\s*[-–]\s*\d{1,2}(?:\s*years?)?', experience, re.I):
                experience = re.sub(r'\s*years?$', '', experience, flags=re.I) + ' years of experience.'
            description = 'Published experience requirement: ' + experience + '\n' + html.unescape(str(row['description']))
            job = _clean_job({'id': identifier, 'title': title, 'company': 'Snoonu',
                'location': ', '.join(filter(None, [city, COUNTRIES[code]])),
                'description': description, 'url': url, 'date': posted,
                'employment_type': {1: 'Part Time', 2: 'Full Time'}.get(row.get('jobType'), ''),
                'salary': row.get('salaryRangeFormat') or ''}, 'Keka/Snoonu')
            if job and fit(job, profile, settings)[0]:
                jobs.append(mark(job, url, checked))
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
        errors.append('Snoonu: public active feed unavailable or changed; coverage is partial')
    return jobs, errors
