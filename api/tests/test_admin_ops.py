"""Tests for Slice 5 Admin Ops endpoints.

Covers:
- Migration: venue_config_overrides, support_messages, leads tables exist
- GET /api/admin/venues/{id}: shape, auth gates, bad/unknown UUIDs
- PATCH /api/admin/venues/{id}: override + audit rows, validation, auth gates
- GET /api/admin/venues/{id}/config-history: order, auth gate
- GET/PATCH /api/admin/support: lifecycle, filters, auth gates
- GET/POST /api/admin/leads: create, list, validation, auth gates
- GET /api/admin/team: seeded users present, auth gates

Every test that mutates shared venue rows restores them in a finally block.
Every test that inserts rows into new tables deletes them in a finally block.
"""
import asyncio
import json
import os
import uuid

import asyncpg

from api.dev_fixtures import (
    ADMIN_CLERK_ID,
    ADMIN_ID,
    OWNER_A_CLERK_ID,
    STAFF_A_CLERK_ID,
    VENUE_A_ID,
)
from api.tests.conftest import dev_login


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# DB helpers — asyncio.run pattern matching test_admin_overview.py
# ---------------------------------------------------------------------------

def _insert_support_message(
    venue_id=None, name=None, email=None, message="test msg", status="open"
):
    msg_id = str(uuid.uuid4())

    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute(
                """
                INSERT INTO support_messages
                    (id, venue_id, name, email, message, status, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, NOW())
                """,
                msg_id, venue_id, name, email, message, status,
            )
        finally:
            await conn.close()

    asyncio.run(_q())
    return msg_id


def _delete_support_message(msg_id):
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute("DELETE FROM support_messages WHERE id = $1", msg_id)
        finally:
            await conn.close()

    asyncio.run(_q())


def _delete_lead(lead_id):
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute("DELETE FROM leads WHERE id = $1", lead_id)
        finally:
            await conn.close()

    asyncio.run(_q())


def _get_venue_fields(venue_id):
    """Read all overridable fields from a venue."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await conn.fetchrow(
                """
                SELECT name, billing_unit, retap_interval_minutes, nightly_cap_weekday,
                       nightly_cap_weekend, restrict_adult_content, is_test, status
                FROM venues WHERE id = $1
                """,
                venue_id,
            )
        finally:
            await conn.close()

    return asyncio.run(_q())


def _restore_venue(venue_id, fields):
    """Restore venue to the given field values. Used in finally blocks."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute(
                """
                UPDATE venues SET
                    name = $1, billing_unit = $2, retap_interval_minutes = $3,
                    nightly_cap_weekday = $4, nightly_cap_weekend = $5,
                    restrict_adult_content = $6, is_test = $7, status = $8
                WHERE id = $9
                """,
                fields["name"], fields["billing_unit"], fields["retap_interval_minutes"],
                fields["nightly_cap_weekday"], fields["nightly_cap_weekend"],
                fields["restrict_adult_content"], fields["is_test"], fields["status"],
                venue_id,
            )
        finally:
            await conn.close()

    asyncio.run(_q())


def _delete_config_overrides(venue_id):
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute(
                "DELETE FROM venue_config_overrides WHERE venue_id = $1", venue_id
            )
        finally:
            await conn.close()

    asyncio.run(_q())


def _count_config_overrides(venue_id):
    """Return number of override rows for a venue — used to verify audit count."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM venue_config_overrides WHERE venue_id = $1",
                venue_id,
            )
        finally:
            await conn.close()

    return asyncio.run(_q())


def _fetch_config_overrides(venue_id):
    """Return all override rows for a venue."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await conn.fetch(
                """
                SELECT field_name, old_value, new_value, reason, changed_by
                FROM venue_config_overrides WHERE venue_id = $1
                ORDER BY created_at DESC
                """,
                venue_id,
            )
        finally:
            await conn.close()

    return asyncio.run(_q())


# ---------------------------------------------------------------------------
# Migration tests (3)
# ---------------------------------------------------------------------------

def test_migration_venue_config_overrides_exists(client, api_key_header):
    """venue_config_overrides table must exist in the database."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await conn.fetchval(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_name = 'venue_config_overrides'
                """,
            )
        finally:
            await conn.close()

    result = asyncio.run(_q())
    assert result == "venue_config_overrides", "venue_config_overrides table must exist"


