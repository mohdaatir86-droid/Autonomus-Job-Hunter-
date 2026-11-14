import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import gulf_sources as gulf
import job_sources
import job_hunter
import application_workflow as workflow
from test_workflow_regressions import client, owner_headers

PROFILE = {'experience_summary': '9+ years of operations experience'}
SETTINGS = {'min_experience_years': 8, 'work_arrangement': 'all'}


def job(title='Sr. Manager Logistics Operations', description='Minimum 5 years of experience. Warehouse operations inventory SLA KPI team leadership', country='Qatar'):
    return job_sources._clean_job({'id': '123', 'title': title, 'company': 'Example employer', 'location': 'Doha, ' + country,
        'description': description, 'date': '2026-01-01', 'url': 'https://jobs.smartrecruiters.com/DeliveryHero/123'}, 'SmartRecruiters/DeliveryHero')


@pytest.mark.parametrize('title,description,accepted', [
    ('Sr. Manager Logistics Operations', 'Minimum 5 years of experience. Warehouse operations', True),
    ('Warehouse Manager', 'Minimum 10 years of experience', False),
    ('Warehouse Manager', '3-5 years of experience', False),
    ('Warehouse Manager', 'Inventory and leadership; experience not published', True),
    ('Junior Warehouse Manager', 'Warehouse operations', False),
    ('Warehouse Coordinator', 'Warehouse operations', False),
    ('Software Engineering Manager', 'Warehouse logistics inventory', False),
    ('Data Analytics Manager - Logistics', 'Warehouse logistics inventory', False),
    ('HSM Performance Senior Manager', 'Logistics quick commerce food delivery business analytics FP&A', False),
    ('Program Manager', 'Warehouse operations multi-site logistics SLA inventory', True),
    ('Program Manager', 'Software delivery programme and KPI', False),
    ('Assistant Director Operations', 'Warehouse operations', True),
])
def test_senior_physical_operations_fit(title, description, accepted):
    assert gulf.fit(job(title, description), PROFILE, SETTINGS)[0] is accepted


def test_unknown_requirements_are_flagged_not_fabricated():
    accepted, notes = gulf.fit(job(description='Warehouse operations. Arabic and citizenship conditions apply.'), {}, SETTINGS)
    assert accepted
    assert any('Review experience' in n for n in notes)
    assert any('nationality' in n for n in notes)
    assert gulf.candidate_years({'experience_summary': 'Experienced manager'}) is None


@pytest.mark.parametrize('url', ['https://evil.test/job', 'https://jobs.smartrecruiters.com.evil.test/a', 'http://jobs.smartrecruiters.com/a', 'https://user@jobs.smartrecruiters.com/a', 'https://jobs.smartrecruiters.com:8443/a', 'javascript:alert(1)'])
def test_apply_allowlist(url):
    assert not gulf.safe_apply_url(url)


def test_evidence_expiry_and_posted_date_unchanged():
    now = datetime.now(timezone.utc)
    row = gulf.mark(job(), 'https://jobs.smartrecruiters.com/oneclick-ui/example', now.isoformat())
    assert row['posted_at'].startswith('2026-01-01')
    assert gulf.current_evidence(row, now)
    assert not gulf.current_evidence(row, now + timedelta(hours=36))
    assert not gulf.current_evidence(row, now - timedelta(seconds=1))
    assert not gulf.current_evidence({'raw_json': 'broken'})


