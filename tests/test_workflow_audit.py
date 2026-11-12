import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import application_state
import application_workflow
import browser_worker
import database
import gmail_alerts
import job_hunter
import main
import outreach


@pytest.fixture
def private_db(tmp_path, monkeypatch):
    path = str(tmp_path / 'workflow-audit.sqlite')
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('DATABASE_PATH', path)
    monkeypatch.setenv('ENABLE_SCHEDULER', 'false')
    monkeypatch.delenv('AI_API_KEY', raising=False)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    for module in (application_state, application_workflow, browser_worker, gmail_alerts, job_hunter):
        monkeypatch.setattr(module, 'DB_PATH', path)
    with TestClient(main.app):
        application_workflow.save_profile({'full_name': 'Test Candidate', 'email': 'old@example.com'})
        with database.connect(path) as db:
            db.execute("UPDATE candidate_profile SET resume_text='Verified fixture resume' WHERE id=1")
            db.execute("INSERT INTO jobs(source,title,company,country,url,description,first_seen_at,last_seen_at) VALUES ('fixture','Operations Manager','Example','India','https://jobs.lever.co/example/123','Operations',?,?)", (job_hunter.now_iso(), job_hunter.now_iso()))
        yield path


class ConnectionHook:
    def __init__(self, connection, hook):
        self.connection, self.hook = connection, hook

    def __enter__(self):
        self.connection.__enter__()
        return self

    def __exit__(self, *args):
        return self.connection.__exit__(*args)

    def execute(self, sql, params=()):
        self.hook(sql, params)
        return self.connection.execute(sql, params)


def test_concurrent_attempt_creation_returns_one_record_and_event(private_db, monkeypatch):
    barrier = threading.Barrier(2)

    def hook(sql, params):
        if 'INSERT INTO application_attempts' in sql:
            barrier.wait(timeout=5)

    monkeypatch.setattr(application_state, 'db_connect', lambda path: ConnectionHook(database.connect(path), hook))
    def create(_):
        return application_state.get_or_create_attempt(portal='lever', source_id='123', url='https://jobs.lever.co/example/123')['id']
    with ThreadPoolExecutor(2) as executor:
        ids = list(executor.map(create, range(2)))
    assert ids[0] == ids[1]
    assert len(application_state.attempt_events(ids[0])) == 1


def make_outreach_draft():
    contact = outreach.create_contact({'email': 'recruiter@example.com', 'source_url': 'https://example.com/team', 'name': 'Recruiter'})
    outreach.verify_contact(contact['id'], {'email_confirmed': True, 'employer_confirmed': True, 'role_confirmed': True, 'note': 'Verified against the official company team page.'})
    draft = outreach.create_draft(contact['id'], 1)
    outreach.approve_draft(draft['id'], {'confirmed': True, 'recipient_relevance_confirmed': True})
    return contact, draft


@pytest.mark.parametrize('action', ['edit', 'approve'])
def test_outreach_cannot_overwrite_concurrently_claimed_send(private_db, monkeypatch, action):
    _, draft = make_outreach_draft()
    fired = False
    prefix = "UPDATE outreach_drafts SET subject=" if action == 'edit' else "UPDATE outreach_drafts SET state='approved'"

    def hook(sql, params):
        nonlocal fired
        if sql.startswith(prefix) and not fired:
            fired = True
            with database.connect(private_db) as other:
                other.execute("UPDATE outreach_drafts SET state='sending',updated_at='concurrent-claim' WHERE id=?", (draft['id'],))

    monkeypatch.setattr(outreach, 'connect', lambda: ConnectionHook(database.connect(private_db), hook))
    with pytest.raises(HTTPException) as error:
        if action == 'edit':
            outreach.edit_draft(draft['id'], {'subject': 'Changed subject', 'body': 'Changed body'})
        else:
            outreach.approve_draft(draft['id'], {'confirmed': True, 'recipient_relevance_confirmed': True})
    assert error.value.status_code == 409
    with database.connect(private_db) as db:
        row = db.execute('SELECT * FROM outreach_drafts WHERE id=?', (draft['id'],)).fetchone()
    assert row['state'] == 'sending'
    assert row['body'] == draft['body']


