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
    job_hunter.init_db()
    assert settings["target_countries"]
    assert "Canada" not in settings["target_countries"]
    assert job_hunter.get_stats()["total_jobs"] == 0


def test_country_is_not_inferred_from_job_description():
    assert job_hunter.detect_country("Remote", description="Manage teams in India and Canada") == "Remote / Unspecified"
    assert job_hunter.detect_country("Toronto, Canada", description="Global role") == "Canada"


def test_search_terms_are_supported_by_job_query(tmp_path, monkeypatch):
    monkeypatch.setattr(job_hunter, "DB_PATH", str(tmp_path / "search.sqlite"))
    job_hunter.init_db()
    with job_hunter.connect() as conn:
        conn.execute(
            """INSERT INTO jobs(source,title,company,location,country,url,match_score,status,first_seen_at,last_seen_at)
               VALUES ('test','Senior Cluster Manager','Example Co','Noida, India','India','https://example.com/job',80,'new','2026-01-01','2026-01-01')"""
        )
    found = job_hunter.list_jobs(q="cluster Noida")
    assert len(found) == 1
    assert found[0]["title"] == "Senior Cluster Manager"
    assert job_hunter.list_jobs(q="accountant") == []


def test_country_aliases_are_location_only():
    cases = {
        "Dubai, UAE": "United Arab Emirates",
        "Riyadh, Saudi Arabia": "Saudi Arabia",
        "Doha, Qatar": "Qatar",
        "Muscat, Oman": "Oman",
        "Toronto, Canada": "Canada",
        "Noida, India": "India",
        "Remote - Worldwide": "Remote / Unspecified",
        "": "Unspecified",
    }
    for location, expected in cases.items():
        assert job_hunter.detect_country(location) == expected


def test_keyword_search_is_case_insensitive_and_all_terms_must_match(tmp_path, monkeypatch):
    monkeypatch.setattr(job_hunter, "DB_PATH", str(tmp_path / "search_terms.sqlite"))
    job_hunter.init_db()
    with job_hunter.connect() as conn:
        conn.execute(
            """INSERT INTO jobs(source,title,company,location,country,url,match_score,status,first_seen_at,last_seen_at)
               VALUES ('test','Senior Cluster Manager','Example Co','Noida, India','India','https://example.com/cluster',80,'new','2026-01-01','2026-01-01')"""
        )
    assert len(job_hunter.list_jobs(q="CLUSTER noida")) == 1
    assert job_hunter.list_jobs(q="cluster Toronto") == []


def test_technical_program_manager_is_not_scored_as_operations_leadership():
    settings = {"target_countries": ["India", "Canada"]}
    job = {
        "title": "Senior Technical Program Manager - Security",
        "location": "USA",
        "description": "Lead security engineering programs and technical teams.",
    }
    score, reasons, country = job_hunter.score_job(job, settings)
    assert score < 35
    assert country == "Unspecified"
    assert any("outside the target operations domain" in reason.lower() for reason in reasons)


def test_operations_cluster_role_remains_high_match():
    settings = {"target_countries": ["India"]}
    job = {
        "title": "Senior Cluster Manager",
        "location": "Noida, India",
        "description": "Dark store operations, last mile, inventory, SLA, team leadership.",
    }
    score, reasons, country = job_hunter.score_job(job, settings)
    assert score >= 65
    assert country == "India"


def test_unrelated_technical_role_cannot_gain_score_from_generic_bonuses():
    settings = {"target_countries": ["India", "Canada"]}
    job = {
        "title": "Senior Software Engineer, Edge Security",
        "location": "Toronto, Canada",
        "description": "Leadership, KPI ownership, SLA improvement, staff management, inventory dashboards.",
    }
    score, reasons, country = job_hunter.score_job(job, settings)
    assert score <= 12
    assert country == "Canada"


def test_generic_program_manager_has_a_hard_score_cap():
    settings = {"target_countries": ["Canada"]}
    job = {
        "title": "Senior Program Manager",
        "location": "Toronto, Canada",
        "description": "Leadership, KPI, SLA, team management, process improvement.",
    }
    score, reasons, country = job_hunter.score_job(job, settings)
    assert score <= 24
    assert country == "Canada"


def test_qsr_multi_unit_role_scores_high():
    settings = {"target_countries": ["India"]}
    job = {
        "title": "QSR Operations Manager - Multi Unit",
        "location": "Gurugram, India",
        "description": "Lead multi-unit QSR operations, store launches, SOPs, team leadership.",
    }
    score, reasons, country = job_hunter.score_job(job, settings)
    assert score >= 60
    assert country == "India"
