"""Regression tests for three session-hardening fixes shipped this session.

Fix 1 -- Host leaves mid-Trivia: round stays in_progress until the remaining
         active players finish, and host migration reassigns origin correctly.
         (Exercises _maybe_complete left_early fix + migrate_host together.)

Fix 2 -- migrate_host resolves MULTIPLE orphan Chooser rounds and bumps
         cards_skipped by len(orphans), not a hard-coded +1.

Fix 3 -- Roulette: plurality loser after a mid-round player-leave correctly
         excludes the left-early player from both the loser pool and the +3
         award (extends coverage beyond the already-tested 2-player tie cases).

Mirrors test_host_migration.py and test_audit_fixes.py exactly:
- autouse _cleanup_test_tags fixture
- _fresh_phone() / _setup_session() helpers (identical signatures)
- TestClient (HTTP) for all assertions
- asyncpg.connect (bare -- statement_cache_size=0 not needed for standalone
  connect, only for pools on Neon's pooler) for direct DB reads
- SUPABASE_* unset in CI -> realtime publish is a no-op; assertions never
  depend on a delivered broadcast.
"""
import asyncio
import os
import uuid

import asyncpg
import pytest

from api.tests.conftest import pair_tag, simulate_tap


# ---------------------------------------------------------------------------
# Autouse cleanup (same as test_trivia.py / test_host_migration.py)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _cleanup_test_tags():
    """Delete test nfc_tags after each test to prevent tag_uid collisions."""
    yield

    async def _delete():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute("DELETE FROM nfc_tags WHERE tag_uid LIKE 'test-tag-%'")
        finally:
            await conn.close()

    asyncio.run(_delete())


# ---------------------------------------------------------------------------
# Shared helpers (identical pattern to test_host_migration.py)
# ---------------------------------------------------------------------------

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
    session_id, table_id, origin phone, the full ordered phones list, tag_uid,
    and lobby_id. Phones are tapped in order so phones[0] has earliest joined_at."""
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


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _leave(client, h, session_id, phone):
    return client.post(
        f"/api/patron/sessions/{session_id}/leave",
        headers=h, json={"phone_id": phone},
    )


def _trivia_start(client, h, session_id, phone):
    return client.post(
        f"/api/patron/sessions/{session_id}/trivia/start",
        headers=h, json={"phone_id": phone},
    )


def _trivia_begin(client, h, rid, phone):
    return client.post(
        f"/api/patron/trivia/{rid}/begin",
        headers=h, json={"phone_id": phone},
    )


def _trivia_answer(client, h, rid, phone, index, option, time_ms=0):
    return client.post(
        f"/api/patron/trivia/{rid}/answer",
        headers=h,
        json={
            "phone_id": phone, "question_index": index,
            "selected_option": option, "time_to_answer_ms": time_ms,
        },
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


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

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


def _trivia_round_status(session_id):
    """Return status of the most recent trivia_round for a session."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await conn.fetchval(
                """
                SELECT status FROM trivia_rounds
                WHERE session_id = $1
                ORDER BY created_at DESC
                LIMIT 1
                """,
                uuid.UUID(session_id),
            )
        finally:
            await conn.close()
    return asyncio.run(_q())


def _get_session_cards_skipped(session_id):
    """Return cards_skipped for a session."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await conn.fetchval(
                "SELECT cards_skipped FROM game_sessions WHERE id = $1",
                uuid.UUID(session_id),
            )
        finally:
            await conn.close()
    return asyncio.run(_q())


def _get_round_results(session_id, round_type="chooser"):
    """Return all result values for rounds of the given type in a session."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            rows = await conn.fetch(
                "SELECT result FROM rounds "
                "WHERE session_id = $1 AND round_type = $2 "
                "ORDER BY created_at ASC",
                uuid.UUID(session_id), round_type,
            )
            return [r["result"] for r in rows]
        finally:
            await conn.close()
    return asyncio.run(_q())


