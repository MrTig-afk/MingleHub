"""Service-level tests for usage billing: per-session finalize math + the
monthly invoice rollup. Direct-DB style (asyncio.run + asyncpg), matching the
rest of the suite. All DB mutations are torn down in finally blocks.
"""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg

from api.dev_fixtures import (
    VENUE_A_ID,
    VENUE_A_TABLE_ID,
    VENUE_A_TABLE_2_ID,
    VENUE_B_ID,
    VENUE_B_TABLE_ID,
)
from api.services.billing_service import (
    cap_blocks,
    finalize_session_billing,
    recompute_invoices,
)


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


def _insert_session(*, table_id, venue_id, started_at, last_activity_at,
                    ended_at=None, total_rounds=0, billable_blocks=None,
                    active_span_seconds=None, active_play_seconds=0,
                    billing_finalized_at=None):
    session_id = str(uuid.uuid4())

    async def _q(conn):
        await conn.execute(
            """
            INSERT INTO game_sessions
                (id, venue_id, table_id, player_count, started_at, ended_at,
                 last_activity_at, total_rounds, billable_blocks,
                 active_span_seconds, active_play_seconds, billing_finalized_at, created_at)
            VALUES ($1, $2, $3, 4, $4, $5, $6, $7, $8, $9, $10, $11, NOW())
            """,
            session_id, venue_id, table_id, started_at, ended_at, last_activity_at,
            total_rounds, billable_blocks, active_span_seconds, active_play_seconds,
            billing_finalized_at,
        )
    _run(_q)
    return session_id


def _insert_round(session_id, created_at, round_number):
    async def _q(conn):
        await conn.execute(
            """INSERT INTO rounds (id, session_id, round_number, round_type, result, created_at)
               VALUES ($1, $2, $3, 'chooser', 'completed', $4)""",
            str(uuid.uuid4()), session_id, round_number, created_at,
        )
    _run(_q)


def _billing_cols(session_id):
    async def _q(conn):
        return await conn.fetchrow(
            """SELECT active_span_seconds, active_play_seconds, billable_blocks,
                      billing_finalized_at FROM game_sessions WHERE id = $1""",
            session_id,
        )
    return _run(_q)


def _delete_session(session_id):
    async def _q(conn):
        await conn.execute("DELETE FROM rounds WHERE session_id = $1", session_id)
        await conn.execute("DELETE FROM game_players WHERE session_id = $1", session_id)
        await conn.execute("DELETE FROM game_sessions WHERE id = $1", session_id)
    _run(_q)


def _venue_unit(venue_id):
    async def _q(conn):
        return await conn.fetchval("SELECT billing_unit FROM venues WHERE id = $1", venue_id)
    return _run(_q)


def _set_venue(venue_id, *, is_test=None, cap_wd=None, cap_we=None):
    async def _q(conn):
        if is_test is not None:
            await conn.execute("UPDATE venues SET is_test = $2 WHERE id = $1", venue_id, is_test)
        if cap_wd is not None:
            await conn.execute(
                "UPDATE venues SET nightly_cap_weekday = $2 WHERE id = $1", venue_id, cap_wd)
        if cap_we is not None:
            await conn.execute(
                "UPDATE venues SET nightly_cap_weekend = $2 WHERE id = $1", venue_id, cap_we)
    _run(_q)


def _venue_save(venue_id):
    async def _q(conn):
        return await conn.fetchrow(
            "SELECT is_test, nightly_cap_weekday, nightly_cap_weekend FROM venues WHERE id = $1",
            venue_id)
    return _run(_q)


def _venue_restore(venue_id, snap):
    async def _q(conn):
        await conn.execute(
            "UPDATE venues SET is_test=$2, nightly_cap_weekday=$3, nightly_cap_weekend=$4 WHERE id=$1",
            venue_id, snap["is_test"], snap["nightly_cap_weekday"], snap["nightly_cap_weekend"])
    _run(_q)


def _clear_invoices(venue_id):
    async def _q(conn):
        await conn.execute(
            "DELETE FROM invoice_line_items WHERE invoice_id IN "
            "(SELECT id FROM invoices WHERE venue_id = $1)", venue_id)
        await conn.execute("DELETE FROM invoices WHERE venue_id = $1", venue_id)
    _run(_q)


def _invoice(venue_id):
    async def _q(conn):
        inv = await conn.fetchrow(
            "SELECT id, total_amount, status FROM invoices WHERE venue_id = $1", venue_id)
        if not inv:
            return None, []
        items = await conn.fetch(
            "SELECT play_date, units_billed, amount, cap_applied FROM invoice_line_items "
            "WHERE invoice_id = $1 ORDER BY play_date", inv["id"])
        return inv, items
    return _run(_q)


# ---------------------------------------------------------------------------
# cap_blocks (pure)
# ---------------------------------------------------------------------------

