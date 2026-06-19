"""Tests for the Chooser round (gamespec.md: Round Type 1 -- Chooser).

Follows the exact pattern of test_lobby.py: fresh_table fixture, _fresh_phone(),
pair_tag, simulate_tap, _tap_with_phone helpers from conftest.
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


def _setup_session(client, api_key_header, owner_a_token, table_info, adults_only=False):
    """Create a lobby, tap 2 phones, claim host, start the game. Returns (session_id, phone_id)."""
    tag_uid = pair_tag(client, api_key_header, owner_a_token, table_info["table_number"])
    host_phone = _fresh_phone()
    second_phone = _fresh_phone()
    body = _tap_with_phone(
        client, api_key_header, table_info["venue_slug"], table_info["table_number"],
        tag_uid, 1, host_phone,
    )
    lobby_id = body["table_state"]["lobby_id"]
    _tap_with_phone(
        client, api_key_header, table_info["venue_slug"], table_info["table_number"],
        tag_uid, 2, second_phone,
    )
    _set_name(client, api_key_header, lobby_id, host_phone, "Player 1")
    _set_name(client, api_key_header, lobby_id, second_phone, "Player 2")

    resp = client.post(
        f"/api/patron/lobby/{lobby_id}/claim-host",
        headers=api_key_header,
        json={"phone_id": host_phone},
    )
    assert resp.status_code == 200, resp.text

    start = client.post(
        f"/api/patron/lobby/{lobby_id}/start",
        headers=api_key_header,
        json={"phone_id": host_phone, "adults_only": adults_only},
    )
    assert start.status_code == 200, start.text
    session_id = start.json()["session_id"]
    return session_id, host_phone


def _pick_hot_seat(client, api_key_header, session_id, phone_id):
    """Select a hot-seat player and return the result dict."""
    resp = client.post(
        f"/api/patron/sessions/{session_id}/select-hot-seat",
        headers=api_key_header,
        json={"phone_id": phone_id},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _draw_card(client, api_key_header, session_id, phone_id, player_id):
    return client.post(
        f"/api/patron/sessions/{session_id}/draw-card",
        headers=api_key_header,
        json={"phone_id": phone_id, "player_id": player_id},
    )


def _complete(client, api_key_header, round_id, phone_id):
    return client.post(
        f"/api/patron/rounds/{round_id}/complete",
        headers=api_key_header,
        json={"phone_id": phone_id},
    )


def _skip(client, api_key_header, round_id, phone_id):
    return client.post(
        f"/api/patron/rounds/{round_id}/skip",
        headers=api_key_header,
        json={"phone_id": phone_id},
    )


def _redraw(client, api_key_header, round_id, phone_id):
    return client.post(
        f"/api/patron/rounds/{round_id}/redraw",
        headers=api_key_header,
        json={"phone_id": phone_id},
    )


def _player_score(player_id):
    """Read a game_players row's score straight from the DB."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await conn.fetchval(
                "SELECT score FROM game_players WHERE id = $1", uuid.UUID(player_id)
            )
        finally:
            await conn.close()
    return asyncio.run(_q())


def test_draw_card_returns_a_card(client, api_key_header, owner_a_token, fresh_table):
    session_id, phone_id = _setup_session(client, api_key_header, owner_a_token, fresh_table)
    hot_seat = _pick_hot_seat(client, api_key_header, session_id, phone_id)

    resp = _draw_card(client, api_key_header, session_id, phone_id, hot_seat["player_id"])
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "card" in data
    assert "content" in data["card"]
    assert "type" in data["card"]
    assert "round_id" in data
    assert data["round_number"] == 1


def test_draw_card_rejects_non_origin_phone(client, api_key_header, owner_a_token, fresh_table):
    session_id, phone_id = _setup_session(client, api_key_header, owner_a_token, fresh_table)
    hot_seat = _pick_hot_seat(client, api_key_header, session_id, phone_id)

    wrong_phone = _fresh_phone()
    resp = _draw_card(client, api_key_header, session_id, wrong_phone, hot_seat["player_id"])
    assert resp.status_code == 403


def test_complete_records_completed_no_points(client, api_key_header, owner_a_token, fresh_table):
    session_id, phone_id = _setup_session(client, api_key_header, owner_a_token, fresh_table)
    hot_seat = _pick_hot_seat(client, api_key_header, session_id, phone_id)

    draw = _draw_card(client, api_key_header, session_id, phone_id, hot_seat["player_id"])
    assert draw.status_code == 200, draw.text
    round_id = draw.json()["round_id"]

    resp = _complete(client, api_key_header, round_id, phone_id)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Chooser awards no points -- completion is logged but no score attributed.
    assert data["result"] == "completed"
    assert data["score_awarded"] == 0
    # The hot-seat player's score stays at 0.
    assert _player_score(hot_seat["player_id"]) == 0


