"""Tests for today's patron + dashboard changes (commits 946a809..61756a8):

  - new_game=1 tap bypasses the recap-lock  (resolve_table_state force_new)
  - a plain re-tap after End still shows recap          (control)
  - new_game on a LIVE session still resumes            (can't hijack/abandon a game)
  - /dashboard/overview active_sessions carry table_id  (Home click-through)

Mirrors test_retap.py: signed taps via simulate_tap, TestClient for HTTP,
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


def test_plain_retap_after_end_shows_recap(client, api_key_header, owner_a_token, fresh_table):
    """Control: a plain re-tap by the origin within the recap window -> phase 'recap'."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table)
    _end_game(client, api_key_header, s["session_id"], s["origin"])
    body = _tap(client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"],
                s["tag_uid"], s["next_counter"], s["origin"])
    assert body["table_state"]["phase"] == "recap", body["table_state"]


def test_new_game_tap_bypasses_recap_lock(client, api_key_header, owner_a_token, fresh_table):
    """new_game=1 re-tap after End -> fresh lobby (force_new skips the recap-lock)."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table)
    _end_game(client, api_key_header, s["session_id"], s["origin"])
    body = _tap(client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"],
                s["tag_uid"], s["next_counter"], s["origin"], new_game=True)
    assert body["table_state"]["phase"] == "lobby", body["table_state"]
    assert "lobby_id" in body["table_state"]


def test_new_game_does_not_disrupt_active_session(client, api_key_header, owner_a_token, fresh_table):
    """Security: new_game on a phone whose session is LIVE still resumes (the resume
    check runs before force_new), so 'New game' can't abandon/hijack an in-progress game."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table)
    body = _tap(client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"],
                s["tag_uid"], s["next_counter"], s["origin"], new_game=True)
    assert body["table_state"]["phase"] == "resume", body["table_state"]


def test_overview_active_sessions_include_table_id(client, api_key_header, owner_a_token, fresh_table):
    """/dashboard/overview active_sessions carry table_id matching the session's table
    (powers the Home click-through to the table detail)."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table)
    h = {**api_key_header, "Authorization": f"Bearer {owner_a_token}"}
    resp = client.get("/api/dashboard/overview", headers=h)
    assert resp.status_code == 200, resp.text
    sessions = resp.json()["active_sessions"]
    mine = [x for x in sessions if x["session_id"] == s["session_id"]]
    assert mine, "our active session should appear in overview"
    assert mine[0]["table_id"] == s["table_id"]
    uuid.UUID(mine[0]["table_id"])  # valid uuid string


def _set_last_activity(session_id, minutes_ago):
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute(
                "UPDATE game_sessions SET last_activity_at = NOW() - $1 * INTERVAL '1 minute'"
                " WHERE id = $2",
                minutes_ago, uuid.UUID(session_id),
            )
        finally:
            await conn.close()
    asyncio.run(_q())


def test_plain_tap_on_lazy_expired_shows_recap(client, api_key_header, owner_a_token, fresh_table):
    """Control (L2): without new_game, a tap on an idle-expired session lazily ends it
    and shows recap."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table)
    _set_last_activity(s["session_id"], 23)  # past the 22-min expiry
    body = _tap(client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"],
                s["tag_uid"], s["next_counter"], s["origin"])
    assert body["table_state"]["phase"] == "recap", body["table_state"]


def test_new_game_bypasses_lazy_expiry_recap(client, api_key_header, owner_a_token, fresh_table):
    """L2 fix: force_new also skips the lazy-expiry recap from the resume check, so a
    New-game tap that coincides with idle-expiry still yields a fresh lobby."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table)
    _set_last_activity(s["session_id"], 23)
    body = _tap(client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"],
                s["tag_uid"], s["next_counter"], s["origin"], new_game=True)
    assert body["table_state"]["phase"] == "lobby", body["table_state"]


def test_tap_other_table_while_active_shows_switch_confirm(
    client, api_key_header, owner_a_token, fresh_table, adults_allowed_table
):
    """Single active seat: a phone active in a game at table A that taps table B gets
    switch_confirm (not a silent second game), carrying the old table's info."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table)
    host = s["origin"]
    tag_b = pair_tag(client, api_key_header, owner_a_token, adults_allowed_table["table_number"])
    body = _tap(client, api_key_header, adults_allowed_table["venue_slug"],
                adults_allowed_table["table_number"], tag_b, 1, host)
    st = body["table_state"]
    assert st["phase"] == "switch_confirm", st
    assert st["other"]["session_id"] == s["session_id"]
    assert st["other"]["table_number"] == fresh_table["table_number"]
    assert "phone_id" not in st["other"]  # the switch payload carries no raw phone id


def test_switch_confirm_clears_after_leaving_old_game(
    client, api_key_header, owner_a_token, fresh_table, adults_allowed_table
):
    """After leaving the old game, tapping the new table proceeds normally (lobby)."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table)
    host = s["origin"]
    leave = client.post(f"/api/patron/sessions/{s['session_id']}/leave",
                        headers=api_key_header, json={"phone_id": host})
    assert leave.status_code == 200, leave.text
    tag_b = pair_tag(client, api_key_header, owner_a_token, adults_allowed_table["table_number"])
    body = _tap(client, api_key_header, adults_allowed_table["venue_slug"],
                adults_allowed_table["table_number"], tag_b, 1, host)
    assert body["table_state"]["phase"] == "lobby", body["table_state"]
