"""Tests for the Trivia round (gamespec.md: Round Type 2 -- Trivia).

Follows test_chooser.py's pattern: fresh_table fixture, _fresh_phone(),
pair_tag/simulate_tap from conftest. Multi-phone, so the setup helper taps and
starts a session with several phones, each bound to its phone_id.

These run CI-equivalent (SUPABASE_* unset -> realtime publish is a no-op), so the
assertions only depend on the HTTP/DB layer, never on a delivered broadcast.
"""
import asyncio
import os
import uuid

import asyncpg
import pytest

from api.services import trivia_service
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


def _setup_session(client, api_key_header, owner_a_token, table_info, num_phones=2, adults_only=False):
    """Tap `num_phones` phones, name them, host=first, start. Returns a dict with
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


# --- trivia HTTP helpers ---

def _start(client, h, session_id, phone):
    return client.post(f"/api/patron/sessions/{session_id}/trivia/start", headers=h, json={"phone_id": phone})


def _join(client, h, rid, phone):
    return client.post(f"/api/patron/trivia/{rid}/join", headers=h, json={"phone_id": phone})


def _begin(client, h, rid, phone):
    return client.post(f"/api/patron/trivia/{rid}/begin", headers=h, json={"phone_id": phone})


def _answer(client, h, rid, phone, index, option, time_ms=0):
    return client.post(
        f"/api/patron/trivia/{rid}/answer", headers=h,
        json={"phone_id": phone, "question_index": index, "selected_option": option,
              "time_to_answer_ms": time_ms},
    )


def _finish(client, h, rid, phone):
    return client.post(f"/api/patron/trivia/{rid}/finish", headers=h, json={"phone_id": phone})


def _abandon(client, h, rid, phone):
    return client.post(f"/api/patron/trivia/{rid}/abandon", headers=h, json={"phone_id": phone})


def _current(client, h, session_id, phone):
    return client.get(f"/api/patron/sessions/{session_id}/trivia/current", headers=h, params={"phone_id": phone})


def _leaderboard(client, h, session_id):
    return client.get(f"/api/patron/sessions/{session_id}/leaderboard", headers=h)


def _leave(client, h, session_id, phone):
    return client.post(f"/api/patron/sessions/{session_id}/leave", headers=h, json={"phone_id": phone})


def _rejoin(client, h, session_id, phone):
    return client.post(f"/api/patron/sessions/{session_id}/rejoin", headers=h, json={"phone_id": phone})


# --- DB helpers (tests may read the answer key directly; the API never leaks it) ---

def _question_key(round_id, index):
    """(question_id, correct_option) for the question at `index` of a round."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            qids = await conn.fetchval(
                "SELECT question_ids FROM trivia_rounds WHERE id = $1", uuid.UUID(round_id)
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


def _player_score(session_id, phone_id):
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


# --- tests ---

def test_start_requires_origin_phone(client, api_key_header, owner_a_token, fresh_table):
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    resp = _start(client, api_key_header, s["session_id"], s["phones"][1])  # non-origin
    assert resp.status_code == 403


def test_start_enrolls_all_active_members(client, api_key_header, owner_a_token, fresh_table):
    # Trivia is auto-entered between rounds, so every active phone is enrolled
    # at start -- no manual tap-to-join.
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    resp = _start(client, api_key_header, s["session_id"], s["origin"])
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "gathering"
    assert data["joined_count"] == 2  # both members auto-enrolled
    assert data["num_questions"] == 5


def test_start_requires_two_active_players(client, api_key_header, owner_a_token, fresh_table):
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    # One player leaves -> only 1 active -> Trivia can't run (engine picks another type).
    leave = _leave(client, api_key_header, s["session_id"], s["phones"][1])
    assert leave.status_code == 200, leave.text
    resp = _start(client, api_key_header, s["session_id"], s["origin"])
    assert resp.status_code == 409
    assert resp.json()["detail"] == "not_enough_players"


