"""Tests for the analytics rollup (venue_daily_stats). Direct-DB style. The key
test is equivalence: the rolled-up summary must equal a live aggregation over the
same raw sessions, so reading from the rollup never changes a displayed number.
"""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg

from api.dev_fixtures import VENUE_A_ID, VENUE_A_TABLE_ID
from api.services.analytics_service import recompute_daily_stats


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


def _days_ago_at_six(n):
    d = (_utcnow().date() - timedelta(days=n))
    return datetime(d.year, d.month, d.day, 6, 0, 0), d


def _insert_session(*, started_at, ended_at, total_rounds=0, player_count=4,
                    trivia_correct=0, trivia_wrong=0, cards_completed=0,
                    cards_skipped=0, total_score=0, venue_id=VENUE_A_ID,
                    table_id=VENUE_A_TABLE_ID):
    sid = str(uuid.uuid4())

    async def _q(conn):
        await conn.execute(
            """
            INSERT INTO game_sessions
                (id, venue_id, table_id, player_count, started_at, ended_at,
                 total_rounds, trivia_correct, trivia_wrong, cards_completed,
                 cards_skipped, total_score, created_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12, NOW())
            """,
            sid, venue_id, table_id, player_count, started_at, ended_at,
            total_rounds, trivia_correct, trivia_wrong, cards_completed,
            cards_skipped, total_score,
        )
    _run(_q)
    return sid


def _delete_sessions(sids):
    async def _q(conn):
        await conn.execute("DELETE FROM game_sessions WHERE id = ANY($1::uuid[])", sids)
    _run(_q)


def _clear_stats(venue_id=VENUE_A_ID):
    _run(lambda c: c.execute("DELETE FROM venue_daily_stats WHERE venue_id = $1", venue_id))


def _stat_row(stat_date, venue_id=VENUE_A_ID):
    async def _q(conn):
        return await conn.fetchrow(
            "SELECT * FROM venue_daily_stats WHERE venue_id = $1 AND stat_date = $2",
            venue_id, stat_date)
    return _run(_q)


# ---------------------------------------------------------------------------

def test_recompute_aggregates_per_day():
    _clear_stats()
    start, day = _days_ago_at_six(3)
    sids = [
        _insert_session(started_at=start, ended_at=start + timedelta(seconds=1200),
                        total_rounds=5, player_count=4, trivia_correct=3, trivia_wrong=1,
                        cards_completed=2, cards_skipped=1, total_score=30),
        _insert_session(started_at=start, ended_at=start + timedelta(seconds=600),
                        total_rounds=3, player_count=2, trivia_correct=1, trivia_wrong=2,
                        cards_completed=1, cards_skipped=0, total_score=10),
    ]
    try:
        _run(lambda c: recompute_daily_stats(c))
        row = _stat_row(day)
        assert row is not None
        assert row["session_count"] == 2
        assert row["ended_count"] == 2
        assert row["total_rounds"] == 8
        assert row["sum_player_count"] == 6
        assert row["sum_duration_seconds"] == 1800
        assert row["trivia_correct"] == 4
        assert row["trivia_wrong"] == 3
        assert row["cards_completed"] == 3
        assert row["cards_skipped"] == 1
        assert row["total_score"] == 40
    finally:
        _delete_sessions(sids)
        _clear_stats()


def test_recompute_idempotent():
    _clear_stats()
    start, day = _days_ago_at_six(4)
    sids = [_insert_session(started_at=start, ended_at=start + timedelta(seconds=900),
                            total_rounds=4)]
    try:
        _run(lambda c: recompute_daily_stats(c))
        _run(lambda c: recompute_daily_stats(c))   # twice
        rows = _run(lambda c: c.fetch(
            "SELECT * FROM venue_daily_stats WHERE venue_id=$1 AND stat_date=$2",
            VENUE_A_ID, day))
        assert len(rows) == 1                       # one row per (venue, day)
        assert rows[0]["total_rounds"] == 4
    finally:
        _delete_sessions(sids)
        _clear_stats()


def test_recompute_window_excludes_old_days():
    _clear_stats()
    old_start, old_day = _days_ago_at_six(40)       # outside the 35-day window
    new_start, new_day = _days_ago_at_six(2)
    sids = [
        _insert_session(started_at=old_start, ended_at=old_start + timedelta(seconds=600),
                        total_rounds=2),
        _insert_session(started_at=new_start, ended_at=new_start + timedelta(seconds=600),
                        total_rounds=2),
    ]
    try:
        _run(lambda c: recompute_daily_stats(c))    # default 35-day window
        assert _stat_row(old_day) is None           # 40 days ago: excluded
        assert _stat_row(new_day) is not None        # 2 days ago: included
    finally:
        _delete_sessions(sids)
        _clear_stats()


def test_rollup_equivalent_to_live_aggregation():
    """The rolled-up totals must equal a direct aggregation over the same raw
    sessions — proving a dashboard can read the rollup without changing numbers."""
    _clear_stats()
    s1, _ = _days_ago_at_six(2)
    s2, _ = _days_ago_at_six(6)
    sids = [
        _insert_session(started_at=s1, ended_at=s1 + timedelta(seconds=1500),
                        total_rounds=7, player_count=5, total_score=50),
        _insert_session(started_at=s1, ended_at=s1 + timedelta(seconds=300),
                        total_rounds=1, player_count=2, total_score=5),
        _insert_session(started_at=s2, ended_at=s2 + timedelta(seconds=900),
                        total_rounds=4, player_count=3, total_score=20),
    ]
    try:
        _run(lambda c: recompute_daily_stats(c))

        async def _both(conn):
            window_start = await conn.fetchval(
                """SELECT ((date_trunc('day',(NOW() AT TIME ZONE 'Australia/Melbourne')
                       - INTERVAL '4 hours') - INTERVAL '35 days' + INTERVAL '4 hours')
                       AT TIME ZONE 'Australia/Melbourne') AT TIME ZONE 'UTC'""")
            live = await conn.fetchrow(
                """SELECT COUNT(*) sc, COALESCE(SUM(total_rounds),0) tr,
                          COALESCE(SUM(player_count),0) pc, COALESCE(SUM(total_score),0) ts
                   FROM game_sessions
                   WHERE venue_id=$1 AND started_at >= $2 AND started_at IS NOT NULL""",
                VENUE_A_ID, window_start)
            rolled = await conn.fetchrow(
                """SELECT COALESCE(SUM(session_count),0) sc, COALESCE(SUM(total_rounds),0) tr,
                          COALESCE(SUM(sum_player_count),0) pc, COALESCE(SUM(total_score),0) ts
                   FROM venue_daily_stats WHERE venue_id=$1""",
                VENUE_A_ID)
            return live, rolled
        live, rolled = _run(_both)
        assert rolled["sc"] == live["sc"]
        assert rolled["tr"] == live["tr"]
        assert rolled["pc"] == live["pc"]
        assert rolled["ts"] == live["ts"]
    finally:
        _delete_sessions(sids)
        _clear_stats()
