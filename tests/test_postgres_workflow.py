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
