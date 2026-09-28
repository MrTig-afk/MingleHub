"""Tests for sweep_abandoned_sessions: sessions idle past the retap-expired
threshold are finalized before the billing rollup picks them up.

Direct-DB style (asyncio.run + asyncpg), matching test_billing.py.
All DB mutations are torn down in finally blocks.
"""
import uuid
from datetime import timedelta

from api.dev_fixtures import (
    VENUE_A_ID,
    VENUE_A_TABLE_ID,
)
from api.services.billing_service import sweep_abandoned_sessions
from api.services.session_service import RETAP_GRACE_SECONDS, RETAP_PAUSE_SECONDS
from api.tests.test_billing import _billing_cols, _delete_session, _run, _utcnow

# VENUE_A has retap_interval_minutes = 15 (default).
# Threshold: 15 * 60 + 120 + 300 = 1320 seconds (22 minutes).
THRESHOLD_SECONDS = 15 * 60 + RETAP_GRACE_SECONDS + RETAP_PAUSE_SECONDS  # 1320


def _insert_session(*, table_id, venue_id, started_at=None, last_activity_at,
                    ended_at=None, end_reason=None, total_rounds=0,
                    billable_blocks=None, active_span_seconds=None,
                    active_play_seconds=0, billing_finalized_at=None):
    session_id = str(uuid.uuid4())

    async def _q(conn):
        await conn.execute(
            """
            INSERT INTO game_sessions
                (id, venue_id, table_id, player_count, started_at, ended_at,
                 end_reason, last_activity_at, total_rounds, billable_blocks,
                 active_span_seconds, active_play_seconds, billing_finalized_at, created_at)
            VALUES ($1, $2, $3, 4, $4, $5, $6, $7, $8, $9, $10, $11, $12, NOW())
            """,
            session_id, venue_id, table_id, started_at, ended_at, end_reason,
            last_activity_at, total_rounds, billable_blocks, active_span_seconds,
            active_play_seconds, billing_finalized_at,
        )
    _run(_q)
    return session_id


def _get_end_info(session_id):
    """Return (ended_at, end_reason) for a session."""
    async def _q(conn):
        row = await conn.fetchrow(
            "SELECT ended_at, end_reason FROM game_sessions WHERE id = $1",
            session_id,
        )
        return row["ended_at"], row["end_reason"]
    return _run(_q)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_sweep_finalizes_stale_session():
    """A session idle for 1 hour (> 22-min threshold) is finalized by the sweep."""
    now = _utcnow()
    started = now - timedelta(hours=2)
    last_active = now - timedelta(hours=1)   # 3600s ago > 1320s threshold
    sid = _insert_session(
        table_id=VENUE_A_TABLE_ID, venue_id=VENUE_A_ID,
        started_at=started, last_activity_at=last_active,
        ended_at=None, total_rounds=3,
    )
    try:
        count = _run(lambda c: sweep_abandoned_sessions(c))
        assert count >= 1, f"Expected >= 1 session swept, got {count}"
        ended_at, end_reason = _get_end_info(sid)
        assert ended_at is not None, "ended_at should be set after sweep"
        assert end_reason == "retap_expired", f"end_reason should be 'retap_expired', got {end_reason!r}"
        row = _billing_cols(sid)
        assert row["billing_finalized_at"] is not None, "billing_finalized_at should be set"
        # active_span = last_activity_at - started_at = 3600s; billable_blocks = floor(3600/900) = 4
        assert row["billable_blocks"] == 4, f"Expected 4 billable blocks, got {row['billable_blocks']}"
    finally:
        _delete_session(sid)


def test_sweep_skips_fresh_session():
    """A session active 5 minutes ago (< 22-min threshold) is not touched."""
    now = _utcnow()
    sid = _insert_session(
        table_id=VENUE_A_TABLE_ID, venue_id=VENUE_A_ID,
        started_at=now - timedelta(minutes=10),
        last_activity_at=now - timedelta(minutes=5),
        ended_at=None, total_rounds=2,
    )
    try:
        _run(lambda c: sweep_abandoned_sessions(c))
        ended_at, _ = _get_end_info(sid)
        assert ended_at is None, "ended_at should still be None for a fresh session"
    finally:
        _delete_session(sid)


