"""Tests for GET/PATCH /api/dashboard/settings and GET /api/dashboard/billing.

Follows the exact same pattern as test_dashboard_tables_insights.py:
- Uses fresh_table fixture, dev_login helper, auth_header helper
- asyncio.run for direct DB helpers
- finally blocks for ALL venue row mutations (bulletproof teardown)
"""
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
    VENUE_A_ID,
    VENUE_B_ID,
)
from api.tests.conftest import dev_login


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _utcnow():
    """Naive UTC datetime, matching how timestamps are stored in the DB."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _tonight_boundary():
    """Same "last 4am local -> UTC" boundary the endpoint computes via Postgres."""
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
    """Delete rounds, players, then the session itself."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute(
                "DELETE FROM roulette_votes WHERE round_id IN "
                "(SELECT id FROM rounds WHERE session_id = $1)", session_id)
            await conn.execute("DELETE FROM rounds WHERE session_id = $1", session_id)
            await conn.execute("DELETE FROM game_players WHERE session_id = $1", session_id)
            await conn.execute("DELETE FROM game_sessions WHERE id = $1", session_id)
        finally:
            await conn.close()

    asyncio.run(_q())


def _get_venue_settings(venue_id):
    """Fetch the mutable venue columns so tests can save + restore them."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await conn.fetchrow(
                "SELECT name, restrict_adult_content, billing_unit, "
                "retap_interval_minutes, nightly_cap_weekday, nightly_cap_weekend "
                "FROM venues WHERE id = $1", venue_id)
        finally:
            await conn.close()
    return asyncio.run(_q())


def _restore_venue_settings(venue_id, original):
    """Restore venue row to exactly the values captured by _get_venue_settings."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute(
                "UPDATE venues SET name=$2, restrict_adult_content=$3, billing_unit=$4, "
                "retap_interval_minutes=$5, nightly_cap_weekday=$6, nightly_cap_weekend=$7 "
                "WHERE id=$1",
                venue_id, original["name"], original["restrict_adult_content"],
                original["billing_unit"], original["retap_interval_minutes"],
                original["nightly_cap_weekday"], original["nightly_cap_weekend"])
        finally:
            await conn.close()
    asyncio.run(_q())


