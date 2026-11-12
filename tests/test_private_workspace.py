import asyncio
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

import application_state
import application_workflow
import browser_worker
import gmail_alerts
import job_hunter
import main


OWNER_KEY = "private-fixture-owner-key-" * 3
OWNER_HEADERS = {"Authorization": "Bearer " + OWNER_KEY}
PRIVATE_READS = ["/api/jobs", "/api/stats", "/api/settings", "/api/sources", "/api/operational-readiness", "/health/db"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = str(tmp_path / "private-workspace.sqlite")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ENABLE_SCHEDULER", "false")
    monkeypatch.setenv("PORTAL_ACCESS_TOKEN", OWNER_KEY)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "fixture-client")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "fixture-secret")
    for module in (job_hunter, application_workflow, application_state, gmail_alerts, browser_worker):
        monkeypatch.setattr(module, "DB_PATH", path)
    with TestClient(main.app) as instance:
        yield instance


@pytest.mark.parametrize("path", PRIVATE_READS)
def test_private_reads_require_owner_authentication(client, path):
    response = client.get(path)
    assert response.status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer wrong-key"}).status_code == 401
    assert client.get(path, headers=OWNER_HEADERS).status_code == 200


def test_imported_mail_content_and_tracking_are_not_public(client):
    now = datetime.now(timezone.utc).isoformat()
    with job_hunter.connect() as db:
        db.execute("""INSERT INTO jobs(source,title,company,country,url,description,status,match_score,first_seen_at,last_seen_at)
            VALUES ('Naukri (email alert)','Cluster Manager','Fixture','India',?,'Private email greeting and mailbox content','saved',95,?,?)""",
            ("https://naukri.com/jobs/1?personal-token=fixture", now, now))
    response = client.get("/api/jobs?status=saved")
    assert response.status_code == 401
    assert "Private email" not in response.text
    assert "personal-token" not in response.text
    private = client.get("/api/jobs?status=saved", headers=OWNER_HEADERS)
    assert private.status_code == 200
    assert private.json()["items"][0]["url"] == "https://naukri.com/jobs/1?personal-token=fixture"
    assert private.json()["items"][0]["status"] == "saved"
    assert "description" not in private.json()["items"][0]


def test_owner_session_can_read_private_workspace_but_profile_change_revokes_it(client):
    application_workflow.save_profile({"email": "owner@example.com"})
    client.cookies.set(main.PORTAL_SESSION_COOKIE, main._issue_portal_session("owner@example.com"))
    assert client.get("/api/settings").status_code == 200
    application_workflow.save_profile({"email": "other@example.com"})
    assert client.get("/api/settings").status_code == 401


def test_public_health_has_no_private_state_or_database_dependency(client, monkeypatch):
    def forbidden():
        raise AssertionError("Public liveness must not read private state")
    monkeypatch.setattr(main, "get_profile", forbidden)
    monkeypatch.setattr(main, "get_gmail_status", forbidden)
    monkeypatch.setattr(main, "database_diagnostics", forbidden)
    response = client.get("/health")
    assert response.status_code == 200
    assert set(response.json()) == {"status", "service", "version", "time"}
    assert client.head("/health").status_code == 200
    assert client.get("/api/auth/google/config").status_code == 200
    # The public auth endpoint may check profile ownership only when a session exists.
    client.cookies.clear()
    assert client.get("/api/auth/session").json() == {"authenticated": False}


def test_google_verification_runs_outside_async_event_loop(client, monkeypatch):
    application_workflow.save_profile({"email": "owner@example.com"})
    verification_threads = []
    event_loop_threads = []
    async def fake_form(request):
        event_loop_threads.append(threading.get_ident())
        return {"credential": "fixture-credential", "g_csrf_token": "fixture-csrf"}
    def verify(credential):
        verification_threads.append(threading.get_ident())
        return {"email": "owner@example.com", "email_verified": True, "sub": "fixture-owner"}
    monkeypatch.setattr(main.Request, "form", fake_form)
    monkeypatch.setattr(main, "_verify_google_credential", verify)
    client.cookies.set("g_csrf_token", "fixture-csrf")
    response = client.post("/api/auth/google/session", follow_redirects=False)
    assert response.status_code == 303
    assert "google_signin=success" in response.headers["location"]
    assert verification_threads[0] != event_loop_threads[0]


