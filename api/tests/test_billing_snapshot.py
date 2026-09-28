"""Tests for billing snapshot: terms frozen at session start (#32).

Imports helpers from test_billing rather than duplicating them.
All DB mutations are torn down in finally blocks.
"""
import os
import uuid
from datetime import timedelta

import pytest

from api.db import _init_connection
from api.dev_fixtures import (
    OWNER_A_CLERK_ID,
    VENUE_A_ID,
    VENUE_A_TABLE_ID,
    VENUE_A_TABLE_2_ID,
)
from api.services.billing_service import cap_blocks, recompute_invoices
from api.services.lobby_service import (
    get_lobby,
    start_game,
)
from api.tests.conftest import dev_login
from api.tests.test_billing import (
    _at_six_utc,
    _clear_invoices,
    _date_in_month,
    _delete_session,
    _insert_session,
    _invoice,
    _run,
    _set_venue,
    _venue_restore,
    _venue_save,
    _venue_unit,
)


# ---------------------------------------------------------------------------
# Test 1: cap change — each night keeps its snapshot cap
# ---------------------------------------------------------------------------

def test_snapshot_cap_change_keeps_old_cap():
    """Night A billed at cap X (its snapshot), night B at cap Y (its snapshot),
    even though cap Y is the venue's current setting by the time recompute runs."""
    snap = _venue_save(VENUE_A_ID)
    unit = float(_venue_unit(VENUE_A_ID))
    _clear_invoices(VENUE_A_ID)
    wed = _date_in_month(2)   # Wednesday (date.weekday() 2)
    thu = _date_in_month(3)   # Thursday  (date.weekday() 3)
    cap_x = unit * 5          # 5-block cap
    cap_y = unit * 8          # 8-block cap
    sid_a = sid_b = None
    try:
        _set_venue(VENUE_A_ID, cap_wd=cap_x)
        sid_a = _insert_session(
            table_id=VENUE_A_TABLE_ID, venue_id=VENUE_A_ID,
            started_at=_at_six_utc(wed),
            last_activity_at=_at_six_utc(wed) + timedelta(minutes=30),
            ended_at=_at_six_utc(wed) + timedelta(minutes=30),
            total_rounds=2, billable_blocks=10,
            billing_finalized_at=_at_six_utc(wed) + timedelta(minutes=31),
            snap_billing_unit=unit, snap_nightly_cap_weekday=cap_x,
        )
        _set_venue(VENUE_A_ID, cap_wd=cap_y)
        sid_b = _insert_session(
            table_id=VENUE_A_TABLE_ID, venue_id=VENUE_A_ID,
            started_at=_at_six_utc(thu),
            last_activity_at=_at_six_utc(thu) + timedelta(minutes=30),
            ended_at=_at_six_utc(thu) + timedelta(minutes=30),
            total_rounds=2, billable_blocks=10,
            billing_finalized_at=_at_six_utc(thu) + timedelta(minutes=31),
            snap_billing_unit=unit, snap_nightly_cap_weekday=cap_y,
        )
        _run(lambda c: recompute_invoices(c))
        _, items = _invoice(VENUE_A_ID)
        by_date = {str(it["play_date"]): it for it in items}
        assert str(wed) in by_date, f"expected night {wed} in invoice"
        assert str(thu) in by_date, f"expected night {thu} in invoice"
        assert by_date[str(wed)]["units_billed"] == cap_blocks(cap_x, unit)
        assert by_date[str(thu)]["units_billed"] == cap_blocks(cap_y, unit)
    finally:
        _venue_restore(VENUE_A_ID, snap)
        for sid in (sid_a, sid_b):
            if sid:
                _delete_session(sid)
        _clear_invoices(VENUE_A_ID)


# ---------------------------------------------------------------------------
# Test 2: unit change — each night keeps its snapshot unit
# ---------------------------------------------------------------------------

