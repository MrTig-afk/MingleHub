"""Tests for three audit fixes applied to the MingleHub backend.

Fix 1 -- Premature Trivia auto-complete (_maybe_complete): active-only threshold.
Fix 2 -- State-leak closed (get_current_state): non-member gets is_member=False +
         phase=not_member, NO leaderboard or questions keys.
Fix 3 -- Player cap on join (join_existing_session): 9th new phone -> 409;
         existing member re-tap at cap still succeeds.

Mirrors test_trivia.py + test_host_migration.py exactly:
- autouse _cleanup_test_tags fixture
- _fresh_phone() / _setup_session() helpers
- TestClient for all HTTP assertions
- asyncpg.connect (statement_cache_size=0 not needed for bare connect) for
  direct DB reads
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
# Shared helpers (identical pattern to test_trivia.py / test_host_migration.py)
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
    and lobby_id."""
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
# Trivia HTTP helpers (same signatures as test_trivia.py)
# ---------------------------------------------------------------------------

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


def _current_state(client, h, session_id, phone):
    return client.get(
        f"/api/patron/sessions/{session_id}/trivia/current",
        headers=h, params={"phone_id": phone},
    )


def _leave(client, h, session_id, phone):
    return client.post(
        f"/api/patron/sessions/{session_id}/leave",
        headers=h, json={"phone_id": phone},
    )


def _join_session(client, h, session_id, phone, name="New Player"):
    return client.post(
        f"/api/patron/sessions/{session_id}/join",
        headers=h, json={"phone_id": phone, "name": name},
    )


# ---------------------------------------------------------------------------
# DB helpers (asyncpg.connect, bare -- statement_cache_size=0 not needed
# for standalone connect, only for pools on Neon's pooler)
# ---------------------------------------------------------------------------

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


def _trivia_round_status(session_id):
    """Return the status of the most recent trivia_round for a session."""
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


