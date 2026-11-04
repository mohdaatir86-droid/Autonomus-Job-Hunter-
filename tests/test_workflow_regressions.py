import asyncio
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
import main
import job_hunter
import job_sources
import application_workflow as workflow
import application_state
import gmail_alerts
import browser_worker


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = str(tmp_path / 'workflow.sqlite')
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('DATABASE_PATH', path)
    monkeypatch.setenv('ENABLE_SCHEDULER', 'false')
    monkeypatch.setenv('PORTAL_ACCESS_TOKEN', 'fixture-private-key-' * 3)
    for module in (job_hunter, workflow, application_state, gmail_alerts, browser_worker):
        monkeypatch.setattr(module, 'DB_PATH', path)
    with TestClient(main.app) as c:
        yield c


def owner_headers():
    return {'Authorization': 'Bearer ' + 'fixture-private-key-' * 3}


@pytest.mark.parametrize('path,body', [
    ('/api/settings', {'min_match_score': 10}),
    ('/api/jobs/1/status', {'status': 'rejected'}),
    ('/api/hunt', {}), ('/api/notify/test', {}), ('/api/gmail/auth', {}),
])
def test_mutations_require_owner(client, path, body):
    assert client.post(path, json=body).status_code == 401


def test_preferences_survive_reads(client):
    response = client.post('/api/settings', headers=owner_headers(), json={
        'target_countries': ['Canada'], 'target_titles': ['Warehouse Manager'],
    })
    assert response.status_code == 200
    settings = client.get('/api/settings').json()
    assert settings['target_countries'] == ['Canada']
    assert settings['target_titles'] == ['Warehouse Manager']


def insert_job():
    now = job_hunter.now_iso()
    with job_hunter.connect() as db:
        db.execute('''INSERT INTO jobs(source,title,company,country,url,first_seen_at,last_seen_at)
            VALUES ('fixture','Warehouse Manager','Example','Canada','https://example.com/jobs/1',?,?)''', (now, now))
        return db.execute("SELECT id FROM jobs WHERE source='fixture'").fetchone()[0]


def test_tracking_stays_consistent_and_applied_draft_is_preserved(client):
    job_id = insert_job()
    draft = asyncio.run(workflow.create_application_draft(job_id))
    workflow.update_draft_status(draft['id'], 'applied_manually')
    with job_hunter.connect() as db:
        assert db.execute('SELECT status FROM jobs WHERE id=?', (job_id,)).fetchone()[0] == 'applied_manually'
    repeated = asyncio.run(workflow.create_application_draft(job_id))
    assert repeated['status'] == 'applied_manually'
    job_sources.set_job_status(job_id, 'interview')
    assert workflow.list_application_drafts()[0]['status'] == 'interview'


def test_public_sources_hide_mailbox(client, monkeypatch):
    monkeypatch.setattr(main, 'get_gmail_status', lambda: {'connected': True, 'email': 'private@example.com'})
    response = client.get('/api/sources')
    assert response.status_code == 200
    assert 'private@example.com' not in response.text
    assert client.get('/api/gmail/status').status_code == 401


def test_browser_cannot_submit_even_when_environment_requests_it(client, monkeypatch):
    monkeypatch.setenv('BROWSER_WORKER_ENABLED', 'true')
    monkeypatch.setenv('BROWSER_ALLOW_SUBMIT', 'true')
    result = asyncio.run(browser_worker.run_application_worker(1, dry_run=False))
    assert result['status'] == 'blocked'
    assert result['reason'] == 'submission_disabled'
    assert client.get('/health').json()['browser_submit_enabled'] is False


def test_oauth_start_requires_owner_and_callback_is_browser_bound(client, monkeypatch):
    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'fixture-client')
    monkeypatch.setenv('GOOGLE_CLIENT_SECRET', 'fixture-secret')
    monkeypatch.setenv('OAUTH_STATE_SECRET', 'fixture-state-secret')
    monkeypatch.setattr(main, 'get_profile', lambda: {'email': 'owner@example.com'})
    start = client.post('/api/gmail/auth', headers=owner_headers())
    assert start.status_code == 200
    state = parse_qs(urlparse(start.json()['url']).query)['state'][0]
    with TestClient(main.app) as stranger:
        assert stranger.get('/api/gmail/callback', params={'code': 'fixture', 'state': state}).status_code == 400
    async def exchange(code):
        return {'ok': True}
    monkeypatch.setattr(main, 'exchange_code', exchange)
    assert client.get('/api/gmail/callback', params={'code': 'fixture', 'state': state}, follow_redirects=False).status_code == 307
    assert client.get('/api/gmail/callback', params={'code': 'fixture', 'state': state}, follow_redirects=False).status_code == 400


def test_job_filter_respects_configured_countries(client):
    insert_job()
    job_hunter.save_settings({'target_countries': ['India']})
    assert job_sources.list_jobs() == []


