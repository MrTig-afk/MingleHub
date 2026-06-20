"""Round-flow logic that runs once a session has started (gamespec.md:
Step 5 — Round Flow). Currently covers just the Finger Picker (selecting
the Hot Seat Player) — Chooser/Trivia/Roulette round content is separate,
later backlog work.

Scope boundary: the finger-placement UI/animation itself already existed
(useMultiTouch.js, carried over from the original FirstMove card game).
This module is the "session integration" layer gamespec calls for:
deciding which real game_players row gets selected and persisting
times_selected server-side, rather than the selection living only in
one phone's local JS state.
"""
import secrets

MIN_PLAYERS_FOR_PICK = 2


async def get_session(conn, session_id: str):
    return await conn.fetchrow(
        "SELECT id, ended_at, origin_phone_id, last_hot_seat_player_id FROM game_sessions WHERE id = $1",
        session_id,
    )


async def select_hot_seat(conn, session: dict, phone_id: str) -> dict:
    """gamespec: Finger Picker — "Players place fingers on session-origin
    phone" / "times_selected incremented on game_players record".

    Selection happens server-side (not just trusting whichever phone ran
    the local picker) so times_selected and the previous-winner exclusion
    are durable across requests/reloads, not just one phone's JS state.
    """
    if session["ended_at"] is not None:
        raise ValueError("session_ended")
    if session["origin_phone_id"] != phone_id:
        raise PermissionError("not_origin_phone")

    players = await conn.fetch(
        "SELECT id, name FROM game_players WHERE session_id = $1 AND left_early = FALSE",
        session["id"],
    )
    if len(players) < MIN_PLAYERS_FOR_PICK:
        raise ValueError("not_enough_players")

    # 2 players: pure random, no exclusion — back-to-back is allowed.
    # 3+ players: exclude the previous winner so the same person can't be
    # picked twice in a row. secrets.choice() does unbiased selection
    # internally (rejection sampling), the server-side equivalent of the
    # crypto.getRandomValues() approach the client-side picker uses.
    pool = players
    if len(players) >= 3 and session["last_hot_seat_player_id"] is not None:
        filtered = [p for p in players if p["id"] != session["last_hot_seat_player_id"]]
        if filtered:  # safety fallback if the previous winner already left
            pool = filtered

    winner = secrets.choice(pool)

    times_selected = await conn.fetchval(
        "UPDATE game_players SET times_selected = times_selected + 1 WHERE id = $1 RETURNING times_selected",
        winner["id"],
    )
    await conn.execute(
        "UPDATE game_sessions SET last_hot_seat_player_id = $1, last_activity_at = NOW() WHERE id = $2",
        winner["id"], session["id"],
    )

    return {"player_id": str(winner["id"]), "name": winner["name"], "times_selected": times_selected}
