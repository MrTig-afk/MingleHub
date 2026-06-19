"""Trivia round logic (gamespec.md: Round Type 2 -- Trivia).

Pure async functions taking `conn` as first arg -- same pattern as
chooser_service.py / round_service.py. Multi-phone: every player answers on
their own device, so points are individually discernible (Scoring ->
Discernibility Principle) -- contrast the Chooser round, which awards none.

Lifecycle (trivia_rounds.status):
    gathering -> in_progress -> complete
    gathering -> abandoned_at_gather   (fewer than 2 phones joined)

The session-origin phone drives the flow: it starts the gather, begins the
questions, advances between them, and finishes. Each question's correct_option
is checked SERVER-SIDE only and is never sent to a browser before that phone
has answered (security.md / coding-practices MingleHub Rules).
"""
import uuid

from api.services.realtime_service import publish as rt_publish

NUM_QUESTIONS = 5
QUESTION_TIMER_SECONDS = 20
MIN_PARTICIPANTS = 2

# gamespec Scoring System -> Trivia. Note the table is exactly as specified:
# a wrong-but-fast answer (3) outscores a correct-but-late one (2) by design.
_SCORE = {
    (True, True): 10,    # correct, before timer
    (False, True): 3,    # wrong,   before timer
    (True, False): 2,    # correct, after timer
    (False, False): 1,   # wrong,   after timer
}


def _channel(table_id) -> str:
    return f"table:{table_id}"


def _question_public(index: int, total: int, q, started_at) -> dict:
    """A question shaped for the browser -- correct_option deliberately omitted."""
    return {
        "index": index,
        "total": total,
        "question": q["question"],
        "options": {
            "A": q["option_a"],
            "B": q["option_b"],
            "C": q["option_c"],
            "D": q["option_d"],
        },
        "category": q["category"],
        "duration_seconds": QUESTION_TIMER_SECONDS,
        "started_at": started_at.isoformat() if started_at else None,
    }


async def _get_session(conn, session_id: str):
    return await conn.fetchrow(
        """
        SELECT id, table_id, ended_at, origin_phone_id, adults_only,
               current_round_number
        FROM game_sessions WHERE id = $1
        """,
        session_id,
    )


async def _resolve_player(conn, session_id, phone_id: str):
    """The game_players row this phone owns in the session, or None if the
    phone is not a member (BOLA: non-members can't join/answer/leave)."""
    return await conn.fetchrow(
        "SELECT id, name, left_early FROM game_players WHERE session_id = $1 AND phone_id = $2",
        session_id, phone_id,
    )


async def _participant_count(conn, trivia_round_id) -> int:
    return await conn.fetchval(
        "SELECT COUNT(*) FROM trivia_participants WHERE trivia_round_id = $1",
        trivia_round_id,
    )


async def _answered_count(conn, trivia_round_id, question_index: int) -> int:
    return await conn.fetchval(
        "SELECT COUNT(*) FROM trivia_answers WHERE trivia_round_id = $1 AND question_index = $2",
        trivia_round_id, question_index,
    )


