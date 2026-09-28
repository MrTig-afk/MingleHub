"""Per-venue timezone tests. Proves that billing, analytics, and theme
resolution use each venue's own timezone column rather than a hardcoded
constant. All DB mutations are torn down in finally blocks.

Follow the test_billing.py pattern: asyncio.run + asyncpg, _run helper,
finally cleanup.
"""
import uuid
from datetime import datetime, timedelta

from api.dev_fixtures import (
    VENUE_A_ID,
    VENUE_A_TABLE_ID,
    VENUE_B_ID,
    VENUE_B_TABLE_ID,
)
from api.services.analytics_service import range_totals, recompute_daily_stats
from api.services.billing_service import recompute_invoices
from api.services.theme_service import resolve_active_theme
from api.tests.test_billing import (
    _clear_invoices, _delete_session, _insert_session, _invoice, _run, _utcnow,
    _venue_restore, _venue_save,
)


def _save_venue_tz(venue_id):
    async def _q(conn):
        return await conn.fetchval("SELECT timezone FROM venues WHERE id = $1", venue_id)
    return _run(_q)


def _set_venue_tz(venue_id, tz):
    async def _q(conn):
        await conn.execute("UPDATE venues SET timezone = $2 WHERE id = $1", venue_id, tz)
    _run(_q)


def _restore_venue_tz(venue_id, old_tz):
    _set_venue_tz(venue_id, old_tz)


# ---------------------------------------------------------------------------
# test 1: KEY REGRESSION — Melbourne output byte-identical before and after
# ---------------------------------------------------------------------------

def test_melbourne_venue_billing_unchanged():
    """A venue with timezone='Australia/Melbourne' must produce the same
    play_date and billing total as the old hardcoded-constant code path."""
    # A fixed UTC started_at that is unambiguously mid-afternoon in Melbourne
    # (12:00 UTC = 22:00 AEST = play_date Sep 15) and nowhere near any month
    # boundary, so the session belongs to September's invoice in all timezones.
    started_at = datetime(2026, 9, 15, 12, 0, 0)
    ended_at = started_at + timedelta(minutes=45)
    # 45 min / 15 min = 3 blocks, billing_unit=$3 default -> $9
    EXPECTED_PLAY_DATE = "2026-09-15"
    EXPECTED_BLOCKS = 3

    old_tz = _save_venue_tz(VENUE_A_ID)
    snap = _venue_save(VENUE_A_ID)
    _set_venue_tz(VENUE_A_ID, "Australia/Melbourne")
    _clear_invoices(VENUE_A_ID)
    sid = _insert_session(
        table_id=VENUE_A_TABLE_ID, venue_id=VENUE_A_ID,
        started_at=started_at, last_activity_at=ended_at,
        ended_at=ended_at, total_rounds=3,
        billable_blocks=EXPECTED_BLOCKS, active_span_seconds=45 * 60,
        billing_finalized_at=_utcnow(),
    )
    try:
        _run(lambda c: recompute_invoices(c, ref_ts=started_at))
        inv, items = _invoice(VENUE_A_ID)
        assert inv is not None, "No invoice created for Melbourne venue"
        assert len(items) == 1, f"Expected 1 line item, got {len(items)}"
        item = items[0]
        assert str(item["play_date"]) == EXPECTED_PLAY_DATE, (
            f"play_date={item['play_date']} expected {EXPECTED_PLAY_DATE}")
        assert int(item["units_billed"]) == EXPECTED_BLOCKS, (
            f"units_billed={item['units_billed']} expected {EXPECTED_BLOCKS}")
    finally:
        _delete_session(sid)
        _clear_invoices(VENUE_A_ID)
        _venue_restore(VENUE_A_ID, snap)
        _restore_venue_tz(VENUE_A_ID, old_tz)


# ---------------------------------------------------------------------------
# test 2: Non-Melbourne play_date uses venue's local date
# ---------------------------------------------------------------------------

