import asyncio,json
from datetime import datetime,timedelta,timezone
import httpx
import pytest
import gulf_sources as gulf
import gulf_web_feeds as web

PROFILE={'experience_summary':'9+ years of operations experience'}
SETTINGS={'work_arrangement':'all'}


def noon_listing(title='Operations Manager', identifier='28'):
    return f'<div id="jobs-list-panel"><div class="job-card" data-job-id="{identifier}"><p class="job-card-title">{title}</p></div></div>'


def noon_detail(**changes):
    data = {'id': 28, 'jobNr': 'NOON-OPS-0028', 'title': 'Operations Manager', 'country': 'ae',
            'city': 'Dubai', 'nubCode': 'noon Minutes', 'isActive': True,
            'postedAt': '2026-04-23T09:11:59', 'createdAt': '2026-01-01T00:00:00Z', 'closesAt': None,
            'roleIntro': '<p>Warehouse operations inventory SLA dispatch team leadership</p>',
            'requirements': '<p>Minimum 5 years of experience.</p>', 'employmentType': 'full-time',
            'recruiterEmail': 'PRIVATE@example.com'}
    return {**data, **changes}


def test_noon_public_listing_and_active_detail_preserve_date_brand_and_requirements():
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path == '/jobs':
            assert request.url.params['department'] == 'Operations & Logistics'
            return httpx.Response(200, text=noon_listing())
        assert request.url.path == '/api/v1/jobs/28'
        return httpx.Response(200, json=noon_detail())
    jobs, errors = run(web.fetch_noon, handler)
    assert len(jobs) == 1 and not errors and len(calls) == 2
    job = jobs[0]
    assert job['posted_at'] == '2026-04-23T09:11:59+00:00'
    assert job['source_id'] == '28' and job['company'] == 'Noon / noon Minutes'
    assert job['url'] == 'https://joinnoon.com/job/NOON-OPS-0028'
    assert 'Minimum 5 years' in job['description']
    assert 'PRIVATE' not in job['description'] + job['raw_json']
    assert gulf.current_evidence(job)
    assert not any('login' in r.url.path or 'application' in r.url.path for r in calls)


@pytest.mark.parametrize('changes', [
    {'isActive': False}, {'isActive': 'true'}, {'id': 29}, {'country': 'in'}, {'country': ''},
    {'title': 'Another Operations Manager'}, {'postedAt': 'not-a-date'},
    {'postedAt': '2099-01-01T00:00:00Z'}, {'closesAt': '2000-01-01T00:00:00Z'},
    {'closesAt': 'invalid'}, {'requirements': '4–7 years of experience in logistics operations'},
    {'requirements': '12–15 years’ experience in operations management'},
])
def test_noon_rejects_inactive_inconsistent_dates_countries_and_experience(changes):
    def handler(request):
        return httpx.Response(200, text=noon_listing()) if request.url.path == '/jobs' else httpx.Response(200, json=noon_detail(**changes))
    jobs, errors = run(web.fetch_noon, handler)
    assert not jobs


def test_noon_does_not_follow_redirects_or_manufacture_health_from_changed_html():
    for response in [httpx.Response(302, headers={'location': 'https://untrusted.test/'}),
                     httpx.Response(200, text='<h1>Maintenance</h1>')]:
        calls = []
        def handler(request):
            calls.append(request)
            return response
        jobs, errors = run(web.fetch_noon, handler)
        assert not jobs and errors and len(calls) == 1


def test_noon_ignores_unlisted_junior_and_unsafe_identifiers():
    calls = []
    listing = noon_listing('Operations Associate', '1') + noon_listing('Operations Manager', '../private')
    def handler(request):
        calls.append(request)
        return httpx.Response(200, text=listing)
    jobs, errors = run(web.fetch_noon, handler)
    assert not jobs and not errors and len(calls) == 1