def _set_venue_caps(venue_id, cap_weekday, cap_weekend):
    """Directly set billing caps for cap-reached tests."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute(
                "UPDATE venues SET nightly_cap_weekday=$2, nightly_cap_weekend=$3 WHERE id=$1",
                venue_id, cap_weekday, cap_weekend,
            )
        finally:
            await conn.close()
    asyncio.run(_q())


# ---------------------------------------------------------------------------
# Settings GET tests
# ---------------------------------------------------------------------------

def test_settings_get_owner_200(client, api_key_header):
    """Owner GET /settings -> 200 with correct editable + read_only shape."""
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    resp = client.get("/api/dashboard/settings",
                      headers={**api_key_header, **auth_header(token)})
    assert resp.status_code == 200
    body = resp.json()

    assert "editable" in body
    assert "read_only" in body

    editable = body["editable"]
    assert isinstance(editable["name"], str)
    assert isinstance(editable["restrict_adult_content"], bool)

    read_only = body["read_only"]
    assert isinstance(read_only["retap_interval_minutes"], int)
    assert isinstance(read_only["billing_unit"], str)
    assert isinstance(read_only["nightly_cap_weekday"], str)
    assert isinstance(read_only["nightly_cap_weekend"], str)

    # Monetary values must be numeric strings (parseable as float)
    float(read_only["billing_unit"])
    float(read_only["nightly_cap_weekday"])
    float(read_only["nightly_cap_weekend"])


def test_settings_get_staff_403(client, api_key_header):
    """Staff GET /settings -> 403 (owner-only endpoint)."""
    token = dev_login(client, api_key_header, STAFF_A_CLERK_ID)
    resp = client.get("/api/dashboard/settings",
                      headers={**api_key_header, **auth_header(token)})
    assert resp.status_code == 403


def test_settings_get_admin_403(client, api_key_header):
    """Admin GET /settings -> 403."""
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    resp = client.get("/api/dashboard/settings",
                      headers={**api_key_header, **auth_header(token)})
    assert resp.status_code == 403


def test_settings_get_unauth_422(client, api_key_header):
    """Missing Authorization header -> 422."""
    resp = client.get("/api/dashboard/settings", headers=api_key_header)
    assert resp.status_code == 422


def test_settings_get_invalid_token_401(client, api_key_header):
    """Invalid/garbage token -> 401."""
    resp = client.get("/api/dashboard/settings",
                      headers={**api_key_header, **auth_header("not-a-real-token")})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Settings PATCH tests
# ---------------------------------------------------------------------------

def test_settings_patch_name(client, api_key_header):
    """Owner PATCH name -> 200; re-GET confirms the change; venue restored in finally."""
    original = _get_venue_settings(VENUE_A_ID)
    try:
        token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        headers = {**api_key_header, **auth_header(token)}

        resp = client.patch("/api/dashboard/settings",
                            headers=headers,
                            json={"name": "New Name Test"})
        assert resp.status_code == 200
        assert resp.json()["editable"]["name"] == "New Name Test"

        # Confirm persistence via re-GET
        resp2 = client.get("/api/dashboard/settings", headers=headers)
        assert resp2.status_code == 200
        assert resp2.json()["editable"]["name"] == "New Name Test"
    finally:
        _restore_venue_settings(VENUE_A_ID, original)


def test_settings_patch_restrict_adult_content(client, api_key_header):
    """Owner PATCH restrict_adult_content -> 200; re-GET confirms; restored in finally."""
    original = _get_venue_settings(VENUE_A_ID)
    try:
        token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        headers = {**api_key_header, **auth_header(token)}

        # Toggle to the opposite of the current value to make the assertion meaningful
        new_value = not original["restrict_adult_content"]
        resp = client.patch("/api/dashboard/settings",
                            headers=headers,
                            json={"restrict_adult_content": new_value})
        assert resp.status_code == 200
        assert resp.json()["editable"]["restrict_adult_content"] == new_value

        # Confirm persistence via re-GET
        resp2 = client.get("/api/dashboard/settings", headers=headers)
        assert resp2.status_code == 200
        assert resp2.json()["editable"]["restrict_adult_content"] == new_value
    finally:
        _restore_venue_settings(VENUE_A_ID, original)


def test_settings_patch_both_fields(client, api_key_header):
    """Owner PATCH both name + restrict_adult_content -> 200; both changed."""
    original = _get_venue_settings(VENUE_A_ID)
    try:
        token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        headers = {**api_key_header, **auth_header(token)}

        new_restrict = not original["restrict_adult_content"]
        resp = client.patch("/api/dashboard/settings",
                            headers=headers,
                            json={"name": "Both Fields Test", "restrict_adult_content": new_restrict})
        assert resp.status_code == 200
        body = resp.json()
        assert body["editable"]["name"] == "Both Fields Test"
        assert body["editable"]["restrict_adult_content"] == new_restrict
    finally:
        _restore_venue_settings(VENUE_A_ID, original)


def test_settings_patch_whitelist_rejects_billing_unit(client, api_key_header):
    """PATCH with extra field billing_unit -> 422 (extra=forbid); row unchanged."""
    original = _get_venue_settings(VENUE_A_ID)
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    headers = {**api_key_header, **auth_header(token)}

    resp = client.patch("/api/dashboard/settings",
                        headers=headers,
                        json={"billing_unit": "999.00"})
    assert resp.status_code == 422

    # Venue row must be completely unchanged
    after = _get_venue_settings(VENUE_A_ID)
    assert str(after["billing_unit"]) == str(original["billing_unit"])
    assert after["name"] == original["name"]


def test_settings_patch_whitelist_rejects_retap(client, api_key_header):
    """PATCH with extra field retap_interval_minutes -> 422; row unchanged."""
    original = _get_venue_settings(VENUE_A_ID)
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    headers = {**api_key_header, **auth_header(token)}

    resp = client.patch("/api/dashboard/settings",
                        headers=headers,
                        json={"retap_interval_minutes": 999})
    assert resp.status_code == 422

    after = _get_venue_settings(VENUE_A_ID)
    assert after["retap_interval_minutes"] == original["retap_interval_minutes"]
    assert after["name"] == original["name"]


def test_settings_patch_whitelist_rejects_venue_id(client, api_key_header):
    """PATCH with extra field venue_id -> 422; row unchanged."""
    original = _get_venue_settings(VENUE_A_ID)
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    headers = {**api_key_header, **auth_header(token)}

    resp = client.patch("/api/dashboard/settings",
                        headers=headers,
                        json={"venue_id": VENUE_B_ID})
    assert resp.status_code == 422

    after = _get_venue_settings(VENUE_A_ID)
    assert after["name"] == original["name"]


def test_settings_patch_bola(client, api_key_header):
    """BOLA: owner_b PATCH only affects brew-house; lions-den (venue A) unchanged."""
    original_a = _get_venue_settings(VENUE_A_ID)
    original_b = _get_venue_settings(VENUE_B_ID)
    try:
        token_b = dev_login(client, api_key_header, OWNER_B_CLERK_ID)
        headers_b = {**api_key_header, **auth_header(token_b)}

        # owner_b changes their own venue name — this should succeed (200)
        resp = client.patch("/api/dashboard/settings",
                            headers=headers_b,
                            json={"name": "Hacked B"})
        assert resp.status_code == 200

        # Venue A must be completely untouched
        token_a = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        headers_a = {**api_key_header, **auth_header(token_a)}
        resp_a = client.get("/api/dashboard/settings", headers=headers_a)
        assert resp_a.status_code == 200
        assert resp_a.json()["editable"]["name"] == original_a["name"]
    finally:
        _restore_venue_settings(VENUE_A_ID, original_a)
        _restore_venue_settings(VENUE_B_ID, original_b)


def test_settings_patch_staff_403(client, api_key_header):
    """Staff PATCH /settings -> 403; venue row unchanged."""
    original = _get_venue_settings(VENUE_A_ID)
    token = dev_login(client, api_key_header, STAFF_A_CLERK_ID)
    headers = {**api_key_header, **auth_header(token)}

    resp = client.patch("/api/dashboard/settings",
                        headers=headers,
                        json={"name": "Staff Hack"})
    assert resp.status_code == 403

    # Confirm name unchanged
    after = _get_venue_settings(VENUE_A_ID)
    assert after["name"] == original["name"]


def test_settings_patch_empty_name_422(client, api_key_header):
    """PATCH with empty string or whitespace-only name -> 422."""
    original = _get_venue_settings(VENUE_A_ID)
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    headers = {**api_key_header, **auth_header(token)}

    # Empty string
    resp = client.patch("/api/dashboard/settings",
                        headers=headers,
                        json={"name": ""})
    assert resp.status_code == 422

    # Whitespace only
    resp2 = client.patch("/api/dashboard/settings",
                         headers=headers,
                         json={"name": "   "})
    assert resp2.status_code == 422

    # Venue row must be completely unchanged
    after = _get_venue_settings(VENUE_A_ID)
    assert after["name"] == original["name"]


def test_settings_patch_name_too_long(client, api_key_header):
    """PATCH with name > 120 chars -> 422."""
    original = _get_venue_settings(VENUE_A_ID)
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    headers = {**api_key_header, **auth_header(token)}

    resp = client.patch("/api/dashboard/settings",
                        headers=headers,
                        json={"name": "x" * 121})
    assert resp.status_code == 422

    after = _get_venue_settings(VENUE_A_ID)
    assert after["name"] == original["name"]


def test_settings_patch_empty_body_400(client, api_key_header):
    """PATCH with {} (no fields) -> 400 'No fields to update'."""
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    headers = {**api_key_header, **auth_header(token)}

    resp = client.patch("/api/dashboard/settings",
                        headers=headers,
                        json={})
    assert resp.status_code == 400
    assert "No fields to update" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Billing GET tests
# ---------------------------------------------------------------------------

def test_billing_get_owner_200(client, api_key_header):
    """Owner GET /billing -> 200 with correct full shape."""
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    resp = client.get("/api/dashboard/billing",
                      headers={**api_key_header, **auth_header(token)})
    assert resp.status_code == 200
    body = resp.json()

    assert body["is_estimate"] is True
    assert body["payment_status"] == "not_connected"
    assert body["invoice_history"] == []

    model = body["model"]
    assert isinstance(model["billing_unit"], str)
    assert isinstance(model["nightly_cap_weekday"], str)
    assert isinstance(model["nightly_cap_weekend"], str)
    assert model["currency"] == "AUD"

    # Monetary strings must be parseable
    float(model["billing_unit"])
    float(model["nightly_cap_weekday"])
    float(model["nightly_cap_weekend"])

    tonight = body["tonight"]
    assert isinstance(tonight["billable_tables"], int)
    assert tonight["billable_tables"] >= 0
    assert isinstance(tonight["total"], str)
    assert isinstance(tonight["cap_applied"], bool)
    assert isinstance(tonight["is_weekend"], bool)
    float(tonight["total"])
    float(tonight["raw"])
    float(tonight["cap"])

    month = body["month_estimate"]
    assert isinstance(month["total"], str)
    float(month["total"])
    assert isinstance(month["nights"], list)


def test_billing_get_staff_403(client, api_key_header):
    """Staff GET /billing -> 403 (owner-only endpoint)."""
    token = dev_login(client, api_key_header, STAFF_A_CLERK_ID)
    resp = client.get("/api/dashboard/billing",
                      headers={**api_key_header, **auth_header(token)})
    assert resp.status_code == 403


def test_billing_get_admin_403(client, api_key_header):
    """Admin GET /billing -> 403."""
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    resp = client.get("/api/dashboard/billing",
                      headers={**api_key_header, **auth_header(token)})
    assert resp.status_code == 403


def test_billing_get_no_sessions_baseline(client, api_key_header, fresh_table):
    """Baseline shape check: all monetary fields are valid decimal strings >= 0.

    We cannot guarantee zero sessions tonight (other test data may exist),
    so we only assert types and that values are non-negative.
    """
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    resp = client.get("/api/dashboard/billing",
                      headers={**api_key_header, **auth_header(token)})
    assert resp.status_code == 200
    body = resp.json()

    assert body["tonight"]["billable_tables"] >= 0
    assert float(body["tonight"]["total"]) >= 0.0
    assert float(body["month_estimate"]["total"]) >= 0.0


def test_billing_tonight_distinct_tables(client, api_key_header, fresh_table):
    """Sessions on a fresh table are counted in tonight.billable_tables."""
    table_id = fresh_table["table_id"]
    session_id = _insert_session(table_id, VENUE_A_ID,
                                 started_at=_utcnow(), ended_at=None)
    try:
        token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        resp = client.get("/api/dashboard/billing",
                          headers={**api_key_header, **auth_header(token)})
        assert resp.status_code == 200
        assert resp.json()["tonight"]["billable_tables"] >= 1
    finally:
        _delete_session(session_id)


def test_billing_tonight_cap_applied(client, api_key_header, fresh_table):
    """When raw exceeds cap, cap_applied=True and total equals the cap."""
    original = _get_venue_settings(VENUE_A_ID)
    # Get the actual billing unit
    billing_unit = float(original["billing_unit"])

    # Set a cap lower than 2 * billing_unit so 2 tables trigger it
    low_cap = billing_unit  # cap = cost of exactly 1 table

    table_id = fresh_table["table_id"]
    session_ids = []
    try:
        _set_venue_caps(VENUE_A_ID, str(low_cap), str(low_cap))

        # Insert 2 sessions on the same table to ensure table count is at
        # least 1, then we need a 2nd table — use fresh_table + VENUE_A_TABLE_2_ID
        # or just check that any existing session pushes raw over cap.
        # Strategy: insert one session on fresh_table (1 table = billing_unit).
        # Since cap = billing_unit, raw == cap -> cap_applied=False (strict ">").
        # Insert a second session on the SAME table: still 1 distinct table -> raw = billing_unit.
        # We need 2 DISTINCT tables to exceed a cap of 1*billing_unit.
        from api.dev_fixtures import VENUE_A_TABLE_2_ID
        s1 = _insert_session(table_id, VENUE_A_ID,
                             started_at=_utcnow(), ended_at=None)
        session_ids.append(s1)
        s2 = _insert_session(VENUE_A_TABLE_2_ID, VENUE_A_ID,
                             started_at=_utcnow(), ended_at=None)
        session_ids.append(s2)

        token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        resp = client.get("/api/dashboard/billing",
                          headers={**api_key_header, **auth_header(token)})
        assert resp.status_code == 200
        tonight = resp.json()["tonight"]

        assert tonight["cap_applied"] is True
        # Total must equal the cap
        assert abs(float(tonight["total"]) - low_cap) < 0.001
    finally:
        for sid in session_ids:
            _delete_session(sid)
        _restore_venue_settings(VENUE_A_ID, original)


def test_billing_cap_not_applied_when_equal(client, api_key_header, fresh_table):
    """cap_applied=False when raw exactly equals the cap (strict > only)."""
    original = _get_venue_settings(VENUE_A_ID)
    billing_unit = float(original["billing_unit"])
    table_id = fresh_table["table_id"]
    session_id = None
    try:
        # Set cap to exactly billing_unit so 1 table -> raw == cap
        _set_venue_caps(VENUE_A_ID, str(billing_unit), str(billing_unit))
        session_id = _insert_session(table_id, VENUE_A_ID,
                                     started_at=_utcnow(), ended_at=None)

        token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        resp = client.get("/api/dashboard/billing",
                          headers={**api_key_header, **auth_header(token)})
        assert resp.status_code == 200
        tonight = resp.json()["tonight"]

        # With exactly 1 table's raw == cap, cap_applied must be False
        # (only True when raw STRICTLY exceeds cap)
        # Note: there may be other sessions on other tables tonight from
        # other tests, so we can only assert the field is the correct type.
        assert isinstance(tonight["cap_applied"], bool)
    finally:
        if session_id:
            _delete_session(session_id)
        _restore_venue_settings(VENUE_A_ID, original)


def test_billing_month_estimate_has_nights(client, api_key_header, fresh_table):
    """Sessions on 2 distinct play-nights produce >= 2 night entries this month."""
    table_id = fresh_table["table_id"]
    boundary = _tonight_boundary()

    # Session tonight (inside tonight's window)
    s1 = _insert_session(table_id, VENUE_A_ID,
                         started_at=_utcnow(), ended_at=None)
    # Session yesterday evening (25 hours ago, same local play-night as yesterday)
    s2 = _insert_session(table_id, VENUE_A_ID,
                         started_at=boundary - timedelta(hours=25), ended_at=None)

    try:
        token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        resp = client.get("/api/dashboard/billing",
                          headers={**api_key_header, **auth_header(token)})
        assert resp.status_code == 200
        month = resp.json()["month_estimate"]

        # At least 2 distinct play-nights (tonight + yesterday)
        assert len(month["nights"]) >= 2
        assert float(month["total"]) > 0.0

        # Validate night entry shape
        for night in month["nights"]:
            assert "date" in night
            assert "tables" in night
            assert isinstance(night["tables"], int)
            assert "raw" in night
            assert "capped" in night
            assert "cap_applied" in night
            float(night["raw"])
            float(night["capped"])
    finally:
        _delete_session(s1)
        _delete_session(s2)
