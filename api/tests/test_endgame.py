"""Tests for End Game + Recap + Idle Cap (spec: .pipeline/spec.md Section J).

Follows test_roulette.py / test_trivia.py pattern exactly: _cleanup_test_tags
autouse fixture, _fresh_phone(), _tap(), _set_name(), _setup_session() helpers,
TestClient (HTTP) for all assertions, asyncpg.connect for direct DB helpers.

These run CI-equivalent (SUPABASE_* unset -> realtime publish is a no-op), so
assertions only depend on the HTTP/DB layer, never on a delivered broadcast.
"""
import asyncio
import os
import uuid

import asyncpg
import pytest

from api.tests.conftest import pair_tag, simulate_tap


@pytest.fixture(autouse=True)
def _cleanup_test_tags():
    """Delete test nfc_tags after each test so tag_uid collisions can't happen."""
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


def _tap(client, api_key_header, venue_slug, table_number, tag_uid, counter, phone_id):
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


def _setup_session(client, api_key_header, owner_a_token, table_info,
                   num_phones=2, adults_only=False):
    """Tap `num_phones` phones, name them, host=first, start. Returns dict with
    session_id, table_id, origin phone, and the full ordered phones list."""
    tag_uid = pair_tag(client, api_key_header, owner_a_token, table_info["table_number"])
    phones = [_fresh_phone() for _ in range(num_phones)]
    first_body = None
    for i, phone in enumerate(phones):
        body = _tap(
            client, api_key_header, table_info["venue_slug"], table_info["table_number"],
            tag_uid, i + 1, phone,
        )
        if i == 0:
            first_body = body
    lobby_id = first_body["table_state"]["lobby_id"]
    table_id = first_body["table_id"]
    for i, phone in enumerate(phones):
        _set_name(client, api_key_header, lobby_id, phone, f"Player {i + 1}")

    resp = client.post(
        f"/api/patron/lobby/{lobby_id}/claim-host",
        headers=api_key_header, json={"phone_id": phones[0]},
    )
    assert resp.status_code == 200, resp.text

    start = client.post(
        f"/api/patron/lobby/{lobby_id}/start",
        headers=api_key_header, json={"phone_id": phones[0], "adults_only": adults_only},
    )
    assert start.status_code == 200, start.text
    return {
        "session_id": start.json()["session_id"],
        "table_id": table_id,
        "origin": phones[0],
        "phones": phones,
        "tag_uid": tag_uid,
    }


# --- HTTP helpers ---

def _end_game(client, h, session_id, phone):
    return client.post(
        f"/api/patron/sessions/{session_id}/end-game",
        headers=h, json={"phone_id": phone},
    )


def _get_recap(client, h, session_id):
    return client.get(
        f"/api/patron/sessions/{session_id}/recap",
        headers=h,
    )


def _start_roulette(client, h, session_id, phone):
    return client.post(
        f"/api/patron/sessions/{session_id}/roulette/start",
        headers=h, json={"phone_id": phone},
    )


def _vote_loser(client, h, round_id, phone, voted_player_id):
    return client.post(
        f"/api/patron/rounds/{round_id}/vote-loser",
        headers=h, json={"phone_id": phone, "voted_player_id": voted_player_id},
    )


def _draw_card(client, h, session_id, phone, player_id):
    return client.post(
        f"/api/patron/sessions/{session_id}/draw-card",
        headers=h, json={"phone_id": phone, "player_id": player_id},
    )


def _complete_round(client, h, round_id, phone):
    return client.post(
        f"/api/patron/rounds/{round_id}/complete",
        headers=h, json={"phone_id": phone},
    )


def _leave(client, h, session_id, phone):
    return client.post(
        f"/api/patron/sessions/{session_id}/leave",
        headers=h, json={"phone_id": phone},
    )


# --- DB helpers ---

