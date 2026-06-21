"""Onboarding: the venue-setup wizard's backend (POST /api/dashboard/setup-venue).

A freshly-provisioned owner has role=venue_owner + venue_id NULL; setup creates their
venue + tables and links them. Uses a dev-login token for a hand-seeded pending owner.
"""
import asyncio
import os
import uuid

import asyncpg

from api.tests.conftest import dev_login


def _seed_pending_owner(clerk_id):
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute(
                "INSERT INTO users (id, clerk_user_id, role, venue_id) "
                "VALUES (gen_random_uuid(), $1, 'venue_owner', NULL)",
                clerk_id,
            )
        finally:
            await conn.close()
    asyncio.run(_q())


def _venue_of(clerk_id):
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            vid = await conn.fetchval("SELECT venue_id FROM users WHERE clerk_user_id = $1", clerk_id)
            tcount = await conn.fetchval("SELECT COUNT(*) FROM tables WHERE venue_id = $1", vid) if vid else 0
            return vid, tcount
        finally:
            await conn.close()
    return asyncio.run(_q())


def _cleanup(clerk_id):
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            vid = await conn.fetchval("SELECT venue_id FROM users WHERE clerk_user_id = $1", clerk_id)
            await conn.execute("DELETE FROM users WHERE clerk_user_id = $1", clerk_id)
            if vid:
                await conn.execute("DELETE FROM tables WHERE venue_id = $1", vid)
                await conn.execute("DELETE FROM venues WHERE id = $1", vid)
        finally:
            await conn.close()
    asyncio.run(_q())


def test_setup_venue_creates_venue_tables_and_links_owner(client, api_key_header):
    clerk_id = f"test-setup-{uuid.uuid4()}"
    _seed_pending_owner(clerk_id)
    try:
        token = dev_login(client, api_key_header, clerk_id)
        h = {**api_key_header, "Authorization": f"Bearer {token}"}
        resp = client.post("/api/dashboard/setup-venue", headers=h, json={
            "name": "The Test Tavern!", "venue_type": "bar", "table_count": 4, "allow_adult": True,
            "address": "1 Test St, Melbourne", "latitude": -37.81, "longitude": 144.96,
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["slug"].startswith("the-test-tavern")  # punctuation stripped
        assert data["table_count"] == 4

        vid, tcount = _venue_of(clerk_id)
        assert vid is not None, "owner's venue_id must be set"
        assert tcount == 4, "4 tables created"

        # Re-setup is blocked once the owner has a venue.
        resp2 = client.post("/api/dashboard/setup-venue", headers=h, json={
            "name": "Second Venue", "venue_type": "pub", "table_count": 1,
        })
        assert resp2.status_code == 409, resp2.text
    finally:
        _cleanup(clerk_id)


def test_setup_venue_rejects_bad_input(client, api_key_header):
    clerk_id = f"test-setup-{uuid.uuid4()}"
    _seed_pending_owner(clerk_id)
    try:
        token = dev_login(client, api_key_header, clerk_id)
        h = {**api_key_header, "Authorization": f"Bearer {token}"}
        # bad venue_type
        assert client.post("/api/dashboard/setup-venue", headers=h, json={
            "name": "X", "venue_type": "nightclub", "table_count": 1}).status_code == 422
        # too many tables
        assert client.post("/api/dashboard/setup-venue", headers=h, json={
            "name": "X", "venue_type": "bar", "table_count": 999}).status_code == 422
        # unknown field
        assert client.post("/api/dashboard/setup-venue", headers=h, json={
            "name": "X", "venue_type": "bar", "table_count": 1, "evil": 1}).status_code == 422
    finally:
        _cleanup(clerk_id)
