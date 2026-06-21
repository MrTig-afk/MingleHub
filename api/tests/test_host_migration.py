"""Tests for Leave + Host Migration (spec: .pipeline/spec.md).

Mirrors test_endgame.py / test_roulette.py fixtures/helpers exactly:
_cleanup_test_tags autouse fixture, _fresh_phone(), _tap(), _set_name(),
_setup_session() helpers, TestClient (HTTP) for all assertions, asyncpg.connect
for direct DB helpers.

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
    session_id, table_id, origin phone, and the full ordered phones list.
    Phones are tapped in order so phones[0] has the earliest joined_at."""
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
        "lobby_id": lobby_id,
    }


# --- HTTP helpers ---

def _leave(client, h, session_id, phone):
    return client.post(
        f"/api/patron/sessions/{session_id}/leave",
        headers=h, json={"phone_id": phone},
    )


def _current_round(client, h, session_id):
    return client.get(
        f"/api/patron/sessions/{session_id}/current-round",
        headers=h,
    )


def _select_hot_seat(client, h, session_id, phone):
    return client.post(
        f"/api/patron/sessions/{session_id}/select-hot-seat",
        headers=h, json={"phone_id": phone},
    )


def _draw_card(client, h, session_id, phone, player_id):
    return client.post(
        f"/api/patron/sessions/{session_id}/draw-card",
        headers=h, json={"phone_id": phone, "player_id": player_id},
    )


def _poll_state(client, h, session_id, phone):
    return client.get(
        f"/api/patron/sessions/{session_id}/trivia/current",
        headers=h,
        params={"phone_id": phone},
    )


# --- DB helpers ---

def _get_origin(session_id):
    """Return origin_phone_id for a session."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await conn.fetchval(
                "SELECT origin_phone_id FROM game_sessions WHERE id = $1",
                uuid.UUID(session_id),
            )
        finally:
            await conn.close()
    return asyncio.run(_q())


def _get_player_left_early(session_id, phone_id):
    """Return left_early bool for a player row."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await conn.fetchval(
                "SELECT left_early FROM game_players WHERE session_id = $1 AND phone_id = $2",
                uuid.UUID(session_id), phone_id,
            )
        finally:
            await conn.close()
    return asyncio.run(_q())


def _get_session_ended(session_id):
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


def _get_round_result(session_id, round_type="chooser"):
    """Return result of the most recent round of the given type."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await conn.fetchval(
                "SELECT result FROM rounds WHERE session_id = $1 AND round_type = $2 "
                "ORDER BY created_at DESC LIMIT 1",
                uuid.UUID(session_id), round_type,
            )
        finally:
            await conn.close()
    return asyncio.run(_q())


# --- tests ---

def test_host_leave_migrates_to_earliest_joined(
    client, api_key_header, owner_a_token, fresh_table
):
    """3-phone session: host (phones[0]=Alice) calls leave.
    The new host must be phones[1]=Bob (earliest-joined non-host active player).
    Old host is left_early; others remain active.
    Response: migrated=True, new_host_name=Bob (raw new-host id redacted)."""
    s = _setup_session(
        client, api_key_header, owner_a_token, fresh_table, num_phones=3
    )
    session_id = s["session_id"]
    phone_a, phone_b, phone_c = s["phones"]

    resp = _leave(client, api_key_header, session_id, phone_a)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data.get("migrated") is True
    assert "new_host_phone_id" not in data  # BOLA: raw new-host id redacted from response
    assert data.get("new_host_name") == "Player 2"  # Bob, by name

    # DB: origin reassigned to earliest-joined (Bob)
    assert _get_origin(session_id) == phone_b

    # DB: old host marked left_early; others not
    assert _get_player_left_early(session_id, phone_a) is True
    assert _get_player_left_early(session_id, phone_b) is False
    assert _get_player_left_early(session_id, phone_c) is False


def test_host_leave_new_host_is_earliest_joined(
    client, api_key_header, owner_a_token, fresh_table
):
    """With 3 non-host players of known join order, the one who joined earliest
    among non-host active players is chosen as the new host.

    phones[0] = host (Alice), phones[1] = Bob (tap 2), phones[2] = Carol (tap 3).
    Host leaves -> Bob (earliest non-host tap) becomes host, NOT Carol."""
    s = _setup_session(
        client, api_key_header, owner_a_token, fresh_table, num_phones=3
    )
    session_id = s["session_id"]
    phone_a, phone_b, phone_c = s["phones"]

    resp = _leave(client, api_key_header, session_id, phone_a)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # Bob (index 1) is earliest-joined non-host; Carol (index 2) is later.
    # Correctness via DB origin; response carries only the redacted name.
    assert "new_host_phone_id" not in data  # BOLA: redacted
    assert _get_origin(session_id) == phone_b


def test_host_leave_with_one_remaining(
    client, api_key_header, owner_a_token, fresh_table
):
    """2-phone session: host leaves -> the single remaining player becomes host."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    session_id = s["session_id"]
    phone_a, phone_b = s["phones"]

    resp = _leave(client, api_key_header, session_id, phone_a)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data.get("migrated") is True
    assert "new_host_phone_id" not in data  # BOLA: redacted
    assert _get_origin(session_id) == phone_b


