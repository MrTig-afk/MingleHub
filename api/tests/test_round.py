import asyncio
import os
import uuid

import asyncpg
import pytest

from api.tests.conftest import pair_tag, simulate_tap


@pytest.fixture(autouse=True)
def _cleanup_test_tags():
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


def _set_name(client, api_key_header, lobby_id, phone_id, name):
    resp = client.post(
        f"/api/patron/lobby/{lobby_id}/set-name",
        headers=api_key_header,
        json={"phone_id": phone_id, "name": name},
    )
    assert resp.status_code == 200, resp.text


def _start_session(client, api_key_header, fresh_table, owner_a_token, player_count=3, names=None):
    """Pairs a tag, taps player_count phones, sets names, claims host, and starts a game.

    Returns (session_id, origin_phone_id).
    """
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    phones = [_fresh_phone() for _ in range(player_count)]
    body = _tap_with_phone(
        client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"], tag_uid, 1, phones[0]
    )
    lobby_id = body["table_state"]["lobby_id"]
    for i, phone in enumerate(phones[1:], start=2):
        _tap_with_phone(
            client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"], tag_uid, i, phone
        )
    for i, phone in enumerate(phones):
        name = names[i] if names and i < len(names) else f"Player {i + 1}"
        _set_name(client, api_key_header, lobby_id, phone, name)
    client.post(f"/api/patron/lobby/{lobby_id}/claim-host", headers=api_key_header, json={"phone_id": phones[0]})
    start = client.post(
        f"/api/patron/lobby/{lobby_id}/start",
        headers=api_key_header,
        json={"phone_id": phones[0]},
    )
    assert start.status_code == 200, start.text
    return start.json()["session_id"], phones[0]


def _pick(client, api_key_header, session_id, phone_id):
    return client.post(
        f"/api/patron/sessions/{session_id}/select-hot-seat",
        headers=api_key_header,
        json={"phone_id": phone_id},
    )


def test_pick_returns_a_player_from_the_session(client, api_key_header, owner_a_token, fresh_table):
    session_id, origin_phone = _start_session(
        client, api_key_header, fresh_table, owner_a_token, player_count=3, names=["Kaushik", "Sarah", "James"]
    )

    resp = _pick(client, api_key_header, session_id, origin_phone)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] in ["Kaushik", "Sarah", "James"]
    assert body["times_selected"] == 1


def test_pick_rejects_phone_that_is_not_the_origin(client, api_key_header, owner_a_token, fresh_table):
    session_id, _origin_phone = _start_session(client, api_key_header, fresh_table, owner_a_token)

    resp = _pick(client, api_key_header, session_id, _fresh_phone())
    assert resp.status_code == 403


def test_pick_excludes_previous_winner_with_three_or_more_players(client, api_key_header, owner_a_token, fresh_table):
    session_id, origin_phone = _start_session(
        client, api_key_header, fresh_table, owner_a_token, player_count=3, names=["A", "B", "C"]
    )

    first = _pick(client, api_key_header, session_id, origin_phone).json()
    # Run several more picks — none should immediately repeat the previous winner.
    last_winner = first["name"]
    for _ in range(10):
        nxt = _pick(client, api_key_header, session_id, origin_phone).json()
        assert nxt["name"] != last_winner
        last_winner = nxt["name"]


def test_pick_increments_times_selected_cumulatively(client, api_key_header, owner_a_token, fresh_table):
    session_id, origin_phone = _start_session(
        client, api_key_header, fresh_table, owner_a_token, player_count=2, names=["A", "B"]
    )

    totals = {"A": 0, "B": 0}
    for _ in range(6):
        body = _pick(client, api_key_header, session_id, origin_phone).json()
        totals[body["name"]] = body["times_selected"]

    async def _scores():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            rows = await conn.fetch(
                "SELECT name, times_selected FROM game_players WHERE session_id = $1", session_id
            )
        finally:
            await conn.close()
        return {r["name"]: r["times_selected"] for r in rows}

    db_totals = asyncio.run(_scores())
    assert db_totals["A"] + db_totals["B"] == 6
    assert db_totals == totals


def test_pick_rejects_ended_session(client, api_key_header, owner_a_token, fresh_table):
    session_id, origin_phone = _start_session(client, api_key_header, fresh_table, owner_a_token)

    async def _end():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute("UPDATE game_sessions SET ended_at = NOW() WHERE id = $1", session_id)
        finally:
            await conn.close()
    asyncio.run(_end())

    resp = _pick(client, api_key_header, session_id, origin_phone)
    assert resp.status_code == 409


def test_pick_rejects_unknown_session(client, api_key_header):
    resp = _pick(client, api_key_header, str(uuid.uuid4()), _fresh_phone())
    assert resp.status_code == 404