def _set_last_activity(session_id, minutes_ago):
    """Set last_activity_at to N minutes in the past."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute(
                "UPDATE game_sessions"
                " SET last_activity_at = NOW() - $1 * INTERVAL '1 minute'"
                " WHERE id = $2",
                minutes_ago, uuid.UUID(session_id),
            )
        finally:
            await conn.close()
    asyncio.run(_q())


def _set_ended_at(session_id, minutes_ago):
    """Set ended_at to N minutes in the past (for testing recap window)."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute(
                "UPDATE game_sessions"
                " SET ended_at = NOW() - $1 * INTERVAL '1 minute',"
                " end_reason = 'manual'"
                " WHERE id = $2",
                minutes_ago, uuid.UUID(session_id),
            )
        finally:
            await conn.close()
    asyncio.run(_q())


def _get_session_end_info(session_id):
    """Return (ended_at, end_reason) for a session."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            row = await conn.fetchrow(
                "SELECT ended_at, end_reason FROM game_sessions WHERE id = $1",
                uuid.UUID(session_id),
            )
            return row["ended_at"], row["end_reason"]
        finally:
            await conn.close()
    return asyncio.run(_q())


def _player_id_by_phone(session_id, phone_id):
    """Return the UUID (str) of the game_players row for this phone."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            row = await conn.fetchval(
                "SELECT id FROM game_players WHERE session_id = $1 AND phone_id = $2",
                uuid.UUID(session_id), phone_id,
            )
            return str(row)
        finally:
            await conn.close()
    return asyncio.run(_q())


# --- tests ---

def test_end_game_origin_only(client, api_key_header, owner_a_token, fresh_table):
    """Non-origin phone gets 403; origin phone gets 200."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)

    # Non-origin (phones[1]) should be denied
    resp_non_origin = _end_game(client, api_key_header, s["session_id"], s["phones"][1])
    assert resp_non_origin.status_code == 403

    # Origin phone should succeed
    resp_origin = _end_game(client, api_key_header, s["session_id"], s["origin"])
    assert resp_origin.status_code == 200
    data = resp_origin.json()
    assert data["ended"] is True
    assert data["session_id"] == s["session_id"]


def test_end_game_sets_ended_at_and_reason(client, api_key_header, owner_a_token, fresh_table):
    """Origin calls end-game -> 200 with {ended: true}; DB row has ended_at set and
    end_reason = 'manual'."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)

    resp = _end_game(client, api_key_header, s["session_id"], s["origin"])
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ended"] is True
    assert data["session_id"] == s["session_id"]

    ended_at, end_reason = _get_session_end_info(s["session_id"])
    assert ended_at is not None, "ended_at should be set after end-game"
    assert end_reason == "manual"


