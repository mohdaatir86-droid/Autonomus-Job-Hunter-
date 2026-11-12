import asyncio
import base64
from datetime import datetime, timedelta, timezone

import httpx
import pytest

import gmail_alerts
import job_hunter
import job_sources


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = str(tmp_path / "sources.sqlite")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(job_hunter, "DB_PATH", path)
    monkeypatch.setattr(gmail_alerts, "DB_PATH", path)
    job_sources.ensure_job_columns()
    gmail_alerts.init_gmail_db()
    return path


def message(body, timestamp):
    return {"internalDate": str(int(timestamp.timestamp() * 1000)), "payload": {
        "mimeType": "text/plain", "headers": [
            {"name": "Subject", "value": "Cluster Manager in Noida"},
            {"name": "From", "value": "alerts@naukri.com"},
        ], "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()},
    }}


def configure_gmail(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "fixture-client")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "fixture-secret")

    async def access():
        return "fixture-access"

    monkeypatch.setattr(gmail_alerts, "_access_token", access)


def test_email_relative_age_uses_message_time_and_listing_context():
    received = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    body = ("Dubai: Cluster Manager posted 1 hour ago https://naukri.com/job-listings-1 "
            "Noida: Warehouse Manager posted 2 days ago https://naukri.com/job-listings-2")
    jobs = gmail_alerts._extract_jobs_from_email("Job alerts", "alerts@naukri.com", body, "fixture", received_at=received)
    assert [(j["location"], j["country"]) for j in jobs] == [("Dubai", "United Arab Emirates"), ("Noida", "India")]
    assert datetime.fromisoformat(jobs[0]["posted_at"]) == received - timedelta(hours=1)
    assert datetime.fromisoformat(jobs[1]["posted_at"]) == received - timedelta(days=2)


def test_gmail_reads_next_page_after_seen_first_page(database, monkeypatch):
    configure_gmail(monkeypatch)
    with gmail_alerts._connect() as conn:
        conn.execute("INSERT INTO gmail_seen_messages VALUES (?,?)", ("seen", "t"))
    pages = []
    received = datetime.now(timezone.utc) - timedelta(days=4)

    async def get(self, url, **kwargs):
        if url.endswith("/messages"):
            token = kwargs["params"].get("pageToken")
            pages.append(token)
            return httpx.Response(200, json={"messages": [{"id": "seen"}], "nextPageToken": "next"} if token is None else {"messages": [{"id": "new"}]})
        return httpx.Response(200, json=message("Noida posted 1 hour ago https://naukri.com/job-listings-1", received))

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    jobs, error = asyncio.run(gmail_alerts.fetch_jobs_from_gmail())
    assert pages == [None, "next"]
    assert error is None and len(jobs) == 1
    assert datetime.fromisoformat(jobs[0]["posted_at"]) < datetime.now(timezone.utc) - timedelta(days=3)
    with gmail_alerts._connect() as conn:
        assert conn.execute("SELECT 1 FROM gmail_seen_messages WHERE message_id='new'").fetchone() is None


def test_failed_message_parse_remains_retryable(database, monkeypatch):
    configure_gmail(monkeypatch)

    async def get(self, url, **kwargs):
        return httpx.Response(200, json={"messages": [{"id": "broken"}]}) if url.endswith("/messages") else httpx.Response(200, content=b"invalid JSON")

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    jobs, error = asyncio.run(gmail_alerts.fetch_jobs_from_gmail())
    assert jobs == [] and "retried" in error
    with gmail_alerts._connect() as conn:
        assert conn.execute("SELECT 1 FROM gmail_seen_messages WHERE message_id='broken'").fetchone() is None


def test_gmail_scan_is_bounded_and_reports_remaining_pages(database, monkeypatch):
    configure_gmail(monkeypatch)
    calls = []

    async def get(self, url, **kwargs):
        calls.append(kwargs["params"])
        return httpx.Response(200, json={"messages": [], "nextPageToken": str(len(calls))})

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    jobs, error = asyncio.run(gmail_alerts.fetch_jobs_from_gmail())
    assert jobs == [] and len(calls) == 5
    assert "limited" in error