def test_host_leave_no_active_remaining_ends_game(
    client, api_key_header, owner_a_token, fresh_table
):
    """2-phone session where the non-host already left_early.
    Host leaves -> no candidate -> session ended, not migrated."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    session_id = s["session_id"]
    phone_a, phone_b = s["phones"]

    # Non-host leaves first
    leave_b = _leave(client, api_key_header, session_id, phone_b)
    assert leave_b.status_code == 200, leave_b.text
    assert leave_b.json().get("left") is True

    # Host leaves with no remaining active players
    resp = _leave(client, api_key_header, session_id, phone_a)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data.get("ended") is True

    # DB: session ended with correct reason
    ended_at, end_reason = _get_session_ended(session_id)
    assert ended_at is not None, "ended_at must be set"
    assert end_reason == "host_left_no_players"


def test_non_origin_leave_does_not_migrate(
    client, api_key_header, owner_a_token, fresh_table
):
    """BOLA: non-host phone calling leave goes through leave_session (left=True),
    NOT migrate_host. origin_phone_id must remain unchanged."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    session_id = s["session_id"]
    phone_a, phone_b = s["phones"]

    resp = _leave(client, api_key_header, session_id, phone_b)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # Non-host leave returns left=True, NOT migrated
    assert data.get("left") is True
    assert "migrated" not in data

    # origin_phone_id unchanged
    assert _get_origin(session_id) == phone_a


def test_stranger_phone_leave_returns_403(
    client, api_key_header, owner_a_token, fresh_table
):
    """A phone that is not a member of the session at all is rejected.
    The stranger is not the origin, so the router routes to leave_session (non-host path).
    leave_session raises PermissionError('not_a_member') -> _run_trivia maps to 403."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    session_id = s["session_id"]
    stranger = _fresh_phone()

    resp = _leave(client, api_key_header, session_id, stranger)
    # leave_session raises PermissionError for unknown phones -> _run_trivia -> 403
    assert resp.status_code == 403, (
        f"Expected 403 for stranger phone, got {resp.status_code}: {resp.text}"
    )


def test_host_leave_resolves_orphan_chooser_round(
    client, api_key_header, owner_a_token, fresh_table
):
    """Draw a Chooser card (creates a round with result IS NULL), then host leaves.
    migrate_host must resolve that round as 'skipped' (no dangling NULL)."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    session_id = s["session_id"]
    phone_a, phone_b = s["phones"]

    # Select hot-seat (needed before drawing card)
    hs_resp = _select_hot_seat(client, api_key_header, session_id, phone_a)
    assert hs_resp.status_code == 200, hs_resp.text
    chosen_player_id = hs_resp.json()["player_id"]

    # Draw a card -> creates a round row with result IS NULL
    draw_resp = _draw_card(client, api_key_header, session_id, phone_a, chosen_player_id)
    assert draw_resp.status_code == 200, draw_resp.text

    # Host leaves -> orphan Chooser round should be resolved as skipped
    resp = _leave(client, api_key_header, session_id, phone_a)
    assert resp.status_code == 200, resp.text

    # DB: the Chooser round's result must now be 'skipped', not NULL
    result = _get_round_result(session_id, "chooser")
    assert result == "skipped", (
        f"Expected orphan Chooser round to be resolved as 'skipped', got {result!r}"
    )


def test_round_number_survives_migration(
    client, api_key_header, owner_a_token, fresh_table
):
    """GET /sessions/{id}/current-round returns the correct current_round_number
    before and after host migration. The round count must not be altered by migration."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    session_id = s["session_id"]
    phone_a, phone_b = s["phones"]

    # Before any round: current_round_number == 0
    before_resp = _current_round(client, api_key_header, session_id)
    assert before_resp.status_code == 200, before_resp.text
    before_data = before_resp.json()
    assert before_data["current_round_number"] == 0
    assert before_data["ended"] is False

    # Draw a card -> increments current_round_number to 1
    hs_resp = _select_hot_seat(client, api_key_header, session_id, phone_a)
    assert hs_resp.status_code == 200, hs_resp.text
    chosen_player_id = hs_resp.json()["player_id"]

    draw_resp = _draw_card(client, api_key_header, session_id, phone_a, chosen_player_id)
    assert draw_resp.status_code == 200, draw_resp.text

    after_draw = _current_round(client, api_key_header, session_id)
    assert after_draw.status_code == 200, after_draw.text
    assert after_draw.json()["current_round_number"] == 1

    # Host leaves -> migration; round number must stay at 1
    leave_resp = _leave(client, api_key_header, session_id, phone_a)
    assert leave_resp.status_code == 200, leave_resp.text
    assert leave_resp.json().get("migrated") is True

    after_migration = _current_round(client, api_key_header, session_id)
    assert after_migration.status_code == 200, after_migration.text
    data = after_migration.json()
    assert data["current_round_number"] == 1, (
        f"Round number should not change on migration, got {data['current_round_number']}"
    )
    assert data["session_id"] == session_id
    assert data["ended"] is False


def test_current_round_endpoint_returns_correct_fields(
    client, api_key_header, owner_a_token, fresh_table
):
    """GET /sessions/{id}/current-round returns all expected fields with correct types."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    session_id = s["session_id"]

    resp = _current_round(client, api_key_header, session_id)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["session_id"] == session_id
    assert isinstance(data["current_round_number"], int)
    assert isinstance(data["active_count"], int)
    assert isinstance(data["ended"], bool)
    assert data["active_count"] == 2  # 2 phones in session, none left
    assert data["ended"] is False