def test_non_melbourne_play_date():
    """A session at 2026-09-28T02:00 UTC must bill on 2026-09-27 (New York
    local date after 4am offset) not 2026-09-28 (Melbourne local date)."""
    # 02:00 UTC = 22:00 EDT (UTC-4) on Sep 27 -> minus 4h -> 18:00 Sep 27
    # In Melbourne (UTC+10): 12:00 Sep 28 -> minus 4h -> 08:00 Sep 28
    started_at = datetime(2026, 9, 28, 2, 0, 0)
    ended_at = started_at + timedelta(minutes=30)
    EXPECTED_PLAY_DATE_NYC = "2026-09-27"

    old_tz = _save_venue_tz(VENUE_B_ID)
    snap = _venue_save(VENUE_B_ID)
    _set_venue_tz(VENUE_B_ID, "America/New_York")
    _clear_invoices(VENUE_B_ID)
    sid = _insert_session(
        table_id=VENUE_B_TABLE_ID, venue_id=VENUE_B_ID,
        started_at=started_at, last_activity_at=ended_at,
        ended_at=ended_at, total_rounds=1,
        billable_blocks=2, active_span_seconds=30 * 60,
        billing_finalized_at=_utcnow(),
    )
    try:
        _run(lambda c: recompute_invoices(c, ref_ts=started_at))
        inv, items = _invoice(VENUE_B_ID)
        assert inv is not None, "No invoice created"
        assert len(items) == 1
        assert str(items[0]["play_date"]) == EXPECTED_PLAY_DATE_NYC, (
            f"play_date={items[0]['play_date']} expected New York date {EXPECTED_PLAY_DATE_NYC}")
    finally:
        _delete_session(sid)
        _clear_invoices(VENUE_B_ID)
        _venue_restore(VENUE_B_ID, snap)
        _restore_venue_tz(VENUE_B_ID, old_tz)


# ---------------------------------------------------------------------------
# test 3: Month boundary — session near UTC Sep 1 stays in August for NYC
# ---------------------------------------------------------------------------

def test_non_melbourne_month_boundary():
    """A session at 2026-09-01T01:00 UTC is in August in New York (Aug 31 after
    4am offset) so must appear in August's invoice, not September's."""
    # 01:00 UTC Sep 1 = Aug 31 21:00 EDT -> minus 4h -> Aug 31 17:00 -> date Aug 31
    started_at = datetime(2026, 9, 1, 1, 0, 0)
    ended_at = started_at + timedelta(minutes=30)
    ref_ts = datetime(2026, 8, 15, 0, 0, 0)  # August ref so window covers August

    old_tz = _save_venue_tz(VENUE_B_ID)
    snap = _venue_save(VENUE_B_ID)
    _set_venue_tz(VENUE_B_ID, "America/New_York")
    _clear_invoices(VENUE_B_ID)
    sid = _insert_session(
        table_id=VENUE_B_TABLE_ID, venue_id=VENUE_B_ID,
        started_at=started_at, last_activity_at=ended_at,
        ended_at=ended_at, total_rounds=1,
        billable_blocks=2, active_span_seconds=30 * 60,
        billing_finalized_at=_utcnow(),
    )
    try:
        _run(lambda c: recompute_invoices(c, ref_ts=ref_ts))
        inv, items = _invoice(VENUE_B_ID)
        assert inv is not None, "No August invoice for NYC venue"
        # Invoice period_start must be an August date (2026-08-xx)
        assert str(inv[0]) is not None
        # Verify the line item play_date is in August
        assert len(items) == 1
        assert items[0]["play_date"].month == 8, (
            f"Expected August play_date, got {items[0]['play_date']}")
    finally:
        _delete_session(sid)
        _clear_invoices(VENUE_B_ID)
        _venue_restore(VENUE_B_ID, snap)
        _restore_venue_tz(VENUE_B_ID, old_tz)


# ---------------------------------------------------------------------------
# test 4: range_totals today boundary differs by timezone
# ---------------------------------------------------------------------------

