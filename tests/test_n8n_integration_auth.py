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
