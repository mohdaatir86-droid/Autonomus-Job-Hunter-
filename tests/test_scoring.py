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
    assert "India" in settings["target_countries"]
    assert "Canada" in settings["target_countries"]
    assert "Bahrain" not in settings["target_countries"]
    assert "Program Manager" in settings["target_titles"]
    assert len(settings["target_countries"]) == 7


def test_country_is_not_inferred_from_job_description():
    assert job_hunter.detect_country("Remote", description="Manage teams in India and Canada") == "Remote / Unspecified"
    assert job_hunter.detect_country("Toronto, Canada", description="Global role") == "Canada"


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
        assert job_hunter.detect_country(location) == expected, (location, job_hunter.detect_country(location))


def test_technical_program_manager_is_not_scored_as_operations_leadership():
    settings = {"target_countries": ["India", "Canada"]}
    job = {
        "title": "Senior Technical Program Manager - Security",
        "location": "USA",
        "description": "Lead security engineering programs and technical teams.",
    }
    score, reasons, country = job_hunter.score_job(job, settings)
    assert score < 35
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


def test_list_jobs_and_hunt_helpers_import():
    from job_sources import list_jobs, get_stats, set_job_status, hunt_jobs
    assert callable(list_jobs)
    assert callable(get_stats)
    assert callable(set_job_status)
    assert callable(hunt_jobs)
