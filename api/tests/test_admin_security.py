"""Tests for Build #1 admin security hardening.

Covers:
- /docs + /openapi.json availability in DEV_MODE
- BOLA: no-auth 401 on all invite endpoints
- SQLi inert (parameterized queries)
- extra=forbid 422 on invite bodies
- rate-limit brute-force defence on redeem-invite
- users.email populated on provision
"""
import asyncio
import os
import uuid

import asyncpg

from api.dev_fixtures import ADMIN_CLERK_ID, OWNER_NOVEN_CLERK_ID
from api.tests.conftest import dev_login
from api.tests.test_venue_invites import _delete_audit_logs_for_target, _delete_invite


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Docs availability in DEV_MODE
# ---------------------------------------------------------------------------

def test_docs_available_in_dev_mode(client, api_key_header):
    """GET /docs -> 200 when DEV_MODE=true (as in test env)."""
    resp = client.get("/docs")
    assert resp.status_code == 200, resp.text


def test_openapi_available_in_dev_mode(client, api_key_header):
    """GET /openapi.json -> 200 when DEV_MODE=true (as in test env)."""
    resp = client.get("/openapi.json")
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# BOLA: no-auth 401 on invite endpoints
# ---------------------------------------------------------------------------

def test_admin_invites_list_no_auth_401(client, api_key_header):
    """GET /api/admin/invites with invalid token -> 401."""
    resp = client.get("/api/admin/invites", headers={**api_key_header, **auth_header("bad-token")})
    assert resp.status_code == 401


def test_admin_invites_create_no_auth_401(client, api_key_header):
    """POST /api/admin/invites with invalid token -> 401."""
    resp = client.post(
        "/api/admin/invites",
        headers={**api_key_header, **auth_header("bad-token")},
        json={"invited_email": "test@example.com", "venue_name": "Test"},
    )
    assert resp.status_code == 401


def test_admin_invites_revoke_no_auth_401(client, api_key_header):
    """POST /api/admin/invites/revoke with invalid token -> 401."""
    resp = client.post(
        "/api/admin/invites/revoke",
        headers={**api_key_header, **auth_header("bad-token")},
        json={"invite_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 401


def test_redeem_invite_no_auth_401(client, api_key_header):
    """POST /api/dashboard/redeem-invite with invalid token -> 401."""
    resp = client.post(
        "/api/dashboard/redeem-invite",
        headers={**api_key_header, **auth_header("bad-token")},
        json={"code": "somecodethatdoesnotmatter"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# SQLi inert (parameterized queries)
# ---------------------------------------------------------------------------

def test_invite_create_sqli_inert(client, api_key_header):
    """Malicious SQL in invited_email -> 201 (safely stored, no crash/injection)."""
    sqli = "'; DROP TABLE venues; --"
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    resp = client.post(
        "/api/admin/invites",
        headers={**api_key_header, **auth_header(token)},
        json={"invited_email": sqli, "venue_name": "SQL Test"},
    )
    assert resp.status_code == 201, resp.text
    invite_id = resp.json().get("id")
    try:
        # Venues table must still exist (injection did not execute)
        async def _check():
            conn = await asyncpg.connect(os.environ["DATABASE_URL"])
            try:
                result = await conn.fetchval(
                    "SELECT table_name FROM information_schema.tables WHERE table_name = 'venues'"
                )
                return result
            finally:
                await conn.close()
        result = asyncio.run(_check())
        assert result == "venues", "venues table was dropped — SQLi was not neutralized"
    finally:
        if invite_id:
            _delete_audit_logs_for_target(invite_id)
            _delete_invite(invite_id)


def test_invite_redeem_sqli_inert(client, api_key_header):
    """Malicious SQL as invite code -> 404 (safely rejected, no crash)."""
    sqli = "'; DROP TABLE venues; --"
    # Need venue_owner token — use OWNER_NOVEN (no venue so it passes the 409 gate)
    from api.dev_fixtures import OWNER_NOVEN_CLERK_ID
    token = dev_login(client, api_key_header, OWNER_NOVEN_CLERK_ID)
    resp = client.post(
        "/api/dashboard/redeem-invite",
        headers={**api_key_header, **auth_header(token)},
        json={"code": sqli},
    )
    # The code is short (< 10 chars minimum), so Pydantic rejects it as 422;
    # OR if it passes length, returns 404 (not found). Either is acceptable — just not 500.
    assert resp.status_code in (404, 422), f"Expected 404 or 422, got {resp.status_code}"


# ---------------------------------------------------------------------------
# extra=forbid 422 enforcement
# ---------------------------------------------------------------------------

def test_invite_create_extra_fields_422(client, api_key_header):
    """Extra field in create invite body -> 422."""
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    resp = client.post(
        "/api/admin/invites",
        headers={**api_key_header, **auth_header(token)},
        json={"invited_email": "x@x.com", "venue_name": "X", "hacker_field": "evil"},
    )
    assert resp.status_code == 422


def test_redeem_invite_extra_fields_422(client, api_key_header):
    """Extra field in redeem invite body -> 422."""
    token = dev_login(client, api_key_header, OWNER_NOVEN_CLERK_ID)
    resp = client.post(
        "/api/dashboard/redeem-invite",
        headers={**api_key_header, **auth_header(token)},
        json={"code": "validlengthcode12345", "extra_field": "evil"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Rate-limit brute-force defence
# ---------------------------------------------------------------------------

def test_redeem_invite_rate_limited_429(client, api_key_header):
    """POST /api/dashboard/redeem-invite is limited to 10/minute.

    Fire 11 requests with the same non-existent code (so no DB side-effects).
    The 11th request must return 429. The autouse _reset_rate_limits fixture
    ensures a clean counter at the start of this test.
    """
    token = dev_login(client, api_key_header, OWNER_NOVEN_CLERK_ID)
    headers = {**api_key_header, **auth_header(token)}
    payload = {"code": "ratelimittestcode99999"}
    status_codes = []
    for _ in range(11):
        r = client.post("/api/dashboard/redeem-invite", headers=headers, json=payload)
        status_codes.append(r.status_code)
    assert 429 in status_codes, (
        f"Expected 429 (rate limited) among responses but got: {status_codes}"
    )


# ---------------------------------------------------------------------------
# users.email populated on provision
# ---------------------------------------------------------------------------

def test_users_email_populated_on_provision(monkeypatch):
    """_provision_user stores the Clerk-resolved email in users.email.

    Uses the same monkeypatching pattern as test_provision_user_role_by_allowlist
    in test_auth.py to avoid a real Clerk API call.
    """
    import asyncio as _asyncio
    import uuid as _uuid
    import asyncpg as _asyncpg
    from api import auth

    test_email = "provision-email-test@example.com"
    clerk_id = f"test-email-prov-{_uuid.uuid4()}"

    async def _fake_email(cid):
        return test_email

    monkeypatch.setattr(auth, "_fetch_clerk_email", _fake_email)
    monkeypatch.setattr(auth, "ADMIN_EMAILS", set())

    async def run():
        conn = await _asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await auth._provision_user(conn, clerk_id)
            row = await conn.fetchrow(
                "SELECT email FROM users WHERE clerk_user_id = $1", clerk_id
            )
            return dict(row) if row else None
        finally:
            await conn.execute(
                "DELETE FROM users WHERE clerk_user_id = $1", clerk_id
            )
            await conn.close()

    result = _asyncio.run(run())
    assert result is not None, "User row not found after provision"
    assert result["email"] == test_email, (
        f"Expected email '{test_email}' in users.email, got {result['email']!r}"
    )
