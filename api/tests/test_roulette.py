"""Tests for the Roulette round (gamespec.md: Round Type 3 -- Roulette).

Follows test_trivia.py/test_chooser.py pattern exactly: fresh_table fixture,
_fresh_phone(), _tap(), _set_name(), _setup_session() helpers from conftest.
Multi-phone, whole-table group challenge: every active phone votes on who lost;
losers get 0 pts; non-losers get +3 each.

Runs CI-equivalent (SUPABASE_* unset -> realtime publish is a no-op), so
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
    }


# --- roulette HTTP helpers ---

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


def _reveal(client, h, round_id, phone):
    return client.post(
        f"/api/patron/rounds/{round_id}/roulette/reveal",
        headers=h, json={"phone_id": phone},
    )


def _skip_roulette(client, h, round_id, phone):
    return client.post(
        f"/api/patron/rounds/{round_id}/roulette/skip",
        headers=h, json={"phone_id": phone},
    )


def _leave(client, h, session_id, phone):
    return client.post(
        f"/api/patron/sessions/{session_id}/leave",
        headers=h, json={"phone_id": phone},
    )


def _current(client, h, session_id, phone):
    return client.get(
        f"/api/patron/sessions/{session_id}/trivia/current",
        headers=h, params={"phone_id": phone},
    )


# --- DB helpers ---

def _player_score_by_phone(session_id, phone_id):
    """Read score for a phone's game_players row directly from DB."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await conn.fetchval(
                "SELECT score FROM game_players WHERE session_id = $1 AND phone_id = $2",
                uuid.UUID(session_id), phone_id,
            )
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


def _session_totals(session_id):
    """Return (total_score, cards_skipped, total_rounds) for a session."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            row = await conn.fetchrow(
                "SELECT total_score, cards_skipped, total_rounds "
                "FROM game_sessions WHERE id = $1",
                uuid.UUID(session_id),
            )
            return row["total_score"], row["cards_skipped"], row["total_rounds"]
        finally:
            await conn.close()
    return asyncio.run(_q())


def _round_card_tier(round_id):
    """Return content_tier of the roulette_card used in this round."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await conn.fetchval(
                """
                SELECT rc.content_tier FROM roulette_cards rc
                JOIN rounds r ON r.card_id = rc.id
                WHERE r.id = $1
                """,
                uuid.UUID(round_id),
            )
        finally:
            await conn.close()
    return asyncio.run(_q())


def _vote_count_in_db(round_id):
    """Count rows in roulette_votes for this round."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM roulette_votes WHERE round_id = $1",
                uuid.UUID(round_id),
            )
        finally:
            await conn.close()
    return asyncio.run(_q())


def _voted_player_in_db(round_id, voter_phone_id):
    """Return the voted_player_id (str) for a given voter phone in this round."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            val = await conn.fetchval(
                "SELECT voted_player_id FROM roulette_votes "
                "WHERE round_id = $1 AND voter_phone_id = $2",
                uuid.UUID(round_id), voter_phone_id,
            )
            return str(val) if val else None
        finally:
            await conn.close()
    return asyncio.run(_q())


# --- tests ---

def test_start_roulette_origin_only(client, api_key_header, owner_a_token, fresh_table):
    """Non-origin phone gets 403 on start."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    resp = _start_roulette(client, api_key_header, s["session_id"], s["phones"][1])
    assert resp.status_code == 403


def test_start_roulette_returns_card_and_players(
    client, api_key_header, owner_a_token, fresh_table
):
    """Origin gets round_id, prompt, drink_consequence, players(2), voted_count=0, active_total=2."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    resp = _start_roulette(client, api_key_header, s["session_id"], s["origin"])
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "round_id" in data
    assert "prompt" in data
    assert "drink_consequence" in data
    assert len(data["players"]) == 2
    assert data["voted_count"] == 0
    assert data["active_total"] == 2
    # Each player entry has id and name
    for p in data["players"]:
        assert "id" in p
        assert "name" in p


def test_start_roulette_adults_filter(
    client, api_key_header, owner_a_token, fresh_table
):
    """With adults_only=False, no adults_allowed card may appear (standard tier only)."""
    # fresh_table has content_ceiling='standard', so adults_only must be False
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table,
                       num_phones=2, adults_only=False)
    resp = _start_roulette(client, api_key_header, s["session_id"], s["origin"])
    assert resp.status_code == 200, resp.text
    round_id = resp.json()["round_id"]
    tier = _round_card_tier(round_id)
    assert tier == "standard", f"Expected standard card, got {tier!r}"