def test_cap_blocks_math():
    assert cap_blocks(30, 3) == 10      # $30 / $3 = 10 blocks
    assert cap_blocks(30, 3.0) == 10
    assert cap_blocks(0, 3) == 0
    assert cap_blocks(30, 0) == 0       # guard zero unit
    assert cap_blocks(30, None) == 0


# ---------------------------------------------------------------------------
# finalize_session_billing
# ---------------------------------------------------------------------------

def test_finalize_span_and_blocks():
    """30-min span with >=1 round -> 2 blocks; span frozen exactly."""
    t0 = _utcnow()
    sid = _insert_session(
        table_id=VENUE_A_TABLE_ID, venue_id=VENUE_A_ID,
        started_at=t0, last_activity_at=t0 + timedelta(minutes=30),
        ended_at=t0 + timedelta(minutes=30), total_rounds=3)
    try:
        _run(lambda c: finalize_session_billing(c, sid))
        row = _billing_cols(sid)
        assert row["active_span_seconds"] == 1800
        assert row["billable_blocks"] == 2          # floor(1800 / 900)
        assert row["billing_finalized_at"] is not None
    finally:
        _delete_session(sid)


def test_finalize_gate_no_rounds_zero_blocks():
    """A 30-min session that never played a round bills 0 blocks (lobby-only)."""
    t0 = _utcnow()
    sid = _insert_session(
        table_id=VENUE_A_TABLE_ID, venue_id=VENUE_A_ID,
        started_at=t0, last_activity_at=t0 + timedelta(minutes=30),
        ended_at=t0 + timedelta(minutes=30), total_rounds=0)
    try:
        _run(lambda c: finalize_session_billing(c, sid))
        row = _billing_cols(sid)
        assert row["active_span_seconds"] == 1800   # span still recorded
        assert row["billable_blocks"] == 0          # but nothing billed
    finally:
        _delete_session(sid)


def test_finalize_active_play_excludes_idle_gap():
    """active_play_seconds sums only gaps <= 2 min; a 9-min idle gap is excluded."""
    t0 = _utcnow()
    sid = _insert_session(
        table_id=VENUE_A_TABLE_ID, venue_id=VENUE_A_ID,
        started_at=t0, last_activity_at=t0 + timedelta(seconds=960), total_rounds=4)
    # events: 0, 30, 60, 600 (9-min gap before this), 630, then last_activity 960
    _insert_round(sid, t0 + timedelta(seconds=30), 1)
    _insert_round(sid, t0 + timedelta(seconds=60), 2)
    _insert_round(sid, t0 + timedelta(seconds=600), 3)
    _insert_round(sid, t0 + timedelta(seconds=630), 4)
    try:
        _run(lambda c: finalize_session_billing(c, sid))
        row = _billing_cols(sid)
        # counted gaps: 30 + 30 + 30 = 90  (540s and 330s gaps excluded as idle)
        assert row["active_play_seconds"] == 90
        assert row["active_span_seconds"] == 960
        assert row["billable_blocks"] == 1          # floor(960 / 900)
    finally:
        _delete_session(sid)


def test_finalize_idempotent():
    """A second finalize is a no-op (frozen values don't move)."""
    t0 = _utcnow()
    sid = _insert_session(
        table_id=VENUE_A_TABLE_ID, venue_id=VENUE_A_ID,
        started_at=t0, last_activity_at=t0 + timedelta(minutes=30), total_rounds=3)
    try:
        _run(lambda c: finalize_session_billing(c, sid))
        first = _billing_cols(sid)
        _run(lambda c: finalize_session_billing(c, sid))   # again
        second = _billing_cols(sid)
        assert first["billable_blocks"] == second["billable_blocks"] == 2
        assert first["billing_finalized_at"] == second["billing_finalized_at"]
    finally:
        _delete_session(sid)


# ---------------------------------------------------------------------------
# recompute_invoices
# ---------------------------------------------------------------------------

def test_recompute_creates_invoice_and_line_item():
    snap = _venue_save(VENUE_A_ID)
    unit = float(_venue_unit(VENUE_A_ID))
    _clear_invoices(VENUE_A_ID)
    _set_venue(VENUE_A_ID, is_test=False, cap_wd=10000, cap_we=10000)  # high cap
    sid = _insert_session(
        table_id=VENUE_A_TABLE_ID, venue_id=VENUE_A_ID,
        started_at=_utcnow(), last_activity_at=_utcnow(), ended_at=_utcnow(),
        total_rounds=5, billable_blocks=4, active_span_seconds=3600)
    try:
        _run(lambda c: recompute_invoices(c))
        inv, items = _invoice(VENUE_A_ID)
        assert inv is not None
        assert len(items) == 1
        assert items[0]["units_billed"] == 4
        assert items[0]["cap_applied"] is False
        assert abs(float(inv["total_amount"]) - unit * 4) < 0.001
    finally:
        _delete_session(sid)
        _clear_invoices(VENUE_A_ID)
        _venue_restore(VENUE_A_ID, snap)


