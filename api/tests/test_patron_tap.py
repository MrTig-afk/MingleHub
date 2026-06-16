import asyncio
import os

import asyncpg
import pytest

from api.tests.conftest import fresh_tag_uid, pair_tag, simulate_tap


@pytest.fixture(scope="module", autouse=True)
def _cleanup_test_tags():
    """See test_nfc_pairing.py — same reasoning, same prefix convention."""
    yield

    async def _delete():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute("DELETE FROM nfc_tags WHERE tag_uid LIKE 'test-tag-%'")
        finally:
            await conn.close()

    asyncio.run(_delete())


def test_valid_tap_succeeds_and_returns_venue_info(client, api_key_header, owner_a_token):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, 1)
    sig = simulate_tap(client, api_key_header, tag_uid, 1)

    resp = client.get(
        "/api/patron/tap",
        headers=api_key_header,
        params={"venue_slug": "lions-den", "table_number": 1, "tag_uid": tag_uid, "counter": 1, "sig": sig},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["venue_name"] == "The Lion's Den"
    assert body["table_number"] == 1


def test_replayed_counter_is_rejected(client, api_key_header, owner_a_token):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, 1)
    sig = simulate_tap(client, api_key_header, tag_uid, 5)
    params = {"venue_slug": "lions-den", "table_number": 1, "tag_uid": tag_uid, "counter": 5, "sig": sig}

    first = client.get("/api/patron/tap", headers=api_key_header, params=params)
    assert first.status_code == 200

    replay = client.get("/api/patron/tap", headers=api_key_header, params=params)
    assert replay.status_code == 401

    # A lower counter than what's already been seen is rejected too.
    lower_sig = simulate_tap(client, api_key_header, tag_uid, 3)
    lower = client.get(
        "/api/patron/tap",
        headers=api_key_header,
        params={"venue_slug": "lions-den", "table_number": 1, "tag_uid": tag_uid, "counter": 3, "sig": lower_sig},
    )
    assert lower.status_code == 401


def test_wrong_signature_is_rejected(client, api_key_header, owner_a_token):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, 1)
    resp = client.get(
        "/api/patron/tap",
        headers=api_key_header,
        params={"venue_slug": "lions-den", "table_number": 1, "tag_uid": tag_uid, "counter": 1, "sig": "0" * 64},
    )
    assert resp.status_code == 401


def test_unknown_tag_uid_is_rejected(client, api_key_header):
    resp = client.get(
        "/api/patron/tap",
        headers=api_key_header,
        params={"venue_slug": "lions-den", "table_number": 1, "tag_uid": fresh_tag_uid(), "counter": 1, "sig": "0" * 64},
    )
    assert resp.status_code == 401


def test_tag_paired_to_different_venue_is_rejected_via_other_venues_route(client, api_key_header, owner_a_token):
    """A tag paired at venue A can't be used to tap into venue B's table,
    even with a perfectly valid signature for that tag — proves the
    lookup is scoped by venue_id + table_id, not tag_uid alone."""
    tag_uid = pair_tag(client, api_key_header, owner_a_token, 1)
    sig = simulate_tap(client, api_key_header, tag_uid, 1)

    resp = client.get(
        "/api/patron/tap",
        headers=api_key_header,
        params={"venue_slug": "brew-house", "table_number": 1, "tag_uid": tag_uid, "counter": 1, "sig": sig},
    )
    assert resp.status_code == 401


def test_revoked_tag_is_rejected(client, api_key_header, owner_a_token):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, 1)
    sig = simulate_tap(client, api_key_header, tag_uid, 1)

    async def _revoke():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute("UPDATE nfc_tags SET status = 'revoked' WHERE tag_uid = $1", tag_uid)
        finally:
            await conn.close()
    asyncio.run(_revoke())

    resp = client.get(
        "/api/patron/tap",
        headers=api_key_header,
        params={"venue_slug": "lions-den", "table_number": 1, "tag_uid": tag_uid, "counter": 1, "sig": sig},
    )
    assert resp.status_code == 401


def test_unknown_venue_slug_returns_404(client, api_key_header):
    resp = client.get(
        "/api/patron/tap",
        headers=api_key_header,
        params={"venue_slug": "not-a-real-venue", "table_number": 1, "tag_uid": "x", "counter": 1, "sig": "0" * 64},
    )
    assert resp.status_code == 404


def test_malformed_venue_slug_returns_404(client, api_key_header):
    resp = client.get(
        "/api/patron/tap",
        headers=api_key_header,
        params={"venue_slug": "../etc/passwd", "table_number": 1, "tag_uid": "x", "counter": 1, "sig": "0" * 64},
    )
    assert resp.status_code == 404