def test_start_roulette_idempotent(client, api_key_header, owner_a_token, fresh_table):
    """Calling start twice returns the same round_id (StrictMode safety)."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    first = _start_roulette(client, api_key_header, s["session_id"], s["origin"])
    assert first.status_code == 200, first.text
    second = _start_roulette(client, api_key_header, s["session_id"], s["origin"])
    assert second.status_code == 200, second.text
    assert first.json()["round_id"] == second.json()["round_id"]


def test_cast_vote_bola(client, api_key_header, owner_a_token, fresh_table):
    """Non-member phone gets 403; voting for a non-existent player gets 404."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    start_resp = _start_roulette(client, api_key_header, s["session_id"], s["origin"])
    assert start_resp.status_code == 200, start_resp.text
    round_id = start_resp.json()["round_id"]
    real_player_id = start_resp.json()["players"][0]["id"]

    # Non-member phone (never tapped)
    stranger = _fresh_phone()
    resp_non_member = _vote_loser(client, api_key_header, round_id, stranger, real_player_id)
    assert resp_non_member.status_code == 403

    # Member phone voting for a fake/non-existent player UUID
    fake_player_id = str(uuid.uuid4())
    resp_bad_target = _vote_loser(
        client, api_key_header, round_id, s["phones"][0], fake_player_id
    )
    assert resp_bad_target.status_code == 404


def test_cast_vote_upsert(client, api_key_header, owner_a_token, fresh_table):
    """Re-voting replaces the previous vote; only one row per voter in roulette_votes."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    start_resp = _start_roulette(client, api_key_header, s["session_id"], s["origin"])
    assert start_resp.status_code == 200, start_resp.text
    round_id = start_resp.json()["round_id"]
    players = start_resp.json()["players"]
    target_a = players[0]["id"]
    target_b = players[1]["id"]

    voter = s["phones"][0]
    # First vote
    r1 = _vote_loser(client, api_key_header, round_id, voter, target_a)
    assert r1.status_code == 200, r1.text
    assert _vote_count_in_db(round_id) == 1
    assert _voted_player_in_db(round_id, voter) == target_a

    # Second vote -- changes the target
    r2 = _vote_loser(client, api_key_header, round_id, voter, target_b)
    assert r2.status_code == 200, r2.text
    # Still only one row for this voter
    assert _vote_count_in_db(round_id) == 1
    assert _voted_player_in_db(round_id, voter) == target_b


def test_all_voted_auto_tally(client, api_key_header, owner_a_token, fresh_table):
    """2 phones both vote for the same player -> auto-tally fires, loser 0 pts, other +3."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    start_resp = _start_roulette(client, api_key_header, s["session_id"], s["origin"])
    assert start_resp.status_code == 200, start_resp.text
    round_id = start_resp.json()["round_id"]

    # Vote for phones[1]'s player as the loser
    loser_player_id = _player_id_by_phone(s["session_id"], s["phones"][1])

    # First vote -- no auto-tally yet
    r1 = _vote_loser(client, api_key_header, round_id, s["phones"][0], loser_player_id)
    assert r1.status_code == 200, r1.text
    assert r1.json().get("auto_tallied") is False

    # Second vote -- triggers auto-tally (all 2 players voted)
    r2 = _vote_loser(client, api_key_header, round_id, s["phones"][1], loser_player_id)
    assert r2.status_code == 200, r2.text
    assert r2.json().get("auto_tallied") is True

    # Loser gets 0; non-loser gets +3
    assert _player_score_by_phone(s["session_id"], s["phones"][1]) == 0
    assert _player_score_by_phone(s["session_id"], s["phones"][0]) == 3

    # Session total_score bumped by 3 (one non-loser * 3)
    total_score, _, _ = _session_totals(s["session_id"])
    assert total_score == 3