def test_skip_awards_zero_points(client, api_key_header, owner_a_token, fresh_table):
    session_id, phone_id = _setup_session(client, api_key_header, owner_a_token, fresh_table)
    hot_seat = _pick_hot_seat(client, api_key_header, session_id, phone_id)

    draw = _draw_card(client, api_key_header, session_id, phone_id, hot_seat["player_id"])
    assert draw.status_code == 200, draw.text
    round_id = draw.json()["round_id"]

    resp = _skip(client, api_key_header, round_id, phone_id)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["score_awarded"] == 0
    assert data["result"] == "skipped"


def test_complete_then_complete_again_is_rejected(client, api_key_header, owner_a_token, fresh_table):
    session_id, phone_id = _setup_session(client, api_key_header, owner_a_token, fresh_table)
    hot_seat = _pick_hot_seat(client, api_key_header, session_id, phone_id)

    draw = _draw_card(client, api_key_header, session_id, phone_id, hot_seat["player_id"])
    assert draw.status_code == 200, draw.text
    round_id = draw.json()["round_id"]

    first = _complete(client, api_key_header, round_id, phone_id)
    assert first.status_code == 200, first.text

    second = _complete(client, api_key_header, round_id, phone_id)
    assert second.status_code == 409


def test_redraw_returns_same_category(client, api_key_header, owner_a_token, fresh_table):
    session_id, phone_id = _setup_session(client, api_key_header, owner_a_token, fresh_table)
    hot_seat = _pick_hot_seat(client, api_key_header, session_id, phone_id)

    draw = _draw_card(client, api_key_header, session_id, phone_id, hot_seat["player_id"])
    assert draw.status_code == 200, draw.text
    original_type = draw.json()["card"]["type"]
    round_id = draw.json()["round_id"]

    resp = _redraw(client, api_key_header, round_id, phone_id)
    assert resp.status_code == 200, resp.text
    assert resp.json()["card"]["type"] == original_type


def test_redraws_increment_count_without_penalty(client, api_key_header, owner_a_token, fresh_table):
    session_id, phone_id = _setup_session(client, api_key_header, owner_a_token, fresh_table)
    hot_seat = _pick_hot_seat(client, api_key_header, session_id, phone_id)

    draw = _draw_card(client, api_key_header, session_id, phone_id, hot_seat["player_id"])
    assert draw.status_code == 200, draw.text
    round_id = draw.json()["round_id"]

    resp1 = _redraw(client, api_key_header, round_id, phone_id)
    assert resp1.status_code == 200, resp1.text
    assert resp1.json()["redraw_count"] == 1
    assert "penalty_applied" not in resp1.json()

    resp2 = _redraw(client, api_key_header, round_id, phone_id)
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["redraw_count"] == 2
    assert "penalty_applied" not in resp2.json()


def test_redraws_do_not_change_score(client, api_key_header, owner_a_token, fresh_table):
    session_id, phone_id = _setup_session(client, api_key_header, owner_a_token, fresh_table)
    hot_seat = _pick_hot_seat(client, api_key_header, session_id, phone_id)

    draw = _draw_card(client, api_key_header, session_id, phone_id, hot_seat["player_id"])
    assert draw.status_code == 200, draw.text
    round_id = draw.json()["round_id"]

    for expected in (1, 2, 3):
        r = _redraw(client, api_key_header, round_id, phone_id)
        assert r.status_code == 200, r.text
        assert r.json()["redraw_count"] == expected

    # No redraw penalty and no completion points -- the hot-seat player's
    # score stays at 0 no matter how many redraws happened.
    complete_resp = _complete(client, api_key_header, round_id, phone_id)
    assert complete_resp.status_code == 200, complete_resp.text
    assert complete_resp.json()["score_awarded"] == 0
    assert _player_score(hot_seat["player_id"]) == 0


