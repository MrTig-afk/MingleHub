"""Tests for Trivia AFK auto-complete timeout (spec: .pipeline/spec.md).

Follows test_trivia.py's pattern exactly: _cleanup_test_tags autouse fixture,
_fresh_phone(), _tap(), _set_name(), _setup_session() helpers, TestClient (HTTP)
for all assertions, asyncpg.connect for direct DB helpers.

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
    }


# --- trivia HTTP helpers ---

def _start(client, h, session_id, phone):
    return client.post(f"/api/patron/sessions/{session_id}/trivia/start", headers=h, json={"phone_id": phone})


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


def _current(client, h, session_id, phone):
    return client.get(f"/api/patron/sessions/{session_id}/trivia/current", headers=h, params={"phone_id": phone})


# --- DB helpers ---

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


def _age_started_at(trivia_round_id, seconds_back):
    """Wind trivia_rounds.started_at back by N seconds for timeout testing."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute(
                "UPDATE trivia_rounds SET started_at = NOW() - make_interval(secs => $1) WHERE id = $2",
                float(seconds_back), uuid.UUID(trivia_round_id),
            )
        finally:
            await conn.close()
    asyncio.run(_q())


def _trivia_round_status(trivia_round_id):
    """Read the current status of a trivia round from the DB."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await conn.fetchval(
                "SELECT status FROM trivia_rounds WHERE id = $1",
                uuid.UUID(trivia_round_id),
            )
        finally:
            await conn.close()
    return asyncio.run(_q())


def _analytics_round_count(session_id, round_type='trivia'):
    """Count how many analytics `rounds` rows exist for this session+type."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM rounds WHERE session_id = $1 AND round_type = $2",
                uuid.UUID(session_id), round_type,
            )
        finally:
            await conn.close()
    return asyncio.run(_q())


# --- tests ---

def test_afk_round_auto_completes_after_timeout(client, api_key_header, owner_a_token, fresh_table):
    """AFK timeout: one player answers all 5, the other answers nothing.
    After winding started_at back past the 180s cap, the next poll must
    force-complete the round and return phase='between_rounds'.
    Only the answers that were submitted contribute to scoring."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    session_id = s["session_id"]
    origin = s["origin"]
    phone2 = s["phones"][1]

    start_resp = _start(client, api_key_header, session_id, origin)
    assert start_resp.status_code == 200, start_resp.text
    rid = start_resp.json()["trivia_round_id"]

    begin_resp = _begin(client, api_key_header, rid, origin)
    assert begin_resp.status_code == 200, begin_resp.text

    # Origin answers all 5 questions correctly (5 x 10 = 50).
    for i in range(5):
        _qid, correct = _question_key(rid, i)
        r = _answer(client, api_key_header, rid, origin, i, correct)
        assert r.status_code == 200, r.text

    # Phone 2 answers nothing (AFK). Round should still be in_progress since
    # not everyone has answered.
    state_before = _current(client, api_key_header, session_id, origin).json()
    assert state_before["phase"] == "question", (
        f"Expected 'question' before timeout, got {state_before['phase']!r}"
    )

    # Wind started_at back 200s — well past the 180s cap.
    _age_started_at(rid, 200)

    # Poll: the AFK check must fire and force-complete the round.
    state_after = _current(client, api_key_header, session_id, origin).json()
    assert state_after["phase"] == "between_rounds", (
        f"Expected 'between_rounds' after timeout poll, got {state_after['phase']!r}"
    )

    # DB: round is complete.
    assert _trivia_round_status(rid) == "complete"

    # Scores: only origin's submitted answers count; phone 2 gets 0.
    assert _player_score(session_id, origin) == 50
    assert _player_score(session_id, phone2) == 0

    # Analytics: exactly one rounds row for this trivia round.
    assert _analytics_round_count(session_id) == 1


def test_round_not_past_cap_stays_in_progress(client, api_key_header, owner_a_token, fresh_table):
    """No false positive: a round in_progress with unanswered players but
    started_at well within the 180s cap must NOT be auto-completed by a poll."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    session_id = s["session_id"]
    origin = s["origin"]

    start_resp = _start(client, api_key_header, session_id, origin)
    assert start_resp.status_code == 200, start_resp.text
    rid = start_resp.json()["trivia_round_id"]

    _begin(client, api_key_header, rid, origin)

    # Origin answers only question 0; phone 2 answers nothing.
    _qid, correct = _question_key(rid, 0)
    r = _answer(client, api_key_header, rid, origin, 0, correct)
    assert r.status_code == 200, r.text

    # Do NOT age started_at — the round is recent (within the 180s cap).

    state = _current(client, api_key_header, session_id, origin).json()
    assert state["phase"] == "question", (
        f"Expected 'question' (no false positive), got {state['phase']!r}"
    )
    assert _trivia_round_status(rid) == "in_progress"


