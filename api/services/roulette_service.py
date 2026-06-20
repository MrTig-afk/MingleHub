"""Roulette round logic (gamespec.md: Round Type 3 -- Roulette).

Pure async functions taking `conn` as first arg -- same pattern as
chooser_service.py / trivia_service.py.

Group challenge: the whole table plays together. Every active phone votes on
who lost the challenge. Losers get 0 pts; every other active player gets
+3 pts (Scoring -> Discernibility Principle: the loser is identified by table
consensus / plurality vote, so points are reliably attributable).

Lifecycle: start -> (players vote) -> tally (auto or origin-forced) / skip
"""
import uuid

from api.services.realtime_service import publish as rt_publish

MIN_ACTIVE_PLAYERS = 2
POINTS_NON_LOSER = 3


def _channel(table_id) -> str:
    return f"table:{table_id}"


async def _pick_roulette_card(conn, session_id: str, adults_only: bool,
                              exclude_ids: list | None = None) -> dict | None:
    """Pick a random roulette_card obeying the session's adults_only flag.

    If adults_only is False, restrict to standard-tier cards only. If True,
    both standard and adults_allowed are eligible.

    exclude_ids: list of UUID strings to always skip (e.g. the current card
    on a redraw). Falls back to allowing repeats if the filtered pool is empty.

    Uses numbered placeholders throughout -- no f-string SQL with data.
    """
    exclude_uuids = [uuid.UUID(eid) for eid in (exclude_ids or [])]

    def _build(include_used: bool):
        """Build WHERE clause and aligned arg list.

        Placeholders are numbered from $1 for each query independently so
        the relaxed query (no used-this-session exclusion) stays in sync.
        """
        args: list = []
        where: list = []

        if not adults_only:
            where.append("rc.content_tier = 'standard'")

        if include_used:
            args.append(session_id)
            where.append(
                f"rc.id NOT IN (SELECT card_id FROM rounds "
                f"WHERE session_id = ${len(args)} AND round_type = 'roulette' "
                f"AND card_id IS NOT NULL)"
            )

        if exclude_uuids:
            placeholders = []
            for eid in exclude_uuids:
                args.append(eid)
                placeholders.append(f"${len(args)}")
            where.append(f"rc.id NOT IN ({', '.join(placeholders)})")

        where_sql = " AND ".join(where) if where else "TRUE"
        return where_sql, args

    # First try excluding already-used cards this session
    where_sql, args = _build(include_used=True)
    row = await conn.fetchrow(
        f"""
        SELECT rc.id, rc.prompt_text, rc.content_tier,
               rc.drink_consequence_standard, rc.drink_consequence_adults
        FROM roulette_cards rc
        WHERE {where_sql}
        ORDER BY random()
        LIMIT 1
        """,
        *args,
    )
    if row:
        return dict(row)

    # Pool exhausted -- relax the "used this session" exclusion
    where_sql, args = _build(include_used=False)
    row = await conn.fetchrow(
        f"""
        SELECT rc.id, rc.prompt_text, rc.content_tier,
               rc.drink_consequence_standard, rc.drink_consequence_adults
        FROM roulette_cards rc
        WHERE {where_sql}
        ORDER BY random()
        LIMIT 1
        """,
        *args,
    )
    return dict(row) if row else None


