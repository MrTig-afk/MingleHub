"""Tests for sweep_abandoned_sessions: sessions idle past the retap-expired
threshold are finalized before the billing rollup picks them up.

Direct-DB style (asyncio.run + asyncpg), matching test_billing.py. The sweep is
GLOBAL, so every test runs on one connection inside a transaction that is always
rolled back: the sweep never commits changes to other rows in the shared dev DB.
"""
import uuid
from datetime import timedelta

from api.dev_fixtures import (
    VENUE_A_ID,
    VENUE_A_TABLE_ID,
)
from api.services.billing_service import sweep_abandoned_sessions
from api.services.lobby_service import _check_phone_session_resume
from api.services.session_service import RETAP_GRACE_SECONDS, RETAP_PAUSE_SECONDS
from api.tests.test_billing import _run, _utcnow

# VENUE_A has retap_interval_minutes = 15 (default).
# Threshold: 15 * 60 + 120 + 300 = 1320 seconds (22 minutes).
THRESHOLD_SECONDS = 15 * 60 + RETAP_GRACE_SECONDS + RETAP_PAUSE_SECONDS  # 1320


def _in_rollback(body):
    """Run body(conn) on one connection inside a transaction that is always rolled back."""
    async def _q(conn):
        tr = conn.transaction()
        await tr.start()
        try:
            return await body(conn)
        finally:
            await tr.rollback()
    return _run(_q)


async def _insert(conn, *, started_at=None, last_activity_at, ended_at=None,
                  end_reason=None, total_rounds=0, billing_finalized_at=None,
                  origin_phone_id=None):
    session_id = str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO game_sessions
            (id, venue_id, table_id, player_count, started_at, ended_at, end_reason,
             last_activity_at, total_rounds, billing_finalized_at, origin_phone_id, created_at)
        VALUES ($1, $2, $3, 4, $4, $5, $6, $7, $8, $9, $10, NOW())
        """,
        session_id, VENUE_A_ID, VENUE_A_TABLE_ID, started_at, ended_at, end_reason,
        last_activity_at, total_rounds, billing_finalized_at, origin_phone_id,
    )
    return session_id


async def _row(conn, session_id):
    return await conn.fetchrow(
        """SELECT ended_at, end_reason, billable_blocks, billing_finalized_at
           FROM game_sessions WHERE id = $1""",
        session_id,
    )


def test_sweep_finalizes_stale_session():
    """A session idle for 1 hour (> 22-min threshold) is ended and billed."""
    async def body(conn):
        now = _utcnow()
        sid = await _insert(conn, started_at=now - timedelta(hours=2),
                            last_activity_at=now - timedelta(hours=1), total_rounds=3)
        count = await sweep_abandoned_sessions(conn)
        row = await _row(conn, sid)
        assert count >= 1, f"Expected >= 1 session swept, got {count}"
        assert row["ended_at"] is not None, "ended_at should be set after sweep"
        assert row["end_reason"] == "retap_expired", row["end_reason"]
        assert row["billing_finalized_at"] is not None, "billing_finalized_at should be set"
        # active_span = last_activity_at - started_at = 3600s; floor(3600/900) = 4 blocks
        assert row["billable_blocks"] == 4, row["billable_blocks"]
    _in_rollback(body)


def test_sweep_skips_fresh_session():
    """A session active 5 minutes ago (< 22-min threshold) is not touched."""
    async def body(conn):
        now = _utcnow()
        sid = await _insert(conn, started_at=now - timedelta(minutes=10),
                            last_activity_at=now - timedelta(minutes=5), total_rounds=2)
        await sweep_abandoned_sessions(conn)
        assert (await _row(conn, sid))["ended_at"] is None, "fresh session must stay open"
    _in_rollback(body)


def test_sweep_skips_already_ended():
    """A session that already has ended_at keeps its original end time and reason."""
    async def body(conn):
        now = _utcnow()
        pre_ended = now - timedelta(minutes=30)
        sid = await _insert(conn, started_at=now - timedelta(hours=2),
                            last_activity_at=now - timedelta(hours=1),
                            ended_at=pre_ended, end_reason="manual", total_rounds=2)
        await sweep_abandoned_sessions(conn)
        row = await _row(conn, sid)
        assert row["end_reason"] == "manual", row["end_reason"]
        assert row["ended_at"] == pre_ended, f"ended_at changed: {row['ended_at']} != {pre_ended}"
    _in_rollback(body)


def test_sweep_idempotent():
    """Running the sweep twice leaves this session's end and billing unchanged."""
    async def body(conn):
        now = _utcnow()
        sid = await _insert(conn, started_at=now - timedelta(hours=2),
                            last_activity_at=now - timedelta(hours=1), total_rounds=3)
        await sweep_abandoned_sessions(conn)
        first = await _row(conn, sid)
        await sweep_abandoned_sessions(conn)
        assert await _row(conn, sid) == first, "second sweep must not change the session"
    _in_rollback(body)