def test_drink_card_triggers_disclaimer_once(client, api_key_header, owner_a_token, fresh_table):
    """Insert a drink-only card set so we can force a drink card draw."""
    _ns = uuid.UUID("d41c0000-0000-0000-0000-000000000000")

    async def _insert_drink_cards(session_id):
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            # Insert two drink-only cards with deterministic IDs for cleanup
            for i in range(2):
                cid = str(uuid.uuid5(_ns, f"test-drink-{i}"))
                await conn.execute(
                    """
                    INSERT INTO bar_cards (id, content, type, is_adults_only)
                    VALUES ($1, $2, 'drink', FALSE)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    cid, f"Test Drink Card {i}",
                )
            # Force the session to have no non-drink cards used yet
            # by clearing current_round_number (already 0 for fresh session)
            return [str(uuid.uuid5(_ns, f"test-drink-{i}")) for i in range(2)]
        finally:
            await conn.close()

    async def _cleanup_drink_cards():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            for i in range(2):
                cid = str(uuid.uuid5(_ns, f"test-drink-{i}"))
                await conn.execute("DELETE FROM bar_cards WHERE id = $1", cid)
        finally:
            await conn.close()

    session_id, phone_id = _setup_session(client, api_key_header, owner_a_token, fresh_table)
    asyncio.run(_insert_drink_cards(session_id))

    try:
        # Draw cards until we get a drink card (or exhaust reasonable attempts)
        # We only seeded drink-type cards with deterministic IDs plus the
        # standard seed; try up to 20 draws to hit a drink card.
        disclaimer_seen = False
        max_attempts = 20
        for _ in range(max_attempts):
            hot_seat = _pick_hot_seat(client, api_key_header, session_id, phone_id)
            draw = _draw_card(client, api_key_header, session_id, phone_id, hot_seat["player_id"])
            assert draw.status_code == 200, draw.text
            data = draw.json()
            round_id = data["round_id"]
            if data.get("show_drink_disclaimer"):
                disclaimer_seen = True
                # Complete this round
                _complete(client, api_key_header, round_id, phone_id)
                break
            _skip(client, api_key_header, round_id, phone_id)

        assert disclaimer_seen, "Expected a drink card to trigger the disclaimer within 20 draws"

        # Draw more cards -- disclaimer should NOT fire again
        for _ in range(5):
            hot_seat = _pick_hot_seat(client, api_key_header, session_id, phone_id)
            draw = _draw_card(client, api_key_header, session_id, phone_id, hot_seat["player_id"])
            assert draw.status_code == 200, draw.text
            data = draw.json()
            assert not data.get("show_drink_disclaimer"), \
                "Disclaimer fired a second time -- should only show once per session"
            _skip(client, api_key_header, data["round_id"], phone_id)
    finally:
        asyncio.run(_cleanup_drink_cards())


def test_redraw_after_complete_is_rejected(client, api_key_header, owner_a_token, fresh_table):
    session_id, phone_id = _setup_session(client, api_key_header, owner_a_token, fresh_table)
    hot_seat = _pick_hot_seat(client, api_key_header, session_id, phone_id)

    draw = _draw_card(client, api_key_header, session_id, phone_id, hot_seat["player_id"])
    assert draw.status_code == 200, draw.text
    round_id = draw.json()["round_id"]

    _complete(client, api_key_header, round_id, phone_id)

    resp = _redraw(client, api_key_header, round_id, phone_id)
    assert resp.status_code == 409


def test_redraw_relaxed_fallback_when_type_pool_exhausted(
    client, api_key_header, owner_a_token, fresh_table
):
    """Regression: when every card of a type is already used this session,
    _pick_card must fall back to allowing a repeat rather than raising a
    parameter-binding error. The relaxed query drops the used-this-session
    exclusion and must renumber its own placeholders independently."""
    session_id, phone_id = _setup_session(client, api_key_header, owner_a_token, fresh_table)

    async def _exercise():
        from api.services import chooser_service

        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            dare_rows = await conn.fetch(
                "SELECT id FROM bar_cards WHERE type = 'dare' AND is_adults_only = FALSE"
            )
            dare_ids = [r["id"] for r in dare_rows]
            assert len(dare_ids) >= 1

            # Mark every 'dare' card as already used this session so the
            # primary (exclude-used) query for type='dare' returns nothing.
            for i, cid in enumerate(dare_ids):
                await conn.execute(
                    """
                    INSERT INTO rounds (id, session_id, round_number, round_type,
                                        card_id, card_type, result)
                    VALUES ($1, $2, $3, 'chooser', $4, 'standard', 'skipped')
                    """,
                    uuid.uuid4(), session_id, 100 + i, cid,
                )

            # Same-type pick must still return a card via the relaxed fallback
            # (a repeat), not error out on mismatched placeholders.
            card = await chooser_service._pick_card(
                conn, session_id, adults_only=False,
                same_type="dare", exclude_ids=[str(dare_ids[0])],
            )
            assert card is not None
            assert card["type"] == "dare"
        finally:
            await conn.close()

    asyncio.run(_exercise())


def test_draw_card_on_ended_session_is_rejected(client, api_key_header, owner_a_token, fresh_table):
    session_id, phone_id = _setup_session(client, api_key_header, owner_a_token, fresh_table)
    hot_seat = _pick_hot_seat(client, api_key_header, session_id, phone_id)

    async def _end():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute("UPDATE game_sessions SET ended_at = NOW() WHERE id = $1", session_id)
        finally:
            await conn.close()
    asyncio.run(_end())

    resp = _draw_card(client, api_key_header, session_id, phone_id, hot_seat["player_id"])
    assert resp.status_code == 409
