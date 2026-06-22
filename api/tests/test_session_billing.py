"""Tests for GET /dashboard/session-billing/{id} — the per-session
rounds->block->cost breakdown."""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg

from api.dev_fixtures import (
    OWNER_A_CLERK_ID,
    OWNER_B_CLERK_ID,
    STAFF_A_CLERK_ID,
    VENUE_A_ID,
    VENUE_A_TABLE_ID,
)
from api.tests.conftest import dev_login


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _run(fn):
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await fn(conn)
        finally:
            await conn.close()
    return asyncio.run(_q())


def _make_session(*, finalized, span_seconds, blocks, total_rounds, type_counts):
    sid = str(uuid.uuid4())
    now = _utcnow()
    started = now - timedelta(seconds=span_seconds)

    async def _q(conn):
        await conn.execute(
            """INSERT INTO game_sessions
                 (id, venue_id, table_id, player_count, started_at, ended_at,
                  last_activity_at, total_rounds, billable_blocks, active_span_seconds,
                  active_play_seconds, billing_finalized_at, created_at)
               VALUES ($1,$2,$3,4,$4,$5,$6,$7,$8,$9,$10,$11, NOW())""",
            sid, VENUE_A_ID, VENUE_A_TABLE_ID, started,
            now if finalized else None, now, total_rounds,
            blocks if finalized else None,
            span_seconds if finalized else None,
            int(span_seconds * 0.9) if finalized else 0,
            now if finalized else None)
        n = 0
        for rtype, count in type_counts.items():
            for _ in range(count):
                n += 1
                await conn.execute(
                    """INSERT INTO rounds (id, session_id, round_number, round_type, result, created_at)
                       VALUES ($1,$2,$3,$4,'completed', NOW())""",
                    str(uuid.uuid4()), sid, n, rtype)
    _run(_q)
    return sid


def _delete(sid):
    async def _q(conn):
        await conn.execute("DELETE FROM rounds WHERE session_id = $1", sid)
        await conn.execute("DELETE FROM game_sessions WHERE id = $1", sid)
    _run(_q)


def test_finalized_session_breakdown(client, api_key_header):
    # 29 min span, 1 block, 9 trivia rounds
    sid = _make_session(finalized=True, span_seconds=1740, blocks=1, total_rounds=9,
                        type_counts={"trivia": 9})
    try:
        token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        resp = client.get(f"/api/dashboard/session-billing/{sid}",
                          headers={**api_key_header, **auth_header(token)})
        assert resp.status_code == 200
        b = resp.json()
        assert b["finalized"] is True
        assert b["rounds_by_type"]["trivia"] == 9
        assert b["billable_blocks"] == 1
        assert float(b["amount"]) > 0
        assert b["active_span_minutes"] == 29.0
        assert b["block_minutes"] == 15
    finally:
        _delete(sid)


def test_live_session_provisional_blocks(client, api_key_header):
    # 16 min live span, >=1 round -> provisional 1 block; not finalized.
    sid = _make_session(finalized=False, span_seconds=16 * 60, blocks=0, total_rounds=4,
                        type_counts={"roulette": 4})
    try:
        token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        resp = client.get(f"/api/dashboard/session-billing/{sid}",
                          headers={**api_key_header, **auth_header(token)})
        assert resp.status_code == 200
        b = resp.json()
        assert b["finalized"] is False
        assert b["billable_blocks"] == 1            # floor(16/15)
        assert b["rounds_by_type"]["roulette"] == 4
        assert 0 < b["minutes_to_next_block"] <= 15
    finally:
        _delete(sid)


def test_no_rounds_zero_blocks(client, api_key_header):
    # Span over 15 min but no rounds played -> 0 blocks (lobby-only gate).
    sid = _make_session(finalized=False, span_seconds=20 * 60, blocks=0, total_rounds=0,
                        type_counts={})
    try:
        token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        resp = client.get(f"/api/dashboard/session-billing/{sid}",
                          headers={**api_key_header, **auth_header(token)})
        assert resp.json()["billable_blocks"] == 0
    finally:
        _delete(sid)


def test_session_billing_bola_404(client, api_key_header):
    sid = _make_session(finalized=True, span_seconds=900, blocks=1, total_rounds=3,
                        type_counts={"chooser": 3})
    try:
        token_b = dev_login(client, api_key_header, OWNER_B_CLERK_ID)  # different venue
        resp = client.get(f"/api/dashboard/session-billing/{sid}",
                          headers={**api_key_header, **auth_header(token_b)})
        assert resp.status_code == 404
    finally:
        _delete(sid)


def test_session_billing_staff_403(client, api_key_header):
    sid = _make_session(finalized=True, span_seconds=900, blocks=1, total_rounds=3,
                        type_counts={"chooser": 3})
    try:
        token = dev_login(client, api_key_header, STAFF_A_CLERK_ID)
        resp = client.get(f"/api/dashboard/session-billing/{sid}",
                          headers={**api_key_header, **auth_header(token)})
        assert resp.status_code == 403
    finally:
        _delete(sid)
