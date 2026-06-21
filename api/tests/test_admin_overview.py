"""Tests for GET /api/admin/me, GET /api/admin/overview, GET /api/admin/venues.

Follows the exact same pattern as test_dashboard_overview.py:
- Uses fresh_table fixture, dev_login helper, auth_header helper
- asyncio.run for direct DB helpers
- finally blocks for cleanup
"""
import asyncio
import os
import uuid
from datetime import datetime, timezone

import asyncpg

from api.dev_fixtures import (
    ADMIN_CLERK_ID,
    OWNER_A_CLERK_ID,
    STAFF_A_CLERK_ID,
    VENUE_A_ID,
    VENUE_B_ID,
    VENUE_B_TABLE_ID,
)
from api.tests.conftest import dev_login


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# DB helpers — same asyncio.run pattern as test_dashboard_overview.py
# ---------------------------------------------------------------------------

def _utcnow():
    """Naive UTC datetime, matching how timestamps are stored in the DB."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _insert_session(table_id, venue_id, started_at=None, ended_at=None):
    session_id = str(uuid.uuid4())

    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute(
                """
                INSERT INTO game_sessions
                    (id, venue_id, table_id, player_count, started_at, ended_at, created_at)
                VALUES ($1, $2, $3, 4, $4, $5, NOW())
                """,
                session_id, venue_id, table_id, started_at, ended_at,
            )
        finally:
            await conn.close()

    asyncio.run(_q())
    return session_id


def _delete_session(session_id):
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute(
                "DELETE FROM roulette_votes WHERE round_id IN "
                "(SELECT id FROM rounds WHERE session_id = $1)", session_id
            )
            await conn.execute("DELETE FROM rounds WHERE session_id = $1", session_id)
            await conn.execute("DELETE FROM game_players WHERE session_id = $1", session_id)
            await conn.execute("DELETE FROM game_sessions WHERE id = $1", session_id)
        finally:
            await conn.close()

    asyncio.run(_q())


def _set_venue_is_test(venue_id, value):
    """Set a venue's is_test flag. Always restore in finally."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute(
                "UPDATE venues SET is_test = $1 WHERE id = $2", value, venue_id
            )
        finally:
            await conn.close()

    asyncio.run(_q())