def test_changed_sender_can_prepare_existing_unsent_draft_for_fresh_review(private_db):
    contact, draft = make_outreach_draft()
    application_workflow.save_profile({'email': 'new@example.com'})
    replacement = outreach.create_draft(contact['id'], 1)
    assert replacement['id'] == draft['id']
    assert replacement['sender_email'] == 'new@example.com'
    assert replacement['state'] == 'draft'
    assert replacement['approval_hash'] == ''
    assert 'old@example.com' not in replacement['body']
    assert '/outreach/unsubscribe?token=' in replacement['body']


def test_opening_job_has_a_single_worker_claim(private_db):
    attempt = application_state.get_or_create_attempt(portal='lever', source_id='123', url='https://jobs.lever.co/example/123')
    for state in ('scored', 'queued', 'opening_job'):
        application_state.transition(attempt['id'], state)
    with pytest.raises(ValueError, match='already claimed'):
        application_state.transition(attempt['id'], 'opening_job')


@pytest.mark.parametrize('field_type', ['checkbox', 'radio', 'password', 'select'])
def test_browser_does_not_report_readiness_for_unfilled_required_controls(private_db, monkeypatch, field_type):
    import playwright.async_api
    monkeypatch.setenv('BROWSER_WORKER_ENABLED', 'true')
    draft = asyncio.run(application_workflow.create_application_draft(1))

    class Element:
        async def is_visible(self): return True
        async def is_enabled(self): return True
        async def get_attribute(self, name):
            return {'type': field_type if field_type != 'select' else '', 'required': '', 'name': 'full_name' if field_type == 'select' else 'Required answer'}.get(name)
        async def fill(self, value, **kwargs): raise ValueError('Select cannot be filled with text')
        async def input_value(self): return ''

    class Locator:
        def __init__(self, kind): self.kind = kind
        async def count(self): return 1 if self.kind in {'form', 'inputs'} else 0
        async def inner_text(self, **kwargs): return 'Apply for the role'
        @property
        def first(self): return self
        def locator(self, selector): return Locator('inputs')
        def nth(self, index): return Element()

    class Page:
        url = 'https://jobs.lever.co/example/123'
        async def goto(self, *args, **kwargs): pass
        async def wait_for_timeout(self, value): pass
        def locator(self, selector): return Locator(selector)

    class Context:
        async def new_page(self): return Page()
        async def close(self): pass

    class Browser:
        async def new_context(self, **kwargs): return Context()
        async def close(self): pass

    class Chromium:
        async def launch(self, **kwargs): return Browser()

    class Playwright:
        chromium = Chromium()
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass

    monkeypatch.setattr(playwright.async_api, 'async_playwright', Playwright)
    result = asyncio.run(browser_worker.run_application_worker(draft['id']))
    assert result['status'] == 'blocked'
    assert result['reason'] == 'unknown_required_fields'
    repeated = asyncio.run(browser_worker.run_application_worker(draft['id']))
    assert repeated['reason'] == 'attempt_requires_manual_review'


@pytest.mark.parametrize('failure_point,expected_closed', [
    ('launch', []), ('context', ['browser']), ('page', ['context', 'browser']),
])
def test_browser_setup_failure_releases_claim_and_closes_partial_resources(private_db, monkeypatch, failure_point, expected_closed):
    import playwright.async_api
    monkeypatch.setenv('BROWSER_WORKER_ENABLED', 'true')
    monkeypatch.delenv('BROWSER_WS_ENDPOINT', raising=False)
    draft = asyncio.run(application_workflow.create_application_draft(1))
    closed = []

    class Context:
        async def new_page(self):
            raise RuntimeError('fixture page setup failure')
        async def close(self):
            closed.append('context')
            # Cleanup failure must not prevent the browser from being closed.
            raise RuntimeError('fixture context close failure')

    class Browser:
        async def new_context(self, **kwargs):
            if failure_point == 'context':
                raise RuntimeError('fixture context setup failure')
            return Context()
        async def close(self): closed.append('browser')

    class Chromium:
        async def launch(self, **kwargs):
            if failure_point == 'launch':
                raise RuntimeError('fixture launch failure')
            return Browser()

    class Playwright:
        chromium = Chromium()
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass

    monkeypatch.setattr(playwright.async_api, 'async_playwright', Playwright)
    for _ in range(2):
        closed.clear()
        result = asyncio.run(browser_worker.run_application_worker(draft['id']))
        assert result['status'] == 'error'
        assert result['reason'] == 'browser_error'
        assert closed == expected_closed
        attempts = application_state.list_attempts()
        assert len(attempts) == 1
        assert attempts[0]['state'] == 'failed'
