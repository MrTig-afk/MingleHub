import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg

from api.dev_fixtures import (
    ADMIN_CLERK_ID,
    OWNER_A_CLERK_ID,
    OWNER_B_CLERK_ID,
    STAFF_A_CLERK_ID,
)
from api.tests.conftest import dev_login


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# DB helpers — same asyncio.run pattern as conftest.py
# ---------------------------------------------------------------------------

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
            await conn.execute("DELETE FROM rounds WHERE session_id = $1", session_id)
            await conn.execute("DELETE FROM game_players WHERE session_id = $1", session_id)
            await conn.execute("DELETE FROM game_sessions WHERE id = $1", session_id)
        finally:
            await conn.close()

    asyncio.run(_q())


def _utcnow():
    # game_sessions timestamps are stored as UTC wall-clock — match that so a
    # session inserted "now" actually falls inside the endpoint's tonight window.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _tonight_boundary():
    # The same "last 4am local -> UTC" boundary the endpoint computes, resolved via
    # Postgres (the only place with a reliable tz database on a Windows dev box).
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await conn.fetchval(
                """
                SELECT (
                    (date_trunc('day', (NOW() AT TIME ZONE $1) - INTERVAL '4 hours')
                        + INTERVAL '4 hours')
                    AT TIME ZONE $1
                ) AT TIME ZONE 'UTC'
                """,
                "Australia/Melbourne",
            )
        finally:
            await conn.close()

    return asyncio.run(_q())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_overview_requires_auth(client, api_key_header):
    resp = client.get("/api/dashboard/overview", headers=api_key_header)
    assert resp.status_code == 422  # missing Authorization header


def test_overview_rejects_invalid_token(client, api_key_header):
    headers = {**api_key_header, **auth_header("not-a-real-token")}
    resp = client.get("/api/dashboard/overview", headers=headers)
    assert resp.status_code == 401


def test_overview_owner_happy_path(client, api_key_header):
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    resp = client.get("/api/dashboard/overview", headers={**api_key_header, **auth_header(token)})
    assert resp.status_code == 200
    body = resp.json()
    tonight = body["tonight"]
    assert isinstance(tonight["active_tables"], int) and tonight["active_tables"] >= 0
    assert isinstance(tonight["players_tonight"], int) and tonight["players_tonight"] >= 0
    assert isinstance(tonight["rounds_tonight"], int) and tonight["rounds_tonight"] >= 0
    assert isinstance(tonight["sessions_tonight"], int) and tonight["sessions_tonight"] >= 0
    assert isinstance(body["active_sessions"], list)


def test_overview_staff_allowed(client, api_key_header):
    token = dev_login(client, api_key_header, STAFF_A_CLERK_ID)
    resp = client.get("/api/dashboard/overview", headers={**api_key_header, **auth_header(token)})
    assert resp.status_code == 200
    body = resp.json()
    assert "tonight" in body
    assert "active_sessions" in body


def test_overview_admin_forbidden(client, api_key_header):
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    resp = client.get("/api/dashboard/overview", headers={**api_key_header, **auth_header(token)})
    assert resp.status_code == 403


def test_overview_bola_venue_isolation(client, api_key_header, fresh_table):
    table_id = fresh_table["table_id"]
    table_number = fresh_table["table_number"]

    # fresh_table belongs to Venue A; insert an active session on it
    from api.dev_fixtures import VENUE_A_ID
    session_id = _insert_session(table_id, VENUE_A_ID, started_at=_utcnow(), ended_at=None)

    try:
        # Venue B owner must NOT see the session
        token_b = dev_login(client, api_key_header, OWNER_B_CLERK_ID)
        resp_b = client.get("/api/dashboard/overview", headers={**api_key_header, **auth_header(token_b)})
        assert resp_b.status_code == 200
        table_numbers_b = [s["table_number"] for s in resp_b.json()["active_sessions"]]
        assert table_number not in table_numbers_b

        # Venue A owner MUST see the session
        token_a = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        resp_a = client.get("/api/dashboard/overview", headers={**api_key_header, **auth_header(token_a)})
        assert resp_a.status_code == 200
        table_numbers_a = [s["table_number"] for s in resp_a.json()["active_sessions"]]
        assert table_number in table_numbers_a
    finally:
        _delete_session(session_id)


def test_overview_tonight_boundary(client, api_key_header, fresh_table):
    table_id = fresh_table["table_id"]
    from api.dev_fixtures import VENUE_A_ID

    boundary = _tonight_boundary()

    # Session before boundary: should be EXCLUDED from sessions_tonight
    before_start = boundary - timedelta(hours=2)
    session_before = _insert_session(table_id, VENUE_A_ID, started_at=before_start, ended_at=_utcnow())

    # Session after boundary: should be INCLUDED in sessions_tonight
    after_start = boundary + timedelta(hours=1)
    session_after = _insert_session(table_id, VENUE_A_ID, started_at=after_start, ended_at=_utcnow())

    try:
        token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        headers = {**api_key_header, **auth_header(token)}

        resp = client.get("/api/dashboard/overview", headers=headers)
        assert resp.status_code == 200
        count_with_both = resp.json()["tonight"]["sessions_tonight"]
        assert count_with_both >= 1

        # Remove the pre-boundary session and re-fetch; count must not change
        _delete_session(session_before)
        session_before = None  # mark cleaned up

        resp2 = client.get("/api/dashboard/overview", headers=headers)
        assert resp2.status_code == 200
        count_after_delete = resp2.json()["tonight"]["sessions_tonight"]
        assert count_after_delete == count_with_both
    finally:
        if session_before:
            _delete_session(session_before)
        _delete_session(session_after)


def test_overview_active_vs_ended(client, api_key_header, fresh_table):
    table_id = fresh_table["table_id"]
    from api.dev_fixtures import VENUE_A_ID

    session_active = _insert_session(table_id, VENUE_A_ID, started_at=_utcnow(), ended_at=None)
    session_ended = _insert_session(table_id, VENUE_A_ID, started_at=_utcnow(), ended_at=_utcnow())

    try:
        token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        resp = client.get("/api/dashboard/overview", headers={**api_key_header, **auth_header(token)})
        assert resp.status_code == 200
        active_session_ids = [s["session_id"] for s in resp.json()["active_sessions"]]
        assert session_active in active_session_ids
        assert session_ended not in active_session_ids
    finally:
        _delete_session(session_active)
        _delete_session(session_ended)