def test_submit_transition_cannot_be_claimed_twice(client):
    attempt = application_state.get_or_create_attempt(portal='lever', source_id='x', url='https://jobs.lever.co/x')
    for state in ['scored', 'queued', 'opening_job', 'form_detected', 'filling_form', 'ready_to_submit', 'submitting']:
        application_state.transition(attempt['id'], state)
    with pytest.raises(ValueError):
        application_state.transition(attempt['id'], 'submitting')


def test_google_transport_is_installed():
    from google.auth.transport.requests import Request
    assert Request is not None


@pytest.mark.parametrize('job,settings,expected', [
    ({'country': 'India', 'salary': '12-18 LPA'}, {'min_india_salary_lpa': 20}, False),
    ({'country': 'India', 'salary': '20-30 LPA'}, {'min_india_salary_lpa': 20}, True),
    ({'country': 'India', 'salary': ''}, {'min_india_salary_lpa': 20}, True),
    ({'country': 'Canada', 'salary': 'CAD 100,000'}, {'min_india_salary_lpa': 20}, True),
    ({'description': 'This is a fully remote role'}, {'work_arrangement': 'onsite'}, False),
    ({'description': ''}, {'work_arrangement': 'remote'}, True),
    ({'description': 'Visa sponsorship is not available'}, {'visa_preference': 'sponsor'}, False),
    ({'description': 'Visa sponsorship available'}, {'visa_preference': 'sponsor'}, True),
    ({'description': 'Minimum 2 years of experience'}, {'min_experience_years': 8}, False),
])
def test_preferences_filter_only_explicit_evidence(job, settings, expected):
    assert job_sources.matches_preferences(job, settings) is expected


def test_posted_window_can_be_changed(client):
    response = client.get('/api/jobs?posted_within_days=7')
    assert response.status_code == 200


def test_profile_status_reports_missing_configuration_without_secrets(client):
    result = client.get('/api/setup', headers=owner_headers())
    assert result.status_code == 200
    data = result.json()
    assert data['submission_enabled'] is False
    assert data['profile']['ready'] is False
    assert 'PDF resume' in data['profile']['missing']
    assert 'fixture-private-key' not in result.text


@pytest.mark.parametrize('location,country,expected', [
    ('Noida, Uttar Pradesh, India', 'India', True),
    ('Lucknow, India', 'India', True),
    ('Dehradun, India', 'India', True),
    ('Delhi NCR, India', 'India', True),
    ('Mumbai, India', 'India', False),
    ('Riyadh, Saudi Arabia', 'Saudi Arabia', True),
    ('Manama, Bahrain', 'Bahrain', True),
])
def test_user_target_locations(location, country, expected):
    assert job_sources.matches_preferences({'location': location, 'country': country}, {'preferred_locations': ['Delhi NCR', 'Uttar Pradesh', 'Dehradun']}) is expected


def test_search_queries_follow_user_targets(client):
    queries = job_sources.build_search_queries(job_hunter.get_settings())
    assert any('Delhi' in query for query in queries)
    assert any('Bahrain' in query for query in queries)
    assert not any('Canada' in query for query in queries)


def test_preparation_skips_existing_drafts_before_limit(client):
    job_id = insert_job()
    with job_hunter.connect() as db:
        db.execute('UPDATE jobs SET posted_at=?,match_score=90 WHERE id=?', (job_hunter.now_iso(),job_id))
    asyncio.run(workflow.create_application_draft(job_id))
    assert job_sources.list_jobs(draft_candidates=True, limit=1) == []


def test_tracked_application_remains_visible_after_posting_expires(client):
    job_id = insert_job()
    with job_hunter.connect() as db:
        db.execute("UPDATE jobs SET posted_at='2020-01-01T00:00:00+00:00',status='applied_manually' WHERE id=?",(job_id,))
    assert len(client.get('/api/jobs?status=applied_manually').json()['items']) == 1