def _question_key(round_id, index):
    """Return (question_id, correct_option) for the question at `index`."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            qids = await conn.fetchval(
                "SELECT question_ids FROM trivia_rounds WHERE id = $1",
                uuid.UUID(round_id),
            )
            qid = qids[index]
            correct = await conn.fetchval(
                "SELECT correct_option FROM trivia_questions WHERE id = $1", qid
            )
            return qid, correct
        finally:
            await conn.close()
    return asyncio.run(_q())


def _wrong_option(correct):
    return "A" if correct != "A" else "B"


def _answer_all(client, h, rid, phone, num_questions):
    """Have `phone` answer all questions (always wrong, maximises DB coverage)."""
    for i in range(num_questions):
        _qid, correct = _question_key(rid, i)
        resp = _trivia_answer(client, h, rid, phone, i, _wrong_option(correct))
        assert resp.status_code == 200, f"answer q{i} failed: {resp.text}"


def _insert_orphan_chooser_rounds(session_id, count):
    """Directly INSERT `count` chooser rounds with result IS NULL, simulating
    a StrictMode double-draw that migrate_host must clean up."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            for _ in range(count):
                await conn.execute(
                    """
                    INSERT INTO rounds
                        (id, session_id, round_number, round_type, result)
                    VALUES ($1, $2, 0, 'chooser', NULL)
                    """,
                    str(uuid.uuid4()), session_id,
                )
        finally:
            await conn.close()
    asyncio.run(_q())


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


# ---------------------------------------------------------------------------
# Fix 1: Host leaves mid-Trivia -> migration + round stays in_progress
# ---------------------------------------------------------------------------

def test_host_leaves_mid_trivia_migration_and_round_stays_in_progress(
    client, api_key_header, owner_a_token, fresh_table
):
    """3-player session. All 3 auto-enroll in Trivia.

    (a) Origin (host) answers all 5 questions, then calls leave (migrate_host).
    (b) Assert: origin_phone_id reassigned to the earliest-joined active player
        (phones[1], since phones[0] is now left_early).
    (c) Assert: the trivia round is NOT prematurely complete just because the
        now-left host finished all questions -- _maybe_complete must only count
        ACTIVE participants. Both remaining players (phones[1] and phones[2])
        are still active and unanswered -> round stays in_progress.
    (d) Have the 2 remaining active players answer all questions.
    (e) Assert: round completes after the last active player finishes.
    """
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=3)
    session_id = s["session_id"]
    phone_host, phone_b, phone_c = s["phones"]

    # Start Trivia (auto-enrolls all 3 active members)
    start_resp = _trivia_start(client, api_key_header, session_id, phone_host)
    assert start_resp.status_code == 200, start_resp.text
    rid = start_resp.json()["trivia_round_id"]
    assert start_resp.json()["joined_count"] == 3, "All 3 should be enrolled"

    # Begin round (origin only)
    begin_resp = _trivia_begin(client, api_key_header, rid, phone_host)
    assert begin_resp.status_code == 200, begin_resp.text
    num_q = len(begin_resp.json()["questions"])
    assert num_q == 5

    # Host answers all 5 questions
    _answer_all(client, api_key_header, rid, phone_host, num_q)

    # Round must still be in_progress (phone_b and phone_c haven't answered)
    assert _trivia_round_status(session_id) == "in_progress", (
        "Round must stay in_progress after only the host answered"
    )

    # Host leaves -> triggers migrate_host (host is origin)
    leave_resp = _leave(client, api_key_header, session_id, phone_host)
    assert leave_resp.status_code == 200, leave_resp.text
    leave_data = leave_resp.json()

    # (a) Migration happened: response says migrated, new_host is earliest-joined
    assert leave_data.get("migrated") is True, (
        f"Expected migrated=True in leave response, got: {leave_data}"
    )
    assert leave_data.get("new_host_phone_id") == phone_b, (
        f"Expected new host to be phones[1]={phone_b}, got {leave_data.get('new_host_phone_id')}"
    )

    # (b) DB: origin reassigned to phones[1]
    assert _get_origin(session_id) == phone_b, (
        "DB origin_phone_id must be reassigned to the earliest-joined active player"
    )

    # (c) Round is still in_progress (the now-left host's answers don't count
    #     toward _maybe_complete's fully_done, because gp.left_early = TRUE now)
    assert _trivia_round_status(session_id) == "in_progress", (
        "Round must NOT complete prematurely when the host leaves after answering -- "
        "remaining active players (phone_b, phone_c) have not yet answered. "
        "This indicates the _maybe_complete left_early fix is NOT working."
    )

    # (d) Remaining active players answer all questions
    _answer_all(client, api_key_header, rid, phone_b, num_q)

    # After phone_b finishes, phone_c still hasn't answered -> still in_progress
    assert _trivia_round_status(session_id) == "in_progress", (
        "Round must stay in_progress while phone_c still has unanswered questions"
    )

    _answer_all(client, api_key_header, rid, phone_c, num_q)

    # (e) Now all active players are done -> round completes
    assert _trivia_round_status(session_id) == "complete", (
        "Round must complete after all remaining active players have answered"
    )