def test_migration_support_messages_exists(client, api_key_header):
    """support_messages table must exist in the database."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await conn.fetchval(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_name = 'support_messages'
                """,
            )
        finally:
            await conn.close()

    result = asyncio.run(_q())
    assert result == "support_messages", "support_messages table must exist"


def test_migration_leads_exists(client, api_key_header):
    """leads table must exist in the database."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await conn.fetchval(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_name = 'leads'
                """,
            )
        finally:
            await conn.close()

    result = asyncio.run(_q())
    assert result == "leads", "leads table must exist"


# ---------------------------------------------------------------------------
# Venue Detail tests (4)
# ---------------------------------------------------------------------------

def test_admin_venue_detail_200(client, api_key_header):
    """Admin GET /api/admin/venues/{VENUE_A_ID} -> 200 with full shape."""
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    resp = client.get(
        f"/api/admin/venues/{VENUE_A_ID}",
        headers={**api_key_header, **auth_header(token)},
    )
    assert resp.status_code == 200
    body = resp.json()

    assert "venue" in body
    venue = body["venue"]
    assert isinstance(venue["name"], str) and venue["name"]
    assert isinstance(venue["billing_unit"], str)
    float(venue["billing_unit"])  # Must be parseable as float
    assert isinstance(venue["is_test"], bool)
    assert isinstance(body["table_count"], int) and body["table_count"] >= 0
    assert isinstance(body["sessions_tonight"], int) and body["sessions_tonight"] >= 0
    assert isinstance(body["lifetime_sessions"], int) and body["lifetime_sessions"] >= 0


def test_admin_venue_detail_owner_403(client, api_key_header):
    """venue_owner GET venue detail -> 403 (inverse-BOLA gate)."""
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    resp = client.get(
        f"/api/admin/venues/{VENUE_A_ID}",
        headers={**api_key_header, **auth_header(token)},
    )
    assert resp.status_code == 403


def test_admin_venue_detail_bad_uuid_404(client, api_key_header):
    """Malformed UUID in path -> 404 (not 500 or 422)."""
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    resp = client.get(
        "/api/admin/venues/not-a-uuid",
        headers={**api_key_header, **auth_header(token)},
    )
    assert resp.status_code == 404