def test_end_game_already_ended_409(client, api_key_header, owner_a_token, fresh_table):
    """Second end-game call on an already-ended session returns 409 session_already_ended."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)

    first = _end_game(client, api_key_header, s["session_id"], s["origin"])
    assert first.status_code == 200, first.text

    second = _end_game(client, api_key_header, s["session_id"], s["origin"])
    assert second.status_code == 409
    assert second.json()["detail"] == "session_already_ended"


def test_end_game_not_found_404(client, api_key_header, owner_a_token, fresh_table):
    """Unknown session id -> 404."""
    fake_id = str(uuid.uuid4())
    resp = _end_game(client, api_key_header, fake_id, _fresh_phone())
    assert resp.status_code == 404


def test_recap_full_stats(client, api_key_header, owner_a_token, fresh_table):
    """After a chooser round (select-hot-seat + draw+complete) and a roulette round
    (all vote same player), GET /recap returns well-formed leaderboard, cards_played,
    roulette_rounds, total_score, share_text, venue_name, and most_picked_player."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)

    # Select hot-seat player (increments times_selected on the chosen player)
    hs_resp = client.post(
        f"/api/patron/sessions/{s['session_id']}/select-hot-seat",
        headers=api_key_header, json={"phone_id": s["origin"]},
    )
    assert hs_resp.status_code == 200, hs_resp.text
    chosen_player_id = hs_resp.json()["player_id"]

    # Play one chooser round: draw card for chosen player, then complete
    draw_resp = _draw_card(
        client, api_key_header, s["session_id"], s["origin"], chosen_player_id
    )
    assert draw_resp.status_code == 200, draw_resp.text
    round_id = draw_resp.json()["round_id"]

    complete_resp = _complete_round(client, api_key_header, round_id, s["origin"])
    assert complete_resp.status_code == 200, complete_resp.text

    # Play one roulette round: both phones vote for Player 2 (phones[1]) as loser
    roulette_resp = _start_roulette(client, api_key_header, s["session_id"], s["origin"])
    assert roulette_resp.status_code == 200, roulette_resp.text
    roulette_round_id = roulette_resp.json()["round_id"]
    loser_player_id = _player_id_by_phone(s["session_id"], s["phones"][1])

    v1 = _vote_loser(client, api_key_header, roulette_round_id, s["phones"][0], loser_player_id)
    assert v1.status_code == 200, v1.text
    v2 = _vote_loser(client, api_key_header, roulette_round_id, s["phones"][1], loser_player_id)
    assert v2.status_code == 200, v2.text
    assert v2.json().get("auto_tallied") is True

    # End game
    end_resp = _end_game(client, api_key_header, s["session_id"], s["origin"])
    assert end_resp.status_code == 200, end_resp.text

    # Get recap
    recap_resp = _get_recap(client, api_key_header, s["session_id"])
    assert recap_resp.status_code == 200, recap_resp.text
    recap = recap_resp.json()

    # Leaderboard has 2 entries ordered by score DESC
    assert len(recap["leaderboard"]) == 2
    scores = [p["score"] for p in recap["leaderboard"]]
    assert scores == sorted(scores, reverse=True)

    # cards_played >= 1 (the completed chooser card)
    assert recap["cards_played"] >= 1

    # roulette_rounds == 1
    assert recap["roulette_rounds"] == 1

    # total_score > 0 (roulette non-loser gets +3; chooser complete gives +5)
    assert recap["total_score"] > 0

    # venue_name from seed data
    assert recap["venue_name"] == "The Lion's Den"

    # share_text contains venue name and total_score
    assert "The Lion's Den" in recap["share_text"]
    assert str(recap["total_score"]) in recap["share_text"]

    # most_picked_player: chooser round selected Player 1
    assert recap["most_picked_player"] is not None
    assert recap["most_picked_player"]["times_selected"] >= 1

    # end_reason is manual
    assert recap["end_reason"] == "manual"


def test_recap_left_early_shown(client, api_key_header, owner_a_token, fresh_table):
    """Player who left before end-game appears in leaderboard flagged left_early=True."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=3)

    # Phone 3 (index 2) leaves
    leave_resp = _leave(client, api_key_header, s["session_id"], s["phones"][2])
    assert leave_resp.status_code == 200, leave_resp.text

    # End game
    end_resp = _end_game(client, api_key_header, s["session_id"], s["origin"])
    assert end_resp.status_code == 200, end_resp.text

    recap_resp = _get_recap(client, api_key_header, s["session_id"])
    assert recap_resp.status_code == 200, recap_resp.text
    recap = recap_resp.json()

    assert len(recap["leaderboard"]) == 3
    left_early_entries = [p for p in recap["leaderboard"] if p["left_early"]]
    assert len(left_early_entries) == 1
    assert left_early_entries[0]["name"] == "Player 3"


def test_recap_trivia_accuracy_null_when_zero(client, api_key_header, owner_a_token, fresh_table):
    """When no trivia was played, trivia_accuracy is None (no divide-by-zero)."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)

    # No trivia played — end immediately
    end_resp = _end_game(client, api_key_header, s["session_id"], s["origin"])
    assert end_resp.status_code == 200, end_resp.text

    recap_resp = _get_recap(client, api_key_header, s["session_id"])
    assert recap_resp.status_code == 200, recap_resp.text
    recap = recap_resp.json()

    assert recap["trivia_accuracy"] is None
    assert recap["trivia_correct"] == 0
    assert recap["trivia_total"] == 0


