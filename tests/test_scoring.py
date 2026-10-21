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