def text_pdf(text):
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>', b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>', b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    stream = ('BT /F1 12 Tf 50 700 Td (' + text + ') Tj ET').encode()
    objects.append(b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream')
    data=b'%PDF-1.4\n'; offsets=[0]
    for i,obj in enumerate(objects,1):
        offsets.append(len(data)); data+=f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n'
    pos=len(data); data+=f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode()
    data+=b''.join(f'{offset:010} 00000 n \n'.encode() for offset in offsets[1:])
    return data+f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{pos}\n%%EOF'.encode()


def test_complete_discovery_profile_draft_and_tracking(client, monkeypatch):
    monkeypatch.delenv('AI_API_KEY', raising=False)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    import notify
    sent=[]
    async def notify_fixture(jobs):
        sent.extend(jobs)
        return {'sent': False, 'skipped': True}
    monkeypatch.setattr(notify, 'notify_high_match_jobs', notify_fixture)
    async def sources(settings):
        def job(city, country, id):
            return job_sources._clean_job({'id':id,'title':'Cluster Manager Operations','company':'Fixture Logistics','location':city+', '+country,'url':'https://example.com/jobs/'+id,'description':'Dark store operations, warehouse inventory, SLA, KPI, last mile team leadership','date':job_hunter.now_iso()}, 'Fixture')
        return [job('Noida','India','ncr'),job('Mumbai','India','mumbai'),job('Manama','Bahrain','gulf')], [], [{'name':'Fixture','status':'working','fetched':3}], {}
    monkeypatch.setattr(job_sources,'fetch_public_sources',sources)
    hunt=client.post('/api/hunt',headers=owner_headers()).json()
    assert hunt['new_jobs']==2
    assert len(sent)==2
    items=client.get('/api/jobs').json()['items']
    assert {job['country'] for job in items}=={'India','Bahrain'}
    assert client.post('/api/profile',headers=owner_headers(),json={'full_name':'Fixture Candidate','email':'fixture@example.com','experience_summary':'Led multi-site warehouse operations.'}).status_code==200
    upload=client.post('/api/profile/resume',headers=owner_headers(),files={'file':('resume.pdf',text_pdf('Fixture Candidate warehouse operations manager'), 'application/pdf')})
    assert upload.status_code==200, upload.text
    assert upload.json()['profile']['has_resume']
    assert client.post('/api/automation/settings',headers=owner_headers(),json={'enabled':True,'min_score':50,'max_per_run':10}).status_code==200
    prepared=client.post('/api/automation/prepare',headers=owner_headers()).json()
    assert prepared['prepared_count']==2, prepared
    again=client.post('/api/automation/prepare',headers=owner_headers()).json()
    assert again['prepared_count']==0
    drafts=client.get('/api/applications',headers=owner_headers()).json()['items']
    assert 'Fixture Candidate' in drafts[0]['cover_letter']
    assert client.post(f"/api/applications/{drafts[0]['id']}/status",headers=owner_headers(),json={'status':'applied_manually'}).status_code==200
    assert client.get('/api/jobs?status=applied_manually').json()['items'][0]['status']=='applied_manually'


@pytest.mark.parametrize('value',[[],{},None,123])
def test_invalid_preference_values_return_validation_error(client,value):
    assert client.post('/api/settings',headers=owner_headers(),json={'work_arrangement':value}).status_code==422
    assert client.post('/api/settings',headers=owner_headers(),json={'visa_preference':value}).status_code==422


def test_cookie_session_needs_csrf_for_mutations(client):
    workflow.save_profile({'full_name':'Fixture','email':'fixture@example.com'})
    client.cookies.set(main.PORTAL_SESSION_COOKIE,main._issue_portal_session('fixture@example.com'))
    client.cookies.set(main.PORTAL_CSRF_COOKIE,'fixture-csrf')
    assert client.post('/api/settings',json={'min_match_score':55}).status_code==403
    assert client.post('/api/settings',headers={'X-JH-CSRF':'fixture-csrf'},json={'min_match_score':55}).status_code==200


def test_gmail_exchange_rejects_different_account_before_saving(client, monkeypatch):
    import httpx
    monkeypatch.setenv('GOOGLE_CLIENT_ID','fixture')
    monkeypatch.setenv('GOOGLE_CLIENT_SECRET','fixture')
    workflow.save_profile({'email':'owner@example.com'})
    async def token(*args,**kwargs):
        return httpx.Response(200,json={'access_token':'fixture-access','refresh_token':'fixture-refresh','expires_in':3600})
    async def email(token): return 'different@example.com'
    monkeypatch.setattr(httpx.AsyncClient,'post',token)
    monkeypatch.setattr(gmail_alerts,'_fetch_profile_email',email)
    assert asyncio.run(gmail_alerts.exchange_code('fixture'))['ok'] is False
    with job_hunter.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM gmail_tokens').fetchone()[0]==0


@pytest.mark.parametrize('location',['Kanpur','Azamgarh, UP','Gautam Buddha Nagar','Rae Bareli'])
def test_up_cities_are_detected_without_country_suffix(location):
    assert job_hunter.detect_country(location)=='India'


def test_whatsapp_does_not_alert_stale_or_undated_jobs(client,monkeypatch):
    import notify
    candidates=[]
    async def capture(jobs):
        candidates.extend(jobs)
        return {'sent':False,'skipped':True}
    async def sources(settings):
        jobs=[]
        for identity,date in [('stale','2020-01-01T00:00:00+00:00'),('undated',None)]:
            jobs.append(job_sources._clean_job({'id':identity,'title':'Senior Cluster Manager','company':'Fixture','location':'Noida, India','url':'https://example.com/'+identity,'description':'Dark store inventory SLA KPI team leadership','date':date},'Fixture'))
        return jobs,[],[],{}
    monkeypatch.setattr(job_sources,'fetch_public_sources',sources)
    monkeypatch.setattr(notify,'notify_high_match_jobs',capture)
    assert client.post('/api/hunt',headers=owner_headers()).status_code==200
    assert candidates==[]
