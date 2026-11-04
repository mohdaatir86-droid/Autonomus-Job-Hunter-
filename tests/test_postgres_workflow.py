"""Run against a disposable PostgreSQL database via TEST_POSTGRES_URL."""
import os
import uuid
import pytest
from fastapi.testclient import TestClient
import main
import job_hunter
import application_workflow
import job_sources


def test_empty_postgres_bootstrap_and_repeatable_workflow(monkeypatch):
    url = os.getenv('TEST_POSTGRES_URL')
    if not url:
        pytest.skip('TEST_POSTGRES_URL not configured')
    monkeypatch.setenv('DATABASE_URL', url)
    monkeypatch.setenv('ENABLE_SCHEDULER', 'false')
    monkeypatch.setenv('PORTAL_ACCESS_TOKEN', 'test-postgres-private-key-' * 3)
    unique = uuid.uuid4().hex
    headers = {'Authorization': 'Bearer ' + 'test-postgres-private-key-' * 3}
    for _ in range(2):
        with TestClient(main.app) as client:
            health = client.get('/health/db').json()
            assert health['ok'], health
            job_sources.ensure_job_columns()
            job_sources.ensure_job_columns()
            assert client.get('/api/jobs').status_code == 200
            assert client.post('/api/settings', headers=headers, json={'target_countries':['India']}).json()['target_countries'] == ['India']
    now = job_hunter.now_iso()
    with job_hunter.connect() as conn:
        conn.execute("INSERT INTO jobs(source,title,company,country,url,first_seen_at,last_seen_at) VALUES ('test','Operations Manager','Fixture','India',?,?,?)", ('https://example.com/' + unique, now, now))
        job_id = conn.execute('SELECT id FROM jobs WHERE url=?', ('https://example.com/' + unique,)).fetchone()[0]
    import asyncio
    draft = asyncio.run(application_workflow.create_application_draft(job_id))
    application_workflow.update_draft_status(draft['id'], 'interview')
    with job_hunter.connect() as conn:
        assert conn.execute('SELECT status FROM jobs WHERE id=?',(job_id,)).fetchone()[0] == 'interview'


def test_postgres_concurrent_outreach_cannot_contact_one_recipient_twice(monkeypatch):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from fastapi import HTTPException
    import outreach,gmail_alerts
    url=os.getenv('TEST_POSTGRES_URL')
    if not url:pytest.skip('TEST_POSTGRES_URL not configured')
    monkeypatch.setenv('DATABASE_URL',url)
    monkeypatch.setenv('ENABLE_SCHEDULER','false')
    unique=uuid.uuid4().hex
    assert __import__('urllib.parse',fromlist=['urlsplit']).urlsplit(url).path.endswith('_test'), 'Use a disposable _test database'
    with TestClient(main.app):
        application_workflow.save_profile({'full_name':'Concurrency Fixture','email':'candidate@example.com','experience_summary':'Led verified warehouse operations.'})
        with job_hunter.connect() as db:
            db.execute("UPDATE candidate_profile SET resume_text='Fixture resume' WHERE id=1")
            ids=[]
            for index in range(2):
                link=f'https://example.com/{unique}/{index}'
                db.execute("INSERT INTO jobs(source,title,company,country,url,first_seen_at,last_seen_at) VALUES ('fixture','Operations Manager','Fixture','India',?,?,?)",(link,job_hunter.now_iso(),job_hunter.now_iso()))
                ids.append(db.execute('SELECT id FROM jobs WHERE url=?',(link,)).fetchone()[0])
        with job_hunter.connect() as db:
            db.execute('DELETE FROM outreach_daily_usage')
        person=outreach.create_contact({'email':unique+'@example.com','name':'Recruiter','source_url':'https://linkedin.com/in/fixture'})
        outreach.verify_contact(person['id'],{'email_confirmed':True,'employer_confirmed':True,'role_confirmed':True,'note':'Fixture verification for concurrent-claim regression.'})
        drafts=[outreach.create_draft(person['id'],job_id) for job_id in ids]
        for draft in drafts:outreach.approve_draft(draft['id'],{'confirmed':True,'recipient_relevance_confirmed':True})
        monkeypatch.setattr(gmail_alerts,'get_gmail_status',lambda:{'send_authorized':True,'email':'candidate@example.com'})
        async def token():return 'fixture-token'
        monkeypatch.setattr(gmail_alerts,'_access_token',token)
        calls=[]
        async def post(*args,**kwargs):
            calls.append(True)
            return __import__('httpx').Response(200,json={'id':'fixture-receipt'})
        monkeypatch.setattr(__import__('httpx').AsyncClient,'post',post)
        barrier=Barrier(2)
        original=outreach.connect
        class Connection:
            def __init__(self):self.db=original()
            def __enter__(self):self.db.__enter__();return self
            def __exit__(self,*args):return self.db.__exit__(*args)
            def execute(self,sql,params=()):
                if sql.startswith("UPDATE outreach_drafts SET state='sending'"):
                    barrier.wait(timeout=15)
                return self.db.execute(sql,params)
        monkeypatch.setattr(outreach,'connect',Connection)
        def send(draft):
            try:return asyncio.run(outreach.send_draft(draft['id']))['state']
            except HTTPException as exc:
                assert exc.status_code==409
                return 'blocked'
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(send,drafts))
        assert sorted(results)==['blocked','sent']
        assert len(calls)==1