def test_recap_not_ended_409(client, api_key_header, owner_a_token, fresh_table):
    """GET /recap on a still-active session returns 409 session_not_ended."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)

    recap_resp = _get_recap(client, api_key_header, s["session_id"])
    assert recap_resp.status_code == 409
    assert recap_resp.json()["detail"] == "session_not_ended"


def test_recap_roulette_count(client, api_key_header, owner_a_token, fresh_table):
    """After one completed roulette round, recap.roulette_rounds == 1."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)

    roulette_resp = _start_roulette(client, api_key_header, s["session_id"], s["origin"])
    assert roulette_resp.status_code == 200, roulette_resp.text
    roulette_round_id = roulette_resp.json()["round_id"]
    loser_player_id = _player_id_by_phone(s["session_id"], s["phones"][1])

    v1 = _vote_loser(client, api_key_header, roulette_round_id, s["phones"][0], loser_player_id)
    assert v1.status_code == 200, v1.text
    v2 = _vote_loser(client, api_key_header, roulette_round_id, s["phones"][1], loser_player_id)
    assert v2.status_code == 200, v2.text
    assert v2.json().get("auto_tallied") is True

    end_resp = _end_game(client, api_key_header, s["session_id"], s["origin"])
    assert end_resp.status_code == 200, end_resp.text

    recap_resp = _get_recap(client, api_key_header, s["session_id"])
    assert recap_resp.status_code == 200, recap_resp.text
    assert recap_resp.json()["roulette_rounds"] == 1


def test_idle_session_not_resumed(client, api_key_header, owner_a_token, fresh_table):
    """Session idle far past the re-tap window is lazily ended on re-tap; phone gets
    recap phase, not resume. The idle-end path is now the unified Re-Tap flow, so the
    reason is 'retap_expired' (the 15-min idle threshold IS the re-tap clock)."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)

    # Wind last_activity_at back 2 hours (well beyond the 15-min threshold + 7-min window)
    _set_last_activity(s["session_id"], 120)

    # Re-tap: origin phone taps again, incrementing the counter
    tag_uid = s["tag_uid"]
    retap_resp = _tap(
        client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"],
        tag_uid, len(s["phones"]) + 1, s["origin"],
    )
    table_state = retap_resp.get("table_state", {})
    assert table_state.get("phase") == "recap", (
        f"Expected phase='recap' after idle expiry, got {table_state}"
    )
    assert table_state.get("session_id") == s["session_id"]

    # DB must show ended with the unified re-tap-expiry reason
    ended_at, end_reason = _get_session_end_info(s["session_id"])
    assert ended_at is not None, "ended_at must be set after idle expiry"
    assert end_reason == "retap_expired"


def test_recently_ended_session_returns_recap_phase(
    client, api_key_header, owner_a_token, fresh_table
):
    """End session via API, immediately re-tap origin phone ->
    table_state.phase == 'recap' with the correct session_id."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)

    end_resp = _end_game(client, api_key_header, s["session_id"], s["origin"])
    assert end_resp.status_code == 200, end_resp.text

    # Re-tap with a new NFC counter (session is ended, so a new tag slot is needed)
    tag_uid = s["tag_uid"]
    retap_resp = _tap(
        client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"],
        tag_uid, len(s["phones"]) + 1, s["origin"],
    )
    table_state = retap_resp.get("table_state", {})
    assert table_state.get("phase") == "recap", (
        f"Expected phase='recap' for recently-ended session, got {table_state}"
    )
    assert table_state.get("session_id") == s["session_id"]


def test_old_ended_session_returns_lobby(client, api_key_header, owner_a_token, fresh_table):
    """Session ended 2 hours ago is outside the recap window; re-tap routes to lobby,
    not recap."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)

    end_resp = _end_game(client, api_key_header, s["session_id"], s["origin"])
    assert end_resp.status_code == 200, end_resp.text

    # Wind ended_at back 2 hours to simulate an old session
    _set_ended_at(s["session_id"], 120)

    tag_uid = s["tag_uid"]
    retap_resp = _tap(
        client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"],
        tag_uid, len(s["phones"]) + 1, s["origin"],
    )
    table_state = retap_resp.get("table_state", {})
    # Phase must not be 'recap' — old session is outside the recap window
    assert table_state.get("phase") != "recap", (
        f"Old-ended session must not show recap, got {table_state}"
    )
    assert table_state.get("phase") == "lobby", (
        f"Expected phase='lobby' after recap window expired, got {table_state}"
    )