def test_snapshot_unit_change_keeps_old_unit():
    """Night A amount = snap_unit_a * blocks_a, night B = snap_unit_b * blocks_b,
    regardless of the venue's current billing_unit."""
    snap = _venue_save(VENUE_A_ID)
    _clear_invoices(VENUE_A_ID)
    wed = _date_in_month(2)
    thu = _date_in_month(3)
    snap_unit_a = 2.00
    snap_unit_b = 5.00
    high_cap = 999.00   # won't constrain blocks
    blocks_a = 3
    blocks_b = 4
    sid_a = sid_b = None
    try:
        sid_a = _insert_session(
            table_id=VENUE_A_TABLE_2_ID, venue_id=VENUE_A_ID,
            started_at=_at_six_utc(wed),
            last_activity_at=_at_six_utc(wed) + timedelta(minutes=30),
            ended_at=_at_six_utc(wed) + timedelta(minutes=30),
            total_rounds=2, billable_blocks=blocks_a,
            billing_finalized_at=_at_six_utc(wed) + timedelta(minutes=31),
            snap_billing_unit=snap_unit_a,
            snap_nightly_cap_weekday=high_cap,
        )
        sid_b = _insert_session(
            table_id=VENUE_A_TABLE_2_ID, venue_id=VENUE_A_ID,
            started_at=_at_six_utc(thu),
            last_activity_at=_at_six_utc(thu) + timedelta(minutes=30),
            ended_at=_at_six_utc(thu) + timedelta(minutes=30),
            total_rounds=2, billable_blocks=blocks_b,
            billing_finalized_at=_at_six_utc(thu) + timedelta(minutes=31),
            snap_billing_unit=snap_unit_b,
            snap_nightly_cap_weekday=high_cap,
        )
        _run(lambda c: recompute_invoices(c))
        _, items = _invoice(VENUE_A_ID)
        by_date = {str(it["play_date"]): it for it in items}
        assert str(wed) in by_date, f"expected night {wed}"
        assert str(thu) in by_date, f"expected night {thu}"
        assert float(by_date[str(wed)]["amount"]) == pytest.approx(snap_unit_a * blocks_a)
        assert float(by_date[str(thu)]["amount"]) == pytest.approx(snap_unit_b * blocks_b)
    finally:
        _venue_restore(VENUE_A_ID, snap)
        for sid in (sid_a, sid_b):
            if sid:
                _delete_session(sid)
        _clear_invoices(VENUE_A_ID)


# ---------------------------------------------------------------------------
# Test 3: legacy NULL snapshot — falls back to current venue values
# ---------------------------------------------------------------------------

def test_legacy_null_snapshot_uses_current_venue():
    """A pre-migration session (all snap columns NULL) bills at the venue's
    current cap/unit — the COALESCE fallback path."""
    snap = _venue_save(VENUE_A_ID)
    unit = float(_venue_unit(VENUE_A_ID))
    _clear_invoices(VENUE_A_ID)
    mon = _date_in_month(0)   # Monday
    cap_wd = unit * 6         # 6-block cap
    sid = None
    try:
        _set_venue(VENUE_A_ID, cap_wd=cap_wd)
        sid = _insert_session(
            table_id=VENUE_A_TABLE_ID, venue_id=VENUE_A_ID,
            started_at=_at_six_utc(mon),
            last_activity_at=_at_six_utc(mon) + timedelta(minutes=30),
            ended_at=_at_six_utc(mon) + timedelta(minutes=30),
            total_rounds=2, billable_blocks=10,
            billing_finalized_at=_at_six_utc(mon) + timedelta(minutes=31),
            # all three snap columns left NULL (pre-migration legacy row)
        )
        _run(lambda c: recompute_invoices(c))
        _, items = _invoice(VENUE_A_ID)
        by_date = {str(it["play_date"]): it for it in items}
        assert str(mon) in by_date
        assert by_date[str(mon)]["units_billed"] == cap_blocks(cap_wd, unit)
    finally:
        _venue_restore(VENUE_A_ID, snap)
        if sid:
            _delete_session(sid)
        _clear_invoices(VENUE_A_ID)


# ---------------------------------------------------------------------------
# Test 4: two sessions same night — earliest snapshot wins
# ---------------------------------------------------------------------------

def test_two_sessions_same_night_uses_earliest_snapshot():
    """When two sessions share a (table, play_date), the FIRST session's
    snapshot cap is used. Even if the second session has a larger cap, the
    earlier one's snapshot governs."""
    snap = _venue_save(VENUE_A_ID)
    unit = float(_venue_unit(VENUE_A_ID))
    _clear_invoices(VENUE_A_ID)
    tue = _date_in_month(1)   # Tuesday
    cap_x = unit * 5          # 5-block cap (first session)
    cap_y = unit * 9          # 9-block cap (second session, should be ignored)
    sid_first = sid_second = None
    try:
        _set_venue(VENUE_A_ID, cap_wd=cap_x)
        t0 = _at_six_utc(tue)
        sid_first = _insert_session(
            table_id=VENUE_A_TABLE_ID, venue_id=VENUE_A_ID,
            started_at=t0,
            last_activity_at=t0 + timedelta(minutes=30),
            ended_at=t0 + timedelta(minutes=30),
            total_rounds=2, billable_blocks=6,
            billing_finalized_at=t0 + timedelta(minutes=31),
            snap_billing_unit=unit, snap_nightly_cap_weekday=cap_x,
        )
        t1 = t0 + timedelta(hours=1)
        sid_second = _insert_session(
            table_id=VENUE_A_TABLE_ID, venue_id=VENUE_A_ID,
            started_at=t1,
            last_activity_at=t1 + timedelta(minutes=30),
            ended_at=t1 + timedelta(minutes=30),
            total_rounds=2, billable_blocks=6,
            billing_finalized_at=t1 + timedelta(minutes=31),
            snap_billing_unit=unit, snap_nightly_cap_weekday=cap_y,
        )
        _run(lambda c: recompute_invoices(c))
        _, items = _invoice(VENUE_A_ID)
        by_date = {str(it["play_date"]): it for it in items}
        assert str(tue) in by_date
        # Total raw_blocks = 12, but cap from first session = cap_blocks(cap_x, unit) = 5
        assert by_date[str(tue)]["units_billed"] == cap_blocks(cap_x, unit)
        assert by_date[str(tue)]["cap_applied"] is True
    finally:
        _venue_restore(VENUE_A_ID, snap)
        for sid in (sid_first, sid_second):
            if sid:
                _delete_session(sid)
        _clear_invoices(VENUE_A_ID)