def test_sweep_skips_already_ended():
    """A session with ended_at already set is not overwritten by the sweep."""
    now = _utcnow()
    pre_ended = now - timedelta(minutes=30)
    sid = _insert_session(
        table_id=VENUE_A_TABLE_ID, venue_id=VENUE_A_ID,
        started_at=now - timedelta(hours=2),
        last_activity_at=now - timedelta(hours=1),
        ended_at=pre_ended, end_reason="manual",
        total_rounds=2,
    )
    try:
        _run(lambda c: sweep_abandoned_sessions(c))
        ended_at, end_reason = _get_end_info(sid)
        # Should not be overwritten
        assert end_reason == "manual", f"end_reason should still be 'manual', got {end_reason!r}"
        assert ended_at is not None, "ended_at should remain set"
    finally:
        _delete_session(sid)


def test_sweep_idempotent():
    """Running the sweep twice leaves this session's end and billing unchanged.

    Asserts on this session only: the sweep is global and the shared dev DB may
    hold other stale sessions, so a global count would be flaky."""
    now = _utcnow()
    sid = _insert_session(
        table_id=VENUE_A_TABLE_ID, venue_id=VENUE_A_ID,
        started_at=now - timedelta(hours=2),
        last_activity_at=now - timedelta(hours=1),
        ended_at=None, total_rounds=3,
    )
    try:
        _run(lambda c: sweep_abandoned_sessions(c))
        first, first_end = _billing_cols(sid), _get_end_info(sid)
        _run(lambda c: sweep_abandoned_sessions(c))
        second = _billing_cols(sid)
        assert _get_end_info(sid) == first_end, "ended_at/end_reason should not change on second sweep"
        assert first["billing_finalized_at"] == second["billing_finalized_at"], \
            "billing_finalized_at should not change on second sweep"
        assert first["billable_blocks"] == second["billable_blocks"], \
            "billable_blocks should not change on second sweep"
    finally:
        _delete_session(sid)


def test_sweep_skips_never_started_lobby():
    """A row with started_at IS NULL (lobby, never started) is not finalized."""
    now = _utcnow()
    sid = _insert_session(
        table_id=VENUE_A_TABLE_ID, venue_id=VENUE_A_ID,
        started_at=None,                                # lobby never started
        last_activity_at=now - timedelta(hours=2),
        ended_at=None, total_rounds=0,
    )
    try:
        _run(lambda c: sweep_abandoned_sessions(c))
        ended_at, _ = _get_end_info(sid)
        assert ended_at is None, "ended_at should remain None for an unstarted lobby"
    finally:
        _delete_session(sid)


def test_sweep_skips_session_just_inside_threshold():
    """A session 21 minutes idle (1260s < 1320s threshold) is not swept."""
    now = _utcnow()
    inside_seconds = THRESHOLD_SECONDS - 60  # 1260s = 21 minutes
    sid = _insert_session(
        table_id=VENUE_A_TABLE_ID, venue_id=VENUE_A_ID,
        started_at=now - timedelta(minutes=30),
        last_activity_at=now - timedelta(seconds=inside_seconds),
        ended_at=None, total_rounds=1,
    )
    try:
        _run(lambda c: sweep_abandoned_sessions(c))
        ended_at, _ = _get_end_info(sid)
        assert ended_at is None, \
            f"Session {inside_seconds}s idle (just inside {THRESHOLD_SECONDS}s threshold) should not be swept"
    finally:
        _delete_session(sid)


def test_sweep_heals_ended_but_unfinalized_session():
    """A session ended by an interrupted run (ended_at set, never finalized) is
    finalized by the next sweep instead of billing $0 forever."""
    now = _utcnow()
    sid = _insert_session(
        table_id=VENUE_A_TABLE_ID, venue_id=VENUE_A_ID,
        started_at=now - timedelta(hours=2),
        last_activity_at=now - timedelta(hours=1),
        ended_at=now - timedelta(minutes=30), end_reason="retap_expired",
        total_rounds=3, billing_finalized_at=None,
    )
    try:
        _run(lambda c: sweep_abandoned_sessions(c))
        row = _billing_cols(sid)
        assert row["billing_finalized_at"] is not None, "unfinalized session should be healed"
        assert row["billable_blocks"] == 4, f"Expected 4 billable blocks, got {row['billable_blocks']}"
    finally:
        _delete_session(sid)
