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

import asyncpg

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


def _question_public(index: int, total: int, q) -> dict:
    """A question shaped for the browser -- correct_option deliberately omitted.

    Self-paced: each phone gets the whole question list up front and walks it at
    its own speed, so the 20s timer is counted client-side per question (from when
    that phone displays it). duration_seconds tells the client how long that is.
    """
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
    }


async def _questions_public(conn, question_ids) -> list[dict]:
    """All of a round's questions in order, each shaped for the browser (no
    correct_option). Sent at the start so every phone can self-pace through them."""
    rows = await conn.fetch(
        """
        SELECT id, question, option_a, option_b, option_c, option_d, category
        FROM trivia_questions WHERE id = ANY($1::uuid[])
        """,
        question_ids,
    )
    by_id = {r["id"]: r for r in rows}
    ordered = [by_id[qid] for qid in question_ids if qid in by_id]
    total = len(ordered)
    return [_question_public(i, total, q) for i, q in enumerate(ordered)]


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


async def start_trivia(conn, session_id: str, phone_id: str) -> dict:
    """Open a Trivia round. Origin-phone only.

    Trivia surfaces automatically between Chooser rounds (the origin's round
    engine decides the type) -- there is no manual gather/tap-to-join. So EVERY
    active session member is auto-enrolled as a participant, and the round opens
    in the brief 'gathering' (get-ready) state before the first question. Picks
    NUM_QUESTIONS questions (respecting adults_only) and broadcasts trivia:gather
    as the "get ready" signal to all phones.

    Raises not_enough_players if fewer than 2 active members remain (the round
    engine then runs a different round type instead).
    """
    session = await _get_session(conn, session_id)
    if not session:
        raise LookupError("session_not_found")
    if session["ended_at"] is not None:
        raise ValueError("session_ended")
    if session["origin_phone_id"] != phone_id:
        raise PermissionError("not_origin_phone")

    # Idempotent for the origin: if a round is already active, return it rather
    # than erroring. The origin legitimately re-calls start on a remount (React
    # StrictMode double-invokes mount effects), a retry, or a re-tap -- a 409
    # there would make the client bail out of the Trivia round entirely. The
    # partial unique index still blocks a genuine second concurrent round.
    existing = await conn.fetchrow(
        """
        SELECT id, status, question_ids FROM trivia_rounds
        WHERE session_id = $1 AND status IN ('gathering', 'in_progress')
        ORDER BY created_at DESC
        LIMIT 1
        """,
        session_id,
    )
    if existing:
        return {
            "trivia_round_id": str(existing["id"]),
            "status": existing["status"],
            "joined_count": await _participant_count(conn, existing["id"]),
            "num_questions": len(existing["question_ids"]),
        }

    # Everyone present plays -- enroll all active members (a phone is bound to
    # its player via game_players.phone_id). Checked before creating the round
    # so a <2 session never leaves an orphan round behind.
    members = await conn.fetch(
        """
        SELECT id, phone_id FROM game_players
        WHERE session_id = $1 AND left_early = FALSE AND phone_id IS NOT NULL
        """,
        session_id,
    )
    if len(members) < MIN_PARTICIPANTS:
        raise ValueError("not_enough_players")

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
    try:
        await conn.execute(
            """
            INSERT INTO trivia_rounds (id, session_id, status, question_ids, adults_only)
            VALUES ($1, $2, 'gathering', $3, $4)
            """,
            trivia_round_id, session_id, question_ids, adults_only,
        )
    except asyncpg.UniqueViolationError:
        # A concurrent start won the race and created the round first (the
        # SELECT above can't see an uncommitted insert, so the one_active_trivia_
        # round_per_session index is the real guard). React StrictMode fires the
        # mount effect twice -> two concurrent start calls; returning the winner's
        # round idempotently here is what stops the loser 500ing and the client
        # bailing out of Trivia entirely.
        existing = await conn.fetchrow(
            """
            SELECT id, status, question_ids FROM trivia_rounds
            WHERE session_id = $1 AND status IN ('gathering', 'in_progress')
            ORDER BY created_at DESC LIMIT 1
            """,
            session_id,
        )
        if existing:
            return {
                "trivia_round_id": str(existing["id"]),
                "status": existing["status"],
                "joined_count": await _participant_count(conn, existing["id"]),
                "num_questions": len(existing["question_ids"]),
            }
        raise
    for m in members:
        await conn.execute(
            """
            INSERT INTO trivia_participants (id, trivia_round_id, phone_id, player_id)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (trivia_round_id, phone_id) DO NOTHING
            """,
            str(uuid.uuid4()), trivia_round_id, m["phone_id"], m["id"],
        )

    joined_count = len(members)
    await rt_publish(
        _channel(session["table_id"]),
        "trivia:gather",
        {
            "session_id": session_id,
            "trivia_round_id": trivia_round_id,
            "joined_count": joined_count,
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


async def begin_trivia(conn, trivia_round_id: str, phone_id: str) -> dict:
    """Origin starts the questions: gather -> in_progress. Returns ALL questions
    so every phone can self-pace through them. Origin-phone only.

    Requires at least MIN_PARTICIPANTS joined phones, else the round must be
    abandoned instead (gamespec: fewer than 2 -> abandoned_at_gather).
    """
    rnd = await _load_round(conn, trivia_round_id)
    if not rnd:
        raise LookupError("trivia_round_not_found")
    if rnd["origin_phone_id"] != phone_id:
        raise PermissionError("not_origin_phone")
    # Idempotent: a re-issued begin (StrictMode / retry) after the round is
    # already in progress just returns the questions again.
    if rnd["status"] == "in_progress":
        return {
            "trivia_round_id": trivia_round_id, "status": "in_progress",
            "questions": await _questions_public(conn, rnd["question_ids"]),
        }
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
        RETURNING id
        """,
        trivia_round_id,
    )
    if not updated:
        # Lost the race to a concurrent begin -- it's in_progress now, return questions.
        return {
            "trivia_round_id": trivia_round_id, "status": "in_progress",
            "questions": await _questions_public(conn, rnd["question_ids"]),
        }

    questions = await _questions_public(conn, rnd["question_ids"])
    await rt_publish(_channel(rnd["table_id"]), "trivia:start",
                     {"trivia_round_id": trivia_round_id, "total": len(questions)})
    return {"trivia_round_id": trivia_round_id, "status": "in_progress", "questions": questions}


async def submit_answer(conn, trivia_round_id: str, phone_id: str,
                        question_index: int, selected_option: str,
                        time_to_answer_ms: int = 0) -> dict:
    """Record a phone's answer to ANY question in the round and award points.

    Self-paced: each phone answers questions at its own speed, so question_index
    is whatever question THAT phone is on (not a shared current_index). The 20s
    timer is measured client-side from when the phone displayed the question and
    sent here as time_to_answer_ms -- the answer CORRECTNESS is still checked
    server-side (the security-relevant part); only the before/after-timer points
    bucket trusts the client's stopwatch, which is fine for a casual game.

    One answer per phone per question (UNIQUE guard -> 409 on retry). Returns the
    correct_option AND the phone's selected_option (safe now -- it has answered),
    so the UI can mark both the wrong pick and the right answer.
    """
    rnd = await _load_round(conn, trivia_round_id)
    if not rnd:
        raise LookupError("trivia_round_not_found")
    if rnd["status"] != "in_progress":
        raise ValueError("round_not_in_progress")
    if not (0 <= question_index < len(rnd["question_ids"])):
        raise ValueError("bad_question_index")

    player = await _resolve_player(conn, rnd["session_id"], phone_id)
    if not player:
        raise PermissionError("not_a_member")
    participant = await conn.fetchval(
        "SELECT 1 FROM trivia_participants WHERE trivia_round_id = $1 AND phone_id = $2",
        trivia_round_id, phone_id,
    )
    if not participant:
        raise PermissionError("not_a_participant")

    question_id = rnd["question_ids"][question_index]
    correct_option = await conn.fetchval(
        "SELECT correct_option FROM trivia_questions WHERE id = $1", question_id
    )

    elapsed_ms = max(0, int(time_to_answer_ms or 0))
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
        str(uuid.uuid4()), trivia_round_id, question_id, question_index, phone_id, player["id"],
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

    # Auto-complete when EVERY active participant has answered EVERY question, so
    # a faster player (incl. the origin) never cuts a slower one off -- each person
    # finishes at their own pace. The origin's explicit finish_trivia stays as a
    # fallback for an AFK player who never finishes.
    await _maybe_complete(conn, rnd)

    # Nudge peers to refresh the live leaderboard.
    await rt_publish(
        _channel(rnd["table_id"]),
        "trivia:answered",
        {"trivia_round_id": trivia_round_id, "question_index": question_index},
    )
    return {
        "index": question_index,
        "is_correct": is_correct,
        "correct_option": correct_option,
        "selected_option": selected_option,
        "score_awarded": score,
        "before_timer": before_timer,
    }


async def _maybe_complete(conn, rnd) -> None:
    """Complete the round once every active participant has answered every
    question. Idempotent via the status guard in the UPDATE."""
    total_q = len(rnd["question_ids"])
    # Active participants = enrolled phones whose player hasn't left.
    active = await conn.fetchval(
        """
        SELECT COUNT(*) FROM trivia_participants tp
        JOIN game_players gp ON gp.id = tp.player_id
        WHERE tp.trivia_round_id = $1 AND gp.left_early = FALSE
        """,
        rnd["id"],
    )
    fully_done = await conn.fetchval(
        """
        SELECT COUNT(*) FROM (
            SELECT phone_id FROM trivia_answers
            WHERE trivia_round_id = $1
            GROUP BY phone_id HAVING COUNT(*) >= $2
        ) t
        """,
        rnd["id"], total_q,
    )
    if active == 0 or fully_done < active:
        return
    done = await conn.fetchrow(
        "UPDATE trivia_rounds SET status = 'complete', ended_at = NOW() "
        "WHERE id = $1 AND status = 'in_progress' RETURNING id",
        rnd["id"],
    )
    if not done:
        return
    total_score = await conn.fetchval(
        "SELECT COALESCE(SUM(score_awarded), 0) FROM trivia_answers WHERE trivia_round_id = $1",
        rnd["id"],
    )
    await _record_analytics_round(conn, rnd, "completed", int(total_score or 0))
    await rt_publish(
        _channel(rnd["table_id"]),
        "trivia:complete",
        {"trivia_round_id": str(rnd["id"]), "leaderboard": await _leaderboard(conn, rnd["session_id"])},
    )


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

    # in_progress -> the whole question list (never any correct_option), plus a
    # map of THIS phone's answers so far. The phone self-paces: it shows the first
    # index it hasn't answered, and on reload resumes from there.
    base["phase"] = "question"
    base["questions"] = await _questions_public(conn, active["question_ids"])
    rows = await conn.fetch(
        """
        SELECT ta.question_index, ta.selected_option, ta.is_correct,
               ta.score_awarded, ta.before_timer, tq.correct_option
        FROM trivia_answers ta
        JOIN trivia_questions tq ON tq.id = ta.question_id
        WHERE ta.trivia_round_id = $1 AND ta.phone_id = $2
        """,
        active["id"], phone_id,
    )
    base["my_answers"] = {
        str(r["question_index"]): {
            "index": r["question_index"],
            "selected_option": r["selected_option"],
            "is_correct": r["is_correct"],
            "score_awarded": r["score_awarded"],
            "before_timer": r["before_timer"],
            "correct_option": r["correct_option"],
        }
        for r in rows
    }
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