async def start_trivia(conn, session_id: str, phone_id: str) -> dict:
    """Open the Trivia gather phase. Origin-phone only.

    Picks NUM_QUESTIONS questions (respecting the session's adults_only flag),
    creates the trivia_round in 'gathering', and auto-joins the origin phone
    as the first participant. Broadcasts trivia:gather.
    """
    session = await _get_session(conn, session_id)
    if not session:
        raise LookupError("session_not_found")
    if session["ended_at"] is not None:
        raise ValueError("session_ended")
    if session["origin_phone_id"] != phone_id:
        raise PermissionError("not_origin_phone")

    # One active trivia round per session (also enforced by a partial unique
    # index) -- surface a clean conflict rather than a DB error.
    existing = await conn.fetchval(
        """
        SELECT id FROM trivia_rounds
        WHERE session_id = $1 AND status IN ('gathering', 'in_progress')
        """,
        session_id,
    )
    if existing:
        raise ValueError("trivia_already_active")

    adults_only = session["adults_only"]
    question_rows = await conn.fetch(
        """
        SELECT id FROM trivia_questions
        WHERE (NOT is_adults_only OR $1)
        ORDER BY random()
        LIMIT $2
        """,
        adults_only, NUM_QUESTIONS,
    )
    if not question_rows:
        raise ValueError("no_questions_available")
    question_ids = [r["id"] for r in question_rows]

    trivia_round_id = str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO trivia_rounds (id, session_id, status, question_ids, adults_only)
        VALUES ($1, $2, 'gathering', $3, $4)
        """,
        trivia_round_id, session_id, question_ids, adults_only,
    )

    # Auto-join the origin phone if it owns a player row (it normally does --
    # it was in the lobby). gamespec: already-joined phones join automatically.
    origin_player = await _resolve_player(conn, session_id, phone_id)
    if origin_player and not origin_player["left_early"]:
        await conn.execute(
            """
            INSERT INTO trivia_participants (id, trivia_round_id, phone_id, player_id)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (trivia_round_id, phone_id) DO NOTHING
            """,
            str(uuid.uuid4()), trivia_round_id, phone_id, origin_player["id"],
        )

    joined_count = await _participant_count(conn, trivia_round_id)
    await rt_publish(
        _channel(session["table_id"]),
        "trivia:gather",
        {
            "session_id": session_id,
            "trivia_round_id": trivia_round_id,
            "joined_count": joined_count,
            "gather_seconds": 60,
        },
    )
    return {
        "trivia_round_id": trivia_round_id,
        "status": "gathering",
        "joined_count": joined_count,
        "num_questions": len(question_ids),
    }


async def _load_round(conn, trivia_round_id: str):
    return await conn.fetchrow(
        """
        SELECT tr.id, tr.session_id, tr.status, tr.question_ids, tr.adults_only,
               tr.category, tr.current_index, tr.current_question_started_at,
               gs.table_id, gs.origin_phone_id, gs.ended_at
        FROM trivia_rounds tr
        JOIN game_sessions gs ON gs.id = tr.session_id
        WHERE tr.id = $1
        """,
        trivia_round_id,
    )


async def join_trivia(conn, trivia_round_id: str, phone_id: str) -> dict:
    """A session-member phone joins the gather. Idempotent (re-tap safe).

    BOLA: the phone must already own a player row in this round's session.
    """
    rnd = await _load_round(conn, trivia_round_id)
    if not rnd:
        raise LookupError("trivia_round_not_found")
    if rnd["status"] != "gathering":
        raise ValueError("gather_closed")

    player = await _resolve_player(conn, rnd["session_id"], phone_id)
    if not player:
        raise PermissionError("not_a_member")
    if player["left_early"]:
        raise ValueError("player_left")

    await conn.execute(
        """
        INSERT INTO trivia_participants (id, trivia_round_id, phone_id, player_id)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (trivia_round_id, phone_id) DO NOTHING
        """,
        str(uuid.uuid4()), trivia_round_id, phone_id, player["id"],
    )
    joined_count = await _participant_count(conn, trivia_round_id)
    await rt_publish(
        _channel(rnd["table_id"]),
        "trivia:participant_joined",
        {"trivia_round_id": trivia_round_id, "joined_count": joined_count},
    )
    return {"trivia_round_id": trivia_round_id, "joined_count": joined_count, "player_id": str(player["id"])}


async def _current_question_row(conn, rnd) -> dict:
    qid = rnd["question_ids"][rnd["current_index"]]
    return await conn.fetchrow(
        """
        SELECT id, question, option_a, option_b, option_c, option_d, category
        FROM trivia_questions WHERE id = $1
        """,
        qid,
    )


async def begin_trivia(conn, trivia_round_id: str, phone_id: str) -> dict:
    """Origin taps "Start Trivia": gather -> in_progress, open question 0.

    Requires at least MIN_PARTICIPANTS joined phones, else the round must be
    abandoned instead (gamespec: fewer than 2 -> abandoned_at_gather).
    """
    rnd = await _load_round(conn, trivia_round_id)
    if not rnd:
        raise LookupError("trivia_round_not_found")
    if rnd["origin_phone_id"] != phone_id:
        raise PermissionError("not_origin_phone")
    if rnd["status"] != "gathering":
        raise ValueError("not_gathering")

    joined_count = await _participant_count(conn, trivia_round_id)
    if joined_count < MIN_PARTICIPANTS:
        raise ValueError("not_enough_players")

    updated = await conn.fetchrow(
        """
        UPDATE trivia_rounds
        SET status = 'in_progress', current_index = 0,
            current_question_started_at = NOW(), started_at = NOW()
        WHERE id = $1 AND status = 'gathering'
        RETURNING current_question_started_at
        """,
        trivia_round_id,
    )
    if not updated:
        raise ValueError("not_gathering")

    rnd = await _load_round(conn, trivia_round_id)
    q = await _current_question_row(conn, rnd)
    public = _question_public(0, len(rnd["question_ids"]), q, rnd["current_question_started_at"])
    await rt_publish(_channel(rnd["table_id"]), "trivia:question", public)
    return {"trivia_round_id": trivia_round_id, "status": "in_progress", "question": public}


async def submit_answer(conn, trivia_round_id: str, phone_id: str,
                        question_index: int, selected_option: str) -> dict:
    """Record a phone's answer and award points. SERVER-SIDE check only.

    Returns the correct_option in the response (it is now safe -- the phone has
    answered). One answer per phone per question (UNIQUE guard -> 409 on retry).
    """
    rnd = await _load_round(conn, trivia_round_id)
    if not rnd:
        raise LookupError("trivia_round_not_found")
    if rnd["status"] != "in_progress":
        raise ValueError("round_not_in_progress")
    if question_index != rnd["current_index"]:
        raise ValueError("stale_question")

    player = await _resolve_player(conn, rnd["session_id"], phone_id)
    if not player:
        raise PermissionError("not_a_member")
    participant = await conn.fetchval(
        "SELECT 1 FROM trivia_participants WHERE trivia_round_id = $1 AND phone_id = $2",
        trivia_round_id, phone_id,
    )
    if not participant:
        raise PermissionError("not_a_participant")

    q = await _current_question_row(conn, rnd)
    correct_option = await conn.fetchval(
        "SELECT correct_option FROM trivia_questions WHERE id = $1", q["id"]
    )

    # Elapsed since this question went live -- decides before/after the 20s timer.
    elapsed_ms = await conn.fetchval(
        "SELECT (EXTRACT(EPOCH FROM (NOW() - current_question_started_at)) * 1000)::bigint "
        "FROM trivia_rounds WHERE id = $1",
        trivia_round_id,
    )
    elapsed_ms = max(0, int(elapsed_ms or 0))
    before_timer = elapsed_ms <= QUESTION_TIMER_SECONDS * 1000
    is_correct = selected_option == correct_option
    score = _SCORE[(is_correct, before_timer)]

    # Insert-or-nothing: the UNIQUE(round, question_index, phone) makes a second
    # submit a no-op, so a phone can't double-score one question.
    inserted = await conn.fetchrow(
        """
        INSERT INTO trivia_answers
            (id, trivia_round_id, question_id, question_index, phone_id, player_id,
             selected_option, is_correct, before_timer, time_to_answer_ms, score_awarded)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        ON CONFLICT (trivia_round_id, question_index, phone_id) DO NOTHING
        RETURNING id
        """,
        str(uuid.uuid4()), trivia_round_id, q["id"], question_index, phone_id, player["id"],
        selected_option, is_correct, before_timer, elapsed_ms, score,
    )
    if not inserted:
        raise ValueError("already_answered")

    # Per-phone scoring + session tallies. The player owns the points because
    # they answered on their own device.
    await conn.execute(
        "UPDATE game_players SET score = score + $1 WHERE id = $2",
        score, player["id"],
    )
    await conn.execute(
        """
        UPDATE game_sessions
        SET total_score = total_score + $1,
            trivia_correct = trivia_correct + $2,
            trivia_wrong = trivia_wrong + $3
        WHERE id = $4
        """,
        score, 1 if is_correct else 0, 0 if is_correct else 1, rnd["session_id"],
    )

    answered_count = await _answered_count(conn, trivia_round_id, question_index)
    await rt_publish(
        _channel(rnd["table_id"]),
        "trivia:answered",
        {"trivia_round_id": trivia_round_id, "question_index": question_index,
         "answered_count": answered_count},
    )
    return {
        "is_correct": is_correct,
        "correct_option": correct_option,
        "score_awarded": score,
        "before_timer": before_timer,
    }


async def next_question(conn, trivia_round_id: str, phone_id: str) -> dict:
    """Origin advances to the next question. Origin-phone only."""
    rnd = await _load_round(conn, trivia_round_id)
    if not rnd:
        raise LookupError("trivia_round_not_found")
    if rnd["origin_phone_id"] != phone_id:
        raise PermissionError("not_origin_phone")
    if rnd["status"] != "in_progress":
        raise ValueError("round_not_in_progress")

    total = len(rnd["question_ids"])
    if rnd["current_index"] >= total - 1:
        raise ValueError("no_more_questions")

    new_index = rnd["current_index"] + 1
    await conn.execute(
        """
        UPDATE trivia_rounds
        SET current_index = $1, current_question_started_at = NOW()
        WHERE id = $2
        """,
        new_index, trivia_round_id,
    )
    rnd = await _load_round(conn, trivia_round_id)
    q = await _current_question_row(conn, rnd)
    public = _question_public(new_index, total, q, rnd["current_question_started_at"])
    await rt_publish(_channel(rnd["table_id"]), "trivia:question", public)
    return {"trivia_round_id": trivia_round_id, "question": public}


async def _leaderboard(conn, session_id) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT name, score, left_early
        FROM game_players
        WHERE session_id = $1
        ORDER BY left_early ASC, score DESC, name ASC
        """,
        session_id,
    )
    return [
        {"name": r["name"], "score": r["score"], "left_early": r["left_early"]}
        for r in rows
    ]


