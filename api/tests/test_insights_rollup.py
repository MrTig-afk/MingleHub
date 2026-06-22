"""Proves the rollup-wired /insights endpoint returns the SAME numbers as the old
live full-scan logic. Seeds sessions across today + completed days, rolls them up,
then compares the endpoint output against a direct aggregation over the same raw
sessions for each range.
"""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg

from api.dev_fixtures import OWNER_A_CLERK_ID, VENUE_A_ID, VENUE_A_TABLE_ID
from api.services.analytics_service import recompute_daily_stats
from api.tests.conftest import dev_login


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _run(coro_fn):
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await coro_fn(conn)
        finally:
            await conn.close()
    return asyncio.run(_q())


def _insert(*, days_ago, hours_dur, total_rounds, player_count, trivia_correct=0,
            trivia_wrong=0, ended=True):
    d = _utcnow().date() - timedelta(days=days_ago)
    start = datetime(d.year, d.month, d.day, 6, 0, 0) if days_ago else _utcnow()
    end = (start + timedelta(seconds=int(hours_dur))) if ended else None
    sid = str(uuid.uuid4())

    async def _q(conn):
        await conn.execute(
            """INSERT INTO game_sessions
                 (id, venue_id, table_id, player_count, started_at, ended_at,
                  total_rounds, trivia_correct, trivia_wrong, created_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9, NOW())""",
            sid, VENUE_A_ID, VENUE_A_TABLE_ID, player_count, start, end,
            total_rounds, trivia_correct, trivia_wrong)
    _run(_q)
    return sid


def _delete(sids):
    _run(lambda c: c.execute("DELETE FROM game_sessions WHERE id = ANY($1::uuid[])", sids))


def _old_insights(range_param):
    """The ORIGINAL live computation, run directly against the DB for comparison."""
    days = {"tonight": 0, "7d": 6, "30d": 29}[range_param]

    async def _q(conn):
        tb = await conn.fetchval(
            """SELECT ((date_trunc('day',(NOW() AT TIME ZONE $1)-INTERVAL '4 hours')
                   +INTERVAL '4 hours') AT TIME ZONE $1) AT TIME ZONE 'UTC'""",
            "Australia/Melbourne")
        rs = tb - timedelta(days=days)
        totals = await conn.fetchrow(
            """SELECT COUNT(*) sessions, COALESCE(SUM(gs.total_rounds),0) rounds,
                      ROUND(AVG(gs.player_count)::numeric,1) avg_players
               FROM game_sessions gs JOIN tables t ON t.id=gs.table_id
               WHERE t.venue_id=$1 AND gs.started_at >= $2""", VENUE_A_ID, rs)
        avg_min = await conn.fetchval(
            """SELECT ROUND(AVG(EXTRACT(EPOCH FROM gs.ended_at-gs.started_at)/60.0)::numeric,1)
               FROM game_sessions gs JOIN tables t ON t.id=gs.table_id
               WHERE t.venue_id=$1 AND gs.started_at >= $2 AND gs.ended_at IS NOT NULL""",
            VENUE_A_ID, rs)
        trivia = await conn.fetchrow(
            """SELECT COALESCE(SUM(gs.trivia_correct),0) c, COALESCE(SUM(gs.trivia_wrong),0) w
               FROM game_sessions gs JOIN tables t ON t.id=gs.table_id
               WHERE t.venue_id=$1 AND gs.started_at >= $2""", VENUE_A_ID, rs)
        trend = await conn.fetch(
            """SELECT date_trunc('day',(gs.started_at AT TIME ZONE 'UTC' AT TIME ZONE $3)
                   -INTERVAL '4 hours')::date d, COUNT(*) c
               FROM game_sessions gs JOIN tables t ON t.id=gs.table_id
               WHERE t.venue_id=$1 AND gs.started_at >= $2
               GROUP BY d ORDER BY d""", VENUE_A_ID, rs, "Australia/Melbourne")
        return {
            "sessions": int(totals["sessions"]),
            "rounds": int(totals["rounds"]),
            "avg_players": float(totals["avg_players"]) if totals["avg_players"] is not None else None,
            "avg_session_minutes": float(avg_min) if avg_min is not None else None,
            "trivia_correct": int(trivia["c"]),
            "trivia_wrong": int(trivia["w"]),
            "trend": [{"date": str(r["d"]), "count": int(r["c"])} for r in trend],
        }
    return _run(_q)


def test_insights_rollup_matches_live(client, api_key_header):
    sids = [
        _insert(days_ago=0, hours_dur=1200, total_rounds=5, player_count=4,
                trivia_correct=3, trivia_wrong=1),                       # today
        _insert(days_ago=0, hours_dur=600, total_rounds=2, player_count=2),  # today
        _insert(days_ago=3, hours_dur=1500, total_rounds=6, player_count=5,
                trivia_correct=2, trivia_wrong=2),                       # closed day
        _insert(days_ago=3, hours_dur=900, total_rounds=3, player_count=3),   # same closed day
        _insert(days_ago=6, hours_dur=1800, total_rounds=8, player_count=6),  # closed day
    ]
    try:
        _run(lambda c: recompute_daily_stats(c))
        token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        for rng in ("tonight", "7d", "30d"):
            resp = client.get(f"/api/dashboard/insights?range={rng}",
                              headers={**api_key_header, **auth_header(token)})
            assert resp.status_code == 200, resp.text
            new = resp.json()
            old = _old_insights(rng)

            # Exact on integers + trend; tiny tolerance on float avg_minutes.
            assert new["totals"]["sessions"] == old["sessions"], rng
            assert new["totals"]["rounds"] == old["rounds"], rng
            assert new["totals"]["avg_players"] == old["avg_players"], rng
            assert new["trivia"]["correct"] == old["trivia_correct"], rng
            assert new["trivia"]["wrong"] == old["trivia_wrong"], rng
            assert new["trend"] == old["trend"], rng
            a, b = new["totals"]["avg_session_minutes"], old["avg_session_minutes"]
            assert (a is None and b is None) or abs(a - b) < 0.05, (rng, a, b)
    finally:
        _delete(sids)
        _run(lambda c: c.execute("DELETE FROM venue_daily_stats WHERE venue_id=$1", VENUE_A_ID))