def test_tally_plurality_loser(client, api_key_header, owner_a_token, fresh_table):
    """3 phones: 2 vote for Player A, 1 for Player B -> A loses 0 pts, B and C get +3."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=3)
    start_resp = _start_roulette(client, api_key_header, s["session_id"], s["origin"])
    assert start_resp.status_code == 200, start_resp.text
    round_id = start_resp.json()["round_id"]

    p1_id = _player_id_by_phone(s["session_id"], s["phones"][1])
    p2_id = _player_id_by_phone(s["session_id"], s["phones"][2])

    # phones[0] and phones[1] vote for phones[2] as loser (2 votes)
    # phones[2] votes for phones[1] (1 vote)
    _vote_loser(client, api_key_header, round_id, s["phones"][0], p2_id)
    _vote_loser(client, api_key_header, round_id, s["phones"][1], p2_id)
    # Third vote triggers auto-tally
    r = _vote_loser(client, api_key_header, round_id, s["phones"][2], p1_id)
    assert r.status_code == 200, r.text
    assert r.json().get("auto_tallied") is True

    # phones[2] is the loser (most votes = 2), gets 0
    assert _player_score_by_phone(s["session_id"], s["phones"][2]) == 0
    # phones[0] and phones[1] are non-losers, each +3
    assert _player_score_by_phone(s["session_id"], s["phones"][0]) == 3
    assert _player_score_by_phone(s["session_id"], s["phones"][1]) == 3

    # total_score += 6 (2 non-losers * 3)
    total_score, _, _ = _session_totals(s["session_id"])
    assert total_score == 6


def test_tally_tie_shared_blame(client, api_key_header, owner_a_token, fresh_table):
    """2 phones each vote for the other -> both losers, neither gets +3, total_score unchanged."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    start_resp = _start_roulette(client, api_key_header, s["session_id"], s["origin"])
    assert start_resp.status_code == 200, start_resp.text
    round_id = start_resp.json()["round_id"]

    p0_id = _player_id_by_phone(s["session_id"], s["phones"][0])
    p1_id = _player_id_by_phone(s["session_id"], s["phones"][1])

    # phones[0] votes for phones[1], phones[1] votes for phones[0]
    _vote_loser(client, api_key_header, round_id, s["phones"][0], p1_id)
    r = _vote_loser(client, api_key_header, round_id, s["phones"][1], p0_id)
    assert r.status_code == 200, r.text
    assert r.json().get("auto_tallied") is True

    # Both are losers (max_votes=1 tied) -> both get 0, no change
    assert _player_score_by_phone(s["session_id"], s["phones"][0]) == 0
    assert _player_score_by_phone(s["session_id"], s["phones"][1]) == 0

    # total_score unchanged (everyone tied -> all-tied rule applies)
    total_score, _, _ = _session_totals(s["session_id"])
    assert total_score == 0


def test_tally_three_way_tie(client, api_key_header, owner_a_token, fresh_table):
    """3 phones each vote for a different person -> all losers, no +3."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=3)
    start_resp = _start_roulette(client, api_key_header, s["session_id"], s["origin"])
    assert start_resp.status_code == 200, start_resp.text
    round_id = start_resp.json()["round_id"]

    p0_id = _player_id_by_phone(s["session_id"], s["phones"][0])
    p1_id = _player_id_by_phone(s["session_id"], s["phones"][1])
    p2_id = _player_id_by_phone(s["session_id"], s["phones"][2])

    # Each player votes for a different person: 0->1, 1->2, 2->0
    _vote_loser(client, api_key_header, round_id, s["phones"][0], p1_id)
    _vote_loser(client, api_key_header, round_id, s["phones"][1], p2_id)
    r = _vote_loser(client, api_key_header, round_id, s["phones"][2], p0_id)
    assert r.status_code == 200, r.text
    assert r.json().get("auto_tallied") is True

    # All tied at 1 vote each -> all are losers, no +3 for anyone
    assert _player_score_by_phone(s["session_id"], s["phones"][0]) == 0
    assert _player_score_by_phone(s["session_id"], s["phones"][1]) == 0
    assert _player_score_by_phone(s["session_id"], s["phones"][2]) == 0

    total_score, _, _ = _session_totals(s["session_id"])
    assert total_score == 0


def test_reveal_force_tally_partial(client, api_key_header, owner_a_token, fresh_table):
    """3 phones, only 1 votes; origin calls reveal. Voted player loses (0), other 2 get +3."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=3)
    start_resp = _start_roulette(client, api_key_header, s["session_id"], s["origin"])
    assert start_resp.status_code == 200, start_resp.text
    round_id = start_resp.json()["round_id"]

    p2_id = _player_id_by_phone(s["session_id"], s["phones"][2])

    # Only phones[0] votes; votes for phones[2]
    r = _vote_loser(client, api_key_header, round_id, s["phones"][0], p2_id)
    assert r.status_code == 200, r.text
    # Not yet auto-tallied (only 1/3 voted)
    assert r.json().get("auto_tallied") is False

    # Origin force-reveals (partial tally)
    reveal_resp = _reveal(client, api_key_header, round_id, s["origin"])
    assert reveal_resp.status_code == 200, reveal_resp.text
    data = reveal_resp.json()
    assert data["result"] == "completed"
    loser_ids = [loser["id"] for loser in data["losers"]]
    assert p2_id in loser_ids

    # phones[2] = loser -> 0 pts
    assert _player_score_by_phone(s["session_id"], s["phones"][2]) == 0
    # phones[0] and phones[1] are non-losers -> +3 each
    assert _player_score_by_phone(s["session_id"], s["phones"][0]) == 3
    assert _player_score_by_phone(s["session_id"], s["phones"][1]) == 3

    total_score, _, _ = _session_totals(s["session_id"])
    assert total_score == 6