def test_current_round_unknown_session_404(
    client, api_key_header, owner_a_token, fresh_table
):
    """GET /sessions/{id}/current-round returns 404 for an unknown session."""
    fake_id = str(uuid.uuid4())
    resp = _current_round(client, api_key_header, fake_id)
    assert resp.status_code == 404


def test_is_origin_flag_in_state(
    client, api_key_header, owner_a_token, fresh_table
):
    """GET /sessions/{id}/state returns is_origin=True for the current origin phone
    and is_origin=False for other phones (poll-based promotion detection)."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    session_id = s["session_id"]
    phone_a, phone_b = s["phones"]

    # Origin phone (phone_a) should see is_origin=True
    state_a = _poll_state(client, api_key_header, session_id, phone_a)
    assert state_a.status_code == 200, state_a.text
    assert state_a.json()["is_origin"] is True

    # Non-origin phone (phone_b) should see is_origin=False
    state_b = _poll_state(client, api_key_header, session_id, phone_b)
    assert state_b.status_code == 200, state_b.text
    assert state_b.json()["is_origin"] is False


def test_is_origin_updates_after_migration(
    client, api_key_header, owner_a_token, fresh_table
):
    """After host migration, the new host's poll response returns is_origin=True
    and the old host's response returns is_origin=False (or is_member=False since
    they left). The new host is correctly identified."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    session_id = s["session_id"]
    phone_a, phone_b = s["phones"]

    # Before migration: only A is origin
    assert _poll_state(client, api_key_header, session_id, phone_a).json()["is_origin"] is True
    assert _poll_state(client, api_key_header, session_id, phone_b).json()["is_origin"] is False

    # Host leaves -> migration
    leave_resp = _leave(client, api_key_header, session_id, phone_a)
    assert leave_resp.status_code == 200, leave_resp.text
    assert leave_resp.json().get("migrated") is True

    # After migration: B is now origin
    state_b = _poll_state(client, api_key_header, session_id, phone_b)
    assert state_b.status_code == 200, state_b.text
    assert state_b.json()["is_origin"] is True


def test_host_leave_migrates_db_state_correct(
    client, api_key_header, owner_a_token, fresh_table
):
    """After host migration, DB state is definitive: new host is origin, old host
    is left_early, session is still active. This test asserts all DB invariants
    independently of the response from a duplicate leave call."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    session_id = s["session_id"]
    phone_a, phone_b = s["phones"]

    # Host leaves: migration
    first = _leave(client, api_key_header, session_id, phone_a)
    assert first.status_code == 200, first.text
    assert first.json().get("migrated") is True
    assert "new_host_phone_id" not in first.json()  # BOLA: redacted

    # DB invariants after migration
    assert _get_origin(session_id) == phone_b
    assert _get_player_left_early(session_id, phone_a) is True
    assert _get_player_left_early(session_id, phone_b) is False

    ended_at, end_reason = _get_session_ended(session_id)
    assert ended_at is None, "Session must remain active after migration (not ended)"
    assert end_reason is None


def test_resume_payload_includes_current_round_number(
    client, api_key_header, owner_a_token, fresh_table
):
    """Re-tap by the origin phone returns phase='resume' with current_round_number
    in the table_state payload, so RoundOrigin can seed its cadence position."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    session_id = s["session_id"]
    phone_a = s["phones"][0]

    # Play one Chooser round so current_round_number is non-zero
    hs_resp = _select_hot_seat(client, api_key_header, session_id, phone_a)
    assert hs_resp.status_code == 200, hs_resp.text
    chosen_player_id = hs_resp.json()["player_id"]

    draw_resp = _draw_card(client, api_key_header, session_id, phone_a, chosen_player_id)
    assert draw_resp.status_code == 200, draw_resp.text

    # Re-tap as the origin phone (new counter)
    tag_uid = s["tag_uid"]
    retap = _tap(
        client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"],
        tag_uid, len(s["phones"]) + 1, phone_a,
    )
    table_state = retap.get("table_state", {})
    assert table_state.get("phase") == "resume", (
        f"Expected phase='resume' on re-tap, got {table_state}"
    )
    assert table_state.get("is_origin") is True
    assert "current_round_number" in table_state, (
        "Resume payload must include current_round_number"
    )
    # current_round_number should be 1 after one Chooser draw
    assert table_state["current_round_number"] == 1