async def _active_players(conn, session_id: str) -> list:
    """All active (non-left-early, phone-bound) players in the session."""
    return await conn.fetch(
        """
        SELECT id, name, phone_id FROM game_players
        WHERE session_id = $1 AND left_early = FALSE AND phone_id IS NOT NULL
        """,
        session_id,
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


async def start_roulette(conn, session_id: str, phone_id: str) -> dict:
    """Open a Roulette round. Origin-phone only.

    Picks a challenge card and broadcasts roulette:start to all phones so
    they transition to the vote UI. Idempotent: if an active roulette round
    already exists (React StrictMode double-mount / retry), returns it.
    """
    session = await conn.fetchrow(
        """
        SELECT id, table_id, ended_at, origin_phone_id, adults_only,
               current_round_number
        FROM game_sessions WHERE id = $1
        """,
        session_id,
    )
    if not session:
        raise LookupError("session_not_found")
    if session["ended_at"] is not None:
        raise ValueError("session_ended")
    if session["origin_phone_id"] != phone_id:
        raise PermissionError("not_origin_phone")

    active = await _active_players(conn, session_id)
    if len(active) < MIN_ACTIVE_PLAYERS:
        raise ValueError("not_enough_players")

    # Idempotent: return the existing active round rather than creating a second
    existing = await conn.fetchrow(
        """
        SELECT id, card_id FROM rounds
        WHERE session_id = $1 AND round_type = 'roulette' AND result IS NULL
        ORDER BY created_at DESC LIMIT 1
        """,
        session_id,
    )
    if existing:
        card = await conn.fetchrow(
            """
            SELECT prompt_text, content_tier,
                   drink_consequence_standard, drink_consequence_adults
            FROM roulette_cards WHERE id = $1
            """,
            existing["card_id"],
        )
        adults_only = session["adults_only"]
        drink_consequence = (
            card["drink_consequence_adults"] if adults_only
            else card["drink_consequence_standard"]
        )
        voted_count = await conn.fetchval(
            "SELECT COUNT(*) FROM roulette_votes WHERE round_id = $1",
            existing["id"],
        )
        return {
            "round_id": str(existing["id"]),
            "round_number": session["current_round_number"],
            "prompt": card["prompt_text"],
            "drink_consequence": drink_consequence,
            "players": [{"id": str(p["id"]), "name": p["name"]} for p in active],
            "voted_count": int(voted_count),
            "active_total": len(active),
        }

    adults_only = session["adults_only"]
    card = await _pick_roulette_card(conn, session_id, adults_only)
    if not card:
        raise ValueError("no_cards_available")

    # Increment round counter atomically and record activity
    round_number = await conn.fetchval(
        """
        UPDATE game_sessions SET current_round_number = current_round_number + 1,
            last_activity_at = NOW()
        WHERE id = $1 RETURNING current_round_number
        """,
        session_id,
    )

    round_id = str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO rounds (id, session_id, round_number, round_type, card_id, result)
        VALUES ($1, $2, $3, 'roulette', $4, NULL)
        """,
        round_id, session_id, round_number, str(card["id"]),
    )

    drink_consequence = (
        card["drink_consequence_adults"] if adults_only
        else card["drink_consequence_standard"]
    )

    await rt_publish(_channel(session["table_id"]), "roulette:start", {
        "session_id": session_id,
        "round_id": round_id,
        "prompt": card["prompt_text"],
        "drink_consequence": drink_consequence,
    })

    return {
        "round_id": round_id,
        "round_number": round_number,
        "prompt": card["prompt_text"],
        "drink_consequence": drink_consequence,
        "players": [{"id": str(p["id"]), "name": p["name"]} for p in active],
        "voted_count": 0,
        "active_total": len(active),
    }


async def cast_vote(conn, round_id: str, voter_phone_id: str, voted_player_id: str) -> dict:
    """Record (or update) a phone's vote for who lost the challenge.

    Any active member may vote. Upserts: you can change your vote before
    tally. Auto-tallies once all active players have voted.
    """
    row = await conn.fetchrow(
        """
        SELECT r.id, r.session_id, r.round_type, r.result,
               gs.table_id, gs.origin_phone_id
        FROM rounds r
        JOIN game_sessions gs ON gs.id = r.session_id
        WHERE r.id = $1
        """,
        round_id,
    )
    if not row:
        raise LookupError("round_not_found")
    if row["round_type"] != "roulette":
        raise ValueError("wrong_round_type")
    if row["result"] is not None:
        raise ValueError("round_already_resolved")

    session_id = str(row["session_id"])

    # BOLA: voter must be an active member of this session
    voter_player = await conn.fetchrow(
        """
        SELECT id FROM game_players
        WHERE session_id = $1 AND phone_id = $2 AND left_early = FALSE
        """,
        session_id, voter_phone_id,
    )
    if not voter_player:
        raise PermissionError("not_a_member")

    # BOLA: voted player must be a member of the same session
    target_player = await conn.fetchrow(
        "SELECT id FROM game_players WHERE id = $1 AND session_id = $2",
        voted_player_id, session_id,
    )
    if not target_player:
        raise LookupError("voted_player_not_found")

    # Upsert: allows changing your vote before tally
    await conn.execute(
        """
        INSERT INTO roulette_votes (id, round_id, voter_phone_id, voted_player_id)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (round_id, voter_phone_id)
        DO UPDATE SET voted_player_id = EXCLUDED.voted_player_id
        """,
        str(uuid.uuid4()), round_id, voter_phone_id, voted_player_id,
    )

    voted_count = int(await conn.fetchval(
        "SELECT COUNT(*) FROM roulette_votes WHERE round_id = $1", round_id
    ))
    active = await _active_players(conn, session_id)
    active_total = len(active)

    await rt_publish(_channel(row["table_id"]), "roulette:vote", {
        "round_id": round_id,
        "voted_count": voted_count,
        "active_total": active_total,
    })

    # Auto-tally once everyone has voted
    if voted_count >= active_total:
        tally = await tally_roulette(conn, round_id)
        return {**tally, "auto_tallied": True}

    return {
        "round_id": round_id,
        "voted_count": voted_count,
        "active_total": active_total,
        "auto_tallied": False,
    }


async def tally_roulette(conn, round_id: str, phone_id: str | None = None) -> dict:
    """Tally votes and award points.

    Called from auto-tally (phone_id=None) or the Reveal route (phone_id=origin).
    Atomic: only the first call flips result from NULL. If already tallied, returns
    the existing result idempotently (does NOT raise).
    """
    row = await conn.fetchrow(
        """
        SELECT r.id, r.session_id, r.round_type, r.result, r.card_id,
               gs.table_id, gs.origin_phone_id, gs.adults_only
        FROM rounds r
        JOIN game_sessions gs ON gs.id = r.session_id
        WHERE r.id = $1
        """,
        round_id,
    )
    if not row:
        raise LookupError("round_not_found")
    if row["round_type"] != "roulette":
        raise ValueError("wrong_round_type")

    # Only origin can force-tally (auto-tally passes phone_id=None)
    if phone_id is not None and row["origin_phone_id"] != phone_id:
        raise PermissionError("not_origin_phone")

    session_id = str(row["session_id"])
    table_id = str(row["table_id"])

    # Drink consequence shown on the result screens (loaded once).
    drink_consequence = ""
    if row["card_id"]:
        card = await conn.fetchrow(
            "SELECT drink_consequence_standard, drink_consequence_adults "
            "FROM roulette_cards WHERE id = $1",
            row["card_id"],
        )
        if card:
            drink_consequence = (
                card["drink_consequence_adults"] if row["adults_only"]
                else card["drink_consequence_standard"]
            )

    # Votes are frozen once result is set (cast_vote rejects a resolved round),
    # so the tally is identical whether or not we win the claim -- compute once
    # so the idempotent (already-tallied) caller returns the same losers/points.
    vote_rows = await conn.fetch(
        """
        SELECT voted_player_id, COUNT(*) AS vote_count
        FROM roulette_votes WHERE round_id = $1
        GROUP BY voted_player_id ORDER BY vote_count DESC
        """,
        round_id,
    )
    tallies = {str(r["voted_player_id"]): int(r["vote_count"]) for r in vote_rows}

    active = await _active_players(conn, session_id)
    active_ids = [str(p["id"]) for p in active]

    loser_ids = []
    if vote_rows:
        max_votes = int(vote_rows[0]["vote_count"])
        loser_ids = [str(r["voted_player_id"]) for r in vote_rows
                     if int(r["vote_count"]) == max_votes]

    # Loser names (a loser may even be a left-early player who was voted for).
    name_map = {str(p["id"]): p["name"] for p in active}
    for lid in loser_ids:
        if lid not in name_map:
            name_map[lid] = await conn.fetchval(
                "SELECT name FROM game_players WHERE id = $1", lid
            ) or "?"
    losers = [{"id": lid, "name": name_map.get(lid, "?")} for lid in loser_ids]

    # If ALL active players are losers (everyone tied) -> nobody gets +3.
    all_tied = bool(active_ids) and set(active_ids) == set(loser_ids)
    awards_points = bool(loser_ids) and not all_tied
    non_loser_count = len([aid for aid in active_ids if aid not in loser_ids])
    points_awarded = POINTS_NON_LOSER if awards_points else 0
    score_increment = points_awarded * non_loser_count

    # Atomic claim -- only the first caller scores + broadcasts. A racing second
    # caller skips straight to the (identical) return below, so scores can't
    # double-apply but it still gets the real losers/points.
    claimed = await conn.fetchrow(
        "UPDATE rounds SET result = 'completed' WHERE id = $1 AND result IS NULL RETURNING id",
        round_id,
    )
    if claimed:
        if awards_points:
            loser_uuids = [uuid.UUID(lid) for lid in loser_ids]
            await conn.execute(
                """
                UPDATE game_players SET score = score + $1
                WHERE session_id = $2 AND left_early = FALSE AND id != ALL($3::uuid[])
                """,
                POINTS_NON_LOSER, session_id, loser_uuids,
            )
        await conn.execute(
            """
            UPDATE game_sessions
            SET total_score = total_score + $1, total_rounds = total_rounds + 1,
                last_activity_at = NOW()
            WHERE id = $2
            """,
            score_increment, session_id,
        )
        await conn.execute(
            "UPDATE rounds SET selected_player_id = $1, score_awarded = $2 WHERE id = $3",
            (loser_ids[0] if loser_ids else None), score_increment, round_id,
        )
        await rt_publish(_channel(table_id), "roulette:result", {
            "round_id": round_id,
            "skipped": False,
            "losers": losers,
            "tallies": tallies,
            "points_awarded": points_awarded,
            "drink_consequence": drink_consequence,
            "leaderboard": await _leaderboard(conn, session_id),
        })

    return {
        "round_id": round_id,
        "result": "completed",
        "losers": losers,
        "tallies": tallies,
        "points_awarded": points_awarded,
        "drink_consequence": drink_consequence,
        "leaderboard": await _leaderboard(conn, session_id),
    }


async def skip_roulette(conn, round_id: str, phone_id: str) -> dict:
    """Origin skips the roulette round -- 0 points, move on."""
    row = await conn.fetchrow(
        """
        SELECT r.id, r.session_id, r.round_type, r.result,
               gs.table_id, gs.origin_phone_id
        FROM rounds r
        JOIN game_sessions gs ON gs.id = r.session_id
        WHERE r.id = $1
        """,
        round_id,
    )
    if not row:
        raise LookupError("round_not_found")
    if row["round_type"] != "roulette":
        raise ValueError("wrong_round_type")
    if row["origin_phone_id"] != phone_id:
        raise PermissionError("not_origin_phone")

    updated = await conn.fetchrow(
        """
        UPDATE rounds SET result = 'skipped', score_awarded = 0
        WHERE id = $1 AND result IS NULL RETURNING id
        """,
        round_id,
    )
    if not updated:
        raise ValueError("round_already_resolved")

    await conn.execute(
        """
        UPDATE game_sessions
        SET cards_skipped = cards_skipped + 1,
            total_rounds = total_rounds + 1,
            last_activity_at = NOW()
        WHERE id = $1
        """,
        str(row["session_id"]),
    )

    await rt_publish(_channel(str(row["table_id"])), "roulette:result", {
        "round_id": round_id,
        "skipped": True,
    })

    return {"round_id": round_id, "result": "skipped", "score_awarded": 0}
