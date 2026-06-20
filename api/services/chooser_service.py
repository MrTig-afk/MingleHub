"""Chooser round logic (gamespec.md: Round Type 1 -- Chooser).

Pure async functions taking `conn` as first arg -- no direct pool access.
Follows the same pattern as round_service.py.

Scope: drawing a card, completing/skipping/redrawing a round, and persisting
score changes. The responsible drinking disclaimer is server-controlled via
game_sessions.drink_disclaimer_shown so it fires exactly once per session
regardless of which client renders it.
"""
import uuid


def _map_card_type(bar_card_type: str) -> str:
    """Map a bar_card.type to the round row's card_type field.

    drink -> 'drink'
    flirty -> 'flirty'
    everything else -> 'standard'
    """
    if bar_card_type == "drink":
        return "drink"
    if bar_card_type == "flirty":
        return "flirty"
    return "standard"


async def _pick_card(conn, session_id: str, adults_only: bool, same_type: str | None = None,
                     exclude_ids: list | None = None) -> dict | None:
    """Pick a random bar_card obeying the session's adults_only flag.

    same_type: if set, restrict to cards of this type (redraw same-category rule).
    exclude_ids: list of UUID strings to avoid (already-used this session).
               If the filtered pool is empty, falls back to allowing repeats.

    Returns a dict with keys: id, content, type. Returns None only if the
    bar_cards table is completely empty after all filters (shouldn't happen
    in practice with the seed data).
    """
    exclude_uuids = [uuid.UUID(eid) for eid in (exclude_ids or [])]

    if not adults_only:
        adults_filter = " AND bc.is_adults_only = FALSE AND bc.type != 'flirty'"
    else:
        adults_filter = ""

    def _build(include_used: bool):
        """Build the WHERE clause and an aligned arg list.

        Placeholders are numbered from $1 for each query independently, so the
        relaxed query (which drops the used-this-session exclusion) stays in
        sync with its own parameter list.
        """
        args: list = []
        where: list = []
        if include_used:
            args.append(session_id)
            where.append(
                f"bc.id NOT IN (SELECT card_id FROM rounds "
                f"WHERE session_id = ${len(args)} AND card_id IS NOT NULL)"
            )
        if same_type:
            args.append(same_type)
            where.append(f"bc.type = ${len(args)}")
        if exclude_uuids:
            placeholders = []
            for eid in exclude_uuids:
                args.append(eid)
                placeholders.append(f"${len(args)}")
            where.append(f"bc.id NOT IN ({', '.join(placeholders)})")
        where_sql = " AND ".join(where) if where else "TRUE"
        where_sql += adults_filter
        return where_sql, args

    # First try excluding already-used cards this session
    where_sql, args = _build(include_used=True)
    row = await conn.fetchrow(
        f"""
        SELECT bc.id, bc.content, bc.type
        FROM bar_cards bc
        WHERE {where_sql}
        ORDER BY random()
        LIMIT 1
        """,
        *args,
    )
    if row:
        return dict(row)

    # Pool exhausted -- relax the "used this session" exclusion but keep
    # the explicit excludes and type/adults filters.
    where_sql, args = _build(include_used=False)
    row = await conn.fetchrow(
        f"""
        SELECT bc.id, bc.content, bc.type
        FROM bar_cards bc
        WHERE {where_sql}
        ORDER BY random()
        LIMIT 1
        """,
        *args,
    )
    return dict(row) if row else None


async def draw_card(conn, session_id: str, player_id: str, phone_id: str) -> dict:
    """Draw a Chooser card and open a new round row.

    BOLA guard: only the session-origin phone may call this.
    """
    session = await conn.fetchrow(
        """
        SELECT id, ended_at, origin_phone_id, adults_only,
               current_round_number, drink_disclaimer_shown
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

    # Verify the player belongs to this session
    player = await conn.fetchrow(
        "SELECT id FROM game_players WHERE id = $1 AND session_id = $2",
        player_id, session_id,
    )
    if not player:
        raise LookupError("player_not_found")

    adults_only = session["adults_only"]
    card = await _pick_card(conn, session_id, adults_only)
    if not card:
        raise ValueError("no_cards_available")

    # Increment round counter atomically and record activity
    round_number = await conn.fetchval(
        """
        UPDATE game_sessions
        SET current_round_number = current_round_number + 1,
            last_activity_at = NOW()
        WHERE id = $1
        RETURNING current_round_number
        """,
        session_id,
    )

    card_type = _map_card_type(card["type"])
    round_id = str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO rounds (id, session_id, round_number, round_type,
                            selected_player_id, card_id, card_type, result)
        VALUES ($1, $2, $3, 'chooser', $4, $5, $6, NULL)
        """,
        round_id, session_id, round_number,
        player_id, str(card["id"]), card_type,
    )

    show_drink_disclaimer = False
    if card_type == "drink" and not session["drink_disclaimer_shown"]:
        await conn.execute(
            "UPDATE game_sessions SET drink_disclaimer_shown = TRUE WHERE id = $1",
            session_id,
        )
        show_drink_disclaimer = True

    return {
        "round_id": round_id,
        "card": {"id": str(card["id"]), "content": card["content"], "type": card["type"]},
        "card_type": card_type,
        "round_number": round_number,
        "show_drink_disclaimer": show_drink_disclaimer,
    }