def test_sweep_skips_never_started_lobby():
    """A row with started_at IS NULL (lobby, never started) is not ended."""
    async def body(conn):
        now = _utcnow()
        sid = await _insert(conn, started_at=None,
                            last_activity_at=now - timedelta(hours=2), total_rounds=0)
        await sweep_abandoned_sessions(conn)
        assert (await _row(conn, sid))["ended_at"] is None, "unstarted lobby must stay open"
    _in_rollback(body)


def test_sweep_skips_session_just_inside_threshold():
    """A session 21 minutes idle (1260s < 1320s threshold) is not swept."""
    async def body(conn):
        now = _utcnow()
        inside_seconds = THRESHOLD_SECONDS - 60
        sid = await _insert(conn, started_at=now - timedelta(minutes=30),
                            last_activity_at=now - timedelta(seconds=inside_seconds),
                            total_rounds=1)
        await sweep_abandoned_sessions(conn)
        assert (await _row(conn, sid))["ended_at"] is None, \
            f"{inside_seconds}s idle is inside the {THRESHOLD_SECONDS}s threshold"
    _in_rollback(body)


def test_sweep_heals_ended_but_unfinalized_session():
    """A session ended by an interrupted run (ended, never finalized) is finalized
    by the next sweep instead of billing $0 forever."""
    async def body(conn):
        now = _utcnow()
        sid = await _insert(conn, started_at=now - timedelta(hours=2),
                            last_activity_at=now - timedelta(hours=1),
                            ended_at=now - timedelta(minutes=30), end_reason="retap_expired",
                            total_rounds=3)
        await sweep_abandoned_sessions(conn)
        row = await _row(conn, sid)
        assert row["billing_finalized_at"] is not None, "unfinalized session should be healed"
        assert row["billable_blocks"] == 4, row["billable_blocks"]
    _in_rollback(body)


class _SweepWinsRace:
    """Connection proxy: right after the resume check reads the open session, the
    sweep ends it (the race), then the resume path's own update runs."""

    def __init__(self, conn):
        self._conn = conn

    async def fetchrow(self, *args):
        row = await self._conn.fetchrow(*args)
        if row is not None and "id" in row.keys():
            await self._conn.execute(
                "UPDATE game_sessions SET ended_at = NOW(), end_reason = 'retap_expired' WHERE id = $1",
                row["id"],
            )
        return row

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_resume_after_sweep_ended_session_returns_recap():
    """A re-tap that read the session as open, but lost the race to the sweep,
    must route to the recap, never 'resume' into an ended game."""
    async def body(conn):
        now = _utcnow()
        phone = str(uuid.uuid4())
        sid = await _insert(conn, started_at=now - timedelta(minutes=20),
                            last_activity_at=now - timedelta(minutes=1), total_rounds=2,
                            origin_phone_id=phone)
        result = await _check_phone_session_resume(_SweepWinsRace(conn), VENUE_A_TABLE_ID, phone)
        assert result == {"phase": "recap", "session_id": sid}, result
    _in_rollback(body)