def _get_venue_is_test(venue_id):
    """Read a venue's current is_test flag from the DB."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await conn.fetchval(
                "SELECT is_test FROM venues WHERE id = $1", venue_id
            )
        finally:
            await conn.close()

    return asyncio.run(_q())


# ---------------------------------------------------------------------------
# Tests: GET /api/admin/me
# ---------------------------------------------------------------------------

def test_admin_me_returns_admin_role(client, api_key_header):
    """dev_admin -> 200, role=admin, venue_id=None."""
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    resp = client.get("/api/admin/me", headers={**api_key_header, **auth_header(token)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "admin"
    assert body["venue_id"] is None
    assert body["clerk_user_id"] == "dev_admin"


def test_admin_me_returns_owner_role(client, api_key_header):
    """dev_owner_a -> 200 (no role gate on /me), role=venue_owner, venue_id present."""
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    resp = client.get("/api/admin/me", headers={**api_key_header, **auth_header(token)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "venue_owner"
    assert body["venue_id"] is not None


def test_admin_me_unauth_422(client, api_key_header):
    """No Authorization header -> 422 (FastAPI missing required dependency)."""
    resp = client.get("/api/admin/me", headers=api_key_header)
    assert resp.status_code == 422


def test_admin_me_invalid_token_401(client, api_key_header):
    """Garbage token -> 401."""
    headers = {**api_key_header, **auth_header("not-a-real-token")}
    resp = client.get("/api/admin/me", headers=headers)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Tests: GET /api/admin/overview
# ---------------------------------------------------------------------------

def test_admin_overview_admin_200(client, api_key_header):
    """dev_admin -> 200 with correct platform+per_venue shape, all values >= 0."""
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    resp = client.get(
        "/api/admin/overview", headers={**api_key_header, **auth_header(token)}
    )
    assert resp.status_code == 200
    body = resp.json()

    assert "platform" in body
    assert "per_venue" in body

    platform = body["platform"]
    for key in (
        "total_venues",
        "active_venues_now",
        "active_sessions_now",
        "sessions_tonight",
        "players_tonight",
        "rounds_tonight",
    ):
        assert key in platform, f"Missing key: {key}"
        assert isinstance(platform[key], int), f"{key} must be int"
        assert platform[key] >= 0, f"{key} must be >= 0"

    assert isinstance(body["per_venue"], list)
    for entry in body["per_venue"]:
        assert "venue_id" in entry
        assert "name" in entry
        assert "slug" in entry
        assert isinstance(entry["active_sessions"], int)
        assert isinstance(entry["sessions_tonight"], int)
        assert isinstance(entry["players_tonight"], int)


def test_admin_overview_owner_403(client, api_key_header):
    """KEY INVERSE-BOLA: venue_owner -> 403 (must never reach cross-venue data)."""
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    resp = client.get(
        "/api/admin/overview", headers={**api_key_header, **auth_header(token)}
    )
    assert resp.status_code == 403


def test_admin_overview_staff_403(client, api_key_header):
    """KEY INVERSE-BOLA: venue_staff -> 403."""
    token = dev_login(client, api_key_header, STAFF_A_CLERK_ID)
    resp = client.get(
        "/api/admin/overview", headers={**api_key_header, **auth_header(token)}
    )
    assert resp.status_code == 403


def test_admin_overview_unauth_422(client, api_key_header):
    """No Authorization header -> 422."""
    resp = client.get("/api/admin/overview", headers=api_key_header)
    assert resp.status_code == 422


def test_admin_overview_invalid_token_401(client, api_key_header):
    """Garbage token -> 401."""
    headers = {**api_key_header, **auth_header("not-a-real-token")}
    resp = client.get("/api/admin/overview", headers=headers)
    assert resp.status_code == 401


def test_admin_overview_cross_venue(client, api_key_header, fresh_table):
    """Admin overview must include sessions from BOTH venue A and venue B.

    Insert an active session on fresh_table (venue A) and on VENUE_B_TABLE_ID
    (venue B). Assert the admin overview shows both venues in per_venue and
    that platform.active_sessions_now >= 2.
    """
    session_a = _insert_session(
        fresh_table["table_id"], VENUE_A_ID, started_at=_utcnow(), ended_at=None
    )
    session_b = _insert_session(
        VENUE_B_TABLE_ID, VENUE_B_ID, started_at=_utcnow(), ended_at=None
    )

    try:
        token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
        resp = client.get(
            "/api/admin/overview", headers={**api_key_header, **auth_header(token)}
        )
        assert resp.status_code == 200
        body = resp.json()

        # Platform-level: at least both sessions visible
        assert body["platform"]["active_sessions_now"] >= 2

        per_venue = body["per_venue"]
        names = [v["name"] for v in per_venue]
        assert "Fifty Five Bar" in names, "Venue A missing from per_venue"
        assert "The Last Chance" in names, "Venue B missing from per_venue"

        venue_a_entry = next(v for v in per_venue if v["name"] == "Fifty Five Bar")
        venue_b_entry = next(v for v in per_venue if v["name"] == "The Last Chance")
        assert venue_a_entry["active_sessions"] >= 1
        assert venue_b_entry["active_sessions"] >= 1
    finally:
        _delete_session(session_a)
        _delete_session(session_b)


def test_admin_overview_is_test_exclusion(client, api_key_header):
    """is_test=TRUE venues must be excluded from overview totals and per_venue list.

    Steps:
    1. Fetch overview with venue B as non-test -> note total_venues count.
    2. Set venue B is_test=TRUE, insert a session on venue B.
    3. Fetch overview -> venue B slug must NOT appear in per_venue.
    4. total_venues must be less than step-1 count (venue B no longer counted).
    5. Restore is_test=FALSE in finally, delete session.
    """
    # Ensure a clean starting state (venue B is not a test venue)
    _set_venue_is_test(VENUE_B_ID, False)

    # Baseline count
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    baseline_resp = client.get(
        "/api/admin/overview", headers={**api_key_header, **auth_header(token)}
    )
    assert baseline_resp.status_code == 200
    baseline_total = baseline_resp.json()["platform"]["total_venues"]

    session_b = None
    try:
        _set_venue_is_test(VENUE_B_ID, True)
        session_b = _insert_session(
            VENUE_B_TABLE_ID, VENUE_B_ID, started_at=_utcnow(), ended_at=None
        )

        resp = client.get(
            "/api/admin/overview", headers={**api_key_header, **auth_header(token)}
        )
        assert resp.status_code == 200
        body = resp.json()

        # Venue B must not appear in per_venue
        slugs = [v["slug"] for v in body["per_venue"]]
        assert "the-last-chance" not in slugs, "is_test venue must not appear in per_venue"

        # total_venues must have decreased by at least 1
        assert body["platform"]["total_venues"] < baseline_total
    finally:
        if session_b:
            _delete_session(session_b)
        _set_venue_is_test(VENUE_B_ID, False)
        # Verify restoration
        assert _get_venue_is_test(VENUE_B_ID) is False


# ---------------------------------------------------------------------------
# Tests: GET /api/admin/venues
# ---------------------------------------------------------------------------

def test_admin_venues_admin_200(client, api_key_header):
    """dev_admin -> 200 with venues list containing both fifty-five-bar and the-last-chance."""
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    resp = client.get(
        "/api/admin/venues", headers={**api_key_header, **auth_header(token)}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "venues" in body
    venues = body["venues"]
    assert isinstance(venues, list)

    slugs = [v["slug"] for v in venues]
    assert "fifty-five-bar" in slugs, "fifty-five-bar missing from /admin/venues"
    assert "the-last-chance" in slugs, "the-last-chance missing from /admin/venues"

    for v in venues:
        assert isinstance(v["id"], str)
        assert isinstance(v["name"], str)
        assert isinstance(v["slug"], str)
        assert isinstance(v["venue_type"], str)
        assert isinstance(v["status"], str)
        assert isinstance(v["is_test"], bool)
        assert isinstance(v["table_count"], int) and v["table_count"] >= 0
        assert isinstance(v["active_sessions"], int) and v["active_sessions"] >= 0
        assert isinstance(v["sessions_tonight"], int) and v["sessions_tonight"] >= 0


def test_admin_venues_owner_403(client, api_key_header):
    """venue_owner -> 403 (inverse-BOLA gate)."""
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    resp = client.get(
        "/api/admin/venues", headers={**api_key_header, **auth_header(token)}
    )
    assert resp.status_code == 403


def test_admin_venues_staff_403(client, api_key_header):
    """venue_staff -> 403 (inverse-BOLA gate)."""
    token = dev_login(client, api_key_header, STAFF_A_CLERK_ID)
    resp = client.get(
        "/api/admin/venues", headers={**api_key_header, **auth_header(token)}
    )
    assert resp.status_code == 403


def test_admin_venues_includes_test_venues(client, api_key_header):
    """/admin/venues INCLUDES is_test venues (flagged with is_test=True).

    Set venue B to is_test=TRUE, fetch /admin/venues, assert the-last-chance appears
    with is_test == True. Restore in finally.
    """
    _set_venue_is_test(VENUE_B_ID, False)  # clean start

    try:
        _set_venue_is_test(VENUE_B_ID, True)

        token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
        resp = client.get(
            "/api/admin/venues", headers={**api_key_header, **auth_header(token)}
        )
        assert resp.status_code == 200
        venues = resp.json()["venues"]

        brew_house = next((v for v in venues if v["slug"] == "the-last-chance"), None)
        assert brew_house is not None, "the-last-chance must appear in /admin/venues even when is_test=True"
        assert brew_house["is_test"] is True
    finally:
        _set_venue_is_test(VENUE_B_ID, False)
        # Confirm restoration
        assert _get_venue_is_test(VENUE_B_ID) is False