def test_recompute_caps_per_table_night():
    snap = _venue_save(VENUE_A_ID)
    unit = float(_venue_unit(VENUE_A_ID))
    _clear_invoices(VENUE_A_ID)
    # cap of 3*unit dollars => 3 blocks/night, both day types.
    _set_venue(VENUE_A_ID, is_test=False, cap_wd=unit * 3, cap_we=unit * 3)
    sid = _insert_session(
        table_id=VENUE_A_TABLE_ID, venue_id=VENUE_A_ID,
        started_at=_utcnow(), last_activity_at=_utcnow(), ended_at=_utcnow(),
        total_rounds=12, billable_blocks=10, active_span_seconds=9000)
    try:
        _run(lambda c: recompute_invoices(c))
        inv, items = _invoice(VENUE_A_ID)
        assert len(items) == 1
        assert items[0]["units_billed"] == 3            # capped from 10
        assert items[0]["cap_applied"] is True
        assert abs(float(inv["total_amount"]) - unit * 3) < 0.001
    finally:
        _delete_session(sid)
        _clear_invoices(VENUE_A_ID)
        _venue_restore(VENUE_A_ID, snap)


def test_recompute_excludes_test_venues():
    snap = _venue_save(VENUE_B_ID)
    _clear_invoices(VENUE_B_ID)
    _set_venue(VENUE_B_ID, is_test=True)
    sid = _insert_session(
        table_id=VENUE_B_TABLE_ID, venue_id=VENUE_B_ID,
        started_at=_utcnow(), last_activity_at=_utcnow(), ended_at=_utcnow(),
        total_rounds=5, billable_blocks=6, active_span_seconds=5400)
    try:
        _run(lambda c: recompute_invoices(c))
        inv, _ = _invoice(VENUE_B_ID)
        assert inv is None      # is_test venue is never invoiced
    finally:
        _delete_session(sid)
        _clear_invoices(VENUE_B_ID)
        _venue_restore(VENUE_B_ID, snap)


def test_recompute_idempotent():
    snap = _venue_save(VENUE_A_ID)
    _clear_invoices(VENUE_A_ID)
    _set_venue(VENUE_A_ID, is_test=False, cap_wd=10000, cap_we=10000)
    sid = _insert_session(
        table_id=VENUE_A_TABLE_ID, venue_id=VENUE_A_ID,
        started_at=_utcnow(), last_activity_at=_utcnow(), ended_at=_utcnow(),
        total_rounds=5, billable_blocks=4, active_span_seconds=3600)
    try:
        _run(lambda c: recompute_invoices(c))
        _run(lambda c: recompute_invoices(c))   # twice
        inv, items = _invoice(VENUE_A_ID)
        assert len(items) == 1                  # no duplicate line items
        assert items[0]["units_billed"] == 4
    finally:
        _delete_session(sid)
        _clear_invoices(VENUE_A_ID)
        _venue_restore(VENUE_A_ID, snap)


def test_recompute_skips_paid_invoice():
    snap = _venue_save(VENUE_A_ID)
    _clear_invoices(VENUE_A_ID)
    _set_venue(VENUE_A_ID, is_test=False, cap_wd=10000, cap_we=10000)
    sid = _insert_session(
        table_id=VENUE_A_TABLE_ID, venue_id=VENUE_A_ID,
        started_at=_utcnow(), last_activity_at=_utcnow(), ended_at=_utcnow(),
        total_rounds=5, billable_blocks=4, active_span_seconds=3600)
    sid2 = None
    try:
        _run(lambda c: recompute_invoices(c))
        inv, _ = _invoice(VENUE_A_ID)
        paid_total = float(inv["total_amount"])
        _run(lambda c: c.execute(
            "UPDATE invoices SET status='paid' WHERE id=$1", inv["id"]))
        # Add more billable play, then recompute — a paid invoice must NOT change.
        sid2 = _insert_session(
            table_id=VENUE_A_TABLE_ID, venue_id=VENUE_A_ID,
            started_at=_utcnow(), last_activity_at=_utcnow(), ended_at=_utcnow(),
            total_rounds=5, billable_blocks=9, active_span_seconds=8100)
        _run(lambda c: recompute_invoices(c))
        inv2, _ = _invoice(VENUE_A_ID)
        assert inv2["status"] == "paid"
        assert abs(float(inv2["total_amount"]) - paid_total) < 0.001
    finally:
        _delete_session(sid)
        if sid2:
            _delete_session(sid2)
        _clear_invoices(VENUE_A_ID)
        _venue_restore(VENUE_A_ID, snap)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def _date_in_month(weekday):
    """A date in the current calendar month falling on the given weekday
    (date.weekday(): Mon=0 .. Sun=6). 06:00 UTC keeps it the same Melbourne
    local day after the 4am-boundary shift, so the night's DOW is stable."""
    first = _utcnow().date().replace(day=1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset)


