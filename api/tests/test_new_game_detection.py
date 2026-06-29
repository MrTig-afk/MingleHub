"""Tests for GET /api/patron/table/{table_id}/new-game (new-game detection endpoint).

Verifies that stranded Recap phones can discover when a new game is forming:
  - Returns null when no new game has started yet.
  - Returns lobby_id once the host has tapped "New game" (open lobby exists).
  - Returns session_id once the new lobby has been converted into a session.
  - Returns 404 for a non-existent table.
  - Works without an after_session parameter (matches any active session).

Mirrors test_new_game.py: signed taps via simulate_tap, TestClient for HTTP,
fresh_table / owner_a_token fixtures from conftest.
"""
import asyncio
import os
import uuid

import asyncpg
import pytest

from api.tests.conftest import pair_tag, simulate_tap


@pytest.fixture(autouse=True)
def _cleanup_test_tags():
    yield

    async def _d():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute("DELETE FROM nfc_tags WHERE tag_uid LIKE 'test-tag-%'")
        finally:
            await conn.close()

    asyncio.run(_d())


def _fresh_phone():
    return f"test-phone-{uuid.uuid4()}"


def _tap(client, h, venue_slug, table_number, tag_uid, counter, phone_id, new_game=False):
    sig = simulate_tap(client, h, tag_uid, counter)
    params = {
        "venue_slug": venue_slug, "table_number": table_number,
        "tag_uid": tag_uid, "counter": counter, "sig": sig, "phone_id": phone_id,
    }
    if new_game:
        params["new_game"] = "1"
    resp = client.get("/api/patron/tap", headers=h, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _setup_session(client, h, owner_a_token, table_info, num_phones=2):
    tag_uid = pair_tag(client, h, owner_a_token, table_info["table_number"])
    phones = [_fresh_phone() for _ in range(num_phones)]
    first = None
    for i, phone in enumerate(phones):
        body = _tap(client, h, table_info["venue_slug"], table_info["table_number"],
                    tag_uid, i + 1, phone)
        if i == 0:
            first = body
    lobby_id = first["table_state"]["lobby_id"]
    table_id = first["table_id"]
    for i, phone in enumerate(phones):
        client.post(f"/api/patron/lobby/{lobby_id}/set-name",
                    headers=h, json={"phone_id": phone, "name": f"P{i + 1}"})
    client.post(f"/api/patron/lobby/{lobby_id}/claim-host",
                headers=h, json={"phone_id": phones[0]})
    start = client.post(f"/api/patron/lobby/{lobby_id}/start",
                        headers=h, json={"phone_id": phones[0], "adults_only": False})
    assert start.status_code == 200, start.text
    return {
        "session_id": start.json()["session_id"],
        "table_id": table_id,
        "origin": phones[0],
        "phones": phones,
        "tag_uid": tag_uid,
        "next_counter": num_phones + 1,
    }


def _end_game(client, h, session_id, phone_id):
    resp = client.post(f"/api/patron/sessions/{session_id}/end-game",
                       headers=h, json={"phone_id": phone_id})
    assert resp.status_code == 200, resp.text


def test_new_game_endpoint_returns_null_when_no_new_game(
    client, api_key_header, owner_a_token, fresh_table
):
    """After ending a game, the endpoint returns null lobby/session when
    no one has started a new game yet."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table)
    _end_game(client, api_key_header, s["session_id"], s["origin"])
    resp = client.get(
        f"/api/patron/table/{s['table_id']}/new-game",
        headers=api_key_header,
        params={"after_session": s["session_id"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["lobby_id"] is None
    assert body["session_id"] is None


def test_new_game_endpoint_returns_lobby_after_host_new_game(
    client, api_key_header, owner_a_token, fresh_table
):
    """After the host taps New game (creating a fresh lobby), the endpoint
    returns that lobby_id."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table)
    _end_game(client, api_key_header, s["session_id"], s["origin"])
    # Host taps "New game" -> creates a fresh lobby
    new_tap = _tap(
        client, api_key_header, fresh_table["venue_slug"],
        fresh_table["table_number"], s["tag_uid"], s["next_counter"],
        s["origin"], new_game=True,
    )
    assert new_tap["table_state"]["phase"] == "lobby"
    new_lobby_id = new_tap["table_state"]["lobby_id"]
    # Now poll the detection endpoint from another phone's perspective
    resp = client.get(
        f"/api/patron/table/{s['table_id']}/new-game",
        headers=api_key_header,
        params={"after_session": s["session_id"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["lobby_id"] == new_lobby_id
    assert body["session_id"] is None


def test_new_game_endpoint_returns_session_after_new_game_starts(
    client, api_key_header, owner_a_token, fresh_table
):
    """After a new game is started (lobby converted), the endpoint returns
    the new session_id."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table)
    _end_game(client, api_key_header, s["session_id"], s["origin"])
    # Two phones start a new game
    phone_a = s["origin"]
    phone_b = _fresh_phone()
    c = s["next_counter"]
    _tap(client, api_key_header, fresh_table["venue_slug"],
         fresh_table["table_number"], s["tag_uid"], c, phone_a, new_game=True)
    c += 1
    _tap(client, api_key_header, fresh_table["venue_slug"],
         fresh_table["table_number"], s["tag_uid"], c, phone_b, new_game=True)
    # Find the lobby, set names, claim host, start
    lobby_resp = _tap(client, api_key_header, fresh_table["venue_slug"],
                      fresh_table["table_number"], s["tag_uid"], c + 1,
                      phone_a, new_game=True)
    lobby_id = lobby_resp["table_state"]["lobby_id"]
    client.post(f"/api/patron/lobby/{lobby_id}/set-name",
                headers=api_key_header,
                json={"phone_id": phone_a, "name": "Host"})
    client.post(f"/api/patron/lobby/{lobby_id}/set-name",
                headers=api_key_header,
                json={"phone_id": phone_b, "name": "Guest"})
    client.post(f"/api/patron/lobby/{lobby_id}/claim-host",
                headers=api_key_header,
                json={"phone_id": phone_a})
    start = client.post(f"/api/patron/lobby/{lobby_id}/start",
                        headers=api_key_header,
                        json={"phone_id": phone_a, "adults_only": False})
    assert start.status_code == 200
    new_session_id = start.json()["session_id"]
    # Detection endpoint should return the new session
    resp = client.get(
        f"/api/patron/table/{s['table_id']}/new-game",
        headers=api_key_header,
        params={"after_session": s["session_id"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == new_session_id


def test_new_game_endpoint_404_for_bad_table(client, api_key_header):
    """A non-existent table_id returns 404."""
    resp = client.get(
        f"/api/patron/table/{uuid.uuid4()}/new-game",
        headers=api_key_header,
    )
    assert resp.status_code == 404


def test_new_game_endpoint_no_after_session(
    client, api_key_header, owner_a_token, fresh_table
):
    """Omitting after_session still works (returns any active session/lobby)."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table)
    # Session is still live, endpoint should find it
    resp = client.get(
        f"/api/patron/table/{s['table_id']}/new-game",
        headers=api_key_header,
    )
    assert resp.status_code == 200
    body = resp.json()
    # Live session has no open lobby (the lobby was converted), but the session is active
    assert body["session_id"] == s["session_id"]
