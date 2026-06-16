import asyncio
import os
import uuid

import asyncpg
import pytest

from api.tests.conftest import pair_tag, simulate_tap


@pytest.fixture(autouse=True)
def _cleanup_test_tags():
    """nfc_tags isn't covered by fresh_table's teardown (a tag can outlive
    the table it was last paired to) — same prefix-based cleanup as the
    other test modules."""
    yield

    async def _delete():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute("DELETE FROM nfc_tags WHERE tag_uid LIKE 'test-tag-%'")
        finally:
            await conn.close()

    asyncio.run(_delete())


def _fresh_phone():
    return f"test-phone-{uuid.uuid4()}"


def _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, counter, phone_id):
    sig = simulate_tap(client, api_key_header, tag_uid, counter)
    resp = client.get(
        "/api/patron/tap",
        headers=api_key_header,
        params={
            "venue_slug": venue_slug, "table_number": table_number,
            "tag_uid": tag_uid, "counter": counter, "sig": sig, "phone_id": phone_id,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _claim_host(client, api_key_header, lobby_id, phone_id):
    resp = client.post(f"/api/patron/lobby/{lobby_id}/claim-host", headers=api_key_header, json={"phone_id": phone_id})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _start(client, api_key_header, lobby_id, phone_id, player_count=2, **kwargs):
    return client.post(
        f"/api/patron/lobby/{lobby_id}/start",
        headers=api_key_header,
        json={"phone_id": phone_id, "player_count": player_count, **kwargs},
    )


def test_first_tap_with_no_session_creates_a_lobby(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    body = _tap_with_phone(
        client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"], tag_uid, 1, _fresh_phone()
    )

    state = body["table_state"]
    assert state["phase"] == "lobby"
    assert state["phone_count"] == 1
    assert state["host_phone_id"] is None


def test_second_phone_tapping_joins_the_same_lobby(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    venue_slug, table_number = fresh_table["venue_slug"], fresh_table["table_number"]
    phone_a, phone_b = _fresh_phone(), _fresh_phone()

    first = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 1, phone_a)
    second = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 2, phone_b)

    assert first["table_state"]["lobby_id"] == second["table_state"]["lobby_id"]
    assert second["table_state"]["phone_count"] == 2


def test_retapping_same_phone_does_not_double_count(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    venue_slug, table_number = fresh_table["venue_slug"], fresh_table["table_number"]
    phone_id = _fresh_phone()

    first = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 1, phone_id)
    second = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 2, phone_id)

    assert first["table_state"]["phone_count"] == 1
    assert second["table_state"]["phone_count"] == 1


def test_claim_host_first_wins_second_is_told_who_won(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    venue_slug, table_number = fresh_table["venue_slug"], fresh_table["table_number"]
    phone_a, phone_b = _fresh_phone(), _fresh_phone()
    body = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 1, phone_a)
    _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 2, phone_b)
    lobby_id = body["table_state"]["lobby_id"]

    assert _claim_host(client, api_key_header, lobby_id, phone_a) == {"you_are_host": True, "host_phone_id": phone_a}
    assert _claim_host(client, api_key_header, lobby_id, phone_b) == {"you_are_host": False, "host_phone_id": phone_a}


def test_claim_host_rejects_phone_not_in_lobby(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    body = _tap_with_phone(
        client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"], tag_uid, 1, _fresh_phone()
    )
    lobby_id = body["table_state"]["lobby_id"]

    resp = client.post(
        f"/api/patron/lobby/{lobby_id}/claim-host", headers=api_key_header, json={"phone_id": _fresh_phone()}
    )
    assert resp.status_code == 403


def test_start_requires_host(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    venue_slug, table_number = fresh_table["venue_slug"], fresh_table["table_number"]
    phone_a, phone_b = _fresh_phone(), _fresh_phone()
    body = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 1, phone_a)
    _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 2, phone_b)
    lobby_id = body["table_state"]["lobby_id"]
    _claim_host(client, api_key_header, lobby_id, phone_a)

    resp = _start(client, api_key_header, lobby_id, phone_b)
    assert resp.status_code == 403