def test_admin_venue_detail_unknown_uuid_404(client, api_key_header):
    """Valid UUID that does not map to any venue -> 404."""
    random_uuid = str(uuid.uuid4())
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    resp = client.get(
        f"/api/admin/venues/{random_uuid}",
        headers={**api_key_header, **auth_header(token)},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Config Override tests (10)
# ---------------------------------------------------------------------------

def test_admin_override_billing_and_is_test(client, api_key_header):
    """Admin PATCH with billing_unit + is_test -> 200; DB updated; 2 audit rows."""
    original = _get_venue_fields(VENUE_A_ID)
    created_lead_ids = []
    try:
        token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
        headers = {**api_key_header, **auth_header(token)}

        # Pick values that differ from current
        new_billing = 99.99
        new_is_test = not bool(original["is_test"])

        resp = client.patch(
            f"/api/admin/venues/{VENUE_A_ID}",
            headers=headers,
            json={
                "reason": "Test override",
                "billing_unit": new_billing,
                "is_test": new_is_test,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "billing_unit" in body["updated_fields"]
        assert "is_test" in body["updated_fields"]
        assert body["overrides_recorded"] == 2

        # Verify venue row was updated
        after = _get_venue_fields(VENUE_A_ID)
        assert abs(float(after["billing_unit"]) - new_billing) < 0.001
        assert bool(after["is_test"]) == new_is_test

        # Verify audit rows in DB
        override_rows = _fetch_config_overrides(VENUE_A_ID)
        field_names = [r["field_name"] for r in override_rows]
        assert "billing_unit" in field_names
        assert "is_test" in field_names

        for row in override_rows:
            assert row["reason"] == "Test override"
            assert str(row["changed_by"]) == ADMIN_ID

        billing_row = next(r for r in override_rows if r["field_name"] == "billing_unit")
        assert billing_row["new_value"] == str(new_billing)

    finally:
        _restore_venue(VENUE_A_ID, original)
        _delete_config_overrides(VENUE_A_ID)
        # Verify clean restore
        restored = _get_venue_fields(VENUE_A_ID)
        assert abs(float(restored["billing_unit"]) - float(original["billing_unit"])) < 0.001
        assert restored["is_test"] == original["is_test"]
        assert _count_config_overrides(VENUE_A_ID) == 0
    _ = created_lead_ids  # suppress unused-variable warning


def test_admin_override_name(client, api_key_header):
    """Admin PATCH name -> 200; venue renamed; audit row written. Owners cannot do this."""
    original = _get_venue_fields(VENUE_A_ID)
    try:
        token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
        headers = {**api_key_header, **auth_header(token)}

        new_name = "Admin Renamed Venue"
        resp = client.patch(
            f"/api/admin/venues/{VENUE_A_ID}",
            headers=headers,
            json={"reason": "Owner requested rename", "name": new_name},
        )
        assert resp.status_code == 200
        assert "name" in resp.json()["updated_fields"]

        after = _get_venue_fields(VENUE_A_ID)
        assert after["name"] == new_name

        override_rows = _fetch_config_overrides(VENUE_A_ID)
        name_row = next(r for r in override_rows if r["field_name"] == "name")
        assert name_row["old_value"] == original["name"]
        assert name_row["new_value"] == new_name
        assert name_row["reason"] == "Owner requested rename"
    finally:
        _restore_venue(VENUE_A_ID, original)
        _delete_config_overrides(VENUE_A_ID)
        assert _get_venue_fields(VENUE_A_ID)["name"] == original["name"]
        assert _count_config_overrides(VENUE_A_ID) == 0


def test_admin_override_name_whitespace_422(client, api_key_header):
    """Admin PATCH name='   ' -> 422 (blank after strip); venue unchanged."""
    original = _get_venue_fields(VENUE_A_ID)
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    headers = {**api_key_header, **auth_header(token)}

    resp = client.patch(
        f"/api/admin/venues/{VENUE_A_ID}",
        headers=headers,
        json={"reason": "blank rename", "name": "   "},
    )
    assert resp.status_code == 422

    after = _get_venue_fields(VENUE_A_ID)
    assert after["name"] == original["name"]


def test_admin_override_reason_missing_422(client, api_key_header):
    """PATCH without reason key -> 422 (Pydantic required field)."""
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    resp = client.patch(
        f"/api/admin/venues/{VENUE_A_ID}",
        headers={**api_key_header, **auth_header(token)},
        json={"billing_unit": 5.00},
    )
    assert resp.status_code == 422


def test_admin_override_reason_empty_422(client, api_key_header):
    """PATCH with empty string reason -> 422 (min_length=1 or blank check)."""
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    resp = client.patch(
        f"/api/admin/venues/{VENUE_A_ID}",
        headers={**api_key_header, **auth_header(token)},
        json={"reason": "", "billing_unit": 5.00},
    )
    assert resp.status_code == 422


def test_admin_override_reason_whitespace_422(client, api_key_header):
    """PATCH with whitespace-only reason -> 422 (stripped blank check)."""
    original = _get_venue_fields(VENUE_A_ID)
    try:
        token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
        resp = client.patch(
            f"/api/admin/venues/{VENUE_A_ID}",
            headers={**api_key_header, **auth_header(token)},
            json={"reason": "   ", "billing_unit": 5.00},
        )
        assert resp.status_code == 422
    finally:
        _restore_venue(VENUE_A_ID, original)
        _delete_config_overrides(VENUE_A_ID)


def test_admin_override_extra_field_422(client, api_key_header):
    """PATCH with non-whitelisted field (slug) -> 422 (extra=forbid)."""
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    resp = client.patch(
        f"/api/admin/venues/{VENUE_A_ID}",
        headers={**api_key_header, **auth_header(token)},
        json={"reason": "test", "slug": "hacked-slug"},
    )
    assert resp.status_code == 422


def test_admin_override_no_fields_400(client, api_key_header):
    """PATCH with only reason (no overridable fields) -> 400."""
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    resp = client.patch(
        f"/api/admin/venues/{VENUE_A_ID}",
        headers={**api_key_header, **auth_header(token)},
        json={"reason": "test"},
    )
    assert resp.status_code == 400


def test_admin_override_owner_403(client, api_key_header):
    """venue_owner PATCH -> 403."""
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    resp = client.patch(
        f"/api/admin/venues/{VENUE_A_ID}",
        headers={**api_key_header, **auth_header(token)},
        json={"reason": "test", "billing_unit": 5.00},
    )
    assert resp.status_code == 403


def test_admin_override_staff_403(client, api_key_header):
    """venue_staff PATCH -> 403."""
    token = dev_login(client, api_key_header, STAFF_A_CLERK_ID)
    resp = client.patch(
        f"/api/admin/venues/{VENUE_A_ID}",
        headers={**api_key_header, **auth_header(token)},
        json={"reason": "test", "billing_unit": 5.00},
    )
    assert resp.status_code == 403


def test_admin_override_bad_venue_id_404(client, api_key_header):
    """PATCH with bad venue UUID -> 404."""
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    resp = client.patch(
        "/api/admin/venues/not-a-uuid",
        headers={**api_key_header, **auth_header(token)},
        json={"reason": "test", "billing_unit": 5.00},
    )
    assert resp.status_code == 404


def test_admin_override_unchanged_value_no_audit_row(client, api_key_header):
    """Providing the same value as current -> no audit row written (skip-if-equal)."""
    original = _get_venue_fields(VENUE_A_ID)
    try:
        token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
        # Send the CURRENT billing_unit value (unchanged)
        current_billing = float(original["billing_unit"])
        resp = client.patch(
            f"/api/admin/venues/{VENUE_A_ID}",
            headers={**api_key_header, **auth_header(token)},
            json={"reason": "no change test", "billing_unit": current_billing},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["overrides_recorded"] == 0
        assert body["updated_fields"] == []

        # No audit row must have been inserted
        assert _count_config_overrides(VENUE_A_ID) == 0
    finally:
        _restore_venue(VENUE_A_ID, original)
        _delete_config_overrides(VENUE_A_ID)


# ---------------------------------------------------------------------------
# Config History tests (2)
# ---------------------------------------------------------------------------

def test_admin_config_history_200(client, api_key_header):
    """Admin GET config-history after an override -> paginated shape with correct entry."""
    original = _get_venue_fields(VENUE_A_ID)
    try:
        token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
        headers = {**api_key_header, **auth_header(token)}

        # Create a known override
        new_billing = 77.77
        client.patch(
            f"/api/admin/venues/{VENUE_A_ID}",
            headers=headers,
            json={"reason": "History test reason", "billing_unit": new_billing},
        )

        resp = client.get(
            f"/api/admin/venues/{VENUE_A_ID}/config-history",
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "history" in body

        # A5 pagination fields must be present
        assert "total" in body
        assert body["total"] >= 1
        assert body["limit"] == 50
        assert body["offset"] == 0

        history = body["history"]
        assert len(history) >= 1

        # Newest entry should be the billing_unit we just changed
        entry = history[0]
        assert entry["field_name"] == "billing_unit"
        assert entry["new_value"] == str(new_billing)
        assert entry["reason"] == "History test reason"
        assert entry["changed_by_clerk_id"] == "dev_admin"

        # Verify newest-first ordering (if multiple rows)
        if len(history) > 1:
            from datetime import datetime
            first_ts = datetime.fromisoformat(history[0]["created_at"])
            last_ts = datetime.fromisoformat(history[-1]["created_at"])
            assert first_ts >= last_ts, "History must be newest-first"

    finally:
        _restore_venue(VENUE_A_ID, original)
        _delete_config_overrides(VENUE_A_ID)
        assert _count_config_overrides(VENUE_A_ID) == 0


def test_admin_config_history_limit_1(client, api_key_header):
    """?limit=1 returns at most 1 history item; total still reflects full count."""
    original = _get_venue_fields(VENUE_A_ID)
    try:
        token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
        headers = {**api_key_header, **auth_header(token)}

        # Insert two overrides so total >= 2
        client.patch(
            f"/api/admin/venues/{VENUE_A_ID}",
            headers=headers,
            json={"reason": "Pagination test A", "billing_unit": 11.11},
        )
        client.patch(
            f"/api/admin/venues/{VENUE_A_ID}",
            headers=headers,
            json={"reason": "Pagination test B", "billing_unit": 22.22},
        )

        resp = client.get(
            f"/api/admin/venues/{VENUE_A_ID}/config-history?limit=1",
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()

        # Only 1 row returned despite total >= 2
        assert len(body["history"]) <= 1
        assert body["limit"] == 1
        assert body["offset"] == 0
        # total must still reflect the full unfiltered count
        assert body["total"] >= 2

    finally:
        _restore_venue(VENUE_A_ID, original)
        _delete_config_overrides(VENUE_A_ID)
        assert _count_config_overrides(VENUE_A_ID) == 0


def test_admin_config_history_limit_too_large_422(client, api_key_header):
    """?limit=999 -> 422 (FastAPI ge=1,le=200 constraint)."""
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    resp = client.get(
        f"/api/admin/venues/{VENUE_A_ID}/config-history?limit=999",
        headers={**api_key_header, **auth_header(token)},
    )
    assert resp.status_code == 422


def test_admin_config_history_negative_offset_422(client, api_key_header):
    """?offset=-1 -> 422 (FastAPI ge=0 constraint)."""
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    resp = client.get(
        f"/api/admin/venues/{VENUE_A_ID}/config-history?offset=-1",
        headers={**api_key_header, **auth_header(token)},
    )
    assert resp.status_code == 422


def test_admin_config_history_owner_403(client, api_key_header):
    """venue_owner GET config-history -> 403."""
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    resp = client.get(
        f"/api/admin/venues/{VENUE_A_ID}/config-history",
        headers={**api_key_header, **auth_header(token)},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Support tests (5)
# ---------------------------------------------------------------------------

def test_admin_support_list_open(client, api_key_header):
    """Insert open message; admin GET ?status=open -> message appears in list."""
    msg_id = _insert_support_message(name="Test User", email="t@example.com")
    try:
        token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
        resp = client.get(
            "/api/admin/support?status=open",
            headers={**api_key_header, **auth_header(token)},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "messages" in body
        ids = [m["id"] for m in body["messages"]]
        assert msg_id in ids, "Inserted message must appear in open list"

        # All returned messages should be open
        for m in body["messages"]:
            assert m["status"] == "open"
    finally:
        _delete_support_message(msg_id)


def test_admin_support_patch_resolved(client, api_key_header):
    """Admin PATCH support message to resolved -> returned message has status=resolved."""
    msg_id = _insert_support_message(name="Resolve Me")
    try:
        token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
        resp = client.patch(
            f"/api/admin/support/{msg_id}",
            headers={**api_key_header, **auth_header(token)},
            json={"status": "resolved"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "resolved"
        assert body["id"] == msg_id
    finally:
        _delete_support_message(msg_id)


def test_admin_support_patch_reopen(client, api_key_header):
    """Admin PATCH resolved message to open -> returned message has status=open."""
    msg_id = _insert_support_message(name="Reopen Me", status="resolved")
    try:
        token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
        resp = client.patch(
            f"/api/admin/support/{msg_id}",
            headers={**api_key_header, **auth_header(token)},
            json={"status": "open"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "open"
    finally:
        _delete_support_message(msg_id)


def test_admin_support_owner_403(client, api_key_header):
    """venue_owner GET /api/admin/support -> 403."""
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    resp = client.get(
        "/api/admin/support",
        headers={**api_key_header, **auth_header(token)},
    )
    assert resp.status_code == 403


def test_admin_support_patch_not_found_404(client, api_key_header):
    """Admin PATCH with random (non-existent) UUID -> 404."""
    random_id = str(uuid.uuid4())
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    resp = client.patch(
        f"/api/admin/support/{random_id}",
        headers={**api_key_header, **auth_header(token)},
        json={"status": "resolved"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Leads tests (5)
# ---------------------------------------------------------------------------

def test_admin_leads_create_and_list(client, api_key_header):
    """Admin POST lead -> 201; GET leads list contains the new entry."""
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    headers = {**api_key_header, **auth_header(token)}
    lead_id = None
    try:
        resp = client.post(
            "/api/admin/leads",
            headers=headers,
            json={"name": "Test Lead", "email": "test@example.com"},
        )
        assert resp.status_code == 201
        created = resp.json()
        assert created["name"] == "Test Lead"
        assert created["email"] == "test@example.com"
        lead_id = created["id"]

        # Verify list endpoint includes it
        list_resp = client.get("/api/admin/leads", headers=headers)
        assert list_resp.status_code == 200
        leads = list_resp.json()["leads"]
        ids = [lead["id"] for lead in leads]
        assert lead_id in ids, "Created lead must appear in GET /leads"
    finally:
        if lead_id:
            _delete_lead(lead_id)


def test_admin_leads_create_name_only(client, api_key_header):
    """Admin POST lead with name only (no email) -> 201."""
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    lead_id = None
    try:
        resp = client.post(
            "/api/admin/leads",
            headers={**api_key_header, **auth_header(token)},
            json={"name": "Name Only"},
        )
        assert resp.status_code == 201
        lead_id = resp.json()["id"]
    finally:
        if lead_id:
            _delete_lead(lead_id)


def test_admin_leads_create_no_name_no_email_422(client, api_key_header):
    """Admin POST lead with neither name nor email -> 422."""
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    resp = client.post(
        "/api/admin/leads",
        headers={**api_key_header, **auth_header(token)},
        json={"phone": "0400000000"},
    )
    assert resp.status_code == 422


def test_admin_leads_list_owner_403(client, api_key_header):
    """venue_owner GET /api/admin/leads -> 403."""
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    resp = client.get(
        "/api/admin/leads",
        headers={**api_key_header, **auth_header(token)},
    )
    assert resp.status_code == 403


def test_admin_leads_create_owner_403(client, api_key_header):
    """venue_owner POST /api/admin/leads -> 403."""
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    resp = client.post(
        "/api/admin/leads",
        headers={**api_key_header, **auth_header(token)},
        json={"name": "Should Fail"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Team tests (2)
# ---------------------------------------------------------------------------

def test_admin_team_list(client, api_key_header):
    """Admin GET /api/admin/team -> 200; seeded users dev_admin + dev_owner_a present."""
    token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    resp = client.get(
        "/api/admin/team",
        headers={**api_key_header, **auth_header(token)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "users" in body
    users = body["users"]
    assert isinstance(users, list) and len(users) > 0

    clerk_ids = [u["clerk_user_id"] for u in users]
    assert "dev_admin" in clerk_ids, "dev_admin must appear in team list"
    assert "dev_owner_a" in clerk_ids, "dev_owner_a must appear in team list"

    admin_entry = next(u for u in users if u["clerk_user_id"] == "dev_admin")
    assert admin_entry["role"] == "admin"

    owner_entry = next(u for u in users if u["clerk_user_id"] == "dev_owner_a")
    assert owner_entry["role"] == "venue_owner"


def test_admin_team_owner_403(client, api_key_header):
    """venue_owner GET /api/admin/team -> 403."""
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    resp = client.get(
        "/api/admin/team",
        headers={**api_key_header, **auth_header(token)},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Support PATCH owner 403 (spec test #30)
# ---------------------------------------------------------------------------

def test_admin_support_patch_owner_403(client, api_key_header):
    """venue_owner PATCH support message -> 403."""
    msg_id = _insert_support_message(name="Owner Patch Test")
    try:
        token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        resp = client.patch(
            f"/api/admin/support/{msg_id}",
            headers={**api_key_header, **auth_header(token)},
            json={"status": "resolved"},
        )
        assert resp.status_code == 403
    finally:
        _delete_support_message(msg_id)


# ---------------------------------------------------------------------------
# Build #1: audit log written on venue config override
# ---------------------------------------------------------------------------

def _delete_audit_logs_for_venue(venue_id):
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute(
                "DELETE FROM admin_audit_log WHERE target_id = $1", venue_id,
            )
        finally:
            await conn.close()
    asyncio.run(_q())


def _fetch_audit_logs_for_venue(venue_id, action):
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await conn.fetch(
                "SELECT action, target_type, target_id, detail FROM admin_audit_log "
                "WHERE action = $1 AND target_id = $2 ORDER BY created_at DESC",
                action, venue_id,
            )
        finally:
            await conn.close()
    return asyncio.run(_q())


def test_venue_override_creates_audit_log(client, api_key_header):
    """PATCH venue config override also writes a row in admin_audit_log."""
    original = _get_venue_fields(VENUE_A_ID)
    try:
        token = dev_login(client, api_key_header, ADMIN_CLERK_ID)
        headers = {**api_key_header, **auth_header(token)}
        resp = client.patch(
            f"/api/admin/venues/{VENUE_A_ID}",
            headers=headers,
            json={"reason": "audit log test", "is_test": not original["is_test"]},
        )
        assert resp.status_code == 200, resp.text

        rows = _fetch_audit_logs_for_venue(VENUE_A_ID, 'venue_config_override')
        assert len(rows) >= 1, "Expected at least one audit_log row for venue_config_override"
        row = rows[0]
        assert row["action"] == "venue_config_override"
        assert row["target_type"] == "venue"
        assert row["target_id"] == VENUE_A_ID
        detail = row["detail"] if isinstance(row["detail"], dict) else json.loads(row["detail"])
        assert detail["field_name"] == "is_test"
        assert detail["reason"] == "audit log test"
    finally:
        _restore_venue(VENUE_A_ID, original)
        _delete_config_overrides(VENUE_A_ID)
        _delete_audit_logs_for_venue(VENUE_A_ID)
