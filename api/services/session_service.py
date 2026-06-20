"""End-game, host migration, and recap logic.

Pure async functions taking `conn` as first arg -- same pattern as
roulette_service.py / chooser_service.py.

end_game:         Origin-only. Atomically marks the session ended and broadcasts
                  game_ended to all phones so they transition to the Recap screen.
migrate_host:     Host-leave path. Marks old host left_early, picks the next host
                  (earliest-joined active player), atomically reassigns
                  origin_phone_id, and broadcasts host_changed + player_left.
                  If no active candidate remains, ends the game inline.
get_recap:        Session-scoped read. Aggregates stats for the Recap screen.
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


async def migrate_host(conn, session_id: str, phone_id: str) -> dict:
    """Host-leave path: mark old host left_early, pick the new host
    (earliest-joined active player), reassign origin_phone_id,
    broadcast host_changed + player_left. If no active candidate
    remains, end the game.

    BOLA: only the current origin_phone_id may trigger this.
    Atomic: UPDATE ... WHERE origin_phone_id = $old.

    Returns:
      {"migrated": True, "new_host_phone_id": ..., "new_host_name": ..., "old_host_name": ...}
    or:
      {"ended": True, "session_id": ...}
    """
    session = await conn.fetchrow(
        "SELECT id, table_id, ended_at, origin_phone_id FROM game_sessions WHERE id = $1",
        session_id,
    )
    if not session:
        raise LookupError("session_not_found")
    if session["ended_at"] is not None:
        raise ValueError("session_already_ended")
    # BOLA: only the current host may trigger migration
    if session["origin_phone_id"] != phone_id:
        raise PermissionError("not_origin_phone")

    table_id = str(session["table_id"])

    # Mark old host left_early; fetch their name for the player_left broadcast.
    old_player = await conn.fetchrow(
        """
        UPDATE game_players SET left_early = TRUE, left_at = NOW()
        WHERE session_id = $1 AND phone_id = $2 AND left_early = FALSE
        RETURNING id, name, score
        """,
        session_id, phone_id,
    )
    if old_player:
        old_host_name = old_player["name"]
    else:
        # Already marked left (idempotent call) -- fetch name for broadcast
        old_host_name = await conn.fetchval(
            "SELECT name FROM game_players WHERE session_id = $1 AND phone_id = $2",
            session_id, phone_id,
        ) or "The host"

    # Resolve any in-flight Chooser round (orphaned card on leaving host's phone)
    orphan = await conn.fetchrow(
        """
        UPDATE rounds SET result = 'skipped', score_awarded = 0
        WHERE session_id = $1 AND round_type = 'chooser' AND result IS NULL
        RETURNING id
        """,
        session_id,
    )
    if orphan:
        await conn.execute(
            """
            UPDATE game_sessions
            SET cards_skipped = cards_skipped + 1,
                total_rounds = total_rounds + 1,
                last_activity_at = NOW()
            WHERE id = $1
            """,
            session_id,
        )

    # Pick new host: earliest-joined active player from the converted lobby
    candidate = await conn.fetchrow(
        """
        SELECT gp.phone_id, gp.name
        FROM game_players gp
        JOIN table_lobby_phones tlp ON tlp.phone_id = gp.phone_id
        JOIN table_lobbies tl ON tl.id = tlp.lobby_id
        WHERE gp.session_id = $1
          AND gp.left_early = FALSE
          AND gp.phone_id IS NOT NULL
          AND gp.phone_id != $2
          AND tl.converted_session_id = $1
        ORDER BY tlp.joined_at ASC
        LIMIT 1
        """,
        session_id, phone_id,
    )

    if not candidate:
        # No active players left — end the game
        await conn.execute(
            """
            UPDATE game_sessions SET ended_at = NOW(), end_reason = 'host_left_no_players'
            WHERE id = $1 AND ended_at IS NULL
            """,
            session_id,
        )
        await rt_publish(_channel(table_id), "game_ended", {"session_id": session_id})
        return {"ended": True, "session_id": session_id}

    new_phone_id = candidate["phone_id"]
    new_host_name = candidate["name"]

    # Atomically reassign origin (WHERE origin_phone_id = old guards against races)
    updated = await conn.fetchrow(
        """
        UPDATE game_sessions SET origin_phone_id = $1
        WHERE id = $2 AND origin_phone_id = $3
        RETURNING id
        """,
        new_phone_id, session_id, phone_id,
    )
    if not updated:
        # Race: another call already changed origin -- treat as idempotent success
        new_phone_id = await conn.fetchval(
            "SELECT origin_phone_id FROM game_sessions WHERE id = $1", session_id
        )
        new_host_name = await conn.fetchval(
            "SELECT name FROM game_players WHERE session_id = $1 AND phone_id = $2",
            session_id, new_phone_id,
        ) or "New host"

    # Single broadcast carries both facts (old host left + new host) so the
    # client shows one combined toast instead of two that clobber each other.
    await rt_publish(
        _channel(table_id),
        "host_changed",
        {
            "session_id": session_id,
            "new_host_phone_id": new_phone_id,
            "new_host_name": new_host_name,
            "old_host_name": old_host_name,
        },
    )
    return {
        "migrated": True,
        "new_host_phone_id": new_phone_id,
        "new_host_name": new_host_name,
        "old_host_name": old_host_name,
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
