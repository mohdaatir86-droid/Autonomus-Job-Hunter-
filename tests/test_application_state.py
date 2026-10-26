import application_state as sm


def test_stable_job_key_prefers_source_id():
    a = sm.stable_job_key("greenhouse", "12345", "https://boards.greenhouse.io/x/jobs/1")
    b = sm.stable_job_key("greenhouse", "12345", "https://other.example/ignore")
    c = sm.stable_job_key("greenhouse", "99999", "https://boards.greenhouse.io/x/jobs/1")
    assert a == b
    assert a != c


def test_stable_job_key_falls_back_to_url():
    a = sm.stable_job_key("lever", None, "https://jobs.lever.co/acme/abc")
    b = sm.stable_job_key("lever", "", "https://jobs.lever.co/acme/abc")
    c = sm.stable_job_key("lever", None, "https://jobs.lever.co/acme/xyz")
    assert a == b
    assert a != c


def test_illegal_transition_from_submitted():
    assert "submitted" in sm.TERMINAL_SUCCESS
    assert sm.ALLOWED_TRANSITIONS["submitted"] == set()


def test_no_auto_retry_includes_unverified():
    assert "submitted_unverified" in sm.NO_AUTO_RETRY
    assert "waiting_for_user" in sm.NO_AUTO_RETRY


def test_valid_states_cover_prompt_list():
    required = {
        "discovered", "scored", "skipped", "queued", "opening_job",
        "form_detected", "filling_form", "validation_failed",
        "waiting_for_user", "ready_to_submit", "submitting",
        "submitted_unverified", "submitted", "failed", "expired_session",
    }
    assert required <= sm.VALID_STATES