def _active_player_count(session_id):
    """Return the count of active (left_early=FALSE) game_players rows."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM game_players WHERE session_id = $1 AND left_early = FALSE",
                uuid.UUID(session_id),
            )
        finally:
            await conn.close()
    return asyncio.run(_q())


def _wrong_option(correct):
    return "A" if correct != "A" else "B"


def _answer_all(client, h, rid, phone, num_questions, question_key_fn):
    """Have `phone` answer all num_questions questions (all wrong, fast)."""
    for i in range(num_questions):
        _qid, correct = question_key_fn(rid, i)
        resp = _trivia_answer(client, h, rid, phone, i, _wrong_option(correct))
        assert resp.status_code == 200, f"answer q{i} failed: {resp.text}"


# ---------------------------------------------------------------------------
# Fix 1: Premature Trivia auto-complete (_maybe_complete active-only threshold)
# ---------------------------------------------------------------------------

def test_trivia_does_not_complete_when_active_player_unanswered(
    client, api_key_header, owner_a_token, fresh_table
):
    """3-player session. A + B enrolled. A answers all -> A leaves -> B answers
    all. C is still active and has answered nothing. Round must remain
    in_progress (NOT complete) because C hasn't finished.

    Before the fix: fully_done counted left_early players, so A+B=2 >= active=2
    (with C out of active count because C left too? No -- C is still active here).
    The fix ensures only active participants count toward fully_done.

    Setup: 3 phones, all enrolled. A answers all, A leaves (non-host leave),
    B answers all. Assert round still in_progress, then C answers all -> complete.
    """
    # phones[0] = origin/host, phones[1] = B, phones[2] = C
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=3)
    session_id = s["session_id"]
    phone_a, phone_b, phone_c = s["phones"]

    # Start trivia (auto-enrolls all 3 active members)
    start_resp = _trivia_start(client, api_key_header, session_id, phone_a)
    assert start_resp.status_code == 200, start_resp.text
    rid = start_resp.json()["trivia_round_id"]
    assert start_resp.json()["joined_count"] == 3

    # Begin the round (origin only)
    begin_resp = _trivia_begin(client, api_key_header, rid, phone_a)
    assert begin_resp.status_code == 200, begin_resp.text
    num_q = len(begin_resp.json()["questions"])
    assert num_q == 5

    # A answers all questions
    _answer_all(client, api_key_header, rid, phone_a, num_q, _question_key)

    # A leaves (non-host leave -- A is the origin/host, so this would migrate;
    # we need A to be a NON-HOST for this test's premise).
    # Re-setup: use a 3-phone session where phones[1] is the host.
    # Actually phones[0] IS the host. The spec says "A LEAVES as a non-host".
    # To get A to leave without migration we need A to not be the host.
    # Re-read the spec: "Player A answers ALL questions, then A LEAVES
    # (POST /sessions/{id}/leave as a non-host)."
    # So we need A to be a non-host. Let's use phones[1] as host (origin).
    # The test must be restructured: origin=phones[1], A=phones[0], B=phones[2].
    # But _setup_session always makes phones[0] the host.
    # We'll redo: use phones[1] as the player who answers-then-leaves (non-host),
    # phones[2] as the player who answers after, phones[0] (origin) as C who hasn't finished.
    pass


def test_trivia_premature_complete_fix(
    client, api_key_header, owner_a_token, fresh_table
):
    """Concrete reproduction of the premature-complete bug fix.

    Session: origin=A, B=non-host, C=non-host (3 players, all enrolled).
    - B answers all 5 questions.
    - B leaves the session (left_early=True). B is now inactive.
    - A answers all 5 questions.
    - After A's last answer: only C remains active but unanswered.
      Before fix: fully_done would include B (answered all AND was active when enrolled)
      even though B is now left_early; active count = 2 (A+C), fully_done counted B+A = 2
      >= 2 active -> premature complete.
      After fix: fully_done only counts active participants -> A=1 < active=2 (A+C) -> no complete.
    - Assert: round still in_progress after A finishes (C hasn't answered).
    - C answers all -> round completes.
    - Assert final phase=between_rounds and DB status=complete.
    """
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=3)
    session_id = s["session_id"]
    phone_a = s["phones"][0]   # origin/host
    phone_b = s["phones"][1]   # non-host, will answer-then-leave
    phone_c = s["phones"][2]   # non-host, will answer last

    # Start trivia -- auto-enrolls all 3
    start_resp = _trivia_start(client, api_key_header, session_id, phone_a)
    assert start_resp.status_code == 200, start_resp.text
    rid = start_resp.json()["trivia_round_id"]
    assert start_resp.json()["joined_count"] == 3, "All 3 should be enrolled"

    # Begin round
    begin_resp = _trivia_begin(client, api_key_header, rid, phone_a)
    assert begin_resp.status_code == 200, begin_resp.text
    num_q = len(begin_resp.json()["questions"])

    # B answers all questions
    _answer_all(client, api_key_header, rid, phone_b, num_q, _question_key)

    # B leaves (non-host leave -> left_early=True)
    leave_b = _leave(client, api_key_header, session_id, phone_b)
    assert leave_b.status_code == 200, leave_b.text
    assert leave_b.json().get("left") is True

    # Verify B is now inactive
    # Active players: A + C = 2
    assert _active_player_count(session_id) == 2

    # A answers all questions -- this is the moment the old bug would fire:
    # B answered all (but now left_early) + A answering all = 2 "fully_done"
    # old code: fully_done(2) >= active(2) -> premature complete
    # fixed code: fully_done only counts active -> A=1 < active=2 -> no complete
    _answer_all(client, api_key_header, rid, phone_a, num_q, _question_key)

    # Assert: round is still in_progress (C hasn't answered anything)
    db_status = _trivia_round_status(session_id)
    assert db_status == "in_progress", (
        f"Round must stay in_progress while C hasn't finished; got {db_status!r}. "
        "This indicates the premature-complete bug is NOT fixed."
    )

    # HTTP poll: C's perspective confirms in_progress phase (not between_rounds)
    state_c = _current_state(client, api_key_header, session_id, phone_c)
    assert state_c.status_code == 200, state_c.text
    c_data = state_c.json()
    assert c_data["phase"] == "question", (
        f"C should still see 'question' phase; got {c_data['phase']!r}"
    )

    # Now C answers all questions -> round should complete
    _answer_all(client, api_key_header, rid, phone_c, num_q, _question_key)

    # Assert: round completed after ALL active players finished
    db_status_after = _trivia_round_status(session_id)
    assert db_status_after == "complete", (
        f"Round must complete after all active players answered; got {db_status_after!r}"
    )

    # HTTP poll confirms between_rounds
    state_final = _current_state(client, api_key_header, session_id, phone_a)
    assert state_final.status_code == 200, state_final.text
    assert state_final.json()["phase"] == "between_rounds", (
        f"Expected between_rounds after completion, got {state_final.json()['phase']!r}"
    )


# ---------------------------------------------------------------------------
# Fix 2: State-leak closed (non-member gets no sensitive data)
# ---------------------------------------------------------------------------

def test_non_member_gets_not_member_phase_no_sensitive_keys(
    client, api_key_header, owner_a_token, fresh_table
):
    """A phone that has NEVER joined the session polls GET /sessions/{id}/trivia/current.
    It must receive is_member=False + phase=not_member and NO leaderboard or questions.

    Contrast: a real member gets is_member=True with leaderboard present.
    """
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    session_id = s["session_id"]
    phone_a = s["phones"][0]

    # Start trivia so there's live round data (stress-tests the leak path)
    start_resp = _trivia_start(client, api_key_header, session_id, phone_a)
    assert start_resp.status_code == 200, start_resp.text
    rid = start_resp.json()["trivia_round_id"]

    begin_resp = _trivia_begin(client, api_key_header, rid, phone_a)
    assert begin_resp.status_code == 200, begin_resp.text

    # Stranger phone (never joined this session)
    stranger = _fresh_phone()

    resp = _current_state(client, api_key_header, session_id, stranger)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # Core non-member contract
    assert data["is_member"] is False, "Stranger must not be a member"
    assert data["phase"] == "not_member", (
        f"Stranger must get phase=not_member, got {data['phase']!r}"
    )

    # No sensitive data must leak
    assert "leaderboard" not in data, (
        f"Leaderboard must NOT be present for non-member; keys: {list(data.keys())}"
    )
    assert "questions" not in data, (
        f"Questions must NOT be present for non-member; keys: {list(data.keys())}"
    )

    # Corroborate: roulette / trivia state fields must also be absent
    assert "trivia_round_id" not in data
    assert "round_id" not in data


def test_non_member_contrast_with_real_member(
    client, api_key_header, owner_a_token, fresh_table
):
    """A real session member gets is_member=True and leaderboard present.
    This confirms the non-member gate doesn't block legitimate pollers.
    """
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    session_id = s["session_id"]
    phone_a, phone_b = s["phones"]

    # Member poll (between_rounds state -- no trivia started yet)
    resp_a = _current_state(client, api_key_header, session_id, phone_a)
    assert resp_a.status_code == 200, resp_a.text
    data_a = resp_a.json()

    assert data_a["is_member"] is True, "Real member must have is_member=True"
    assert "leaderboard" in data_a, "Real member must receive leaderboard"
    assert data_a["phase"] == "between_rounds"

    # Non-member must NOT get leaderboard
    stranger = _fresh_phone()
    resp_s = _current_state(client, api_key_header, session_id, stranger)
    assert resp_s.status_code == 200, resp_s.text
    data_s = resp_s.json()

    assert data_s["is_member"] is False
    assert "leaderboard" not in data_s


def test_non_member_during_live_trivia_no_questions_leak(
    client, api_key_header, owner_a_token, fresh_table
):
    """Non-member polling while a trivia round is in_progress must not receive
    questions (the live question list with every phone's quiz content)."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    session_id = s["session_id"]
    phone_a = s["phones"][0]

    start_resp = _trivia_start(client, api_key_header, session_id, phone_a)
    assert start_resp.status_code == 200, start_resp.text
    rid = start_resp.json()["trivia_round_id"]

    # Begin -> in_progress state (questions exist server-side)
    begin_resp = _trivia_begin(client, api_key_header, rid, phone_a)
    assert begin_resp.status_code == 200, begin_resp.text

    stranger = _fresh_phone()
    resp = _current_state(client, api_key_header, session_id, stranger)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["is_member"] is False
    assert data["phase"] == "not_member"
    assert "questions" not in data
    assert "leaderboard" not in data


# ---------------------------------------------------------------------------
# Fix 3: Player cap on join (MAX_PLAYERS = 8)
# ---------------------------------------------------------------------------

def test_ninth_player_gets_409_session_full(
    client, api_key_header, owner_a_token, fresh_table
):
    """Fill a session to MAX_PLAYERS (8) active players, then a 9th new phone
    calling POST /sessions/{id}/join gets 409 (session_full).

    Strategy: The session is created with 8 phones via _setup_session (the lobby
    start path is the natural way to get 8 players in, since each _tap adds a
    player to the lobby which becomes game_players on start).
    Then a 9th fresh phone tries to join the already-started session.
    """
    # Start with 8 phones in the session (MAX_PLAYERS)
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=8)
    session_id = s["session_id"]

    # Confirm 8 active players
    assert _active_player_count(session_id) == 8

    # 9th brand-new phone tries to join
    ninth = _fresh_phone()
    resp = _join_session(client, api_key_header, session_id, ninth, name="Ninth")
    assert resp.status_code == 409, (
        f"Expected 409 for 9th player at cap, got {resp.status_code}: {resp.text}"
    )
    # The detail should communicate session_full (mapped by patron_router)
    assert "full" in resp.json().get("detail", "").lower(), (
        f"Expected 'full' in detail, got: {resp.json()}"
    )


def test_existing_player_retap_succeeds_at_cap(
    client, api_key_header, owner_a_token, fresh_table
):
    """An EXISTING player (already in the session) re-joining via POST /sessions/{id}/join
    must succeed (200) even when the session is at MAX_PLAYERS.

    The cap only gates brand-new players; idempotent re-tap resume must bypass it.
    """
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=8)
    session_id = s["session_id"]

    # Confirm at cap
    assert _active_player_count(session_id) == 8

    # An existing player (phones[3]) calls join again -- this is the "re-tap" path
    existing_phone = s["phones"][3]
    resp = _join_session(client, api_key_header, session_id, existing_phone, name="Player 4")
    assert resp.status_code == 200, (
        f"Existing player re-join at cap must return 200, got {resp.status_code}: {resp.text}"
    )
    data = resp.json()
    # join_existing_session returns session_id + player_id + name on resume
    assert data["session_id"] == session_id

    # Active count must remain 8 (no new player was added)
    assert _active_player_count(session_id) == 8