def test_left_early_excluded(client, api_key_header, owner_a_token, fresh_table):
    """3 phones, one leaves before start; left-early phone excluded from voting and from +3."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=3)

    # phones[2] leaves before the roulette round starts
    leave_resp = _leave(client, api_key_header, s["session_id"], s["phones"][2])
    assert leave_resp.status_code == 200, leave_resp.text

    start_resp = _start_roulette(client, api_key_header, s["session_id"], s["origin"])
    assert start_resp.status_code == 200, start_resp.text
    round_id = start_resp.json()["round_id"]
    # Only 2 active players shown
    assert start_resp.json()["active_total"] == 2
    assert len(start_resp.json()["players"]) == 2

    p0_id = _player_id_by_phone(s["session_id"], s["phones"][0])
    p1_id = _player_id_by_phone(s["session_id"], s["phones"][1])

    # Left-early phone cannot vote (403 - not_a_member because left_early=TRUE)
    resp_left = _vote_loser(client, api_key_header, round_id, s["phones"][2], p0_id)
    assert resp_left.status_code == 403

    # Active phones vote; phones[0] votes for phones[1] as loser, phones[1] votes back
    _vote_loser(client, api_key_header, round_id, s["phones"][0], p1_id)
    r = _vote_loser(client, api_key_header, round_id, s["phones"][1], p0_id)
    assert r.status_code == 200, r.text
    # 2 active phones both voted -> auto-tally fires
    assert r.json().get("auto_tallied") is True

    # With 2 active each voting the other (all tied), no +3 for anyone
    assert _player_score_by_phone(s["session_id"], s["phones"][0]) == 0
    assert _player_score_by_phone(s["session_id"], s["phones"][1]) == 0
    # Left-early player also gets 0 (not eligible for +3)
    assert _player_score_by_phone(s["session_id"], s["phones"][2]) == 0


def test_skip_roulette(client, api_key_header, owner_a_token, fresh_table):
    """Origin skips: result='skipped', score_awarded=0, no score changes, cards_skipped bumped."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    start_resp = _start_roulette(client, api_key_header, s["session_id"], s["origin"])
    assert start_resp.status_code == 200, start_resp.text
    round_id = start_resp.json()["round_id"]

    resp = _skip_roulette(client, api_key_header, round_id, s["origin"])
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["result"] == "skipped"
    assert data["score_awarded"] == 0

    # No scores changed
    for phone in s["phones"]:
        assert _player_score_by_phone(s["session_id"], phone) == 0

    # cards_skipped bumped
    _, cards_skipped, _ = _session_totals(s["session_id"])
    assert cards_skipped == 1


def test_skip_roulette_non_origin_rejected(client, api_key_header, owner_a_token, fresh_table):
    """Non-origin phone gets 403 on skip."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    start_resp = _start_roulette(client, api_key_header, s["session_id"], s["origin"])
    assert start_resp.status_code == 200, start_resp.text
    round_id = start_resp.json()["round_id"]

    resp = _skip_roulette(client, api_key_header, round_id, s["phones"][1])
    assert resp.status_code == 403


def test_start_roulette_min_2_players(client, api_key_header, owner_a_token, fresh_table):
    """With only 1 active player, start returns 409 not_enough_players."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    # phones[1] leaves -> only 1 active
    leave_resp = _leave(client, api_key_header, s["session_id"], s["phones"][1])
    assert leave_resp.status_code == 200, leave_resp.text

    resp = _start_roulette(client, api_key_header, s["session_id"], s["origin"])
    assert resp.status_code == 409
    assert resp.json()["detail"] == "not_enough_players"


def test_get_current_state_roulette_phase(client, api_key_header, owner_a_token, fresh_table):
    """Polling /sessions/{sid}/trivia/current during active roulette returns phase='roulette'."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    start_resp = _start_roulette(client, api_key_header, s["session_id"], s["origin"])
    assert start_resp.status_code == 200, start_resp.text
    round_id = start_resp.json()["round_id"]

    state = _current(client, api_key_header, s["session_id"], s["origin"]).json()
    assert state["phase"] == "roulette"
    assert state["round_id"] == round_id
    assert "prompt" in state
    assert "players" in state
    assert state["my_vote"] is None
    assert state["voted_count"] == 0
    assert state["active_total"] == 2
