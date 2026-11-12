"""Authorize only this repository's main-branch discovery workflow via GitHub OIDC.

No portal key or long-lived scheduler secret is shared with GitHub Actions.
"""
import asyncio
import time

import httpx
import jwt

ISSUER = "https://token.actions.githubusercontent.com"
JWKS_URL = ISSUER + "/.well-known/jwks"
AUDIENCE = "https://autonomous-job-hunter-docker.onrender.com/api/integrations/github/hunt"
REPOSITORY = "mohdaatir86-droid/Autonomus-Job-Hunter-"
SUBJECT = "repo:mohdaatir86-droid@335108625/Autonomus-Job-Hunter-@1428124047:ref:refs/heads/main"
WORKFLOW_REF = REPOSITORY + "/.github/workflows/discovery.yml@refs/heads/main"
_cached_keys = []
_keys_expire_at = 0.0
_last_fetch_at = 0.0
_keys_lock = asyncio.Lock()


async def _signing_keys(force=False):
    global _cached_keys, _keys_expire_at, _last_fetch_at
    async with _keys_lock:
        if _cached_keys and time.monotonic() < _keys_expire_at and (not force or time.monotonic() - _last_fetch_at < 30):
            return _cached_keys
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                response = await client.get(JWKS_URL)
                response.raise_for_status()
                payload = response.json()
                keys = payload.get("keys") if isinstance(payload, dict) else None
            if not isinstance(keys, list) or not keys:
                raise ValueError("No signing keys")
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise RuntimeError("GitHub identity service unavailable") from exc
        _cached_keys = keys
        _last_fetch_at = time.monotonic()
        # Bound cache lifetime; key IDs are never resolved from a token-supplied URL.
        _keys_expire_at = time.monotonic() + 300
        return keys


async def verify_github_discovery_token(token: str) -> None:
    if not token or len(token) > 16384:
        raise ValueError("Invalid GitHub identity")
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise ValueError("Invalid signing algorithm")
    except jwt.PyJWTError as exc:
        raise ValueError("Invalid GitHub identity") from exc
    keys = await _signing_keys()
    key = next((item for item in keys if isinstance(item, dict) and item.get("kid") == header["kid"]), None)
    if key is None:
        # Refresh for legitimate rotation, but at most once per 30 seconds so
        # arbitrary attacker-supplied key IDs cannot flood the identity provider.
        keys = await _signing_keys(force=True)
        key = next((item for item in keys if isinstance(item, dict) and item.get("kid") == header["kid"]), None)
        if key is None:
            raise ValueError("Unknown GitHub signing key")
    try:
        claims = jwt.decode(
            token, jwt.PyJWK.from_dict(key, algorithm="RS256").key,
            algorithms=["RS256"], issuer=ISSUER, audience=AUDIENCE, leeway=15,
            options={"require": ["exp", "iat", "nbf", "iss", "aud", "sub", "repository_id", "repository_owner_id", "workflow_ref", "ref", "event_name"]},
        )
        expected = {
            "repository_id": "1428124047", "repository_owner_id": "335108625",
            "sub": SUBJECT,
            "workflow_ref": WORKFLOW_REF, "ref": "refs/heads/main",
        }
        if any(claims.get(name) != value for name, value in expected.items()):
            raise ValueError("Untrusted workflow")
        if claims["event_name"] not in {"schedule", "workflow_dispatch", "push"}:
            raise ValueError("Untrusted trigger")
        if time.time() - claims["iat"] > 600 or claims["exp"] - claims["iat"] > 600:
            raise ValueError("Identity lifetime too long")
    except (jwt.PyJWTError, TypeError, KeyError) as exc:
        raise ValueError("Invalid GitHub identity") from exc
