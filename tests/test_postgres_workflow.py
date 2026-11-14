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
            health = client.get('/health/db', headers=headers).json()
            assert health['ok'], health
            job_sources.ensure_job_columns()
            job_sources.ensure_job_columns()
            assert client.get('/api/jobs', headers=headers).status_code == 200
            for scope in ['', '?gulf_open=true', '?gulf_open=true&country=Qatar&q=' + unique]:
                stats = client.get('/api/stats' + scope, headers=headers)
                assert stats.status_code == 200
                listed = client.get('/api/jobs' + scope, headers=headers).json()['items']
                assert stats.json()['total_jobs'] == len(listed)

            import outreach_program
            research = outreach_program.save({'enabled':False,'job_found':False})
            assert research['enabled'] is False
            assert client.get('/api/outreach/program',headers=headers).json()['targets']
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
            import resume_store
            from test_workflow_regressions import text_pdf
            resume_store.save(db,'fixture.pdf',text_pdf('Fixture operations resume'))
            db.execute("UPDATE candidate_profile SET resume_text='Fixture resume' WHERE id=1")
            ids=[]
            for index in range(2):
                link=f'https://example.com/{unique}/{index}'
                db.execute("INSERT INTO jobs(source,title,company,country,url,first_seen_at,last_seen_at) VALUES ('fixture','Operations Manager','Fixture','India',?,?,?)",(link,job_hunter.now_iso(),job_hunter.now_iso()))
                ids.append(db.execute('SELECT id FROM jobs WHERE url=?',(link,)).fetchone()[0])
        with job_hunter.connect() as db:
            db.execute('DELETE FROM outreach_daily_usage')
        person=outreach.create_contact({'email':unique+'@example.com','name':'Recruiter','source_url':'https://linkedin.com/in/fixture'})
        outreach.verify_contact(person['id'],{'email_confirmed':True,'employer_confirmed':True,'role_confirmed':True,'note':'Fixture verification for concurrent-claim regression.','timezone':'Asia/Kolkata'})
        import outreach_engine
        from datetime import datetime,timezone
        monkeypatch.setattr(outreach_engine,'utcnow',lambda:datetime(2026,11,13,5,0,tzinfo=timezone.utc))
        outreach_engine.save_settings({'paused':False})
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


def test_postgres_campaign_schema_and_resume_survive_reinitialization(monkeypatch):
    import outreach,outreach_engine,resume_store
    from test_workflow_regressions import text_pdf
    url=os.getenv('TEST_POSTGRES_URL')
    if not url:pytest.skip('TEST_POSTGRES_URL not configured')
    assert __import__('urllib.parse',fromlist=['urlsplit']).urlsplit(url).path.endswith('_test')
    monkeypatch.setenv('DATABASE_URL',url)
    monkeypatch.setenv('ENABLE_SCHEDULER','false')
    with TestClient(main.app):
        content=text_pdf('Persistent PostgreSQL fixture PDF')
        application_workflow.store_resume('pg-fixture.pdf',content)
        c=outreach_engine.create_campaign({'name':'PG '+uuid.uuid4().hex,'kind':'introduction'})
        outreach.init_outreach_db()
        assert resume_store.get(include_bytes=True)['content']==content
        with outreach.connect() as db:
            assert db.execute('SELECT kind FROM outreach_campaigns WHERE id=?',(c['id'],)).fetchone()[0]=='introduction'


def test_postgres_warm_reads_do_not_repeat_schema_or_default_writes(monkeypatch):
    import database, gmail_alerts, automation_settings, resume_store
    url = os.getenv('TEST_POSTGRES_URL')
    if not url:
        pytest.skip('TEST_POSTGRES_URL not configured')
    monkeypatch.setenv('DATABASE_URL', url)
    monkeypatch.setenv('ENABLE_SCHEDULER', 'false')
    with TestClient(main.app):
        statements = []
        execute = database.PgConnection.execute

        def traced(connection, sql, params=()):
            statements.append(sql.strip().upper())
            return execute(connection, sql, params)

        monkeypatch.setattr(database.PgConnection, 'execute', traced)
        for _ in range(2):
            job_hunter.get_settings()
            application_workflow.get_profile()
            gmail_alerts.get_gmail_status()
            automation_settings.get_automation_settings()
            job_sources.ensure_job_columns()
            resume_store.get()
        assert statements
        assert all(sql.startswith('SELECT') for sql in statements), statements
        assert not any('CONTENT_B64' in sql for sql in statements)
        # Only initialization is cached: a settings edit remains immediately visible.
        job_hunter.save_settings({'min_match_score': 57})
        assert job_hunter.get_settings()['min_match_score'] == 57


def test_postgres_pool_reuses_connections_commits_and_rolls_back(monkeypatch):
    import database
    url = os.getenv('TEST_POSTGRES_URL')
    if not url:
        pytest.skip('TEST_POSTGRES_URL not configured')
    monkeypatch.setenv('DATABASE_URL', url)
    monkeypatch.setenv('ENABLE_SCHEDULER', 'false')
    unique = uuid.uuid4().hex
    with TestClient(main.app):
        database.close_postgres_pools()
        pool = database._postgres_pool()
        pool.wait(timeout=15)
        pids = set()
        for _ in range(20):
            with database.connect() as db:
                assert db._raw.prepare_threshold is None
                pids.add(db.execute('SELECT pg_backend_pid()').fetchone()[0])
        assert len(pids) == 1
        assert pool.get_stats()['connections_num'] == 1
        with database.connect() as db:
            db.execute('INSERT INTO settings(key,value) VALUES (?,?)',
                       (unique, '"committed"'))
        with pytest.raises(ValueError):
            with database.connect() as db:
                db.execute('UPDATE settings SET value=? WHERE key=?', ('"rolled back"', unique))
                raise ValueError('fixture rollback')
        abandoned = database.connect()
        abandoned.execute('UPDATE settings SET value=? WHERE key=?', ('"abandoned"', unique))
        abandoned.close()
        with database.connect() as db:
            assert db.execute('SELECT value FROM settings WHERE key=?', (unique,)).fetchone()[0] == '"committed"'
        database.close_postgres_pools()
        with database.connect() as db:
            assert db.execute('SELECT 1').fetchone()[0] == 1
