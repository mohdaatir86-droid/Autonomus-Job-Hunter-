"""Read performance and physical-operations relevance; no external providers."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

import pytest

import database
import job_hunter
import job_sources


def test_postgres_bootstrap_is_coordinated_and_database_specific(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql://fixture/first_test')
    calls = []
    entered, release = Event(), Event()

    @database.postgres_initialization_once
    def initialize():
        calls.append(True)
        entered.set()
        assert release.wait(timeout=5)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(initialize) for _ in range(8)]
        assert entered.wait(timeout=5)
        release.set()
        for future in futures:
            future.result(timeout=5)
    assert len(calls) == 1
    monkeypatch.setenv('DATABASE_URL', 'postgresql://fixture/second_test')
    initialize()
    assert len(calls) == 2
    monkeypatch.delenv('DATABASE_URL')
    initialize()
    initialize()
    assert len(calls) == 4  # SQLite still bootstraps each selected fixture file.


def test_failed_bootstrap_is_not_cached(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql://fixture/retry_test')
    calls = []

    @database.postgres_initialization_once
    def initialize():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError('Fixture database unavailable')

    with pytest.raises(RuntimeError):
        initialize()
    initialize()
    initialize()
    assert len(calls) == 2


def test_resume_metadata_does_not_fetch_pdf_bytes():
    import resume_store
    queries = []

    class MetadataDatabase:
        def execute(self, sql):
            queries.append(sql)
            assert 'content_b64' not in sql
            return self

        def fetchone(self):
            return dict(id=1, filename='fixture.pdf', sha256='fixture-digest',
                        version=2, uploaded_at='fixture-date', size_bytes=8_000_000)

    result = resume_store.get(MetadataDatabase())
    assert result['size_bytes'] == 8_000_000
    assert 'content' not in result
    assert len(queries) == 1


@pytest.mark.parametrize('title,description', [
    ('Program Manager', 'Lead warehouse operations, last-mile logistics and inventory.'),
    ('Programme Manager', 'Own QSR operations, multi-unit restaurant launches and team leadership.'),
    ('Supply Chain Program Manager', 'Own distribution planning, inventory and service KPIs.'),
])
def test_operations_programmes_are_not_rejected_by_generic_title(title, description):
    score, _, _ = job_hunter.score_job(dict(title=title, description=description,
        location='Dubai, UAE', url='https://example.com/jobs/fixture'),
        {'target_countries': ['United Arab Emirates']})
    assert score >= 65


@pytest.mark.parametrize('title', [
    'Senior Software Engineer, Warehouse', 'Technical Program Manager, Supply Chain',
    'Product Manager, Fulfillment', 'IT Operations Manager',
    'Sales Manager, Logistics', 'Talent Acquisition Manager, Dark Stores',
])
def test_employer_or_title_domain_does_not_make_an_unrelated_function_relevant(title):
    job = dict(title=title, description='Warehouse operations, logistics, inventory, leadership and KPI ownership.',
               location='Dubai, UAE', url='https://example.com/fixture')
    assert not job_hunter.is_relevant_role(job)
    assert job_hunter.score_job(job, {'target_countries': ['United Arab Emirates']})[0] <= 12


def test_generic_business_operations_and_unknown_titles_do_not_gain_high_match_badge():
    for title, ceiling in [('Business Operations Manager', 64), ('Senior Customer Specialist', 24)]:
        job = dict(title=title, description='Leadership, KPI, SLA, staff management and cost control.',
                   location='Toronto, Canada', url='https://example.com/fixture')
        assert job_hunter.score_job(job, {'target_countries': ['Canada']})[0] <= ceiling


def test_saved_roles_rotate_without_more_search_queries():
    roles = ['Cluster Manager', 'City Operations Manager', 'Operations Manager',
             'Warehouse Manager', 'Supply Chain Manager', 'Fulfillment Manager', 'Head of Operations']
    settings = dict(target_titles=roles, target_countries=['India', 'United Arab Emirates'],
                    preferred_locations=['Delhi NCR'])
    first = datetime(2026, 11, 14, tzinfo=timezone.utc)
    covered = set()
    for tick in range(3):
        queries = job_sources.build_search_queries(settings, first + timedelta(hours=6*tick))
        assert len(queries) == 6
        covered.update(query.split(' (')[0] for query in queries)
        assert queries == job_sources.build_search_queries(settings, first + timedelta(hours=6*tick, minutes=1))
    assert covered == set(roles)


def test_irrelevant_legacy_jobs_are_hidden_without_losing_tracked_history(tmp_path, monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setattr(job_hunter, 'DB_PATH', str(tmp_path / 'relevance.sqlite'))
    job_hunter.init_db()
    job_hunter.save_settings({'target_countries': ['India'], 'preferred_locations': [],
                              'min_experience_years': 0, 'min_india_salary_lpa': 0})
    with job_hunter.connect() as db:
        for index, (title, status) in enumerate([
            ('Warehouse Manager', 'new'), ('Software Engineer, Warehouse', 'new'),
            ('Software Engineer, Warehouse', 'saved')]):
            db.execute('''INSERT INTO jobs(source,title,company,location,country,url,description,
                posted_at,match_score,status,first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                ('fixture', title, 'Fixture', 'Noida, India', 'India', f'https://example.com/{index}',
                 'Warehouse operations and inventory', job_hunter.now_iso(), 95, status,
                 job_hunter.now_iso(), job_hunter.now_iso()))
    assert [job['title'] for job in job_sources.list_jobs(min_score=0)] == ['Warehouse Manager']
    assert job_sources.get_stats()['total_jobs'] == 1
    assert job_sources.get_stats()['high_match_jobs'] == 1
    assert len(job_sources.list_jobs(status='saved', min_score=0)) == 1
