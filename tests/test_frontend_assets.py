from fastapi.testclient import TestClient
import main


def test_frontend_runtime_assets_are_available():
    client=TestClient(main.app)
    try:
        root=client.get('/')
        assert root.status_code==200
        assert '/static/app.js' in root.text and '/static/app.css' in root.text
        script=client.get('/static/app.js')
        styles=client.get('/static/app.css')
        assert script.status_code==200 and 'javascript' in script.headers['content-type']
        assert styles.status_code==200 and 'text/css' in styles.headers['content-type']
    finally:client.close()


def test_operational_readiness_does_not_claim_auto_submission():
    from unittest.mock import patch
    from main import operational_readiness

    with patch("job_sources.get_source_report", return_value=None), \
         patch("main.get_stats", return_value={"total_jobs": 0, "last_run": None}), \
         patch("main.get_profile", return_value={"has_resume": False}), \
         patch("automation_settings.get_automation_settings", return_value={"enabled": False}), \
         patch("main.get_gmail_status", return_value={"connected": False}):
        result = operational_readiness()
    assert result["automatic_submission_enabled"] is False
    assert result["jobs_in_current_window"] == 0
    assert result["resume_uploaded"] is False
    assert result["blockers"]
    assert "email" not in result