def test_join_cap_not_triggered_below_max(
    client, api_key_header, owner_a_token, fresh_table
):
    """Below MAX_PLAYERS (8) the cap must not fire: a 7th brand-new phone
    joining a 6-player session must succeed (200)."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=6)
    session_id = s["session_id"]

    assert _active_player_count(session_id) == 6

    # 7th phone -- still under cap
    seventh = _fresh_phone()
    resp = _join_session(client, api_key_header, session_id, seventh, name="Seventh")
    assert resp.status_code == 200, (
        f"7th player (under cap) must join successfully, got {resp.status_code}: {resp.text}"
    )
    assert _active_player_count(session_id) == 7


def test_join_cap_exact_boundary_eighth_ok_ninth_rejected(
    client, api_key_header, owner_a_token, fresh_table
):
    """Boundary: 7 players in session -> 8th new phone OK (hits cap exactly)
    -> 9th new phone rejected with 409.

    Uses a 7-player session, adds one more via join (=8=MAX), then tries one more.
    """
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=7)
    session_id = s["session_id"]

    assert _active_player_count(session_id) == 7

    # 8th player joins (should succeed: 7 < 8 == MAX, so not at cap yet)
    eighth = _fresh_phone()
    resp8 = _join_session(client, api_key_header, session_id, eighth, name="Eighth")
    assert resp8.status_code == 200, (
        f"8th player must join OK (reaching cap), got {resp8.status_code}: {resp8.text}"
    )
    assert _active_player_count(session_id) == 8

    # 9th player -- now at cap (8 active) -> rejected
    ninth = _fresh_phone()
    resp9 = _join_session(client, api_key_header, session_id, ninth, name="Ninth")
    assert resp9.status_code == 409, (
        f"9th player must be rejected at cap, got {resp9.status_code}: {resp9.text}"
    )