async def session_leaderboard(conn, session_id: str) -> dict:
    """Public per-player leaderboard for the between-rounds screen."""
    return {"session_id": session_id, "leaderboard": await _leaderboard(conn, session_id)}


async def finish_trivia(conn, trivia_round_id: str, phone_id: str) -> dict:
    """Origin finishes after the last question: in_progress -> complete.

    Writes one analytics `rounds` row for the trivia round and returns the
    session leaderboard. Broadcasts trivia:complete.
    """
    rnd = await _load_round(conn, trivia_round_id)
    if not rnd:
        raise LookupError("trivia_round_not_found")
    if rnd["origin_phone_id"] != phone_id:
        raise PermissionError("not_origin_phone")
    if rnd["status"] != "in_progress":
        raise ValueError("round_not_in_progress")

    updated = await conn.fetchrow(
        """
        UPDATE trivia_rounds SET status = 'complete', ended_at = NOW()
        WHERE id = $1 AND status = 'in_progress'
        RETURNING id
        """,
        trivia_round_id,
    )
    if not updated:
        raise ValueError("round_not_in_progress")

    total_score = await conn.fetchval(
        "SELECT COALESCE(SUM(score_awarded), 0) FROM trivia_answers WHERE trivia_round_id = $1",
        trivia_round_id,
    )
    await _record_analytics_round(conn, rnd, "completed", int(total_score or 0))

    leaderboard = await _leaderboard(conn, rnd["session_id"])
    await rt_publish(
        _channel(rnd["table_id"]),
        "trivia:complete",
        {"trivia_round_id": trivia_round_id, "leaderboard": leaderboard},
    )
    return {"trivia_round_id": trivia_round_id, "status": "complete", "leaderboard": leaderboard}


