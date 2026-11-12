import asyncio
import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

import github_discovery as github
import main


@pytest.fixture(autouse=True)
def scheduler_off(monkeypatch):
    monkeypatch.setenv("ENABLE_SCHEDULER", "false")


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def identity(monkeypatch, signing_key):
    key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
    key.update(kid="fixture", alg="RS256", use="sig")
    async def keys(force=False):
        return [key]
    monkeypatch.setattr(github, "_signing_keys", keys)
    now = int(time.time())
    claims = {
        "iss": github.ISSUER, "aud": github.AUDIENCE,
        "iat": now, "nbf": now, "exp": now + 300,
        "sub": "repo:" + github.REPOSITORY + ":ref:refs/heads/main",
        "repository_id": "1428124047", "repository_owner_id": "335108625",
        "workflow_ref": github.WORKFLOW_REF, "ref": "refs/heads/main", "event_name": "schedule",
    }
    def token(changes=None, remove=None):
        data = {**claims, **(changes or {})}
        if remove:
            data.pop(remove, None)
        return jwt.encode(data, signing_key, algorithm="RS256", headers={"kid": "fixture"})
    return token


def test_correct_workflow_identity_can_trigger_discovery(identity, monkeypatch):
    calls = []
    async def hunt():
        calls.append(True)
        return {"status": "completed", "fetched": 5, "errors": []}
    monkeypatch.setattr(main, "hunt_jobs", hunt)
    with TestClient(main.app) as client:
        response = client.post("/api/integrations/github/hunt", headers={"Authorization": "Bearer " + identity()})
    assert response.status_code == 200
    assert calls == [True]


@pytest.mark.parametrize("changes", [
    {"iss": "https://attacker.example"}, {"aud": "another-service"},
    {"sub": "repo:" + github.REPOSITORY + ":pull_request"},
    {"repository_id": "42"}, {"repository_owner_id": "42"},
    {"workflow_ref": github.WORKFLOW_REF.replace("discovery.yml", "ci.yml")},
    {"ref": "refs/heads/fork"}, {"event_name": "pull_request"},
    {"exp": int(time.time()) - 60}, {"exp": int(time.time()) + 3600},
    {"iat": int(time.time()) - 700},
])
def test_untrusted_identity_is_rejected(identity, changes):
    with pytest.raises(ValueError):
        asyncio.run(github.verify_github_discovery_token(identity(changes)))


@pytest.mark.parametrize("claim", ["exp", "iat", "nbf", "sub", "workflow_ref", "repository_id"])
def test_required_claim_cannot_be_omitted(identity, claim):
    with pytest.raises(ValueError):
        asyncio.run(github.verify_github_discovery_token(identity(remove=claim)))


def test_unsigned_and_wrong_algorithm_tokens_are_rejected():
    token = jwt.encode({"sub": "anything"}, "fixture-key-that-is-long-enough-123", algorithm="HS256", headers={"kid": "fixture"})
    with pytest.raises(ValueError):
        asyncio.run(github.verify_github_discovery_token(token))


def test_identity_outage_fails_closed_without_hunting(monkeypatch):
    async def unavailable(token):
        raise RuntimeError("private provider failure")
    monkeypatch.setattr(github, "verify_github_discovery_token", unavailable)
    monkeypatch.setattr(main, "hunt_jobs", lambda: pytest.fail("must not hunt"))
    with TestClient(main.app) as client:
        response = client.post("/api/integrations/github/hunt", headers={"Authorization": "Bearer fixture"})
    assert response.status_code == 503
    assert "private provider failure" not in response.text


def test_missing_identity_is_rejected_without_hunting(monkeypatch):
    monkeypatch.setattr(main, "hunt_jobs", lambda: pytest.fail("must not hunt"))
    with TestClient(main.app) as client:
        assert client.post("/api/integrations/github/hunt").status_code == 401


def test_rotating_key_is_refreshed_once(identity, signing_key, monkeypatch):
    key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
    key.update(kid="fixture", alg="RS256")
    calls = []
    async def keys(force=False):
        calls.append(force)
        return [key] if force else [{**key, "kid": "retired"}]
    monkeypatch.setattr(github, "_signing_keys", keys)
    asyncio.run(github.verify_github_discovery_token(identity()))
    assert calls == [False, True]


def test_malformed_identity_provider_keys_become_safe_unavailable(monkeypatch):
    monkeypatch.setattr(github, "_cached_keys", [])
    async def get(self, url):
        return httpx.Response(200, json=[], request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    with pytest.raises(RuntimeError, match="identity service unavailable"):
        asyncio.run(github._signing_keys())


def test_forged_signature_cannot_trigger_hunt(identity, monkeypatch):
    token = identity()
    # Keep all claims intact but replace the signature with a different key's signature.
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    claims = jwt.decode(token, options={"verify_signature": False})
    forged = jwt.encode(claims, other_key, algorithm="RS256", headers={"kid": "fixture"})
    with pytest.raises(ValueError):
        asyncio.run(github.verify_github_discovery_token(forged))