def test_cancelled_gmail_consent_returns_to_connections_and_consumes_state(client, monkeypatch):
    state = parse_qs(urlsplit(gmail_alerts.build_auth_url()).query)["state"][0]
    client.cookies.set("jh_gmail_state", state, path="/api/gmail")
    response = client.get("/api/gmail/callback", params={"state": state, "error": "access_denied"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/?gmail=denied#sources"
    assert not gmail_alerts.verify_oauth_state(state, consume=True)


def store_expired_grant():
    expired = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with gmail_alerts._connect() as db:
        db.execute("""INSERT INTO gmail_tokens(id,access_token,refresh_token,token_expiry,email,updated_at,granted_scopes)
            VALUES (1,'expired-access','fixture-refresh',?,'owner@example.com',?,'https://www.googleapis.com/auth/gmail.send')""", (expired, expired))


@pytest.mark.parametrize("code,status_code,invalidated", [("invalid_grant", 400, True), ("invalid_client", 401, False), ("server_error", 503, False)])
def test_failed_token_refresh_never_returns_expired_token(client, monkeypatch, code, status_code, invalidated):
    store_expired_grant()
    async def post(*args, **kwargs):
        return httpx.Response(status_code, json={"error": code, "error_description": "sensitive fixture-secret"})
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    with pytest.raises(gmail_alerts.GmailAuthError) as error:
        asyncio.run(gmail_alerts._access_token())
    assert "sensitive" not in str(error.value)
    assert "fixture-secret" not in str(error.value)
    with gmail_alerts._connect() as db:
        row = db.execute("SELECT * FROM gmail_tokens WHERE id=1").fetchone()
    assert bool(row["refresh_token"]) is not invalidated
    assert gmail_alerts.get_gmail_status()["connected"] is not invalidated
    if invalidated:
        assert row["access_token"] is None
        assert not gmail_alerts.get_gmail_status()["send_authorized"]


def test_transient_refresh_network_failure_keeps_saved_grant(client, monkeypatch):
    store_expired_grant()
    async def post(*args, **kwargs):
        raise httpx.ConnectError("fixture outage")
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    with pytest.raises(httpx.ConnectError):
        asyncio.run(gmail_alerts._access_token())
    with gmail_alerts._connect() as db:
        assert db.execute("SELECT refresh_token FROM gmail_tokens WHERE id=1").fetchone()[0] == "fixture-refresh"


def test_email_without_usable_tokens_is_not_connected(client):
    store_expired_grant()
    with gmail_alerts._connect() as db:
        db.execute("UPDATE gmail_tokens SET refresh_token=NULL WHERE id=1")
    assert not gmail_alerts.get_gmail_status()["connected"]
    assert asyncio.run(gmail_alerts._access_token()) is None


@pytest.mark.parametrize("payload", [[], {}, {"access_token": "fixture", "expires_in": "invalid"}, {"access_token": "fixture", "expires_in": -1}, {"access_token": "fixture", "refresh_token": {"unexpected": "object"}}])
def test_malformed_google_token_payload_fails_safely(client, monkeypatch, payload):
    async def post(*args, **kwargs):
        return httpx.Response(200, json=payload)
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    monkeypatch.setattr(gmail_alerts, "_fetch_profile_email", lambda token: pytest.fail("must validate before requesting profile"))
    result = asyncio.run(gmail_alerts.exchange_code("fixture-code"))
    assert result["ok"] is False
    assert "Try again later" in result["error"]


def test_default_oauth_callback_uses_active_origin(monkeypatch):
    monkeypatch.delenv("GOOGLE_REDIRECT_URI", raising=False)
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://fixture-service.onrender.com/")
    assert gmail_alerts._redirect_uri() == "https://fixture-service.onrender.com/api/gmail/callback"
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    assert gmail_alerts._redirect_uri() == "https://autonomous-job-hunter-docker.onrender.com/api/gmail/callback"


def test_non_ascii_authorization_and_callback_state_fail_closed(client):
    response = client.get("/api/jobs", headers=[(b"Authorization", b"Bearer \xff")])
    assert response.status_code == 401
    client.cookies.set("jh_gmail_state", "fixture-ascii-state", path="/api/gmail")
    assert client.get("/api/gmail/callback", params={"state": "\u00e9"}).status_code == 400
