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


def _insert_session(table_id, venue_id, started_at=None, ended_at=None,
                    billable_blocks=None, total_rounds=0,
                    active_span_seconds=None, active_play_seconds=0):
    """Insert a session. Billing now counts blocks on FINALIZED (ended) sessions —
    pass ended_at + billable_blocks to make a session count toward billing."""
    session_id = str(uuid.uuid4())

    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute(
                """
                INSERT INTO game_sessions
                    (id, venue_id, table_id, player_count, started_at, ended_at,
                     total_rounds, billable_blocks, active_span_seconds,
                     active_play_seconds, created_at)
                VALUES ($1, $2, $3, 4, $4, $5, $6, $7, $8, $9, NOW())
                """,
                session_id, venue_id, table_id, started_at, ended_at,
                total_rounds, billable_blocks, active_span_seconds, active_play_seconds,
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
    """BOLA: owner_b PATCH only affects the-last-chance; fifty-five-bar (venue A) unchanged."""
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
    """Owner GET /billing -> 200 with the block-based shape."""
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    resp = client.get("/api/dashboard/billing",
                      headers={**api_key_header, **auth_header(token)})
    assert resp.status_code == 200
    body = resp.json()

    assert body["is_estimate"] is True
    assert isinstance(body["invoice_history"], list)
    assert isinstance(body["is_test_venue"], bool)

    model = body["model"]
    assert isinstance(model["billing_unit"], str)
    assert model["block_minutes"] == 15
    assert isinstance(model["blocks_per_night_cap_weekday"], int)
    assert isinstance(model["blocks_per_night_cap_weekend"], int)
    assert model["currency"] == "AUD"
    float(model["billing_unit"])
    float(model["nightly_cap_weekday"])

    tonight = body["tonight"]
    assert isinstance(tonight["blocks_billed"], int)
    assert tonight["blocks_billed"] >= 0
    assert isinstance(tonight["cap_applied"], bool)
    float(tonight["total"])

    month = body["month_estimate"]
    float(month["total"])
    assert isinstance(month["blocks_billed"], int)
    assert isinstance(month["nights"], list)

    pa = body["play_analytics"]
    assert isinstance(pa["billed_span_minutes"], (int, float))
    assert isinstance(pa["actual_play_minutes"], (int, float))
    assert isinstance(pa["billed_blocks"], int)
    assert isinstance(pa["unbilled_remainder_minutes"], (int, float))


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
    """Baseline shape check: monetary fields valid and non-negative."""
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    resp = client.get("/api/dashboard/billing",
                      headers={**api_key_header, **auth_header(token)})
    assert resp.status_code == 200
    body = resp.json()

    assert body["tonight"]["blocks_billed"] >= 0
    assert float(body["tonight"]["total"]) >= 0.0
    assert float(body["month_estimate"]["total"]) >= 0.0


def test_billing_only_counts_finalized_blocks(client, api_key_header, fresh_table):
    """An in-progress session (no billable_blocks) contributes nothing; a finalized
    session with blocks shows up in tonight + month totals."""
    table_id = fresh_table["table_id"]
    in_progress = _insert_session(table_id, VENUE_A_ID, started_at=_utcnow(), ended_at=None)
    finalized = _insert_session(table_id, VENUE_A_ID, started_at=_utcnow(),
                                ended_at=_utcnow(), billable_blocks=3, total_rounds=5)
    try:
        token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        resp = client.get("/api/dashboard/billing",
                          headers={**api_key_header, **auth_header(token)})
        assert resp.status_code == 200
        tonight = resp.json()["tonight"]
        # The 3 finalized blocks count; the in-progress session adds zero.
        assert tonight["blocks_billed"] >= 3
    finally:
        _delete_session(in_progress)
        _delete_session(finalized)


def test_billing_tonight_cap_applied(client, api_key_header, fresh_table):
    """Blocks beyond the per-table-per-night cap are not billed; cap_applied=True."""
    original = _get_venue_settings(VENUE_A_ID)
    unit = float(original["billing_unit"])
    table_id = fresh_table["table_id"]
    session_id = None
    try:
        # cap of 2*unit dollars => 2 blocks/night. A session with 5 blocks is capped to 2.
        _set_venue_caps(VENUE_A_ID, str(unit * 2), str(unit * 2))
        session_id = _insert_session(table_id, VENUE_A_ID, started_at=_utcnow(),
                                     ended_at=_utcnow(), billable_blocks=5, total_rounds=8)

        token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        resp = client.get("/api/dashboard/billing",
                          headers={**api_key_header, **auth_header(token)})
        assert resp.status_code == 200
        tonight = resp.json()["tonight"]

        assert tonight["cap_applied"] is True
        # This table is capped at 2 blocks; nothing else billable tonight in clean state.
        assert tonight["blocks_billed"] >= 2
        assert abs(float(tonight["total"]) - unit * 2) < 0.001
    finally:
        if session_id:
            _delete_session(session_id)
        _restore_venue_settings(VENUE_A_ID, original)


def test_billing_play_analytics_remainder(client, api_key_header, fresh_table):
    """play_analytics reports actual span/play minutes and the unbilled remainder
    (your '1 block + 14 min' = 29 min span billed as 1 block)."""
    original = _get_venue_settings(VENUE_A_ID)
    table_id = fresh_table["table_id"]
    session_id = None
    try:
        # 29 min of span (1740s), 25 min true play, 1 block billed.
        session_id = _insert_session(
            table_id, VENUE_A_ID, started_at=_utcnow(), ended_at=_utcnow(),
            billable_blocks=1, total_rounds=4,
            active_span_seconds=1740, active_play_seconds=1500)

        token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        resp = client.get("/api/dashboard/billing",
                          headers={**api_key_header, **auth_header(token)})
        assert resp.status_code == 200
        pa = resp.json()["play_analytics"]

        assert pa["billed_blocks"] >= 1
        assert pa["billed_span_minutes"] >= 29.0
        assert pa["actual_play_minutes"] >= 25.0
        # 29 min span, 1 block (15 min) billed -> at least 14 min unbilled remainder
        assert pa["unbilled_remainder_minutes"] >= 14.0
    finally:
        if session_id:
            _delete_session(session_id)
        _restore_venue_settings(VENUE_A_ID, original)


def test_billing_bola(client, api_key_header, fresh_table):
    """Owner B's billing never includes Venue A's blocks (BOLA)."""
    table_id = fresh_table["table_id"]  # a Venue A table
    session_id = _insert_session(table_id, VENUE_A_ID, started_at=_utcnow(),
                                 ended_at=_utcnow(), billable_blocks=7, total_rounds=9)
    try:
        token_b = dev_login(client, api_key_header, OWNER_B_CLERK_ID)
        resp = client.get("/api/dashboard/billing",
                          headers={**api_key_header, **auth_header(token_b)})
        assert resp.status_code == 200
        # Venue B owner must not see Venue A's 7 blocks. In clean state B has 0.
        assert resp.json()["month_estimate"]["blocks_billed"] == 0
    finally:
        _delete_session(session_id)


def test_billing_month_estimate_has_nights(client, api_key_header, fresh_table):
    """Finalized sessions on 2 distinct play-nights produce >= 2 night entries."""
    table_id = fresh_table["table_id"]
    boundary = _tonight_boundary()

    s1 = _insert_session(table_id, VENUE_A_ID, started_at=_utcnow(),
                         ended_at=_utcnow(), billable_blocks=2, total_rounds=3)
    s2 = _insert_session(table_id, VENUE_A_ID, started_at=boundary - timedelta(hours=25),
                         ended_at=boundary - timedelta(hours=24), billable_blocks=2, total_rounds=3)
    try:
        token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        resp = client.get("/api/dashboard/billing",
                          headers={**api_key_header, **auth_header(token)})
        assert resp.status_code == 200
        month = resp.json()["month_estimate"]

        assert len(month["nights"]) >= 2
        assert float(month["total"]) > 0.0

        for night in month["nights"]:
            assert isinstance(night["tables"], int)
            assert isinstance(night["blocks_raw"], int)
            assert isinstance(night["blocks_billed"], int)
            assert isinstance(night["cap_applied"], bool)
            float(night["amount"])
    finally:
        _delete_session(s1)
        _delete_session(s2)


def _set_billing_unit(venue_id, unit):
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute("UPDATE venues SET billing_unit=$2 WHERE id=$1", venue_id, unit)
        finally:
            await conn.close()
    asyncio.run(_q())


def test_billing_zero_unit_does_not_crash(client, api_key_header, fresh_table):
    """A misconfigured zero billing_unit must not 500 the endpoint — it caps to 0."""
    original = _get_venue_settings(VENUE_A_ID)
    table_id = fresh_table["table_id"]
    sid = None
    try:
        _set_billing_unit(VENUE_A_ID, "0")
        sid = _insert_session(table_id, VENUE_A_ID, started_at=_utcnow(),
                              ended_at=_utcnow(), billable_blocks=5, total_rounds=8)
        token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        resp = client.get("/api/dashboard/billing",
                          headers={**api_key_header, **auth_header(token)})
        assert resp.status_code == 200
        body = resp.json()
        assert float(body["tonight"]["total"]) == 0.0
        assert body["model"]["blocks_per_night_cap_weekday"] == 0
    finally:
        if sid:
            _delete_session(sid)
        _restore_venue_settings(VENUE_A_ID, original)