def test_host_leaves_mid_trivia_leave_response_shape(
    client, api_key_header, owner_a_token, fresh_table
):
    """The leave response from a host leaving mid-Trivia must carry the
    migrated/new_host_phone_id fields (not just left=True). This is the
    shape the frontend uses to detect promotion."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=3)
    session_id = s["session_id"]
    phone_host, phone_b, _phone_c = s["phones"]

    start_resp = _trivia_start(client, api_key_header, session_id, phone_host)
    assert start_resp.status_code == 200, start_resp.text
    rid = start_resp.json()["trivia_round_id"]

    begin_resp = _trivia_begin(client, api_key_header, rid, phone_host)
    assert begin_resp.status_code == 200, begin_resp.text
    num_q = len(begin_resp.json()["questions"])

    _answer_all(client, api_key_header, rid, phone_host, num_q)

    leave_resp = _leave(client, api_key_header, session_id, phone_host)
    assert leave_resp.status_code == 200, leave_resp.text
    data = leave_resp.json()

    # Must carry migration keys, not the plain non-host leave shape
    assert "migrated" in data, f"Expected 'migrated' key in response: {data}"
    assert "new_host_phone_id" in data, f"Expected 'new_host_phone_id' in response: {data}"
    assert data["migrated"] is True
    assert data["new_host_phone_id"] == phone_b
    # Must NOT carry 'left' key (that's the non-host path)
    assert "left" not in data, (
        f"Host migration must not return 'left'; response: {data}"
    )


# ---------------------------------------------------------------------------
# Fix 2: migrate_host resolves MULTIPLE orphan Chooser rounds
# ---------------------------------------------------------------------------

def test_migrate_host_resolves_multiple_orphan_chooser_rounds(
    client, api_key_header, owner_a_token, fresh_table
):
    """StrictMode double-draw simulation: INSERT two chooser rounds with
    result IS NULL directly via SQL. Host leaves -> migrate_host must resolve
    BOTH orphan rounds as 'skipped' AND bump cards_skipped by 2 (not 1).

    This exercises the fetch+len fix in migrate_host: the old hard-coded +1
    would leave cards_skipped=1 even though two rounds were resolved.
    """
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    session_id = s["session_id"]
    phone_host, phone_b = s["phones"]

    # Confirm starting state
    assert _get_session_cards_skipped(session_id) == 0

    # Directly INSERT two orphan chooser rounds (simulates StrictMode double-draw)
    _insert_orphan_chooser_rounds(session_id, 2)

    # Verify the two orphan rounds exist with result IS NULL
    results_before = _get_round_results(session_id, "chooser")
    assert results_before.count(None) == 2, (
        f"Expected 2 NULL-result chooser rounds before host leave, got: {results_before}"
    )

    # Host leaves -> migrate_host
    leave_resp = _leave(client, api_key_header, session_id, phone_host)
    assert leave_resp.status_code == 200, leave_resp.text
    assert leave_resp.json().get("migrated") is True

    # Both orphan rounds must now be result='skipped'
    results_after = _get_round_results(session_id, "chooser")
    assert all(r == "skipped" for r in results_after), (
        f"All orphan chooser rounds must be 'skipped' after migration, got: {results_after}"
    )
    assert len(results_after) == 2, (
        f"Expected exactly 2 chooser rounds, got {len(results_after)}"
    )

    # cards_skipped must be bumped by 2 (len(orphans)), not 1
    cards_skipped = _get_session_cards_skipped(session_id)
    assert cards_skipped == 2, (
        f"cards_skipped must be 2 (one per resolved orphan), got {cards_skipped}. "
        "This indicates the fetch+len fix is NOT applied -- only +1 was added."
    )


def test_migrate_host_no_orphan_rounds_leaves_cards_skipped_unchanged(
    client, api_key_header, owner_a_token, fresh_table
):
    """When no orphan Chooser rounds exist (clean state), migrate_host must
    NOT increment cards_skipped at all."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    session_id = s["session_id"]
    phone_host, _phone_b = s["phones"]

    assert _get_session_cards_skipped(session_id) == 0

    # No draws, no orphan rounds -> host leaves
    leave_resp = _leave(client, api_key_header, session_id, phone_host)
    assert leave_resp.status_code == 200, leave_resp.text
    assert leave_resp.json().get("migrated") is True

    # cards_skipped must remain 0
    assert _get_session_cards_skipped(session_id) == 0, (
        "cards_skipped must not increase when there are no orphan Chooser rounds"
    )


