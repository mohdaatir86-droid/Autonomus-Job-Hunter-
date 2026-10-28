from fastapi.testclient import TestClient

import main


def test_n8n_discovery_route_requires_dedicated_bearer_token(monkeypatch):
    monkeypatch.delenv("N8N_AUTOMATION_TOKEN", raising=False)
    monkeypatch.setenv("ENABLE_SCHEDULER", "false")
    calls = []

    async def fake_hunt_jobs():
        calls.append(True)
        return {"fetched": 2, "new_jobs": 1, "errors": []}

    monkeypatch.setattr(main, "hunt_jobs", fake_hunt_jobs)
    client = TestClient(main.app)
    try:
        missing = client.post("/api/integrations/n8n/hunt")
        assert missing.status_code == 503
        assert calls == []

        token = "test-integration-token-" + ("x" * 32)
        monkeypatch.setenv("N8N_AUTOMATION_TOKEN", token)

        unauthorized = client.post(
            "/api/integrations/n8n/hunt",
            headers={"Authorization": "Bearer incorrect-token"},
        )
        assert unauthorized.status_code == 401
        assert calls == []

        accepted = client.post(
            "/api/integrations/n8n/hunt",
            headers={"Authorization": "Bearer " + token},
        )
        assert accepted.status_code == 200
        assert accepted.json()["new_jobs"] == 1
        assert calls == [True]

        # Preserve the existing portal route while adding the separately guarded n8n route.
        portal = client.post("/api/hunt")
        assert portal.status_code == 200
        assert calls == [True, True]
    finally:
        client.close()


def test_internal_hunt_scheduler_can_be_delegated_to_n8n(monkeypatch):
    monkeypatch.delenv("ENABLE_INTERNAL_HUNT_SCHEDULER", raising=False)
    monkeypatch.setenv("ENABLE_SCHEDULER", "true")
    assert main.internal_hunt_scheduler_enabled() is True

    monkeypatch.setenv("ENABLE_INTERNAL_HUNT_SCHEDULER", "false")
    assert main.internal_hunt_scheduler_enabled() is False

    monkeypatch.setenv("ENABLE_INTERNAL_HUNT_SCHEDULER", "true")
    assert main.internal_hunt_scheduler_enabled() is True

    monkeypatch.delenv("ENABLE_INTERNAL_HUNT_SCHEDULER", raising=False)
    monkeypatch.setenv("ENABLE_SCHEDULER", "false")
    assert main.internal_hunt_scheduler_enabled() is False


def test_application_draft_paths_require_a_complete_private_profile(monkeypatch):
    token = "test-portal-token-" + ("x" * 32)
    monkeypatch.setenv("PORTAL_ACCESS_TOKEN", token)
    monkeypatch.setenv("ENABLE_SCHEDULER", "false")
    monkeypatch.setattr(main, "get_profile", lambda: {
        "full_name": "", "email": "", "has_resume": False,
    })
    monkeypatch.setattr(main, "list_jobs", lambda **kwargs: (_ for _ in ()).throw(AssertionError("jobs queried before profile setup")))
    monkeypatch.setattr(main, "create_application_draft", lambda job_id: (_ for _ in ()).throw(AssertionError("draft created before profile setup")))
    monkeypatch.setattr(main, "generate_form_answers", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("form assistance ran before profile setup")))

    headers = {"Authorization": "Bearer " + token}
    client = TestClient(main.app)
    try:
        responses = [
            client.post("/api/automation/settings", headers=headers, json={"enabled": True}),
            client.post("/api/automation/prepare", headers=headers),
            client.post("/api/auto-apply/queue", headers=headers),
            client.post("/api/applications/draft/1", headers=headers),
            client.post(
                "/api/auto-fill",
                headers=headers,
                json={"page_title": "Application", "page_url": "https://boards.greenhouse.io/apply", "fields": [{"id": "name", "label": "Name"}]},
            ),
        ]
        assert [response.status_code for response in responses] == [409, 409, 409, 409, 409]
        assert all("PDF resume" in response.json()["detail"] for response in responses)
    finally:
        client.close()


def test_auto_fill_rejects_unapproved_job_board_domains(monkeypatch):
    token = "test-portal-token-" + ("x" * 32)
    monkeypatch.setenv("PORTAL_ACCESS_TOKEN", token)
    monkeypatch.setenv("ENABLE_SCHEDULER", "false")
    monkeypatch.setattr(main, "get_profile", lambda: (_ for _ in ()).throw(AssertionError("profile accessed for unsupported board")))
    monkeypatch.setattr(main, "generate_form_answers", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("AI called for unsupported board")))

    client = TestClient(main.app)
    try:
        response = client.post(
            "/api/auto-fill",
            headers={"Authorization": "Bearer " + token},
            json={"page_title": "Application", "page_url": "https://www.linkedin.com/jobs/view/123", "fields": [{"id": "name", "label": "Name"}]},
        )
        assert response.status_code == 422
        assert "Greenhouse and Lever" in response.json()["detail"]
    finally:
        client.close()
