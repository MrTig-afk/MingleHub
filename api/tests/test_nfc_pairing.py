import asyncio
import os

import asyncpg
import pytest

from api.dev_fixtures import (
    OWNER_A_CLERK_ID,
    OWNER_B_CLERK_ID,
    STAFF_A_CLERK_ID,
)
from api.tests.conftest import dev_login, fresh_tag_uid
from api.tests.test_auth import auth_header


@pytest.fixture(scope="module", autouse=True)
def _cleanup_test_tags():
    """Deletes every nfc_tags row this module creates once it's done.

    Unlike scripts/seed_platform.py's deterministic upserts, these tests
    pair fresh random tag_uids on every run — without cleanup they'd pile
    up in the shared dev DB forever (and inflate /dashboard/tables results).
    """
    yield

    async def _delete():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute("DELETE FROM nfc_tags WHERE tag_uid LIKE 'test-tag-%'")
        finally:
            await conn.close()

    asyncio.run(_delete())


def test_pair_tag_requires_venue_owner(client, api_key_header):
    token = dev_login(client, api_key_header, STAFF_A_CLERK_ID)
    headers = {**api_key_header, **auth_header(token)}
    resp = client.post(
        "/api/dashboard/pair-tag",
        headers=headers,
        json={"tag_uid": fresh_tag_uid(), "table_number": 1},
    )
    assert resp.status_code == 403


def test_pair_tag_creates_new_tag_for_owners_table(client, api_key_header):
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    headers = {**api_key_header, **auth_header(token)}
    tag_uid = fresh_tag_uid()

    resp = client.post(
        "/api/dashboard/pair-tag",
        headers=headers,
        json={"tag_uid": tag_uid, "table_number": 1},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tag_uid"] == tag_uid
    assert body["status"] == "active"
    assert body["table_number"] == 1
    assert "aes_key_encrypted" not in body  # never returned to the client


def test_pair_tag_rejects_table_not_in_venue(client, api_key_header):
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    headers = {**api_key_header, **auth_header(token)}
    resp = client.post(
        "/api/dashboard/pair-tag",
        headers=headers,
        json={"tag_uid": fresh_tag_uid(), "table_number": 999},
    )
    assert resp.status_code == 404


def test_pair_tag_moves_existing_tag_within_same_venue(client, api_key_header):
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    headers = {**api_key_header, **auth_header(token)}
    tag_uid = fresh_tag_uid()

    first = client.post(
        "/api/dashboard/pair-tag",
        headers=headers,
        json={"tag_uid": tag_uid, "table_number": 1},
    )
    assert first.status_code == 200
    assert first.json()["table_number"] == 1

    moved = client.post(
        "/api/dashboard/pair-tag",
        headers=headers,
        json={"tag_uid": tag_uid, "table_number": 2},
    )
    assert moved.status_code == 200
    assert moved.json()["table_number"] == 2
    assert moved.json()["id"] == first.json()["id"]  # same tag row, re-pointed


def test_pair_tag_rejects_uid_already_owned_by_other_venue(client, api_key_header):
    """BOLA proof: owner B can't claim a tag_uid owner A already paired."""
    token_a = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    token_b = dev_login(client, api_key_header, OWNER_B_CLERK_ID)
    tag_uid = fresh_tag_uid()

    paired = client.post(
        "/api/dashboard/pair-tag",
        headers={**api_key_header, **auth_header(token_a)},
        json={"tag_uid": tag_uid, "table_number": 1},
    )
    assert paired.status_code == 200

    stolen = client.post(
        "/api/dashboard/pair-tag",
        headers={**api_key_header, **auth_header(token_b)},
        json={"tag_uid": tag_uid, "table_number": 1},
    )
    assert stolen.status_code == 409


def test_list_tags_returns_only_own_venue_tags(client, api_key_header):
    token_a = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    token_b = dev_login(client, api_key_header, OWNER_B_CLERK_ID)
    tag_uid = fresh_tag_uid()

    client.post(
        "/api/dashboard/pair-tag",
        headers={**api_key_header, **auth_header(token_a)},
        json={"tag_uid": tag_uid, "table_number": 1},
    )

    tags_a = client.get("/api/dashboard/tags", headers={**api_key_header, **auth_header(token_a)})
    tags_b = client.get("/api/dashboard/tags", headers={**api_key_header, **auth_header(token_b)})

    assert tags_a.status_code == 200
    assert any(t["tag_uid"] == tag_uid for t in tags_a.json())
    assert all("aes_key_encrypted" not in t for t in tags_a.json())
    assert not any(t["tag_uid"] == tag_uid for t in tags_b.json())


def test_list_tables_returns_only_own_venue_tables(client, api_key_header):
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    headers = {**api_key_header, **auth_header(token)}
    resp = client.get("/api/dashboard/tables", headers=headers)
    assert resp.status_code == 200
    table_numbers = {t["table_number"] for t in resp.json()}
    assert table_numbers == {1, 2}