def test_noon_detail_failures_are_partial_and_other_records_survive():
    def handler(request):
        if request.url.path == '/jobs':
            return httpx.Response(200, text=noon_listing() + noon_listing(identifier='29'))
        if request.url.path.endswith('/29'):
            return httpx.Response(503)
        return httpx.Response(200, json=noon_detail())
    jobs, errors = run(web.fetch_noon, handler)
    assert len(jobs) == 1 and errors


def test_noon_changed_detail_schema_is_not_reported_as_healthy():
    def handler(request):
        return httpx.Response(200, text=noon_listing()) if request.url.path == '/jobs' else httpx.Response(200, json={'unexpected': []})
    jobs, errors = run(web.fetch_noon, handler)
    assert not jobs and errors


def run(function,handler):
    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await function(client,PROFILE,SETTINGS)
    return asyncio.run(execute())


def test_amazon_country_qualifications_original_dates_and_safe_paths():
    rows=[{'job_path':'/en/jobs/123/operations-manager','title':'Operations Manager','country_code':'ARE','city':'Dubai','posted_date':'November  1, 2026',
           'basic_qualifications':'5+ years of experience','description':'Warehouse operations inventory SLA multi-site team leadership'},
          {'job_path':'/en/jobs/124/operations-manager','title':'Operations Manager','country_code':'USA','description':'Warehouse operations'},
          {'job_path':'//attacker.test/1','title':'Operations Manager','country_code':'ARE','description':'Warehouse operations'},
          {'job_path':'/en/jobs/125/it-manager','title':'IT Manager','country_code':'ARE','description':'Warehouse operations'}]
    calls=[]
    def handler(request):calls.append(request);return httpx.Response(200,json={'hits':4,'error':None,'jobs':rows})
    jobs,errors=run(web.fetch_amazon,handler)
    assert len(jobs)==1 and not errors
    assert jobs[0]['posted_at']=='2026-11-01T00:00:00+00:00'
    assert '5+ years' in jobs[0]['description']
    assert jobs[0]['country']=='United Arab Emirates'
    assert gulf.current_evidence(jobs[0])
    assert set(calls[0].url.params.get_list('normalized_country_code[]'))==set(web.ISO3)


def test_amazon_unrecognised_schema_is_not_healthy():
    jobs,errors=run(web.fetch_amazon,lambda req:httpx.Response(200,json=[]))
    assert not jobs and errors


def alshaya_detail(end='2099-01-01T00:00:00Z',apply=True):
    return f'''<h2>Store Operations Manager</h2><dl class="job-fields"><dt>Location:</dt><dd><span class="location">UAE</span></dd></dl>
    <div id="job-details"><p>Warehouse operations inventory SLA multi-site team leadership</p><p>Minimum 5 years of experience</p></div>
    <span class="open-date"><time datetime="2026-01-01T00:00:00Z">1 Jan</time></span><span class="close-date"><time datetime="{end}">end</time></span>
    {'<a href="https://secure.pageuppeople.com/apply">Apply now</a>' if apply else ''}<script>PRIVATE SHOULD NOT APPEAR</script>'''


def test_alshaya_requires_current_listing_apply_control_and_explicit_country():
    def handler(request):
        if request.url.path.endswith('/job'):
            return httpx.Response(200,text='<tbody id="search-results-content"><a href="/cau/en/job/123/store-operations-manager">Store Operations Manager</a><a href="https://evil.test/cau/en/job/999/store-manager">Store Manager</a></tbody>')
        return httpx.Response(200,text=alshaya_detail())
    jobs,errors=run(web.fetch_alshaya,handler)
    assert len(jobs)==1 and not errors
    assert jobs[0]['source_id']=='123' and jobs[0]['country']=='United Arab Emirates'
    assert 'PRIVATE' not in jobs[0]['description']+jobs[0]['raw_json']
    assert gulf.current_evidence(jobs[0])