def test_start_rejects_out_of_range_player_count(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    phone_id = _fresh_phone()
    body = _tap_with_phone(
        client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"], tag_uid, 1, phone_id
    )
    lobby_id = body["table_state"]["lobby_id"]
    _claim_host(client, api_key_header, lobby_id, phone_id)

    resp = _start(client, api_key_header, lobby_id, phone_id, player_count=1)
    assert resp.status_code == 422


def test_host_start_creates_session_and_converts_lobby(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    phone_id = _fresh_phone()
    body = _tap_with_phone(
        client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"], tag_uid, 1, phone_id
    )
    lobby_id = body["table_state"]["lobby_id"]
    _claim_host(client, api_key_header, lobby_id, phone_id)

    start = _start(
        client, api_key_header, lobby_id, phone_id, player_count=3,
        player_names=["Kaushik", "Sarah", "James"],
    )
    assert start.status_code == 200, start.text
    assert start.json()["player_count"] == 3
    assert start.json()["group_label"] == f"Table {fresh_table['table_number']} Group 1"

    poll = client.get(f"/api/patron/lobby/{lobby_id}", headers=api_key_header)
    assert poll.status_code == 200
    assert poll.json()["status"] == "converted"
    assert poll.json()["converted_session_id"] == start.json()["session_id"]


def test_tap_after_session_active_shows_join_or_new(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    venue_slug, table_number = fresh_table["venue_slug"], fresh_table["table_number"]
    host_phone = _fresh_phone()
    body = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 1, host_phone)
    lobby_id = body["table_state"]["lobby_id"]
    _claim_host(client, api_key_header, lobby_id, host_phone)
    _start(client, api_key_header, lobby_id, host_phone)

    late = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 2, _fresh_phone())
    assert late["table_state"]["phase"] == "join_or_new"
    assert len(late["table_state"]["groups"]) == 1
    assert late["table_state"]["table_id"] == fresh_table["table_id"]


def test_join_existing_session_adds_a_player(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    host_phone = _fresh_phone()
    body = _tap_with_phone(
        client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"], tag_uid, 1, host_phone
    )
    lobby_id = body["table_state"]["lobby_id"]
    _claim_host(client, api_key_header, lobby_id, host_phone)
    start = _start(client, api_key_header, lobby_id, host_phone)
    session_id = start.json()["session_id"]

    join = client.post(
        f"/api/patron/sessions/{session_id}/join",
        headers=api_key_header,
        json={"phone_id": _fresh_phone(), "name": "Late Arrival"},
    )
    assert join.status_code == 200
    assert join.json()["name"] == "Late Arrival"


def test_join_rejects_ended_session(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    host_phone = _fresh_phone()
    body = _tap_with_phone(
        client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"], tag_uid, 1, host_phone
    )
    lobby_id = body["table_state"]["lobby_id"]
    _claim_host(client, api_key_header, lobby_id, host_phone)
    start = _start(client, api_key_header, lobby_id, host_phone)
    session_id = start.json()["session_id"]

    async def _end():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute("UPDATE game_sessions SET ended_at = NOW() WHERE id = $1", session_id)
        finally:
            await conn.close()
    asyncio.run(_end())

    join = client.post(
        f"/api/patron/sessions/{session_id}/join",
        headers=api_key_header,
        json={"phone_id": _fresh_phone()},
    )
    assert join.status_code == 404


def test_new_group_creates_second_lobby_while_first_session_active(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    host_phone = _fresh_phone()
    body = _tap_with_phone(
        client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"], tag_uid, 1, host_phone
    )
    lobby_id = body["table_state"]["lobby_id"]
    _claim_host(client, api_key_header, lobby_id, host_phone)
    _start(client, api_key_header, lobby_id, host_phone)

    new_group = client.post(
        f"/api/patron/table/{fresh_table['table_id']}/new-group",
        headers=api_key_header, json={"phone_id": _fresh_phone()},
    )
    assert new_group.status_code == 200
    assert new_group.json()["lobby_id"] != lobby_id


def test_new_group_rejects_when_table_already_has_three_groups(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    venue_slug, table_number, table_id = (
        fresh_table["venue_slug"], fresh_table["table_number"], fresh_table["table_id"]
    )

    for i in range(3):
        host_phone = _fresh_phone()
        if i == 0:
            body = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, i + 1, host_phone)
            lobby_id = body["table_state"]["lobby_id"]
        else:
            resp = client.post(
                f"/api/patron/table/{table_id}/new-group", headers=api_key_header, json={"phone_id": host_phone}
            )
            assert resp.status_code == 200
            lobby_id = resp.json()["lobby_id"]
        _claim_host(client, api_key_header, lobby_id, host_phone)
        _start(client, api_key_header, lobby_id, host_phone)

    full = client.post(
        f"/api/patron/table/{table_id}/new-group", headers=api_key_header, json={"phone_id": _fresh_phone()}
    )
    assert full.status_code == 409

    # The 4th tap should also see table_full, not lobby or join_or_new.
    fourth_tap = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 99, _fresh_phone())
    assert fourth_tap["table_state"]["phase"] == "table_full"
    assert len(fourth_tap["table_state"]["groups"]) == 3