def test_mixed_night_legacy_first_session_uses_current_venue_terms():
    """The night's FIRST session decides. When it predates the snapshot columns
    (NULL), the night falls back to the venue's current cap, even though a later
    session that night carries a snapshot."""
    snap = _venue_save(VENUE_A_ID)
    unit = float(_venue_unit(VENUE_A_ID))
    _clear_invoices(VENUE_A_ID)
    tue = _date_in_month(1)   # Tuesday
    cap_now = unit * 7        # venue's current cap (fallback for the legacy first session)
    cap_later = unit * 3      # later session's snapshot, must be ignored
    sid_first = sid_second = None
    try:
        _set_venue(VENUE_A_ID, cap_wd=cap_now)
        t0 = _at_six_utc(tue)
        sid_first = _insert_session(
            table_id=VENUE_A_TABLE_ID, venue_id=VENUE_A_ID,
            started_at=t0,
            last_activity_at=t0 + timedelta(minutes=30),
            ended_at=t0 + timedelta(minutes=30),
            total_rounds=2, billable_blocks=6,
            billing_finalized_at=t0 + timedelta(minutes=31),
        )
        t1 = t0 + timedelta(hours=1)
        sid_second = _insert_session(
            table_id=VENUE_A_TABLE_ID, venue_id=VENUE_A_ID,
            started_at=t1,
            last_activity_at=t1 + timedelta(minutes=30),
            ended_at=t1 + timedelta(minutes=30),
            total_rounds=2, billable_blocks=6,
            billing_finalized_at=t1 + timedelta(minutes=31),
            snap_billing_unit=unit, snap_nightly_cap_weekday=cap_later,
        )
        _run(lambda c: recompute_invoices(c))
        _, items = _invoice(VENUE_A_ID)
        by_date = {str(it["play_date"]): it for it in items}
        # 12 raw blocks; the legacy first session means the current 7-block cap applies, not 3
        assert by_date[str(tue)]["units_billed"] == cap_blocks(cap_now, unit)
    finally:
        _venue_restore(VENUE_A_ID, snap)
        for sid in (sid_first, sid_second):
            if sid:
                _delete_session(sid)
        _clear_invoices(VENUE_A_ID)


# ---------------------------------------------------------------------------
# Test 5: start_game writes snapshot into the session row
# ---------------------------------------------------------------------------