def test_smartrecruiters_pagination_details_closed_and_country_validation():
    calls = []
    def handler(request):
        calls.append(str(request.url))
        if request.url.path.endswith('/postings'):
            if request.url.params['country'] != 'qa':
                return httpx.Response(200, json={'content': [], 'totalFound': 0})
            offset = int(request.url.params['offset'])
            if offset == 0:
                rows = [{'id': str(i), 'name': 'Warehouse Coordinator', 'location': {'country': 'qa'}} for i in range(100)]
            else:
                rows = [{'id': '123', 'name': 'Sr. Manager Logistics Operations', 'location': {'country': 'qa'}},
                        {'id': '124', 'name': 'Warehouse Manager', 'location': {'country': 'qa'}},
                        {'id': '125', 'name': 'Warehouse Manager', 'location': {'country': 'in'}}]
            return httpx.Response(200, json={'content': rows, 'totalFound': 103})
        if request.url.path.endswith('/124'):
            return httpx.Response(404)
        return httpx.Response(200, json={'id': '123', 'name': 'Sr. Manager Logistics Operations', 'location': {'city': 'Doha'},
            'applyUrl': 'https://jobs.smartrecruiters.com/oneclick-ui/example', 'releasedDate': '2026-01-01',
            'customField': [{'fieldLabel': 'Brands', 'valueLabel': 'Example brand'}],
            'jobAd': {'sections': {'jobDescription': {'text': '<p>Warehouse operations. Minimum 5 years of experience</p>'}}}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await gulf.fetch_smartrecruiters(c, PROFILE, SETTINGS)
    rows, errors = asyncio.run(run())
    assert len(rows) == 1 and rows[0]['company'] == 'Example brand'
    assert gulf.current_evidence(rows[0])
    assert errors and any('offset=100' in c for c in calls)
    assert not any(c.endswith('/125') for c in calls)
    assert all(c.startswith('https://api.smartrecruiters.com/') for c in calls)


def test_gulf_private_apis_and_original_listing_detail(client):
    for path in ['/api/gulf/sources', '/api/jobs/123', '/api/jobs?gulf_open=true', '/api/stats?gulf_open=true']:
        assert client.get(path).status_code == 401
    assert client.post('/api/gulf/refresh').status_code == 401
    response = client.get('/api/gulf/sources', headers=owner_headers())
    assert len(response.json()['items']) == 22
    assert {i['mode'] for i in response.json()['items']} == {'direct', 'indexed', 'external'}


def test_import_and_view_preserve_history_dates_duplicate_identity_and_other_source_reports(client, monkeypatch):
    workflow.save_profile(PROFILE)
    from gulf_sources import FEED_NAME
    row = gulf.mark(job(), 'https://jobs.smartrecruiters.com/oneclick-ui/example', job_hunter.now_iso())
    async def fetch(c, settings): return [row], None
    monkeypatch.setattr(gulf, 'fetch_gulf_sources', fetch)
    async def forbidden(*args, **kwargs): raise AssertionError('No mailbox, paid provider or messaging call allowed')
    import notify
    monkeypatch.setattr(notify, 'notify_high_match_jobs', forbidden)
    monkeypatch.setattr(job_sources, 'fetch_public_sources', forbidden)
    job_sources.persist_source_report([{'name': 'Google Jobs via SerpApi', 'status': 'degraded', 'fetched': 1}], {'bayt': 1})
    assert client.post('/api/gulf/refresh', headers=owner_headers()).json()['new_jobs'] == 1
    result = client.get('/api/jobs?gulf_open=true', headers=owner_headers()).json()['items']
    assert len(result) == 1 and result[0]['posted_at'].startswith('2026-01-01')
    assert 'description' not in result[0] and 'raw_json' not in result[0]
    assert client.get('/api/stats?gulf_open=true', headers=owner_headers()).json()['total_jobs'] == 1
    assert client.get('/api/stats', headers=owner_headers()).json()['total_jobs'] == 0
    for query, expected in [('country=India', 0), ('country=Qatar', 1), ('q=nonexistent', 0), ('q=Logistics', 1), ('source=missing', 0), ('status=saved', 0), ('min_score=100', 0)]:
        scope = 'gulf_open=true&' + query
        stats = client.get('/api/stats?' + scope, headers=owner_headers()).json()
        listed = client.get('/api/jobs?' + scope, headers=owner_headers()).json()['items']
        assert stats['total_jobs'] == len(listed) == expected
        assert stats['freshness_scope'] == 'published_checked_36h'
    identifier = result[0]['id']
    details = client.get(f'/api/jobs/{identifier}', headers=owner_headers()).json()
    assert details['apply_url'].startswith('https://jobs.smartrecruiters.com/')
    assert details['description'] and details['published_recently_checked']
    client.post(f'/api/jobs/{identifier}/status', json={'status': 'applied_manually'}, headers=owner_headers())
    assert client.post('/api/gulf/refresh', headers=owner_headers()).json()['new_jobs'] == 0
    with job_hunter.connect() as db:
        record = db.execute('SELECT * FROM jobs WHERE id=?', (identifier,)).fetchone()
        assert record['status'] == 'applied_manually'
        old = gulf.mark(dict(record), details['apply_url'], (datetime.now(timezone.utc) - timedelta(hours=40)).isoformat())
        db.execute('UPDATE jobs SET raw_json=? WHERE id=?', (old['raw_json'], identifier))
    assert client.get('/api/stats?gulf_open=true', headers=owner_headers()).json()['total_jobs'] == 0
    assert client.get('/api/stats?status=applied_manually', headers=owner_headers()).json()['total_jobs'] == 1
    assert client.get('/api/jobs?gulf_open=true', headers=owner_headers()).json()['items'] == []
    assert client.get('/api/jobs?status=applied_manually', headers=owner_headers()).json()['items']
    assert not client.get(f'/api/jobs/{identifier}', headers=owner_headers()).json()['published_recently_checked']
    report = job_sources.get_source_report()
    assert report['portal_counts'] == {'bayt': 1}
    assert {r['name'] for r in report['sources']} == {'Google Jobs via SerpApi', FEED_NAME}


def test_database_discovery_lease_blocks_overlap_and_expired_lease_recovers(client, monkeypatch):
    job_sources.ensure_job_columns()
    with job_hunter.connect() as db:
        db.execute("INSERT INTO discovery_leases VALUES ('hunt','other',?)", ((datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(),))
    assert asyncio.run(job_sources.hunt_jobs(gulf_only=True))['status'] == 'already_running'
    with job_hunter.connect() as db:
        db.execute("UPDATE discovery_leases SET expires_at='2000-01-01T00:00:00+00:00'")
    async def fetch(c, settings): return [], None
    monkeypatch.setattr(gulf, 'fetch_gulf_sources', fetch)
    assert asyncio.run(job_sources.hunt_jobs(gulf_only=True))['status'] == 'completed'
    with job_hunter.connect() as db:
        assert db.execute('SELECT count(*) FROM discovery_leases').fetchone()[0] == 0


def test_rss_uses_explicit_job_location_and_preserves_dates():
    rss = '''<rss xmlns:tt="https://teamtailor.com/locations"><channel>
    <item><title>Store Manager</title><link>https://careers.gmg.com/jobs/123</link><description>Warehouse operations inventory. Minimum 5 years of experience.</description><pubDate>Thu, 05 Nov 2026 13:48:33 +0400</pubDate><tt:locations><tt:location><tt:city>Doha</tt:city><tt:country>Qatar</tt:country></tt:location></tt:locations></item>
    <item><title>Warehouse Manager</title><link>https://evil.test/123</link><description>Warehouse operations Dubai</description></item>
    <item><title>Warehouse Manager</title><link>https://careers.gmg.com/jobs/124</link><description>Company has Dubai office; job location is unknown</description></item>
    </channel></rss>'''
    def handler(request):
        return httpx.Response(200, text=rss if request.url.host == 'careers.gmg.com' else '<rss><channel/></rss>')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await gulf.fetch_rss_employers(c, PROFILE, SETTINGS)
    rows, errors = asyncio.run(run())
    assert len(rows) == 1 and not errors
    assert rows[0]['country'] == 'Qatar'
    assert rows[0]['posted_at'] == '2026-11-05T09:48:33+00:00'
    assert gulf.current_evidence(rows[0])


def test_rss_rejects_xml_entities():
    def handler(request):
        return httpx.Response(200, text='<!DOCTYPE rss [<!ENTITY secret SYSTEM "file:///etc/passwd">]><rss/>')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await gulf.fetch_rss_employers(c, PROFILE, SETTINGS)
    rows, errors = asyncio.run(run())
    assert rows == [] and len(errors) == 3


def test_oracle_public_postings_details_dates_country_and_pagination():
    calls=[]
    def handler(request):
        calls.append(str(request.url))
        params=request.url.params
        if request.url.path.endswith('recruitingCEJobRequisitions'):
            if request.url.host.startswith('ehpv'):
                return httpx.Response(200,json={'items':[{'TotalJobsCount':0,'requisitionList':[]}]})
            if 'offset=0' in params['finder']:
                return httpx.Response(200,json={'items':[{'TotalJobsCount':102,'requisitionList':[
                    {'Id':'1','Title':'Warehouse Manager','PrimaryLocationCountry':'AE','PostedDate':'2026-01-01'},
                    {'Id':'2','Title':'Warehouse Manager','PrimaryLocationCountry':'AE','PostedDate':'2026-01-01'},
                    {'Id':'3','Title':'Warehouse Manager','PrimaryLocationCountry':'AE','PostedDate':'2026-01-01'},
                    {'Id':'4','Title':'Warehouse Manager','PrimaryLocationCountry':'AE','PostedDate':'2026-01-01'},
                    {'Id':'5','Title':'Warehouse Coordinator','PrimaryLocationCountry':'AE'},
                ]}]})
            return httpx.Response(200,json={'items':[{'TotalJobsCount':102,'requisitionList':[]}]})
        identifier=params['finder'].split('Id=')[1].split(',')[0]
        if identifier=='2':return httpx.Response(404)
        return httpx.Response(200,json={'items':[{'Id':identifier,'Title':'Warehouse Manager',
            'PrimaryLocationCountry':'IN' if identifier=='3' else 'AE','PrimaryLocation':'Dubai',
            'ExternalPostedStartDate':'2026-01-01',
            'ExternalPostedEndDate':'2000-01-01' if identifier=='4' else '2099-01-01',
            'ExternalResponsibilitiesStr':'<p>Warehouse operations and inventory SLA team leadership.</p>',
            'ExternalQualificationsStr':'Minimum 5 years of experience',
            'InternalResponsibilitiesStr':'PRIVATE INTERNAL FIELD MUST NEVER BE RETAINED'}]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await gulf.fetch_oracle_employers(c,PROFILE,SETTINGS)
    rows,errors=asyncio.run(run())
    assert len(rows)==1 and errors
    row=rows[0]
    assert row['source']=='Oracle Careers/Landmark Group'
    assert row['country']=='United Arab Emirates' and row['posted_at'].startswith('2026-01-01')
    assert 'PRIVATE INTERNAL' not in row['description']+row['raw_json']
    assert gulf.current_evidence(row)
    assert any('offset%3D100' in c for c in calls)
    assert not any('Id%3D5' in c for c in calls)
    assert all('recruitingICE' not in c for c in calls)


def test_explicit_closing_time_overrides_feed_freshness():
    now=datetime.now(timezone.utc)
    row=gulf.mark(job(),'https://efhi.fa.em3.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/123',now.isoformat(),(now+timedelta(minutes=1)).isoformat())
    assert gulf.current_evidence(row,now)
    assert not gulf.current_evidence(row,now+timedelta(minutes=2))
    assert gulf.evidence({'raw_json':'{"_gulf":"untrusted"}'})=={}


def test_oracle_host_allowlist():
    assert not gulf.safe_apply_url('https://attacker.oraclecloud.com/hcmUI/CandidateExperience/job/123')
    assert gulf.safe_apply_url('https://ehpv.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/123')


def test_provider_groups_run_together_and_keep_healthy_results(monkeypatch):
    async def run():
        oracle_started=asyncio.Event()
        async def smart(*args):
            await oracle_started.wait()
            return [job()],[]
        async def oracle(*args):
            oracle_started.set()
            raise ValueError('malformed optional feed')
        async def rss(*args):return [],[]
        monkeypatch.setattr(gulf,'fetch_smartrecruiters',smart)
        monkeypatch.setattr(gulf,'fetch_oracle_employers',oracle)
        monkeypatch.setattr(gulf,'fetch_rss_employers',rss)
        monkeypatch.setattr(workflow,'get_profile_full',lambda:PROFILE)
        def handler(request):return httpx.Response(200,json={'jobs':[]} if 'greenhouse' in request.url.host else [])
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await asyncio.wait_for(gulf.fetch_gulf_sources(c,SETTINGS),timeout=1)
    rows,error=asyncio.run(run())
    assert len(rows)==1 and 'Oracle careers' in error


def test_alfuttaim_rss_country_only_location_is_not_guessed():
    xml='''<rss><channel><item><title>Warehouse Manager (AE)</title><link>https://www.afuturewithus.com/job/123</link><description>Warehouse operations inventory SLA. Minimum 5 years of experience</description><pubDate>Fri, 13 Nov 2026 02:00:00 GMT</pubDate></item></channel></rss>'''
    def handler(request):return httpx.Response(200,text=xml if request.url.host=='www.afuturewithus.com' else '<rss><channel/></rss>')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await gulf.fetch_rss_employers(c,PROFILE,SETTINGS)
    rows,error=asyncio.run(run())
    assert len(rows)==1 and error==[]
    assert rows[0]['location']=='United Arab Emirates'
    assert rows[0]['country']=='United Arab Emirates'


def test_snoonu_only_active_gulf_ops_with_explicit_experience_and_real_dates():
    jobs=[{'id':1,'title':'Vertical Operations Director','description':'Warehouse operations and multi-site fulfilment inventory SOP. Minimum 5 years of experience.',
           'jobLocations':[{'city':'Lusail','countryCode':'QA'}],'publishedOn':'2026-01-01T00:00:00Z','jobType':2},
          {'id':2,'title':'Tech Lead – Logistics Systems','description':'Warehouse logistics software engineering','jobLocations':[{'city':'Lusail','countryCode':'QA'}]},
          {'id':3,'title':'Operations Manager','description':'Warehouse operations logistics leadership','jobLocations':[{'city':'Lahore','countryCode':'PK'}]},
          {'id':4,'title':'Operations Manager','description':'Warehouse operations logistics. '+('x '*10000),'experience':'5-7', 'jobLocations':[{'city':'Lusail','countryCode':'QA'}]},
          {'id':5,'title':'Operations Manager','description':'Warehouse operations','jobLocations':[{'city':'Lusail','countryCode':'QA'}],'publishedOn':'2099-01-01'},
          {'id':'../../private','title':'Operations Manager','description':'Warehouse operations','jobLocations':[{'countryCode':'QA'}]}]
    calls=[]
    def handler(request):calls.append(str(request.url));return httpx.Response(200,json=jobs)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await gulf.fetch_snoonu(c,PROFILE,SETTINGS)
    rows,errors=asyncio.run(run())
    assert len(rows)==1 and not errors
    assert rows[0]['country']=='Qatar' and rows[0]['posted_at'].startswith('2026-01-01')
    assert gulf.current_evidence(rows[0])
    assert gulf.evidence(rows[0])['apply_url']=='https://snoonu.keka.com/careers/jobdetails/1'
    assert len(calls)==1 and '/active/' in calls[0]


def test_known_retail_leadership_is_ranked_with_physical_context_only():
    settings={'target_countries':['Qatar']}
    physical='Multi-site warehouse operations, fulfilment, inventory, workforce, SLA and KPI. Store operations cost control.'
    regional=job('Regional Concept Business Head - Home Centre',physical)
    store=job('Store Manager',physical)
    tech=job('Tech Lead – Logistics Systems',physical+' software engineering')
    assert job_hunter.score_job(regional,settings)[0]>job_hunter.score_job(store,settings)[0]>24
    assert job_hunter.score_job(tech,settings)[0]<=12
    assert not job_hunter.is_relevant_role(tech)
    assert job_hunter.score_job(job('Regional Concept Business Head - Home Centre','Business strategy only'),settings)[0]<=24


def test_snoonu_bad_response_cannot_claim_working_or_infer_countries():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'error':'changed'}))) as c:
            return await gulf.fetch_snoonu(c,PROFILE,SETTINGS)
    rows,errors=asyncio.run(run())
    assert rows==[] and errors
