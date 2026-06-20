"""End-game and recap logic.

Pure async functions taking `conn` as first arg -- same pattern as
roulette_service.py / chooser_service.py.

end_game:       Origin-only. Atomically marks the session ended and broadcasts
                game_ended to all phones so they transition to the Recap screen.
get_recap:      Session-scoped read. Aggregates stats for the Recap screen.
                No secrets -- same access level as /leaderboard.
idle_end_session: Called lazily by lobby_service when a session's last_activity_at
                is further in the past than retap_interval_minutes. No broadcast
                (nobody is listening at that point).
"""
from api.services.realtime_service import publish as rt_publish


def _channel(table_id) -> str:
    return f"table:{table_id}"


async def end_game(conn, session_id: str, phone_id: str) -> dict:
    """End the game for everyone. Origin-phone only.

    Atomic: UPDATE ... WHERE ended_at IS NULL means only the first concurrent
    call succeeds. A racing second call finds the row already updated and
    returns idempotent success rather than raising.
    """
    session = await conn.fetchrow(
        "SELECT id, table_id, ended_at, origin_phone_id FROM game_sessions WHERE id = $1",
        session_id,
    )
    if not session:
        raise LookupError("session_not_found")
    if session["ended_at"] is not None:
        raise ValueError("session_already_ended")
    # BOLA: only the phone that opened the session may end it
    if session["origin_phone_id"] != phone_id:
        raise PermissionError("not_origin_phone")

    table_id = str(session["table_id"])

    # Atomic: only the first caller flips ended_at from NULL
    updated = await conn.fetchrow(
        """
        UPDATE game_sessions SET ended_at = NOW(), end_reason = 'manual'
        WHERE id = $1 AND ended_at IS NULL
        RETURNING id
        """,
        session_id,
    )
    if not updated:
        # Race: another call beat us -- return idempotent success
        return {"ended": True, "session_id": session_id}

    await rt_publish(_channel(table_id), "game_ended", {"session_id": session_id})
    return {"ended": True, "session_id": session_id}


async def get_recap(conn, session_id: str) -> dict:
    """Aggregated recap stats for an ended session.

    Session-scoped: no venue secrets or cross-session data. Same access
    level as the existing /leaderboard endpoint.
    """
    session = await conn.fetchrow(
        """
        SELECT gs.id, gs.total_score, gs.cards_completed, gs.cards_skipped,
               gs.trivia_correct, gs.trivia_wrong, gs.ended_at, gs.end_reason,
               v.name AS venue_name
        FROM game_sessions gs
        JOIN venues v ON v.id = gs.venue_id
        WHERE gs.id = $1
        """,
        session_id,
    )
    if not session:
        raise LookupError("session_not_found")
    if session["ended_at"] is None:
        raise ValueError("session_not_ended")

    # Leaderboard: includes times_selected for "Most Picked Player" stat
    player_rows = await conn.fetch(
        """
        SELECT name, score, left_early, times_selected
        FROM game_players WHERE session_id = $1
        ORDER BY left_early ASC, score DESC, name ASC
        """,
        session_id,
    )
    leaderboard = [
        {
            "name": r["name"],
            "score": r["score"],
            "left_early": r["left_early"],
            "times_selected": r["times_selected"],
        }
        for r in player_rows
    ]

    # Most Picked Player: the player with the highest times_selected
    most_picked_player = None
    if leaderboard:
        best = max(leaderboard, key=lambda r: r["times_selected"])
        if best["times_selected"] > 0:
            most_picked_player = {
                "name": best["name"],
                "times_selected": best["times_selected"],
            }

    # Roulette rounds completed
    roulette_rounds = int(await conn.fetchval(
        """
        SELECT COUNT(*) FROM rounds
        WHERE session_id = $1 AND round_type = 'roulette' AND result = 'completed'
        """,
        session_id,
    ))

    # Trivia accuracy
    trivia_correct = session["trivia_correct"] or 0
    trivia_wrong = session["trivia_wrong"] or 0
    trivia_total = trivia_correct + trivia_wrong
    if trivia_total == 0:
        trivia_accuracy = None
    else:
        trivia_accuracy = trivia_correct / trivia_total

    venue_name = session["venue_name"]
    total_score = session["total_score"] or 0
    share_text = f"We scored {total_score} points at {venue_name} \U0001f37a"

    return {
        "session_id": session_id,
        "venue_name": venue_name,
        "leaderboard": leaderboard,
        "most_picked_player": most_picked_player,
        "cards_played": (session["cards_completed"] or 0) + (session["cards_skipped"] or 0),
        "trivia_accuracy": trivia_accuracy,
        "trivia_correct": trivia_correct,
        "trivia_total": trivia_total,
        "total_score": total_score,
        "roulette_rounds": roulette_rounds,
        "end_reason": session["end_reason"],
        "share_text": share_text,
    }


async def idle_end_session(conn, session_id: str) -> None:
    """Lazily mark a session ended due to inactivity.

    Called from _check_phone_session_resume when the SQL idle check fires.
    No broadcast -- nobody is actively listening at this point.
    """
    await conn.execute(
        """
        UPDATE game_sessions
        SET ended_at = NOW(), end_reason = 'idle_timeout'
        WHERE id = $1 AND ended_at IS NULL
        """,
        session_id,
    )
