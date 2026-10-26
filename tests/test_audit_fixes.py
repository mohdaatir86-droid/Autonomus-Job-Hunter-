import asyncio
import os
from urllib.parse import parse_qs, urlparse

import job_hunter
import job_sources
import gmail_alerts


def test_default_markets_and_program_manager():
    assert job_hunter.DEFAULT_COUNTRIES == [
        "India", "United Arab Emirates", "Saudi Arabia", "Qatar", "Kuwait", "Oman", "Canada",
    ]
    assert "Bahrain" not in job_hunter.DEFAULT_COUNTRIES
    assert "Program Manager" in job_hunter.DEFAULT_TITLES


def test_lucknow_and_gulf_are_target_markets():
    assert job_sources.is_target_market_job({"location": "Lucknow, India"}, "India")
    assert job_sources.is_target_market_job({"location": "Doha, Qatar"}, "Qatar")
    assert "Bahrain" not in job_sources.LISTABLE_COUNTRIES


def test_operations_program_manager_scores_above_generic_cap():
    settings = {"target_countries": ["India", "Canada"], "target_titles": ["Program Manager"]}
    job = {
        "title": "Program Manager - Supply Chain",
        "location": "Lucknow, India",
        "description": "Warehouse, inventory and multi-site logistics program.",
        "url": "https://example.com/jobs/pm-supply",
    }
    score, reasons, country = job_hunter.score_job(job, settings)
    assert country == "India"
    assert score >= 45
    assert any("Program management" in reason for reason in reasons)


def test_missing_salary_does_not_zero_a_strong_role():
    settings = {"target_countries": ["United Arab Emirates"]}
    job = {"title": "Cluster Manager", "location": "Dubai, UAE", "description": "Dark store cluster operations", "salary": ""}
    score, reasons, _ = job_hunter.score_job(job, settings)
    assert score >= 65
    assert any("not used to reject" in reason for reason in reasons)


def test_url_normalization_strips_tracking():
    left = job_sources.normalize_job_url("https://www.Example.com/jobs/1/?utm_source=google&utm_medium=organic")
    right = job_sources.normalize_job_url("https://example.com/jobs/1")
    assert left == right


def test_duplicate_lookup_uses_normalized_url(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(job_hunter, "DB_PATH", str(tmp_path / "jobs.sqlite"))
    job_sources.ensure_job_columns()
    with job_sources.connect() as conn:
        conn.execute(
            """INSERT INTO jobs(source,source_id,title,company,location,country,url,description,
            employment_type,salary,posted_at,match_score,match_reasons,status,first_seen_at,last_seen_at,raw_json,normalized_url)
            VALUES ('test','1','Cluster Manager','Acme','Dubai','United Arab Emirates',?, '', '', '', NULL, 70, '[]', 'new', 't', 't', '{}', ?)""",
            (
                "https://example.com/jobs/1?utm_source=google",
                job_sources.normalize_job_url("https://example.com/jobs/1?utm_source=google"),
            ),
        )
        found = job_sources._find_existing_job(
            conn,
            {"title": "Cluster Manager", "company": "Acme", "location": "Dubai", "url": "https://www.example.com/jobs/1"},
            job_sources.normalize_job_url("https://www.example.com/jobs/1"),
        )
    assert found is not None


def test_source_status_classification():
    assert job_sources.status_from_error_text(None) == "working"
    assert job_sources.status_from_error_text("SERPAPI_API_KEY not set") == "missing_configuration"
    assert job_sources.status_from_error_text("HTTP 401") == "unauthorized"
    assert job_sources.status_from_error_text("HTTP 429") == "rate_limited"
    assert job_sources.status_from_error_text("request timed out") == "unavailable"


def test_oauth_state_and_exchange_error(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("OAUTH_STATE_SECRET", "state-secret")
    url = gmail_alerts.build_auth_url()
    state = parse_qs(urlparse(url).query)["state"][0]
    assert gmail_alerts.verify_oauth_state(state)
    assert not gmail_alerts.verify_oauth_state("bad-state")
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    result = asyncio.run(gmail_alerts.exchange_code(""))
    assert result["ok"] is False


def test_gmail_alert_parse_and_seen_rows(tmp_path, monkeypatch):
    jobs = gmail_alerts._extract_jobs_from_email(
        "Job alert: Cluster Manager in Dubai",
        "alerts@naukri.com",
        "Role https://www.naukri.com/job-listings-cluster-manager-dubai-123 unsubscribe https://www.naukri.com/unsubscribe",
        "msg-1",
    )
    assert len(jobs) == 1
    assert jobs[0]["source"].startswith("Naukri")
    assert "unsubscribe" not in jobs[0]["url"]
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(job_hunter, "DB_PATH", str(tmp_path / "gmail.sqlite"))
    monkeypatch.setattr(gmail_alerts, "DB_PATH", str(tmp_path / "gmail.sqlite"))
    gmail_alerts.init_gmail_db()
    with gmail_alerts._connect() as conn:
        conn.execute("INSERT OR IGNORE INTO gmail_seen_messages(message_id, seen_at) VALUES (?,?)", ("msg-1", "t"))
        conn.execute("INSERT OR IGNORE INTO gmail_seen_messages(message_id, seen_at) VALUES (?,?)", ("msg-1", "t2"))
        count = conn.execute("SELECT COUNT(*) FROM gmail_seen_messages").fetchone()[0]
    assert count == 1


def test_submit_stays_disabled_by_default(monkeypatch):
    monkeypatch.delenv("BROWSER_ALLOW_SUBMIT", raising=False)
    assert os.getenv("BROWSER_ALLOW_SUBMIT", "false").lower() == "false"


def test_health_and_db(monkeypatch, tmp_path):
    db = str(tmp_path / "health.sqlite")
    monkeypatch.setenv("ENABLE_SCHEDULER", "false")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_PATH", db)
    monkeypatch.delenv("BROWSER_ALLOW_SUBMIT", raising=False)
    monkeypatch.setattr(job_hunter, "DB_PATH", db)
    monkeypatch.setattr(gmail_alerts, "DB_PATH", db)
    import application_workflow
    import application_state
    monkeypatch.setattr(application_workflow, "DB_PATH", db)
    monkeypatch.setattr(application_state, "DB_PATH", db)
    from fastapi.testclient import TestClient
    import main

    with TestClient(main.app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        body = health.json()
        assert body["status"] == "ok"
        assert body["version"] == "0.6.0"
        assert body["live_applications_enabled"] is False
        assert body["browser_submit_enabled"] is False
        db_health = client.get("/health/db")
        assert db_health.status_code == 200
        assert db_health.json()["connected"] is True
        assert db_health.json()["missing_tables"] == []
        settings = client.get("/api/settings")
        assert settings.status_code == 200
        assert settings.json()["target_countries"] == job_hunter.DEFAULT_COUNTRIES
        assert "Program Manager" in settings.json()["target_titles"]