async def abandon_trivia(conn, trivia_round_id: str, phone_id: str) -> dict:
    """Origin abandons during gather (fewer than 2 joined). Origin-phone only."""
    rnd = await _load_round(conn, trivia_round_id)
    if not rnd:
        raise LookupError("trivia_round_not_found")
    if rnd["origin_phone_id"] != phone_id:
        raise PermissionError("not_origin_phone")
    if rnd["status"] != "gathering":
        raise ValueError("not_gathering")

    await conn.execute(
        """
        UPDATE trivia_rounds SET status = 'abandoned_at_gather', ended_at = NOW()
        WHERE id = $1 AND status = 'gathering'
        """,
        trivia_round_id,
    )
    await _record_analytics_round(conn, rnd, "abandoned_at_gather", 0)
    await rt_publish(
        _channel(rnd["table_id"]),
        "trivia:abandoned",
        {"trivia_round_id": trivia_round_id},
    )
    return {"trivia_round_id": trivia_round_id, "status": "abandoned_at_gather"}


async def _record_analytics_round(conn, rnd, result: str, score_awarded: int) -> None:
    """One `rounds` row per trivia round for session analytics. Per-phone /
    per-question detail lives in trivia_answers; this is the round-level record
    (gamespec Analytics -> per round)."""
    round_number = await conn.fetchval(
        """
        UPDATE game_sessions
        SET current_round_number = current_round_number + 1,
            total_rounds = total_rounds + 1
        WHERE id = $1
        RETURNING current_round_number
        """,
        rnd["session_id"],
    )
    await conn.execute(
        """
        INSERT INTO rounds (id, session_id, round_number, round_type,
                            trivia_question_id, trivia_category, result, score_awarded)
        VALUES ($1, $2, $3, 'trivia', $4, $5, $6, $7)
        """,
        str(uuid.uuid4()), rnd["session_id"], round_number,
        rnd["question_ids"][0] if rnd["question_ids"] else None,
        rnd["category"], result, score_awarded,
    )


