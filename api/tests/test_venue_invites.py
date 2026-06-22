"""Tests for Build #1: venue invites CRUD + redeem flow + /me gate.

Covers:
- POST /api/admin/invites: create, auth gates, validation, audit log
- GET /api/admin/invites: list, auth gate
- POST /api/admin/invites/revoke: revoke, audit log, already-used 404, auth gate
- POST /api/dashboard/redeem-invite: redeem, captures used_by, expired/used/revoked/404
- GET /api/dashboard/me: has_redeemed_invite field

Every test that inserts rows cleans up in a finally block.
"""
import asyncio
import os
import uuid
from datetime import datetime, timedelta

import asyncpg
import pytest

from api.dev_fixtures import (
    ADMIN_CLERK_ID,
    OWNER_A_CLERK_ID,
    OWNER_NOVEN_CLERK_ID,
    OWNER_NOVEN_ID,
    STAFF_A_CLERK_ID,
)
from api.tests.conftest import dev_login


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _insert_invite(invited_email, venue_name, status="active", expires_hours=24, created_by=None):
    """Insert an invite directly into DB for test setup. Returns invite_id (str)."""
    invite_id = str(uuid.uuid4())
    code = f"testcode-{uuid.uuid4().hex}"
    expires_at = datetime.utcnow() + timedelta(hours=expires_hours)

    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute(
                """
                INSERT INTO venue_invites
                    (id, code, invited_email, venue_name, status, expires_at, created_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                invite_id, code, invited_email, venue_name, status, expires_at, created_by,
            )
        finally:
            await conn.close()

    asyncio.run(_q())
    return invite_id


def _insert_invite_with_code(code, invited_email, venue_name, status="active", expires_hours=24):
    """Insert an invite with a specific code. Returns invite_id (str)."""
    invite_id = str(uuid.uuid4())
    expires_at = datetime.utcnow() + timedelta(hours=expires_hours)

    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute(
                """
                INSERT INTO venue_invites
                    (id, code, invited_email, venue_name, status, expires_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                invite_id, code, invited_email, venue_name, status, expires_at,
            )
        finally:
            await conn.close()

    asyncio.run(_q())
    return invite_id


def _delete_invite(invite_id):
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute("DELETE FROM venue_invites WHERE id = $1", invite_id)
        finally:
            await conn.close()

    asyncio.run(_q())


def _get_invite(invite_id):
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await conn.fetchrow(
                "SELECT id, code, invited_email, venue_name, status, "
                "used_by, used_at, signup_email, address, lat, lng, place_id "
                "FROM venue_invites WHERE id = $1",
                invite_id,
            )
        finally:
            await conn.close()

    return asyncio.run(_q())


def _delete_audit_logs_for_target(target_id):
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute("DELETE FROM admin_audit_log WHERE target_id = $1", target_id)
        finally:
            await conn.close()

    asyncio.run(_q())


def _count_audit_logs(action, target_id=None):
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            if target_id is not None:
                return await conn.fetchval(
                    "SELECT COUNT(*) FROM admin_audit_log WHERE action = $1 AND target_id = $2",
                    action, target_id,
                )
            return await conn.fetchval(
                "SELECT COUNT(*) FROM admin_audit_log WHERE action = $1", action,
            )
        finally:
            await conn.close()

    return asyncio.run(_q())


def _reset_owner_noven():
    """Reset the venue-less owner fixture to have venue_id=NULL and delete any venues created."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            # Find and delete any venues this user owns.
            venue_id = await conn.fetchval(
                "SELECT venue_id FROM users WHERE id = $1", OWNER_NOVEN_ID,
            )
            await conn.execute(
                "UPDATE users SET venue_id = NULL WHERE id = $1", OWNER_NOVEN_ID,
            )
            # Clear any invite rows used_by this owner.
            await conn.execute(
                "UPDATE venue_invites SET status = 'active', used_by = NULL, used_at = NULL, signup_email = NULL "
                "WHERE used_by = $1",
                OWNER_NOVEN_ID,
            )
            if venue_id:
                await conn.execute("DELETE FROM tables WHERE venue_id = $1", venue_id)
                await conn.execute("DELETE FROM venues WHERE id = $1", venue_id)
        finally:
            await conn.close()

    asyncio.run(_q())


# ---------------------------------------------------------------------------
# Invite CRUD tests (admin)
# ---------------------------------------------------------------------------

def test_create_invite_201(client, api_key_header):
    """Admin POST /api/admin/invites -> 201, response has code/id/status=active."""
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    resp = client.post(
        "/api/admin/invites",
        headers={**api_key_header, **auth_header(token)},
        json={"invited_email": "newvenue@example.com", "venue_name": "Test Pub"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    invite_id = body.get("id")
    try:
        assert invite_id
        assert body["status"] == "active"
        assert body["code"]
        assert body["invited_email"] == "newvenue@example.com"
        assert body["venue_name"] == "Test Pub"
        assert body["expires_at"]
    finally:
        if invite_id:
            _delete_audit_logs_for_target(invite_id)
            _delete_invite(invite_id)


def test_create_invite_code_is_high_entropy(client, api_key_header):
    """The invite code is at least 32 characters long."""
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    resp = client.post(
        "/api/admin/invites",
        headers={**api_key_header, **auth_header(token)},
        json={"invited_email": "entropy@example.com", "venue_name": "Entropy Bar"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    invite_id = body.get("id")
    try:
        assert len(body["code"]) >= 32
    finally:
        if invite_id:
            _delete_audit_logs_for_target(invite_id)
            _delete_invite(invite_id)


def test_create_invite_expires_in_24h(client, api_key_header):
    """expires_at is approximately 24 hours from now (within 5-min tolerance)."""
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    resp = client.post(
        "/api/admin/invites",
        headers={**api_key_header, **auth_header(token)},
        json={"invited_email": "expire@example.com", "venue_name": "Expiry Test"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    invite_id = body.get("id")
    try:
        # Parse the ISO expires_at and check it's ~24h from now
        expires = datetime.fromisoformat(body["expires_at"])
        now = datetime.utcnow()
        diff = abs((expires - now).total_seconds() - 86400)
        assert diff < 300, f"expires_at should be ~24h from now, diff={diff}s"
    finally:
        if invite_id:
            _delete_audit_logs_for_target(invite_id)
            _delete_invite(invite_id)


def test_create_invite_writes_audit_log(client, api_key_header):
    """Creating an invite inserts an admin_audit_log row with action=invite_create."""
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    resp = client.post(
        "/api/admin/invites",
        headers={**api_key_header, **auth_header(token)},
        json={"invited_email": "auditlog@example.com", "venue_name": "Audit Venue"},
    )
    assert resp.status_code == 201, resp.text
    invite_id = resp.json().get("id")
    try:
        count = _count_audit_logs('invite_create', invite_id)
        assert count == 1, f"Expected 1 audit log row, got {count}"
    finally:
        if invite_id:
            _delete_audit_logs_for_target(invite_id)
            _delete_invite(invite_id)


def test_create_invite_with_address_prefill(client, api_key_header):
    """Invite created with geo data stores it in the DB."""
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    resp = client.post(
        "/api/admin/invites",
        headers={**api_key_header, **auth_header(token)},
        json={
            "invited_email": "geo@example.com",
            "venue_name": "Geo Pub",
            "address": "55 Elizabeth St, Melbourne VIC 3000",
            "latitude": -37.8169,
            "longitude": 144.9648,
            "place_id": "osm123",
        },
    )
    assert resp.status_code == 201, resp.text
    invite_id = resp.json().get("id")
    try:
        row = _get_invite(invite_id)
        assert row is not None
        # Geo prefill must actually persist, not just the invite row existing.
        assert row["address"] == "55 Elizabeth St, Melbourne VIC 3000"
        assert float(row["lat"]) == pytest.approx(-37.8169)
        assert float(row["lng"]) == pytest.approx(144.9648)
        assert row["place_id"] == "osm123"
    finally:
        if invite_id:
            _delete_audit_logs_for_target(invite_id)
            _delete_invite(invite_id)


def test_create_invite_owner_403(client, api_key_header):
    """venue_owner POST /api/admin/invites -> 403."""
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    resp = client.post(
        "/api/admin/invites",
        headers={**api_key_header, **auth_header(token)},
        json={"invited_email": "owner@example.com", "venue_name": "Owner Venue"},
    )
    assert resp.status_code == 403


def test_create_invite_staff_403(client, api_key_header):
    """venue_staff POST /api/admin/invites -> 403."""
    token = dev_login(client, api_key_header, STAFF_A_CLERK_ID)
    resp = client.post(
        "/api/admin/invites",
        headers={**api_key_header, **auth_header(token)},
        json={"invited_email": "staff@example.com", "venue_name": "Staff Venue"},
    )
    assert resp.status_code == 403


def test_create_invite_no_auth_401(client, api_key_header):
    """Invalid auth token -> 401."""
    resp = client.post(
        "/api/admin/invites",
        headers={**api_key_header, **auth_header("bad-token")},
        json={"invited_email": "noauth@example.com", "venue_name": "No Auth"},
    )
    assert resp.status_code == 401


def test_create_invite_extra_field_422(client, api_key_header):
    """Extra field in body -> 422 (extra=forbid)."""
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    resp = client.post(
        "/api/admin/invites",
        headers={**api_key_header, **auth_header(token)},
        json={"invited_email": "extra@example.com", "venue_name": "Extra", "unknown_field": "bad"},
    )
    assert resp.status_code == 422


def test_create_invite_missing_email_422(client, api_key_header):
    """Missing invited_email -> 422."""
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    resp = client.post(
        "/api/admin/invites",
        headers={**api_key_header, **auth_header(token)},
        json={"venue_name": "No Email"},
    )
    assert resp.status_code == 422


def test_create_invite_missing_venue_name_422(client, api_key_header):
    """Missing venue_name -> 422."""
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    resp = client.post(
        "/api/admin/invites",
        headers={**api_key_header, **auth_header(token)},
        json={"invited_email": "noname@example.com"},
    )
    assert resp.status_code == 422


def test_list_invites_200(client, api_key_header):
    """Admin GET /api/admin/invites -> 200, contains the inserted invite."""
    invite_id = _insert_invite("list@example.com", "List Test Venue")
    try:
        token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
        resp = client.get(
            "/api/admin/invites",
            headers={**api_key_header, **auth_header(token)},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "invites" in body
        ids = [inv["id"] for inv in body["invites"]]
        assert invite_id in ids
    finally:
        _delete_invite(invite_id)


def test_list_invites_owner_403(client, api_key_header):
    """venue_owner GET /api/admin/invites -> 403."""
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    resp = client.get(
        "/api/admin/invites",
        headers={**api_key_header, **auth_header(token)},
    )
    assert resp.status_code == 403


def test_revoke_invite_200(client, api_key_header):
    """Admin POST /api/admin/invites/revoke -> 200, DB status=revoked."""
    invite_id = _insert_invite("revoke@example.com", "Revoke Test Venue")
    try:
        token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
        resp = client.post(
            "/api/admin/invites/revoke",
            headers={**api_key_header, **auth_header(token)},
            json={"invite_id": invite_id},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == invite_id
        assert body["status"] == "revoked"
        row = _get_invite(invite_id)
        assert row["status"] == "revoked"
    finally:
        _delete_audit_logs_for_target(invite_id)
        _delete_invite(invite_id)


def test_revoke_invite_writes_audit_log(client, api_key_header):
    """Revoking an invite writes an admin_audit_log row with action=invite_revoke."""
    invite_id = _insert_invite("revokeaudit@example.com", "Revoke Audit Venue")
    try:
        token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
        client.post(
            "/api/admin/invites/revoke",
            headers={**api_key_header, **auth_header(token)},
            json={"invite_id": invite_id},
        )
        count = _count_audit_logs('invite_revoke', invite_id)
        assert count == 1, f"Expected 1 audit log row for revoke, got {count}"
    finally:
        _delete_audit_logs_for_target(invite_id)
        _delete_invite(invite_id)


def test_revoke_invite_already_used_404(client, api_key_header):
    """Revoking a 'used' invite -> 404."""
    invite_id = _insert_invite("usedrevoke@example.com", "Used Venue", status="used")
    try:
        token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
        resp = client.post(
            "/api/admin/invites/revoke",
            headers={**api_key_header, **auth_header(token)},
            json={"invite_id": invite_id},
        )
        assert resp.status_code == 404
    finally:
        _delete_invite(invite_id)


def test_revoke_invite_unknown_id_404(client, api_key_header):
    """Random UUID -> 404."""
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    resp = client.post(
        "/api/admin/invites/revoke",
        headers={**api_key_header, **auth_header(token)},
        json={"invite_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404


def test_revoke_invite_owner_403(client, api_key_header):
    """venue_owner POST /api/admin/invites/revoke -> 403."""
    invite_id = _insert_invite("ownerrevoke@example.com", "Owner Revoke Test")
    try:
        token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        resp = client.post(
            "/api/admin/invites/revoke",
            headers={**api_key_header, **auth_header(token)},
            json={"invite_id": invite_id},
        )
        assert resp.status_code == 403
    finally:
        _delete_invite(invite_id)


# ---------------------------------------------------------------------------
# Invite redeem tests (dashboard, venue_owner)
# ---------------------------------------------------------------------------

def test_redeem_invite_200(client, api_key_header):
    """Venue-less owner redeems valid code -> 200, response has prefill data, DB status=used."""
    invite_id = _insert_invite("redeem@example.com", "Redeem Test Venue")
    row = _get_invite(invite_id)
    try:
        token = dev_login(client, api_key_header, OWNER_NOVEN_CLERK_ID)
        resp = client.post(
            "/api/dashboard/redeem-invite",
            headers={**api_key_header, **auth_header(token)},
            json={"code": row["code"]},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "invite" in body
        assert body["invite"]["venue_name"] == "Redeem Test Venue"
        db_row = _get_invite(invite_id)
        assert db_row["status"] == "used"
    finally:
        _reset_owner_noven()
        _delete_invite(invite_id)


def test_redeem_invite_captures_used_by(client, api_key_header):
    """After redemption, venue_invites.used_by = the owner's user id."""
    invite_id = _insert_invite("captureused@example.com", "Capture Used Venue")
    row = _get_invite(invite_id)
    try:
        token = dev_login(client, api_key_header, OWNER_NOVEN_CLERK_ID)
        client.post(
            "/api/dashboard/redeem-invite",
            headers={**api_key_header, **auth_header(token)},
            json={"code": row["code"]},
        )
        db_row = _get_invite(invite_id)
        assert str(db_row["used_by"]) == OWNER_NOVEN_ID
    finally:
        _reset_owner_noven()
        _delete_invite(invite_id)


def test_redeem_invite_expired_404(client, api_key_header):
    """Expired invite (expires_at in past) -> 404."""
    invite_id = _insert_invite("expired@example.com", "Expired Venue", expires_hours=-1)
    row = _get_invite(invite_id)
    try:
        token = dev_login(client, api_key_header, OWNER_NOVEN_CLERK_ID)
        resp = client.post(
            "/api/dashboard/redeem-invite",
            headers={**api_key_header, **auth_header(token)},
            json={"code": row["code"]},
        )
        assert resp.status_code == 404
    finally:
        _delete_invite(invite_id)


def test_redeem_invite_already_used_404(client, api_key_header):
    """Already-used invite -> 404."""
    invite_id = _insert_invite("alreadyused@example.com", "Already Used Venue", status="used")
    row = _get_invite(invite_id)
    try:
        token = dev_login(client, api_key_header, OWNER_NOVEN_CLERK_ID)
        resp = client.post(
            "/api/dashboard/redeem-invite",
            headers={**api_key_header, **auth_header(token)},
            json={"code": row["code"]},
        )
        assert resp.status_code == 404
    finally:
        _delete_invite(invite_id)


def test_redeem_invite_revoked_404(client, api_key_header):
    """Revoked invite -> 404."""
    invite_id = _insert_invite("revoked@example.com", "Revoked Venue", status="revoked")
    row = _get_invite(invite_id)
    try:
        token = dev_login(client, api_key_header, OWNER_NOVEN_CLERK_ID)
        resp = client.post(
            "/api/dashboard/redeem-invite",
            headers={**api_key_header, **auth_header(token)},
            json={"code": row["code"]},
        )
        assert resp.status_code == 404
    finally:
        _delete_invite(invite_id)


def test_redeem_invite_nonexistent_code_404(client, api_key_header):
    """Random code string -> 404."""
    token = dev_login(client, api_key_header, OWNER_NOVEN_CLERK_ID)
    resp = client.post(
        "/api/dashboard/redeem-invite",
        headers={**api_key_header, **auth_header(token)},
        json={"code": "nonexistentcode1234567890abcdef"},
    )
    assert resp.status_code == 404


def test_redeem_invite_owner_with_venue_409(client, api_key_header):
    """Owner who already has a venue tries to redeem -> 409."""
    invite_id = _insert_invite("withvenue@example.com", "With Venue Test")
    row = _get_invite(invite_id)
    try:
        # OWNER_A already has a venue (VENUE_A_ID)
        token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        resp = client.post(
            "/api/dashboard/redeem-invite",
            headers={**api_key_header, **auth_header(token)},
            json={"code": row["code"]},
        )
        assert resp.status_code == 409
    finally:
        _delete_invite(invite_id)


# ---------------------------------------------------------------------------
# Gate tests: GET /api/dashboard/me has_redeemed_invite
# ---------------------------------------------------------------------------

def test_me_has_redeemed_invite_false(client, api_key_header):
    """GET /me for OWNER_NOVEN (no invite redeemed) -> has_redeemed_invite=False."""
    token = dev_login(client, api_key_header, OWNER_NOVEN_CLERK_ID)
    resp = client.get(
        "/api/dashboard/me",
        headers={**api_key_header, **auth_header(token)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_redeemed_invite"] is False


def test_me_has_redeemed_invite_true(client, api_key_header):
    """After redeeming, GET /me -> has_redeemed_invite=True."""
    invite_id = _insert_invite("hasinvite@example.com", "Has Invite Venue")
    row = _get_invite(invite_id)
    try:
        token = dev_login(client, api_key_header, OWNER_NOVEN_CLERK_ID)
        # Redeem the invite
        redeem_resp = client.post(
            "/api/dashboard/redeem-invite",
            headers={**api_key_header, **auth_header(token)},
            json={"code": row["code"]},
        )
        assert redeem_resp.status_code == 200
        # Now check /me
        me_resp = client.get(
            "/api/dashboard/me",
            headers={**api_key_header, **auth_header(token)},
        )
        assert me_resp.status_code == 200
        body = me_resp.json()
        assert body["has_redeemed_invite"] is True
    finally:
        _reset_owner_noven()
        _delete_invite(invite_id)