async def complete_round(conn, round_id: str, phone_id: str) -> dict:
    """Mark a round as completed. The Chooser round awards NO points -- there's
    no reliable way to verify which physical person performed the card, so
    completion is logged for stats (cards_completed) but no score is attributed.
    Points only exist where the actor is individually discernible (e.g. Trivia).

    Ownership and round-type are verified by a read BEFORE any mutation
    (matching draw_card/redraw). The state change then uses
    UPDATE ... WHERE result IS NULL RETURNING ... for atomicity so two
    concurrent calls cannot both succeed (only the first's UPDATE will
    find result IS NULL).
    """
    # Read first: BOLA + round-type guards before touching any state
    row = await conn.fetchrow(
        """
        SELECT r.id, r.session_id, r.selected_player_id, r.round_type,
               gs.origin_phone_id
        FROM rounds r
        JOIN game_sessions gs ON gs.id = r.session_id
        WHERE r.id = $1
        """,
        round_id,
    )
    if not row:
        raise LookupError("round_not_found")
    if row["origin_phone_id"] != phone_id:
        raise PermissionError("not_origin_phone")
    if row["round_type"] != "chooser":
        raise ValueError("wrong_round_type")

    # Atomic guard: only the first concurrent call flips result from NULL
    updated = await conn.fetchrow(
        """
        UPDATE rounds
        SET result = 'completed', score_awarded = 0
        WHERE id = $1 AND result IS NULL
        RETURNING id
        """,
        round_id,
    )
    if not updated:
        raise ValueError("round_already_resolved")

    # No score change -- log completion for stats only (cards_completed /
    # total_rounds). No player score, no total_score: the Chooser round
    # awards no points.
    await conn.execute(
        """
        UPDATE game_sessions
        SET cards_completed = cards_completed + 1,
            total_rounds = total_rounds + 1,
            last_activity_at = NOW()
        WHERE id = $1
        """,
        row["session_id"],
    )

    return {
        "round_id": round_id,
        "result": "completed",
        "score_awarded": 0,
    }


async def skip_round(conn, round_id: str, phone_id: str) -> dict:
    """Mark a round as skipped, no score change.

    Ownership and round-type are verified by a read BEFORE any mutation
    (matching draw_card/redraw), then the atomic UPDATE ... WHERE result
    IS NULL guards against concurrent resolution.
    """
    row = await conn.fetchrow(
        """
        SELECT r.id, r.session_id, r.round_type, gs.origin_phone_id
        FROM rounds r
        JOIN game_sessions gs ON gs.id = r.session_id
        WHERE r.id = $1
        """,
        round_id,
    )
    if not row:
        raise LookupError("round_not_found")
    if row["origin_phone_id"] != phone_id:
        raise PermissionError("not_origin_phone")
    if row["round_type"] != "chooser":
        raise ValueError("wrong_round_type")

    updated = await conn.fetchrow(
        """
        UPDATE rounds
        SET result = 'skipped', score_awarded = 0
        WHERE id = $1 AND result IS NULL
        RETURNING id
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
        row["session_id"],
    )

    return {
        "round_id": round_id,
        "result": "skipped",
        "score_awarded": 0,
    }


async def redraw(conn, round_id: str, phone_id: str) -> dict:
    """Replace the current card with a new one from the same category.

    The Chooser round awards no points, so a redraw carries no penalty either.
    redraw_count is still incremented (analytics). Card pool is per-type, same
    adults_only filter.
    """
    round_row = await conn.fetchrow(
        """
        SELECT r.id, r.session_id, r.round_type, r.result, r.card_id,
               r.card_type, r.redraw_count, r.selected_player_id,
               bc.type AS bar_card_type,
               gs.origin_phone_id, gs.adults_only, gs.drink_disclaimer_shown
        FROM rounds r
        JOIN game_sessions gs ON gs.id = r.session_id
        LEFT JOIN bar_cards bc ON bc.id = r.card_id
        WHERE r.id = $1
        """,
        round_id,
    )
    if not round_row:
        raise LookupError("round_not_found")

    if round_row["origin_phone_id"] != phone_id:
        raise PermissionError("not_origin_phone")
    if round_row["result"] is not None:
        raise ValueError("round_already_resolved")

    adults_only = round_row["adults_only"]
    same_type = round_row["bar_card_type"]

    # Exclude current card from the new draw
    exclude_ids = [str(round_row["card_id"])] if round_row["card_id"] else []

    new_card = await _pick_card(conn, round_row["session_id"], adults_only,
                                same_type=same_type, exclude_ids=exclude_ids)

    no_alternatives = False
    if not new_card:
        # No card at all -- return same card with flag
        no_alternatives = True
        # Fetch the current card to return it
        current_card_row = await conn.fetchrow(
            "SELECT id, content, type FROM bar_cards WHERE id = $1",
            round_row["card_id"],
        )
        if current_card_row:
            new_card = dict(current_card_row)
        else:
            raise ValueError("no_cards_available")

    # Increment redraw count
    new_redraw_count = await conn.fetchval(
        """
        UPDATE rounds SET redraw_count = redraw_count + 1, card_id = $2
        WHERE id = $1
        RETURNING redraw_count
        """,
        round_id, str(new_card["id"]),
    )

    show_drink_disclaimer = False
    new_card_type = _map_card_type(new_card["type"])
    if new_card_type == "drink" and not round_row["drink_disclaimer_shown"]:
        await conn.execute(
            "UPDATE game_sessions SET drink_disclaimer_shown = TRUE WHERE id = $1",
            round_row["session_id"],
        )
        show_drink_disclaimer = True

    # Update card_type on the round to match the new card
    await conn.execute(
        "UPDATE rounds SET card_type = $1 WHERE id = $2",
        new_card_type, round_id,
    )

    return {
        "round_id": round_id,
        "card": {"id": str(new_card["id"]), "content": new_card["content"], "type": new_card["type"]},
        "redraw_count": new_redraw_count,
        "show_drink_disclaimer": show_drink_disclaimer,
        "no_alternatives": no_alternatives,
    }
