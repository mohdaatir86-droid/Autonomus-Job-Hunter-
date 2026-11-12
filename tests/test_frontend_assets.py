from fastapi.testclient import TestClient
import main
import pytest


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


@pytest.mark.parametrize('ready', [False, True])
def test_readiness_http_contract_for_mission_control(ready):
    from unittest.mock import patch

    last = {'finished_at': '2026-11-10T12:00:00+00:00', 'fetched': 7, 'errors': '[]'} if ready else None
    with patch('job_sources.get_source_report', return_value={} if ready else None), \
         patch('main.get_stats', return_value={'total_jobs': 3 if ready else 0, 'last_run': last}), \
         patch('main.get_profile', return_value={'has_resume': ready, 'email': 'private@example.com'}), \
         patch('automation_settings.get_automation_settings', return_value={'enabled': ready}), \
         patch('main.get_gmail_status', return_value={'connected': ready, 'email': 'private@example.com'}), \
         TestClient(main.app) as client:
        response = client.get('/api/operational-readiness')
    assert response.status_code == 200
    data = response.json()
    assert data['jobs_in_current_window'] == (3 if ready else 0)
    assert data['last_hunt'] == last
    assert data['resume_uploaded'] is ready
    assert data['gmail_connected'] is ready
    assert data['automatic_submission_enabled'] is False
    assert data['application_policy_enabled'] is ready
    assert isinstance(data['blockers'], list)
    assert 'private@example.com' not in response.text