def test_abandon_at_gather(client, api_key_header, owner_a_token, fresh_table):
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    rid = _start(client, api_key_header, s["session_id"], s["origin"]).json()["trivia_round_id"]
    resp = _abandon(client, api_key_header, rid, s["origin"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "abandoned_at_gather"
    # An analytics round row should record the abandon.
    state = _current(client, api_key_header, s["session_id"], s["origin"]).json()
    assert state["phase"] == "between_rounds"


def test_stranger_cannot_join(client, api_key_header, owner_a_token, fresh_table):
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    rid = _start(client, api_key_header, s["session_id"], s["origin"]).json()["trivia_round_id"]
    resp = _join(client, api_key_header, rid, _fresh_phone())  # never in the session
    assert resp.status_code == 403


def test_correct_option_never_leaked_before_answer(client, api_key_header, owner_a_token, fresh_table):
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    rid = _start(client, api_key_header, s["session_id"], s["origin"]).json()["trivia_round_id"]
    _join(client, api_key_header, rid, s["phones"][1])
    begin = _begin(client, api_key_header, rid, s["origin"])
    assert begin.status_code == 200, begin.text
    questions = begin.json()["questions"]
    # The full question list is sent up front, but NONE may carry the answer.
    assert len(questions) == 5
    assert "correct_option" not in str(questions)
    # The poll endpoint's questions must not leak it either, and no answers yet.
    state = _current(client, api_key_header, s["session_id"], s["phones"][1]).json()
    assert "correct_option" not in str(state["questions"])
    assert state["my_answers"] == {}


def test_correct_before_timer_awards_10(client, api_key_header, owner_a_token, fresh_table):
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    rid = _start(client, api_key_header, s["session_id"], s["origin"]).json()["trivia_round_id"]
    _join(client, api_key_header, rid, s["phones"][1])
    _begin(client, api_key_header, rid, s["origin"])

    _qid, correct = _question_key(rid, 0)
    resp = _answer(client, api_key_header, rid, s["origin"], 0, correct)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["is_correct"] is True
    assert data["before_timer"] is True
    assert data["score_awarded"] == 10
    assert data["correct_option"] == correct  # safe to reveal AFTER answering
    # The response also echoes the phone's own pick so the UI can mark both the
    # wrong tile (red) and the right tile (green).
    assert data["selected_option"] == correct
    assert _player_score(s["session_id"], s["origin"]) == 10


def test_wrong_before_timer_awards_3(client, api_key_header, owner_a_token, fresh_table):
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    rid = _start(client, api_key_header, s["session_id"], s["origin"]).json()["trivia_round_id"]
    _join(client, api_key_header, rid, s["phones"][1])
    _begin(client, api_key_header, rid, s["origin"])

    _qid, correct = _question_key(rid, 0)
    resp = _answer(client, api_key_header, rid, s["origin"], 0, _wrong_option(correct))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["is_correct"] is False
    assert data["before_timer"] is True
    assert data["score_awarded"] == 3


def test_after_timer_scores_two_and_one(client, api_key_header, owner_a_token, fresh_table):
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    rid = _start(client, api_key_header, s["session_id"], s["origin"]).json()["trivia_round_id"]
    _join(client, api_key_header, rid, s["phones"][1])
    _begin(client, api_key_header, rid, s["origin"])

    _qid, correct = _question_key(rid, 0)
    # Self-paced: the client reports how long it took. > 20s -> after the timer.
    late_ms = 25000

    # Correct after timer -> 2
    r1 = _answer(client, api_key_header, rid, s["origin"], 0, correct, time_ms=late_ms)
    assert r1.status_code == 200, r1.text
    assert r1.json()["before_timer"] is False
    assert r1.json()["score_awarded"] == 2

    # Wrong after timer -> 1 (the second phone)
    r2 = _answer(client, api_key_header, rid, s["phones"][1], 0, _wrong_option(correct), time_ms=late_ms)
    assert r2.status_code == 200, r2.text
    assert r2.json()["score_awarded"] == 1


def test_answer_twice_rejected(client, api_key_header, owner_a_token, fresh_table):
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    rid = _start(client, api_key_header, s["session_id"], s["origin"]).json()["trivia_round_id"]
    _join(client, api_key_header, rid, s["phones"][1])
    _begin(client, api_key_header, rid, s["origin"])

    _qid, correct = _question_key(rid, 0)
    first = _answer(client, api_key_header, rid, s["origin"], 0, correct)
    assert first.status_code == 200, first.text
    second = _answer(client, api_key_header, rid, s["origin"], 0, correct)
    assert second.status_code == 409
    assert second.json()["detail"] == "already_answered"


def test_member_joining_after_start_cannot_answer(client, api_key_header, owner_a_token, fresh_table):
    # Everyone active at start is auto-enrolled. A phone that joins the session
    # AFTER the round started is a member but not a participant -> can't answer.
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    rid = _start(client, api_key_header, s["session_id"], s["origin"]).json()["trivia_round_id"]
    _begin(client, api_key_header, rid, s["origin"])

    # The late phone must tap the table first (join-presence BOLA); counter 3 is
    # unused (the 2 setup phones used counters 1 and 2).
    late_phone = _fresh_phone()
    _tap(
        client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"],
        s["tag_uid"], 3, late_phone,
    )
    joined = client.post(
        f"/api/patron/sessions/{s['session_id']}/join",
        headers=api_key_header, json={"phone_id": late_phone, "name": "Late"},
    )
    assert joined.status_code == 200, joined.text

    _qid, correct = _question_key(rid, 0)
    resp = _answer(client, api_key_header, rid, late_phone, 0, correct)
    assert resp.status_code == 403


def test_self_paced_any_index_ok_but_out_of_range_rejected(client, api_key_header, owner_a_token, fresh_table):
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    rid = _start(client, api_key_header, s["session_id"], s["origin"]).json()["trivia_round_id"]
    _join(client, api_key_header, rid, s["phones"][1])
    _begin(client, api_key_header, rid, s["origin"])
    # Self-paced: answering a later question (index 2) before index 0 is allowed.
    _qid, correct2 = _question_key(rid, 2)
    ok = _answer(client, api_key_header, rid, s["origin"], 2, correct2)
    assert ok.status_code == 200, ok.text
    # But an out-of-range index is rejected.
    bad = _answer(client, api_key_header, rid, s["origin"], 99, "A")
    assert bad.status_code == 409
    assert bad.json()["detail"] == "bad_question_index"


def test_full_five_question_flow(client, api_key_header, owner_a_token, fresh_table):
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    rid = _start(client, api_key_header, s["session_id"], s["origin"]).json()["trivia_round_id"]
    _join(client, api_key_header, rid, s["phones"][1])
    begin = _begin(client, api_key_header, rid, s["origin"])
    questions = begin.json()["questions"]
    total = len(questions)
    assert total == 5

    # Self-paced: each phone answers all 5 at its own pace (no shared "next").
    for index in range(total):
        _qid, correct = _question_key(rid, index)
        # Origin answers correct (10), second phone answers wrong (3).
        r_origin = _answer(client, api_key_header, rid, s["origin"], index, correct)
        assert r_origin.status_code == 200, r_origin.text
        r_two = _answer(client, api_key_header, rid, s["phones"][1], index, _wrong_option(correct))
        assert r_two.status_code == 200, r_two.text

    # Once everyone has answered all 5, the round auto-completes (no one is cut off).
    state = _current(client, api_key_header, s["session_id"], s["origin"]).json()
    assert state["phase"] == "between_rounds"
    board = state["leaderboard"]
    # Origin: 5x10 = 50; second: 5x3 = 15.
    by_name = {row["name"]: row["score"] for row in board}
    assert by_name["Player 1"] == 50
    assert by_name["Player 2"] == 15
    # Leaderboard is ordered best-first.
    assert board[0]["name"] == "Player 1"
    assert _player_score(s["session_id"], s["origin"]) == 50
    # The explicit finish is now a no-op fallback -- the round already completed.
    assert _finish(client, api_key_header, rid, s["origin"]).status_code == 409


def test_leaderboard_endpoint_and_leave(client, api_key_header, owner_a_token, fresh_table):
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    rid = _start(client, api_key_header, s["session_id"], s["origin"]).json()["trivia_round_id"]
    _join(client, api_key_header, rid, s["phones"][1])
    _begin(client, api_key_header, rid, s["origin"])
    _qid, correct = _question_key(rid, 0)
    _answer(client, api_key_header, rid, s["origin"], 0, correct)

    board = _leaderboard(client, api_key_header, s["session_id"]).json()["leaderboard"]
    assert any(r["score"] == 10 for r in board)

    # Second phone leaves -> marked left_early, sorted after active players.
    left = _leave(client, api_key_header, s["session_id"], s["phones"][1])
    assert left.status_code == 200, left.text
    assert left.json()["left"] is True
    board2 = _leaderboard(client, api_key_header, s["session_id"]).json()["leaderboard"]
    left_rows = [r for r in board2 if r["left_early"]]
    assert len(left_rows) == 1
    assert left_rows[0]["name"] == "Player 2"


def test_leave_then_rejoin_restores_active_and_keeps_score(client, api_key_header, owner_a_token, fresh_table):
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    rid = _start(client, api_key_header, s["session_id"], s["origin"]).json()["trivia_round_id"]
    _join(client, api_key_header, rid, s["phones"][1])
    _begin(client, api_key_header, rid, s["origin"])
    _qid, correct = _question_key(rid, 0)
    _answer(client, api_key_header, rid, s["phones"][1], 0, correct)  # Player 2 scores 10

    # Leave -> left_early, score preserved.
    assert _leave(client, api_key_header, s["session_id"], s["phones"][1]).json()["left"] is True
    p2 = next(r for r in _leaderboard(client, api_key_header, s["session_id"]).json()["leaderboard"]
              if r["name"] == "Player 2")
    assert p2["left_early"] is True and p2["score"] == 10

    # Rejoin -> active again, same score, no longer "Left early".
    rj = _rejoin(client, api_key_header, s["session_id"], s["phones"][1])
    assert rj.status_code == 200, rj.text
    assert rj.json()["rejoined"] is True and rj.json()["score"] == 10
    p2b = next(r for r in _leaderboard(client, api_key_header, s["session_id"]).json()["leaderboard"]
               if r["name"] == "Player 2")
    assert p2b["left_early"] is False and p2b["score"] == 10
    # (Idempotency + non-member BOLA are inherited from leave_session's identical
    # guard: UPDATE ... WHERE left_early=... then _resolve_player -> 403 if absent.)


def test_concurrent_start_is_race_safe(client, api_key_header, owner_a_token, fresh_table):
    # React StrictMode fires the mount effect twice, so the origin issues two
    # concurrent start calls. They race past the "already active?" SELECT, and the
    # one_active_trivia_round_per_session index rejects the loser's INSERT -- which
    # must be caught and returned idempotently, not surfaced as a 500 (a 500 makes
    # the client bail out of Trivia entirely). Three concurrent starts on separate
    # connections must all succeed and return the same round.
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    sid, origin = s["session_id"], s["origin"]

    async def _race():
        pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=3, max_size=5)
        try:
            async def call():
                async with pool.acquire() as conn:
                    return await trivia_service.start_trivia(conn, sid, origin)
            return await asyncio.gather(call(), call(), call(), return_exceptions=True)
        finally:
            await pool.close()

    results = asyncio.run(_race())
    round_ids = set()
    for r in results:
        assert not isinstance(r, Exception), f"concurrent start raised: {r!r}"
        round_ids.add(r["trivia_round_id"])
    assert len(round_ids) == 1, f"expected one shared round, got {round_ids}"


def test_start_is_idempotent_for_origin(client, api_key_header, owner_a_token, fresh_table):
    # The origin re-calling start (StrictMode remount, retry, re-tap) must get the
    # SAME active round back, not an error -- otherwise the client bails out of Trivia.
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    first = _start(client, api_key_header, s["session_id"], s["origin"])
    assert first.status_code == 200, first.text
    second = _start(client, api_key_header, s["session_id"], s["origin"])
    assert second.status_code == 200, second.text
    assert second.json()["trivia_round_id"] == first.json()["trivia_round_id"]
