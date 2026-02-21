"""Integration tests for /v1/auth endpoints.

Covers: register, login, token refresh, logout, /me.
Real services: PostgreSQL, Redis.
"""
from __future__ import annotations

import httpx

from tests.conftest import do_register, unique_email


# ── Register ──────────────────────────────────────────────────────────────────


async def test_register_success(client: httpx.AsyncClient):
    """POST /register → 201 with access_token, refresh_token, token_type."""
    email = unique_email()
    r = await client.post(
        "/v1/auth/register",
        json={
            "email": email,
            "password": "StrongPassword123!",
            "display_name": "Alice",
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] > 0


async def test_register_duplicate_email_returns_409(client: httpx.AsyncClient):
    """Registering the same email twice → 409 CONFLICT."""
    email = unique_email()
    payload = {
        "email": email,
        "password": "StrongPassword123!",
        "display_name": "Bob",
    }
    r1 = await client.post("/v1/auth/register", json=payload)
    assert r1.status_code == 201

    r2 = await client.post("/v1/auth/register", json=payload)
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "CONFLICT"


async def test_register_short_password_returns_422(client: httpx.AsyncClient):
    """Password < 12 characters → 422 validation error."""
    r = await client.post(
        "/v1/auth/register",
        json={
            "email": unique_email(),
            "password": "short",
            "display_name": "Charlie",
        },
    )
    assert r.status_code == 422


async def test_register_invalid_email_returns_422(client: httpx.AsyncClient):
    """Non-email string → 422 validation error."""
    r = await client.post(
        "/v1/auth/register",
        json={
            "email": "not-an-email",
            "password": "StrongPassword123!",
            "display_name": "Dave",
        },
    )
    assert r.status_code == 422


# ── Login ─────────────────────────────────────────────────────────────────────


async def test_login_success(client: httpx.AsyncClient):
    """POST /login with correct credentials → 200 with tokens."""
    email = unique_email()
    password = "MyStrongPass123!"
    await do_register(client, email=email, password=password)

    r = await client.post(
        "/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert r.status_code == 200
    body = r.json()
    assert "access_token" in body
    assert "refresh_token" in body


async def test_login_wrong_password_returns_401(client: httpx.AsyncClient):
    """Login with wrong password → 401."""
    email = unique_email()
    await do_register(client, email=email, password="CorrectPassword123!")

    r = await client.post(
        "/v1/auth/login",
        json={"email": email, "password": "WrongPassword123!"},
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


async def test_login_unknown_email_returns_401(client: httpx.AsyncClient):
    """Login with unregistered email → 401 (no user-enumeration leak)."""
    r = await client.post(
        "/v1/auth/login",
        json={"email": "nobody@example.com", "password": "SomePassword123!"},
    )
    assert r.status_code == 401


# ── Token refresh ─────────────────────────────────────────────────────────────


async def test_refresh_returns_new_tokens(client: httpx.AsyncClient, auth_data: dict):
    """POST /refresh with a valid refresh token → 200 with new token pair."""
    r = await client.post(
        "/v1/auth/refresh",
        json={"refresh_token": auth_data["refresh_token"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert "access_token" in body
    assert "refresh_token" in body
    # New tokens must differ from old ones
    assert body["access_token"] != auth_data["access_token"]
    assert body["refresh_token"] != auth_data["refresh_token"]


async def test_refresh_replay_rejected(client: httpx.AsyncClient, auth_data: dict):
    """Using a refresh token twice → 401 on the second call (JTI revocation)."""
    original_refresh = auth_data["refresh_token"]

    # First use: succeeds and revokes the old JTI
    r1 = await client.post(
        "/v1/auth/refresh",
        json={"refresh_token": original_refresh},
    )
    assert r1.status_code == 200

    # Second use of same token: denied
    r2 = await client.post(
        "/v1/auth/refresh",
        json={"refresh_token": original_refresh},
    )
    assert r2.status_code == 401


async def test_refresh_with_access_token_rejected(
    client: httpx.AsyncClient, auth_data: dict
):
    """Submitting an access token as a refresh token → 401."""
    r = await client.post(
        "/v1/auth/refresh",
        json={"refresh_token": auth_data["access_token"]},
    )
    assert r.status_code == 401


async def test_refresh_malformed_token_returns_401(client: httpx.AsyncClient):
    """Completely invalid token string → 401."""
    r = await client.post(
        "/v1/auth/refresh",
        json={"refresh_token": "this.is.not.a.jwt"},
    )
    assert r.status_code == 401


# ── Logout ────────────────────────────────────────────────────────────────────


async def test_logout_revokes_refresh_token(
    client: httpx.AsyncClient, auth_data: dict
):
    """DELETE /logout then /refresh with old token → 401."""
    refresh_token = auth_data["refresh_token"]

    # Logout
    r = await client.request(
        "DELETE",
        "/v1/auth/logout",
        json={"refresh_token": refresh_token},
    )
    assert r.status_code == 204

    # Old refresh token is now revoked
    r2 = await client.post(
        "/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert r2.status_code == 401


# ── /me ───────────────────────────────────────────────────────────────────────


async def test_me_requires_auth(client: httpx.AsyncClient):
    """GET /me without Authorization header → 401 (bearer missing)."""
    r = await client.get("/v1/auth/me")
    assert r.status_code == 401


async def test_me_returns_profile(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
):
    """GET /me with valid token → 200 with user profile."""
    r = await client.get("/v1/auth/me", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert "user_id" in body
    assert "email" in body
    assert "role" in body
    assert body["role"] == "editor"