def test_greenhouse_ranks_relevant_listings_before_cap(database, monkeypatch):
    monkeypatch.setenv("GREENHOUSE_BOARDS", "fixture")
    rows = [{"title": "Software Engineer", "id": i, "location": {"name": "New York"}, "absolute_url": f"https://boards.greenhouse.io/fixture/jobs/{i}", "updated_at": datetime.now(timezone.utc).isoformat()} for i in range(40)]
    rows.append({"title": "Warehouse Manager", "id": 41, "location": {"name": "Noida, India"}, "absolute_url": "https://boards.greenhouse.io/fixture/jobs/41", "created_at": "2020-01-01T00:00:00Z", "updated_at": datetime.now(timezone.utc).isoformat()})

    async def data(*args, **kwargs):
        return {"jobs": rows}

    monkeypatch.setattr(job_sources, "_get_json", data)
    jobs, error = asyncio.run(job_sources.fetch_greenhouse(None, 10))
    assert error is None and len(jobs) == 10
    assert jobs[0]["title"] == "Warehouse Manager"
    assert jobs[0]["posted_at"].startswith("2020-01-01")
    assert not jobs[0]["_board_complete"]
    assert jobs[1]["posted_at"] is None  # Edit timestamps are not posting dates.


@pytest.mark.parametrize("fail_write", [False, True])
def test_gmail_acknowledgement_commits_with_job(database, monkeypatch, fail_write):
    job = job_sources._clean_job({"title": "Cluster Manager", "location": "Noida, India", "url": "https://naukri.com/job-listings-1"}, "Naukri (email alert)")
    job["_gmail_message_id"] = "commit-fixture"
    if fail_write:
        job["raw_json"] = {"invalid_sql_binding": True}

    async def sources(settings):
        return [job], [], [], {}

    monkeypatch.setattr(job_sources, "fetch_public_sources", sources)
    if fail_write:
        with pytest.raises(Exception):
            asyncio.run(job_sources._hunt_jobs_locked())
    else:
        assert asyncio.run(job_sources._hunt_jobs_locked())["new_jobs"] == 1
    with gmail_alerts._connect() as conn:
        seen = conn.execute("SELECT 1 FROM gmail_seen_messages WHERE message_id='commit-fixture'").fetchone()
        assert bool(seen) is not fail_write


def test_distinct_same_title_requisitions_survive_deduplication(database, monkeypatch):
    jobs = [job_sources._clean_job({
        "id": str(number), "title": "Cluster Manager", "company": "Acme",
        "location": "Noida, India", "url": f"https://careers.example.com/jobs/{number}",
    }, "Acme careers") for number in (1, 2)]

    async def sources(settings):
        return jobs, [], [], {}

    monkeypatch.setattr(job_sources, "fetch_public_sources", sources)
    result = asyncio.run(job_sources._hunt_jobs_locked())
    assert result["new_jobs"] == 2
    with job_hunter.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2


@pytest.mark.parametrize("same_provider_identity", [False, True])
def test_canonical_url_or_stable_provider_identity_still_deduplicates(database, monkeypatch, same_provider_identity):
    original = job_sources._clean_job({"id": "stable", "title": "Cluster Manager", "company": "Acme", "location": "Noida, India", "url": "https://careers.example.com/jobs/1"}, "Acme careers")
    update = dict(original)
    if same_provider_identity:
        update.update(url="https://careers.example.com/new-jobs/1", title="Senior Cluster Manager")
    else:
        update.update(source="Google Jobs", source_id="aggregator-id", url="https://www.careers.example.com/jobs/1/?utm_source=google")

    async def sources(settings):
        return [original, update], [], [], {}

    monkeypatch.setattr(job_sources, "fetch_public_sources", sources)
    result = asyncio.run(job_sources._hunt_jobs_locked())
    assert result["new_jobs"] == 1
    assert result["updated_jobs"] == 1
    with job_hunter.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