@pytest.mark.parametrize('end,apply',[('2000-01-01T00:00:00Z',True),('2099-01-01T00:00:00Z',False)])
def test_alshaya_closed_or_missing_apply_is_not_published(end,apply):
    def handler(request):
        if request.url.path.endswith('/job'):return httpx.Response(200,text='<tbody id="search-results-content"><a href="/cau/en/job/123/store-operations-manager">Store Operations Manager</a></tbody>')
        return httpx.Response(200,text=alshaya_detail(end,apply))
    jobs,errors=run(web.fetch_alshaya,handler)
    assert not jobs


def test_alshaya_changed_page_is_reported():
    jobs,errors=run(web.fetch_alshaya,lambda req:httpx.Response(200,text='<h1>Maintenance</h1>'))
    assert not jobs and errors


def phenom(value):return '<script>phApp.ddo = '+json.dumps(value)+';</script>'


@pytest.mark.parametrize('state,country,expected',[('new','Qatar',1),('closed','Qatar',0),('new','India',0)])
def test_embedded_employer_detail_requires_external_active_job_and_matching_country(state,country,expected):
    rows=[{'jobId':'123','jobSeqNo':'S123','title':'Operations Manager','country':'Qatar'}]
    def handler(request):
        if request.url.path.endswith('search-results'):
            return httpx.Response(200,text=phenom({'eagerLoadRefineSearch':{'status':200,'totalHits':1,'data':{'jobs':rows}}}))
        return httpx.Response(200,text=phenom({'jobDetail':{'status':200,'data':{'job':{'jobId':'123','title':'Operations Manager','country':country,'city':'Doha','jobStatus':state,'siteType':'external',
             'postedDate':'2026-01-01T00:00:00.000+0000','description':'Warehouse operations inventory SLA team leadership. Minimum 5 years of experience.',
             'coordinatorEmail':'private@example.com','onboardingOfficer':'PRIVATE'}}}}))
    async def fetch(c,p,s):return await web.fetch_phenom(c,p,s,'Majid Al Futtaim','careers.majidalfuttaim.com')
    jobs,errors=run(fetch,handler)
    assert len(jobs)==expected
    if jobs:
        assert gulf.current_evidence(jobs[0])
        assert 'PRIVATE' not in jobs[0]['raw_json']+jobs[0]['description']
        assert 'private@example.com' not in jobs[0]['raw_json']+jobs[0]['description']


def test_embedded_employer_does_not_execute_js_or_follow_untrusted_redirects():
    with pytest.raises(ValueError):web._phenom_data('phApp.ddo = fetch("https://evil.test/");')
    calls=[]
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302,headers={'location':'https://evil.test/collect'})
    async def fetch(c,p,s):return await web.fetch_phenom(c,p,s,'DHL','careers.dhl.com')
    jobs,errors=run(fetch,handler)
    assert not jobs and errors and len(calls)==1


@pytest.mark.parametrize('date', ['not-a-date', '2099-01-01T00:00:00Z'])
def test_amazon_invalid_or_future_publication_is_excluded(date):
    row={'job_path':'/en/jobs/123/operations-manager','title':'Operations Manager','country_code':'ARE',
         'description':'Warehouse operations inventory SLA team leadership','posted_date':date}
    jobs,errors=run(web.fetch_amazon,lambda req:httpx.Response(200,json={'hits':1,'jobs':[row]}))
    assert not jobs


def test_amazon_malformed_row_is_reported_without_crashing():
    jobs,errors=run(web.fetch_amazon,lambda req:httpx.Response(200,json={'hits':1,'jobs':[None]}))
    assert not jobs and errors


@pytest.mark.parametrize('date', ['not-a-date', '2099-01-01T00:00:00Z'])
def test_alshaya_invalid_or_future_publication_is_excluded(date):
    def handler(request):
        if request.url.path.endswith('/job'):
            return httpx.Response(200,text='<tbody id="search-results-content"><a href="/cau/en/job/123/store-operations-manager">Store Operations Manager</a></tbody>')
        return httpx.Response(200,text=alshaya_detail().replace('2026-01-01T00:00:00Z',date))
    jobs,errors=run(web.fetch_alshaya,handler)
    assert not jobs
