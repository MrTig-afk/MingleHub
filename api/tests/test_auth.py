from api.dev_fixtures import (
    ADMIN_CLERK_ID,
    OWNER_A_CLERK_ID,
    OWNER_B_CLERK_ID,
    STAFF_A_CLERK_ID,
)
from api.tests.conftest import dev_login


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def test_dashboard_me_requires_auth_header(client, api_key_header):
    resp = client.get("/api/dashboard/me", headers=api_key_header)
    assert resp.status_code == 422  # missing Authorization header


def test_dashboard_me_rejects_garbage_token(client, api_key_header):
    headers = {**api_key_header, **auth_header("not-a-real-token")}
    resp = client.get("/api/dashboard/me", headers=headers)
    assert resp.status_code == 401


def test_dashboard_me_rejects_unknown_user(client, api_key_header):
    token = dev_login(client, api_key_header, "someone_not_seeded")
    headers = {**api_key_header, **auth_header(token)}
    resp = client.get("/api/dashboard/me", headers=headers)
    assert resp.status_code == 401


def test_dashboard_me_returns_identity_for_known_user(client, api_key_header):
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    headers = {**api_key_header, **auth_header(token)}
    resp = client.get("/api/dashboard/me", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["clerk_user_id"] == OWNER_A_CLERK_ID
    assert body["role"] == "venue_owner"
    assert body["venue_id"] is not None


def test_venue_owner_sees_only_their_own_venue(client, api_key_header):
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    headers = {**api_key_header, **auth_header(token)}
    resp = client.get("/api/dashboard/venue", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["slug"] == "lions-den"


def test_venue_isolation_across_owners(client, api_key_header):
    """BOLA proof: two different venue_owners hitting the same venue-scoped
    endpoint get back two different venues, derived purely from their own
    identity — there is no venue_id parameter for either to manipulate."""
    token_a = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    token_b = dev_login(client, api_key_header, OWNER_B_CLERK_ID)

    venue_a = client.get("/api/dashboard/venue", headers={**api_key_header, **auth_header(token_a)}).json()
    venue_b = client.get("/api/dashboard/venue", headers={**api_key_header, **auth_header(token_b)}).json()

    assert venue_a["slug"] == "lions-den"
    assert venue_b["slug"] == "brew-house"
    assert venue_a["id"] != venue_b["id"]


def test_venue_staff_can_access_own_dashboard_venue(client, api_key_header):
    token = dev_login(client, api_key_header, STAFF_A_CLERK_ID)
    headers = {**api_key_header, **auth_header(token)}
    resp = client.get("/api/dashboard/venue", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["slug"] == "lions-den"


def test_admin_cannot_use_venue_dashboard_endpoint(client, api_key_header):
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    headers = {**api_key_header, **auth_header(token)}
    resp = client.get("/api/dashboard/venue", headers=headers)
    assert resp.status_code == 403


def test_venue_owner_cannot_access_admin_endpoint(client, api_key_header):
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    headers = {**api_key_header, **auth_header(token)}
    resp = client.get("/api/admin/ping", headers=headers)
    assert resp.status_code == 403


def test_admin_can_access_admin_endpoint(client, api_key_header):
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    headers = {**api_key_header, **auth_header(token)}
    resp = client.get("/api/admin/ping", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["admin"] == ADMIN_CLERK_ID


def test_dev_login_requires_api_key(client):
    resp = client.post("/api/auth/dev-login", json={"clerk_user_id": OWNER_A_CLERK_ID})
    assert resp.status_code == 422  # missing X-API-Key header


def test_dev_login_rejects_wrong_api_key(client):
    resp = client.post(
        "/api/auth/dev-login",
        headers={"X-API-Key": "wrong-key"},
        json={"clerk_user_id": OWNER_A_CLERK_ID},
    )
    assert resp.status_code == 401


def test_verify_clerk_jwt_validates_signature_issuer_and_expiry(monkeypatch):
    """Clerk-mode _verify_clerk_jwt: accepts a well-formed RS256 JWT and returns its
    sub; rejects wrong-issuer and expired tokens. Uses a self-generated keypair, so no
    live Clerk instance is needed — this covers the prod verification path in CI."""
    import time
    import pytest
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from api import auth

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub = key.public_key()
    monkeypatch.setattr(auth, "CLERK_ISSUER", "https://test.clerk")
    now = int(time.time())

    good = jwt.encode({"sub": "user_abc", "iss": "https://test.clerk", "exp": now + 300}, key, algorithm="RS256")
    assert auth._verify_clerk_jwt(good, pub) == "user_abc"

    wrong_iss = jwt.encode({"sub": "u", "iss": "https://evil", "exp": now + 300}, key, algorithm="RS256")
    with pytest.raises(jwt.InvalidIssuerError):
        auth._verify_clerk_jwt(wrong_iss, pub)

    expired = jwt.encode({"sub": "u", "iss": "https://test.clerk", "exp": now - 10}, key, algorithm="RS256")
    with pytest.raises(jwt.ExpiredSignatureError):
        auth._verify_clerk_jwt(expired, pub)


def test_provision_user_role_by_allowlist(monkeypatch):
    """First-login auto-provision: an allowlisted email -> admin row; anyone else ->
    venue_owner with no venue (which the dashboard turns into the setup wizard)."""
    import asyncio
    import os
    import uuid as _uuid
    import asyncpg
    from api import auth

    monkeypatch.setattr(auth, "ADMIN_EMAILS", {"boss@example.com"})

    async def _email(cid):
        return "boss@example.com" if "admin" in cid else "rando@example.com"
    monkeypatch.setattr(auth, "_fetch_clerk_email", _email)

    admin_id = f"test-prov-admin-{_uuid.uuid4()}"
    owner_id = f"test-prov-owner-{_uuid.uuid4()}"

    async def run():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            a = await auth._provision_user(conn, admin_id)
            o = await auth._provision_user(conn, owner_id)
            return dict(a), dict(o)
        finally:
            await conn.execute("DELETE FROM users WHERE clerk_user_id = ANY($1::text[])", [admin_id, owner_id])
            await conn.close()

    a, o = asyncio.run(run())
    assert a["role"] == "admin" and a["venue_id"] is None
    assert o["role"] == "venue_owner" and o["venue_id"] is None