def test_all_answered_still_completes_normally(client, api_key_header, owner_a_token, fresh_table):
    """Regression: the extract-method refactor into _finalize_trivia_round must
    not break the normal _maybe_complete path. When every active player answers
    all questions, the round completes without any timeout."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    session_id = s["session_id"]
    origin = s["origin"]
    phone2 = s["phones"][1]

    start_resp = _start(client, api_key_header, session_id, origin)
    assert start_resp.status_code == 200, start_resp.text
    rid = start_resp.json()["trivia_round_id"]

    _begin(client, api_key_header, rid, origin)

    # Both phones answer all 5 questions: origin correct (10 each), phone 2 wrong (3 each).
    for i in range(5):
        _qid, correct = _question_key(rid, i)
        r_origin = _answer(client, api_key_header, rid, origin, i, correct)
        assert r_origin.status_code == 200, r_origin.text
        r2 = _answer(client, api_key_header, rid, phone2, i, _wrong_option(correct))
        assert r2.status_code == 200, r2.text

    # After all answered, poll must show between_rounds (completed via _maybe_complete).
    state = _current(client, api_key_header, session_id, origin).json()
    assert state["phase"] == "between_rounds", (
        f"Expected 'between_rounds' after all answered, got {state['phase']!r}"
    )

    # Scores: origin 5x10=50, phone2 5x3=15.
    assert _player_score(session_id, origin) == 50
    assert _player_score(session_id, phone2) == 15

    # DB: round is complete.
    assert _trivia_round_status(rid) == "complete"

    # Analytics: exactly one rounds row.
    assert _analytics_round_count(session_id) == 1


def test_afk_timeout_is_idempotent(client, api_key_header, owner_a_token, fresh_table):
    """Two polls past the cap must not double-score or write two analytics rows.
    The atomic UPDATE ... WHERE status='in_progress' RETURNING id is the
    serialization point: only one poll wins, the other is a no-op."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=3)
    session_id = s["session_id"]
    origin = s["origin"]
    phone2 = s["phones"][1]
    phone3 = s["phones"][2]

    start_resp = _start(client, api_key_header, session_id, origin)
    assert start_resp.status_code == 200, start_resp.text
    rid = start_resp.json()["trivia_round_id"]

    _begin(client, api_key_header, rid, origin)

    # Origin answers all 5 correctly (score = 50). Phones 2 and 3 answer nothing.
    for i in range(5):
        _qid, correct = _question_key(rid, i)
        r = _answer(client, api_key_header, rid, origin, i, correct)
        assert r.status_code == 200, r.text

    # Age past the cap.
    _age_started_at(rid, 200)

    # First poll: phone 2 — should trigger finalization.
    state2 = _current(client, api_key_header, session_id, phone2).json()
    assert state2["phase"] == "between_rounds", (
        f"First poll expected 'between_rounds', got {state2['phase']!r}"
    )

    # Second poll: phone 3 — round already complete; must not re-finalize.
    state3 = _current(client, api_key_header, session_id, phone3).json()
    assert state3["phase"] == "between_rounds", (
        f"Second poll expected 'between_rounds', got {state3['phase']!r}"
    )

    # Exactly one analytics row — not two.
    assert _analytics_round_count(session_id) == 1, (
        "Double finalization: expected exactly 1 analytics row, got more"
    )

    # Origin's score is 50, not 100 (not double-scored).
    assert _player_score(session_id, origin) == 50, (
        "Double-scoring detected: origin score should be 50, not 100"
    )


def test_finish_trivia_still_works_via_shared_helper(client, api_key_header, owner_a_token, fresh_table):
    """finish_trivia (the explicit origin-driven finish path) completes the round
    via the shared _finalize_trivia_round helper. With an AFK phone 2, _maybe_complete
    never fires (fully_done < active), so finish_trivia is the only completion path.
    Regression on the refactored helper."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    session_id = s["session_id"]
    origin = s["origin"]

    start_resp = _start(client, api_key_header, session_id, origin)
    assert start_resp.status_code == 200, start_resp.text
    rid = start_resp.json()["trivia_round_id"]

    _begin(client, api_key_header, rid, origin)

    # Origin answers all 5 correctly; phone 2 answers nothing (AFK).
    # _maybe_complete is gated by fully_done < active (1 < 2), so the round
    # stays in_progress after the last submit_answer call.
    for i in range(5):
        _qid, correct = _question_key(rid, i)
        r = _answer(client, api_key_header, rid, origin, i, correct)
        assert r.status_code == 200, r.text

    # Round is still in_progress — _maybe_complete did not fire.
    assert _trivia_round_status(rid) == "in_progress"

    # Explicit finish by the origin must succeed via _finalize_trivia_round.
    finish_resp = _finish(client, api_key_header, rid, origin)
    assert finish_resp.status_code == 200, (
        f"Expected 200 from finish_trivia, got {finish_resp.status_code}: {finish_resp.text}"
    )
    data = finish_resp.json()
    assert data["status"] == "complete"
    assert "leaderboard" in data

    # DB confirms the round is complete.
    assert _trivia_round_status(rid) == "complete"

    # Analytics: exactly one row — _finalize_trivia_round called once.
    assert _analytics_round_count(session_id) == 1

    # Calling finish again must return 409 (already finalized, atomic guard).
    second_finish = _finish(client, api_key_header, rid, origin)
    assert second_finish.status_code == 409
    assert second_finish.json()["detail"] == "round_not_in_progress"