def test_migrate_host_single_orphan_chooser_bumps_by_one(
    client, api_key_header, owner_a_token, fresh_table
):
    """Single orphan Chooser round (the normal draw-then-leave case):
    cards_skipped increments by exactly 1."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    session_id = s["session_id"]
    phone_host, phone_b = s["phones"]

    # Hot-seat + draw-card creates one chooser round with result IS NULL
    hs_resp = _select_hot_seat(client, api_key_header, session_id, phone_host)
    assert hs_resp.status_code == 200, hs_resp.text
    chosen_player_id = hs_resp.json()["player_id"]

    draw_resp = _draw_card(client, api_key_header, session_id, phone_host, chosen_player_id)
    assert draw_resp.status_code == 200, draw_resp.text

    # Confirm one orphan exists
    results_before = _get_round_results(session_id, "chooser")
    assert results_before.count(None) == 1

    # Host leaves -> migrate_host resolves the single orphan
    leave_resp = _leave(client, api_key_header, session_id, phone_host)
    assert leave_resp.status_code == 200, leave_resp.text
    assert leave_resp.json().get("migrated") is True

    # Round resolved as skipped
    results_after = _get_round_results(session_id, "chooser")
    assert results_after == ["skipped"]

    # cards_skipped bumped by exactly 1
    assert _get_session_cards_skipped(session_id) == 1


# ---------------------------------------------------------------------------
# Fix 3: Roulette -- tie cases already covered; add untested plurality variant
# ---------------------------------------------------------------------------
# test_roulette.py already covers:
#   test_tally_tie_shared_blame   -- 2-player all-tied, no +3
#   test_tally_three_way_tie      -- 3-player all-tied, no +3
#   test_tally_plurality_loser    -- 3-player plurality (basic)
#   test_reveal_force_tally_partial -- force-tally with partial votes
#
# The scenario NOT yet covered: a left-early player who was present when the
# round started (and appears in the players list) is voted for after leaving.
# The left-early player is the plurality loser; remaining active players should
# still receive +3. This validates that the loser can be left-early but the
# non-loser award only goes to currently active players.

def test_roulette_left_early_player_can_be_plurality_loser(
    client, api_key_header, owner_a_token, fresh_table
):
    """4-player session.
    - All 4 start a roulette round.
    - phones[3] leaves (left_early=True) mid-round AFTER the round started.
    - 3 remaining active players all vote for the left-early player (phones[3]'s player).
    - Force-reveal (origin): left-early player is the plurality loser.
    - The 3 active non-loser players each receive +3.
    - The left-early player's score stays 0 (they cannot receive +3).
    - total_score increases by 9 (3 non-losers * 3).

    This confirms:
    (a) A left-early player CAN be voted as loser (no guard removes them from candidates).
    (b) The all_tied guard only fires when all ACTIVE players are losers --
        here the loser is left-early, so all_tied is False and +3 awards correctly.
    (c) +3 only goes to active (left_early=FALSE) non-losers.
    """
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=4)
    session_id = s["session_id"]
    phone_host, phone_b, phone_c, phone_d = s["phones"]

    # Start roulette (all 4 active)
    start_resp = _start_roulette(client, api_key_header, session_id, phone_host)
    assert start_resp.status_code == 200, start_resp.text
    round_id = start_resp.json()["round_id"]
    assert start_resp.json()["active_total"] == 4

    # phones[3] leaves mid-round (non-host leave)
    leave_resp = _leave(client, api_key_header, session_id, phone_d)
    assert leave_resp.status_code == 200, leave_resp.text
    assert leave_resp.json().get("left") is True

    # Get the left-early player's UUID
    left_player_id = _player_id_by_phone(session_id, phone_d)

    # The 3 remaining active players all vote for the left-early player
    _vote_loser(client, api_key_header, round_id, phone_host, left_player_id)
    _vote_loser(client, api_key_header, round_id, phone_b, left_player_id)
    _vote_loser(client, api_key_header, round_id, phone_c, left_player_id)
    # 3 votes cast but auto-tally checks active_total (now 3 after phone_d left)
    # -> auto-tally fires (3 active players all voted)

    # Force reveal to ensure tally (auto-tally may already have fired above;
    # reveal is idempotent if already resolved -- but we need the response data).
    reveal_resp = _reveal(client, api_key_header, round_id, phone_host)
    assert reveal_resp.status_code == 200, reveal_resp.text
    reveal_data = reveal_resp.json()

    # Left-early player is the loser
    loser_ids = [loser["id"] for loser in reveal_data["losers"]]
    assert left_player_id in loser_ids, (
        f"Left-early player must be the plurality loser; losers: {reveal_data['losers']}"
    )

    # points_awarded must be 3 (not 0 -- the loser is not "everyone" since
    # the 3 active players are non-losers)
    assert reveal_data["points_awarded"] == 3, (
        f"Expected points_awarded=3 (loser is the only loser, not all active), "
        f"got {reveal_data['points_awarded']}"
    )

    # Each active non-loser gets +3
    assert _player_score_by_phone(session_id, phone_host) == 3
    assert _player_score_by_phone(session_id, phone_b) == 3
    assert _player_score_by_phone(session_id, phone_c) == 3

    # Left-early player's score stays 0 (not eligible for +3)
    assert _player_score_by_phone(session_id, phone_d) == 0


def test_roulette_all_active_tied_no_plus_three(
    client, api_key_header, owner_a_token, fresh_table
):
    """Explicit tie case: 3 active players each vote for a different player
    -> all active players receive max_votes=1 each -> all_tied=True -> no +3.

    This mirrors test_roulette.py's test_tally_three_way_tie but is included
    here as an independent regression anchor for the session-hardening suite.
    Using reveal (force-tally) to avoid relying on auto-tally timing.
    """
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=3)
    session_id = s["session_id"]
    phone_host, phone_b, phone_c = s["phones"]

    start_resp = _start_roulette(client, api_key_header, session_id, phone_host)
    assert start_resp.status_code == 200, start_resp.text
    round_id = start_resp.json()["round_id"]

    p0_id = _player_id_by_phone(session_id, phone_host)
    p1_id = _player_id_by_phone(session_id, phone_b)
    p2_id = _player_id_by_phone(session_id, phone_c)

    # Cyclic votes: 0->1, 1->2, 2->0 (each gets exactly 1 vote)
    _vote_loser(client, api_key_header, round_id, phone_host, p1_id)
    _vote_loser(client, api_key_header, round_id, phone_b, p2_id)
    _vote_loser(client, api_key_header, round_id, phone_c, p0_id)

    reveal_resp = _reveal(client, api_key_header, round_id, phone_host)
    assert reveal_resp.status_code == 200, reveal_resp.text
    data = reveal_resp.json()

    # points_awarded must be 0 (all active are co-losers -> all_tied rule)
    assert data["points_awarded"] == 0, (
        f"Expected points_awarded=0 when all active players tie, got {data['points_awarded']}"
    )

    # No score changes for anyone
    assert _player_score_by_phone(session_id, phone_host) == 0
    assert _player_score_by_phone(session_id, phone_b) == 0
    assert _player_score_by_phone(session_id, phone_c) == 0