def test_start_game_writes_snapshot():
    """The real start_game path writes snap_billing_unit/cap_wd/cap_we from
    the venue row into game_sessions at the moment the game starts."""
    lobby_id = session_id = None
    phone_a = str(uuid.uuid4())
    phone_b = str(uuid.uuid4())

    async def _drive(conn):
        nonlocal lobby_id, session_id
        await _init_connection(conn)   # the app pool's jsonb codec, as in production
        # Close any pre-existing open lobby for this table so the unique index
        # (one_open_lobby_per_table) doesn't block our INSERT.
        await conn.execute(
            "UPDATE table_lobbies SET status = 'cancelled' "
            "WHERE table_id = $1 AND status = 'open'",
            VENUE_A_TABLE_2_ID,
        )
        lobby_id_local = str(uuid.uuid4())
        lobby_id = lobby_id_local
        await conn.execute(
            "INSERT INTO table_lobbies (id, venue_id, table_id, status, host_phone_id) "
            "VALUES ($1, $2, $3, 'open', $4)",
            lobby_id_local, VENUE_A_ID, VENUE_A_TABLE_2_ID, phone_a,
        )
        await conn.execute(
            "INSERT INTO table_lobby_phones (id, lobby_id, phone_id, name) "
            "VALUES ($1, $2, $3, $4)",
            str(uuid.uuid4()), lobby_id_local, phone_a, "Alice",
        )
        await conn.execute(
            "INSERT INTO table_lobby_phones (id, lobby_id, phone_id, name) "
            "VALUES ($1, $2, $3, $4)",
            str(uuid.uuid4()), lobby_id_local, phone_b, "Bob",
        )
        lobby = await get_lobby(conn, lobby_id_local)
        result = await start_game(conn, lobby, phone_a, False, None)
        session_id = result["session_id"]
        return await conn.fetchrow(
            "SELECT snap_billing_unit, snap_nightly_cap_weekday, snap_nightly_cap_weekend, "
            "jsonb_typeof(player_names) AS names_type, player_names "
            "FROM game_sessions WHERE id = $1",
            session_id,
        )

    async def _cleanup(conn):
        # Clear FK before deleting session: start_game sets converted_session_id.
        if lobby_id:
            await conn.execute(
                "UPDATE table_lobbies SET converted_session_id = NULL WHERE id = $1", lobby_id)
        if session_id:
            await conn.execute("DELETE FROM game_players WHERE session_id = $1", session_id)
            await conn.execute("DELETE FROM game_sessions WHERE id = $1", session_id)
        if lobby_id:
            await conn.execute("DELETE FROM table_lobby_phones WHERE lobby_id = $1", lobby_id)
            await conn.execute("DELETE FROM table_lobbies WHERE id = $1", lobby_id)

    async def _venue_billing(conn):
        return await conn.fetchrow(
            "SELECT billing_unit, nightly_cap_weekday, nightly_cap_weekend "
            "FROM venues WHERE id = $1",
            VENUE_A_ID,
        )

    try:
        snap_row = _run(_drive)
        venue_row = _run(_venue_billing)
        assert snap_row["snap_billing_unit"] == venue_row["billing_unit"]
        assert snap_row["snap_nightly_cap_weekday"] == venue_row["nightly_cap_weekday"]
        assert snap_row["snap_nightly_cap_weekend"] == venue_row["nightly_cap_weekend"]
        # Stored once-encoded: a JSON array, not a JSON string holding an array.
        assert snap_row["names_type"] == "array", snap_row["names_type"]
        assert snap_row["player_names"] == ["Alice", "Bob"]
    finally:
        _run(_cleanup)


# ---------------------------------------------------------------------------
# Test 6: dashboard billing estimate matches invoice after cap change
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    from api.index import app
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c
    import api.db
    api.db._pool = None
    from api.security import limiter
    limiter.reset()


@pytest.fixture
def api_key_header():
    return {"X-API-Key": os.environ["API_KEY"]}


def test_dashboard_billing_matches_invoice_after_cap_change(client, api_key_header):
    """dashboard /billing and recompute_invoices must agree — both use the
    snapshot cap, not the venue's current cap."""
    snap = _venue_save(VENUE_A_ID)
    unit = float(_venue_unit(VENUE_A_ID))
    _clear_invoices(VENUE_A_ID)
    wed = _date_in_month(2)
    cap_x = unit * 5   # snapshot cap
    cap_y = unit * 8   # new current cap (should NOT affect billing)
    sid = None
    try:
        _set_venue(VENUE_A_ID, cap_wd=cap_x)
        sid = _insert_session(
            table_id=VENUE_A_TABLE_ID, venue_id=VENUE_A_ID,
            started_at=_at_six_utc(wed),
            last_activity_at=_at_six_utc(wed) + timedelta(minutes=30),
            ended_at=_at_six_utc(wed) + timedelta(minutes=30),
            total_rounds=2, billable_blocks=10,
            billing_finalized_at=_at_six_utc(wed) + timedelta(minutes=31),
            snap_billing_unit=unit, snap_nightly_cap_weekday=cap_x,
        )
        # Change cap so dashboard and recompute would disagree if they don't use snapshot.
        _set_venue(VENUE_A_ID, cap_wd=cap_y)

        token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        resp = client.get(
            "/api/dashboard/billing",
            headers={**api_key_header, "Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        dashboard_total = float(resp.json()["month_estimate"]["total"])

        _run(lambda c: recompute_invoices(c))
        inv, _ = _invoice(VENUE_A_ID)
        assert inv is not None
        invoice_total = float(inv["total_amount"])

        # Both must use the snapshot cap (cap_x), not the current cap (cap_y).
        expected = unit * cap_blocks(cap_x, unit)
        assert dashboard_total == pytest.approx(expected, abs=0.01)
        assert invoice_total == pytest.approx(expected, abs=0.01)
    finally:
        _venue_restore(VENUE_A_ID, snap)
        if sid:
            _delete_session(sid)
        _clear_invoices(VENUE_A_ID)