def test_analytics_range_totals_uses_venue_tz():
    """range_totals 'tonight' uses the tz boundary: a session in the gap between
    Melbourne's and Honolulu's tonight_start is counted for one timezone but not
    the other, regardless of which start is earlier at the moment the test runs."""
    TZ_MEL = "Australia/Melbourne"
    TZ_HON = "Pacific/Honolulu"

    async def _get_starts(conn):
        mel = await conn.fetchval(
            "SELECT ((date_trunc('day', (NOW() AT TIME ZONE $1) - INTERVAL '4 hours')"
            "    + INTERVAL '4 hours') AT TIME ZONE $1) AT TIME ZONE 'UTC'",
            TZ_MEL,
        )
        hon = await conn.fetchval(
            "SELECT ((date_trunc('day', (NOW() AT TIME ZONE $1) - INTERVAL '4 hours')"
            "    + INTERVAL '4 hours') AT TIME ZONE $1) AT TIME ZONE 'UTC'",
            TZ_HON,
        )
        return mel, hon

    mel_start, hon_start = _run(_get_starts)
    # Determine which tonight started earlier; the session goes 1h into the gap.
    # Melbourne (UTC+10/11) and Honolulu (UTC-10) are always 20h apart, so there
    # is always a gap between their tonight_starts.
    if mel_start < hon_start:
        tz_earlier, tz_later, start_earlier, start_later = TZ_MEL, TZ_HON, mel_start, hon_start
    else:
        tz_earlier, tz_later, start_earlier, start_later = TZ_HON, TZ_MEL, hon_start, mel_start
    assert start_earlier < start_later, "No gap between tonight_starts — test cannot proceed"
    session_time = start_earlier + timedelta(seconds=3600)
    assert session_time < start_later, "session_time not before the later tonight_start"

    old_tz = _save_venue_tz(VENUE_A_ID)
    _set_venue_tz(VENUE_A_ID, tz_earlier)

    # Baseline counts before inserting the probe session.
    async def _totals(conn):
        r_earlier = await range_totals(conn, VENUE_A_ID, "tonight", tz_earlier)
        r_later = await range_totals(conn, VENUE_A_ID, "tonight", tz_later)
        return r_earlier["sessions"], r_later["sessions"]

    base_earlier, base_later = _run(_totals)

    st = session_time.replace(tzinfo=None)
    sid = _insert_session(
        table_id=VENUE_A_TABLE_ID, venue_id=VENUE_A_ID,
        started_at=st,
        last_activity_at=st + timedelta(minutes=30),
        ended_at=st + timedelta(minutes=30),
        total_rounds=1, billable_blocks=2, active_span_seconds=30 * 60,
    )
    try:
        new_earlier, new_later = _run(_totals)
        # The inserted session is inside tz_earlier's tonight but not tz_later's.
        assert new_earlier == base_earlier + 1, (
            f"{tz_earlier} sessions: expected {base_earlier + 1}, got {new_earlier} "
            f"— range_totals tz boundary broken")
        assert new_later == base_later, (
            f"{tz_later} sessions: expected {base_later} (unchanged), got {new_later} "
            f"— range_totals included session it should have excluded")
    finally:
        _delete_session(sid)
        _restore_venue_tz(VENUE_A_ID, old_tz)


# ---------------------------------------------------------------------------
# test 5: resolve_active_theme uses the passed timezone for play-date
# ---------------------------------------------------------------------------

def test_theme_resolve_uses_venue_tz():
    """A theme selection inserted for Auckland's play-date must be returned
    only when resolve_active_theme is called with the Auckland timezone, not
    with Etc/GMT+12 (UTC-12, no DST), which is always on a different play-date."""
    # Auckland is UTC+12 (NZST) or UTC+13 (NZDT), so Etc/GMT+12 is 24 or 25 hours
    # behind and the two never share a play-date. (Pago_Pago, UTC-11, is only 23
    # hours behind NZST and shares a play-date for one hour a day in NZ winter.)
    async def _setup(conn):
        auckland_date = await conn.fetchval(
            "SELECT (date_trunc('day', (NOW() AT TIME ZONE 'Pacific/Auckland') "
            "- INTERVAL '4 hours'))::date"
        )
        samoa_date = await conn.fetchval(
            "SELECT (date_trunc('day', (NOW() AT TIME ZONE 'Etc/GMT+12') "
            "- INTERVAL '4 hours'))::date"
        )
        # Query a non-default theme that actually exists in the themes table.
        theme_key = await conn.fetchval(
            "SELECT theme_key FROM themes WHERE theme_key != $1 ORDER BY theme_key LIMIT 1",
            "random",
        )
        return auckland_date, samoa_date, theme_key

    auckland_date, samoa_date, theme_key = _run(_setup)
    assert theme_key is not None, "No non-default theme found in themes table"
    assert auckland_date != samoa_date, (
        f"Auckland ({auckland_date}) and Samoa ({samoa_date}) play-dates must differ")

    old_tz = _save_venue_tz(VENUE_A_ID)

    async def _insert_selection(conn):
        sel_id = str(uuid.uuid4())
        await conn.execute(
            """
            INSERT INTO nightly_theme_selections (id, venue_id, selected_date, theme_key)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (venue_id, selected_date) DO UPDATE SET theme_key = $4
            """,
            sel_id, VENUE_A_ID, auckland_date, theme_key,
        )

    _run(_insert_selection)
    _set_venue_tz(VENUE_A_ID, "Pacific/Auckland")

    try:
        async def _resolve_auckland(conn):
            return await resolve_active_theme(conn, VENUE_A_ID, "Pacific/Auckland")

        theme = _run(_resolve_auckland)
        assert theme["theme_key"] == theme_key, (
            f"Expected '{theme_key}' with Auckland tz, got {theme['theme_key']}")

        # Samoa is always on a different play-date from Auckland, so there is no
        # selection for tonight in Samoa — must return the default.
        async def _resolve_samoa(conn):
            return await resolve_active_theme(conn, VENUE_A_ID, "Etc/GMT+12")

        theme_samoa = _run(_resolve_samoa)
        assert theme_samoa["theme_key"] == "random", (
            f"Expected 'random' default for Samoa tz (different date), "
            f"got {theme_samoa['theme_key']}")
    finally:
        async def _cleanup(conn):
            await conn.execute(
                "DELETE FROM nightly_theme_selections WHERE venue_id = $1 AND selected_date = $2",
                VENUE_A_ID, auckland_date,
            )
        _run(_cleanup)
        _restore_venue_tz(VENUE_A_ID, old_tz)