async def get_current_state(conn, session_id: str, phone_id: str) -> dict | None:
    """Poll fallback for joined phones (realtime accelerates, this is the source
    of truth). Returns the phone's current Trivia view: gather, live question
    (no correct_option), or between-rounds leaderboard.
    """
    session = await conn.fetchrow(
        "SELECT id, ended_at FROM game_sessions WHERE id = $1", session_id
    )
    if not session:
        return None

    player = await _resolve_player(conn, session_id, phone_id)
    base = {
        "session_id": session_id,
        "is_member": player is not None,
        "left_early": bool(player["left_early"]) if player else False,
        "leaderboard": await _leaderboard(conn, session_id),
    }

    active = await conn.fetchrow(
        """
        SELECT id, status, question_ids, current_index, current_question_started_at
        FROM trivia_rounds
        WHERE session_id = $1 AND status IN ('gathering', 'in_progress')
        ORDER BY created_at DESC
        LIMIT 1
        """,
        session_id,
    )
    if not active:
        base["phase"] = "between_rounds"
        return base

    trivia_round_id = str(active["id"])
    is_participant = bool(await conn.fetchval(
        "SELECT 1 FROM trivia_participants WHERE trivia_round_id = $1 AND phone_id = $2",
        active["id"], phone_id,
    ))
    base.update({
        "trivia_round_id": trivia_round_id,
        "is_participant": is_participant,
        "joined_count": await _participant_count(conn, active["id"]),
    })

    if active["status"] == "gathering":
        base["phase"] = "gather"
        return base

    # in_progress -> the live question, never including correct_option, plus
    # this phone's own answer if it has already answered.
    rnd = await _load_round(conn, trivia_round_id)
    q = await _current_question_row(conn, rnd)
    base["phase"] = "question"
    base["question"] = _question_public(
        active["current_index"], len(active["question_ids"]), q,
        active["current_question_started_at"],
    )
    base["answered_count"] = await _answered_count(conn, active["id"], active["current_index"])
    my = await conn.fetchrow(
        """
        SELECT ta.selected_option, ta.is_correct, ta.score_awarded, ta.before_timer,
               tq.correct_option
        FROM trivia_answers ta
        JOIN trivia_questions tq ON tq.id = ta.question_id
        WHERE ta.trivia_round_id = $1 AND ta.question_index = $2 AND ta.phone_id = $3
        """,
        active["id"], active["current_index"], phone_id,
    )
    base["my_answer"] = (
        {
            "selected_option": my["selected_option"],
            "is_correct": my["is_correct"],
            "score_awarded": my["score_awarded"],
            "before_timer": my["before_timer"],
            "correct_option": my["correct_option"],
        }
        if my else None
    )
    return base


async def leave_session(conn, session_id: str, phone_id: str) -> dict:
    """Basic leave-mid-session: mark the player left_early (gamespec: Leaving
    Mid-Session). Partial score is preserved; the session continues for others.
    """
    session = await conn.fetchrow(
        "SELECT table_id FROM game_sessions WHERE id = $1 AND ended_at IS NULL", session_id
    )
    if not session:
        raise LookupError("session_not_found_or_ended")

    row = await conn.fetchrow(
        """
        UPDATE game_players SET left_early = TRUE, left_at = NOW()
        WHERE session_id = $1 AND phone_id = $2 AND left_early = FALSE
        RETURNING id, name, score
        """,
        session_id, phone_id,
    )
    if not row:
        # Already left, or not a member -- treat as idempotent success if the
        # phone is a member, else a clean not-found.
        existing = await _resolve_player(conn, session_id, phone_id)
        if not existing:
            raise PermissionError("not_a_member")
        return {"left": True, "name": existing["name"], "score": existing["score"]}

    await rt_publish(
        _channel(session["table_id"]),
        "trivia:participant_joined",  # reuse: nudges peers to re-poll the leaderboard
        {"session_id": session_id, "left_phone": True},
    )
    return {"left": True, "name": row["name"], "score": row["score"]}