def _at_six_utc(d):
    return datetime(d.year, d.month, d.day, 6, 0, 0)


def test_finalize_span_excludes_idle_tail():
    """active_span stops at last_activity_at, NOT ended_at — a session active for
    20 min then auto-ended 20 min later bills on the 20, not 40."""
    t0 = _utcnow()
    sid = _insert_session(
        table_id=VENUE_A_TABLE_ID, venue_id=VENUE_A_ID,
        started_at=t0, last_activity_at=t0 + timedelta(minutes=20),
        ended_at=t0 + timedelta(minutes=40), total_rounds=2)
    try:
        _run(lambda c: finalize_session_billing(c, sid))
        row = _billing_cols(sid)
        assert row["active_span_seconds"] == 1200      # 20 min, not 40
        assert row["billable_blocks"] == 1             # floor(1200/900), not 2
    finally:
        _delete_session(sid)


def test_recompute_multi_night_multi_table():
    """Line items roll up per (table, play-date): 2 tables across 2 nights -> 3
    distinct line items; invoice total sums them."""
    snap = _venue_save(VENUE_A_ID)
    unit = float(_venue_unit(VENUE_A_ID))
    _clear_invoices(VENUE_A_ID)
    _set_venue(VENUE_A_ID, is_test=False, cap_wd=10000, cap_we=10000)
    wed, sat = _date_in_month(2), _date_in_month(5)
    sids = [
        _insert_session(table_id=VENUE_A_TABLE_ID, venue_id=VENUE_A_ID,
                        started_at=_at_six_utc(wed), last_activity_at=_at_six_utc(wed),
                        ended_at=_at_six_utc(wed), total_rounds=5, billable_blocks=3),
        _insert_session(table_id=VENUE_A_TABLE_ID, venue_id=VENUE_A_ID,
                        started_at=_at_six_utc(sat), last_activity_at=_at_six_utc(sat),
                        ended_at=_at_six_utc(sat), total_rounds=5, billable_blocks=2),
        _insert_session(table_id=VENUE_A_TABLE_2_ID, venue_id=VENUE_A_ID,
                        started_at=_at_six_utc(wed), last_activity_at=_at_six_utc(wed),
                        ended_at=_at_six_utc(wed), total_rounds=5, billable_blocks=4),
    ]
    try:
        _run(lambda c: recompute_invoices(c))
        inv, items = _invoice(VENUE_A_ID)
        assert len(items) == 3                          # (t1,wed) (t1,sat) (t2,wed)
        assert abs(float(inv["total_amount"]) - unit * (3 + 2 + 4)) < 0.001
    finally:
        for s in sids:
            _delete_session(s)
        _clear_invoices(VENUE_A_ID)
        _venue_restore(VENUE_A_ID, snap)


def test_recompute_cap_selected_by_day_of_week():
    """The weekday cap applies to weekday nights, the weekend cap to weekend
    nights — same blocks, different outcome by DOW."""
    snap = _venue_save(VENUE_A_ID)
    unit = float(_venue_unit(VENUE_A_ID))
    _clear_invoices(VENUE_A_ID)
    # Weekday cap = 2 blocks; weekend cap = huge.
    _set_venue(VENUE_A_ID, is_test=False, cap_wd=unit * 2, cap_we=unit * 1000)
    wed, sat = _date_in_month(2), _date_in_month(5)
    sids = [
        _insert_session(table_id=VENUE_A_TABLE_ID, venue_id=VENUE_A_ID,
                        started_at=_at_six_utc(wed), last_activity_at=_at_six_utc(wed),
                        ended_at=_at_six_utc(wed), total_rounds=12, billable_blocks=10),
        _insert_session(table_id=VENUE_A_TABLE_2_ID, venue_id=VENUE_A_ID,
                        started_at=_at_six_utc(sat), last_activity_at=_at_six_utc(sat),
                        ended_at=_at_six_utc(sat), total_rounds=12, billable_blocks=10),
    ]
    try:
        _run(lambda c: recompute_invoices(c))
        _, items = _invoice(VENUE_A_ID)
        by_date = {i["play_date"]: i for i in items}
        assert by_date[wed]["units_billed"] == 2 and by_date[wed]["cap_applied"] is True
        assert by_date[sat]["units_billed"] == 10 and by_date[sat]["cap_applied"] is False
    finally:
        for s in sids:
            _delete_session(s)
        _clear_invoices(VENUE_A_ID)
        _venue_restore(VENUE_A_ID, snap)