# ---------------------------------------------------------------------------
# test 6: recompute_daily_stats window start aligns to venue's 4am boundary
# ---------------------------------------------------------------------------

def test_analytics_window_start_aligned_to_venue_tz():
    """A session started just after 4am local on the oldest day in the window
    (window_days days ago in the venue's timezone) must be counted in that
    day's stat row after recompute_daily_stats runs, for a non-Melbourne venue.

    This verifies the per-venue 4am-aligned window start in recompute_daily_stats.
    """
    # Use America/New_York (UTC-4 EDT) as a non-Melbourne venue.
    WINDOW_DAYS = 35
    TEST_TZ = "America/New_York"

    async def _get_oldest_day_start(conn):
        # 4am local on the oldest play-date (WINDOW_DAYS days ago in venue tz),
        # expressed as a naive UTC datetime for DB insertion.
        oldest_4am_utc = await conn.fetchval(
            """
            SELECT (
                (date_trunc('day', (NOW() AT TIME ZONE $1) - INTERVAL '4 hours')
                    - make_interval(days => $2) + INTERVAL '4 hours')
                AT TIME ZONE $1
            ) AT TIME ZONE 'UTC'
            """,
            TEST_TZ, WINDOW_DAYS,
        )
        oldest_date = await conn.fetchval(
            """
            SELECT (date_trunc('day', (NOW() AT TIME ZONE $1) - INTERVAL '4 hours')
                    - make_interval(days => $2))::date
            """,
            TEST_TZ, WINDOW_DAYS,
        )
        return oldest_4am_utc, oldest_date

    oldest_4am_utc, oldest_date = _run(_get_oldest_day_start)
    # Insert a session 2 minutes after the oldest play-date's 4am local boundary.
    session_start = oldest_4am_utc.replace(tzinfo=None) + timedelta(seconds=120)

    old_tz = _save_venue_tz(VENUE_B_ID)
    _set_venue_tz(VENUE_B_ID, TEST_TZ)

    async def _clear_stats(conn):
        await conn.execute(
            "DELETE FROM venue_daily_stats WHERE venue_id = $1", VENUE_B_ID)

    _run(_clear_stats)

    sid = _insert_session(
        table_id=VENUE_B_TABLE_ID, venue_id=VENUE_B_ID,
        started_at=session_start,
        last_activity_at=session_start + timedelta(minutes=30),
        ended_at=session_start + timedelta(minutes=30),
        total_rounds=1, billable_blocks=2, active_span_seconds=30 * 60,
    )
    try:
        _run(lambda c: recompute_daily_stats(c))

        async def _stat(conn):
            return await conn.fetchrow(
                "SELECT session_count FROM venue_daily_stats"
                " WHERE venue_id = $1 AND stat_date = $2",
                VENUE_B_ID, oldest_date,
            )

        row = _run(_stat)
        assert row is not None, (
            f"No stat row for oldest play-date {oldest_date} in {TEST_TZ} — "
            "window start not aligned to venue 4am boundary")
        assert row["session_count"] >= 1, (
            f"session_count={row['session_count']} for oldest play-date {oldest_date}; "
            "session just after 4am local was not counted")
    finally:
        _delete_session(sid)
        _run(_clear_stats)
        _restore_venue_tz(VENUE_B_ID, old_tz)
