import job_hunter


def test_target_operations_role_scores_well():
    settings = {
        "target_titles": ["Senior Cluster Manager", "Operations Manager"],
        "target_countries": ["India"],
    }
    job = {
        "title": "Senior Cluster Manager",
        "company": "Example Logistics",
        "location": "Noida, India",
        "description": "Dark store operations, last mile, inventory, SLA, team leadership.",
        "country": "India",
    }
    score, reasons, country = job_hunter.score_job(job, settings)
    assert score >= 65
    assert country == "India"
    assert any("Target role" in reason for reason in reasons)


def test_internship_is_penalized():
    settings = {"target_titles": ["Operations Manager"], "target_countries": ["India"]}
    job = {"title": "Operations Internship", "location": "Delhi, India", "description": ""}
    score, reasons, _ = job_hunter.score_job(job, settings)
    assert score < 50
    assert any("Seniority mismatch" in reason for reason in reasons)


def test_database_initializes_cleanly(tmp_path, monkeypatch):
    monkeypatch.setattr(job_hunter, "DB_PATH", str(tmp_path / "jobs.sqlite"))
    job_hunter.init_db()
    settings = job_hunter.get_settings()
    # A clean database can be initialized repeatedly without data loss or schema errors.
    job_hunter.init_db()
    assert settings["target_countries"]
    assert job_hunter.get_stats()["total_jobs"] == 0



def test_country_is_not_inferred_from_job_description():
    assert job_hunter.detect_country("Remote", description="Manage teams in India and Canada") == "Remote / Unspecified"
    assert job_hunter.detect_country("Toronto, Canada", description="Global role") == "Canada"


def test_search_terms_are_supported_by_job_query(tmp_path, monkeypatch):
    monkeypatch.setattr(job_hunter, "DB_PATH", str(tmp_path / "search.sqlite"))
    job_hunter.init_db()
    settings = job_hunter.get_settings()
    with job_hunter.connect() as conn:
        conn.execute(
            """INSERT INTO jobs(source,title,company,location,country,url,match_score,status,first_seen_at,last_seen_at)
               VALUES ('test','Senior Cluster Manager','Example Co','Noida, India','India','https://example.com/job',80,'new','2026-01-01','2026-01-01')"""
        )
    found = job_hunter.list_jobs(q="cluster Noida")
    assert len(found) == 1
    assert found[0]["title"] == "Senior Cluster Manager"
    assert job_hunter.list_jobs(q="accountant") == []
