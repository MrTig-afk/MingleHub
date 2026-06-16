"""Lobby + Join-or-New session routing (gamespec.md: Player Flow Step 2/3).

Scope boundary: this module gets a table from "tap" to "a game_sessions row
exists with players in it" — lobby formation, host election, the 3-groups-
per-table cap, and joining an in-progress group. It does NOT render rounds;
that's the round-engine slices (Chooser/Trivia/Roulette) still to come.

Concurrency notes:
- Only one OPEN lobby per table is allowed (partial unique index in
  migrate.py). Two phones tapping at the same instant with no existing
  lobby can both attempt to create one; the loser's INSERT raises a
  unique violation, caught here and turned into "fetch the winner's lobby
  and join it instead" rather than a 500.
- Host election is a single atomic UPDATE ... WHERE host_phone_id IS NULL —
  whoever's UPDATE affects a row wins; no read-then-write race.
- Re-tapping (same phone_id) is idempotent via ON CONFLICT DO NOTHING.
"""
import uuid

import asyncpg

MAX_GROUPS_PER_TABLE = 3
MIN_PLAYERS = 2
MAX_PLAYERS = 8


async def resolve_table_state(conn, venue_id: str, table_id: str, table_number: int, phone_id: str) -> dict:
    """Called right after a tap verifies. Decides what this phone should see:
    a lobby to wait in, a Join-or-New chooser, or "table full"."""
    groups = await _active_sessions(conn, table_id)

    if groups:
        phase = "table_full" if len(groups) >= MAX_GROUPS_PER_TABLE else "join_or_new"
        # table_id included so the frontend can call POST /table/{id}/new-group —
        # not needed for the "lobby" phase below, where there's nothing to choose between yet.
        return {"phase": phase, "groups": groups, "table_id": str(table_id)}

    lobby = await _get_or_create_open_lobby(conn, venue_id, table_id)
    phone_count = await _join_lobby_phone(conn, lobby["id"], phone_id)
    return {
        "phase": "lobby",
        "lobby_id": str(lobby["id"]),
        "phone_count": phone_count,
        "host_phone_id": lobby["host_phone_id"],
        "created_at": lobby["created_at"].isoformat(),
    }


async def start_new_group(conn, venue_id: str, table_id: str, phone_id: str) -> dict:
    """"Start a new group at this table" from the Join-or-New chooser —
    explicit, unlike the auto-create-lobby path in resolve_table_state,
    since a tap with active sessions already present always shows
    Join-or-New first rather than silently spawning a 2nd/3rd lobby."""
    groups = await _active_sessions(conn, table_id)
    if len(groups) >= MAX_GROUPS_PER_TABLE:
        raise ValueError("table_full")

    lobby = await _get_or_create_open_lobby(conn, venue_id, table_id)
    phone_count = await _join_lobby_phone(conn, lobby["id"], phone_id)
    return {
        "lobby_id": str(lobby["id"]),
        "phone_count": phone_count,
        "host_phone_id": lobby["host_phone_id"],
        "created_at": lobby["created_at"].isoformat(),
    }


async def _active_sessions(conn, table_id: str) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT id, group_label, player_count, started_at
        FROM game_sessions
        WHERE table_id = $1 AND ended_at IS NULL
        ORDER BY created_at
        """,
        table_id,
    )
    return [
        {
            "session_id": str(r["id"]),
            "group_label": r["group_label"],
            "player_count": r["player_count"],
            "started_at": r["started_at"].isoformat() if r["started_at"] else None,
        }
        for r in rows
    ]


async def _get_or_create_open_lobby(conn, venue_id: str, table_id: str):
    existing = await conn.fetchrow(
        "SELECT id, host_phone_id, created_at FROM table_lobbies WHERE table_id = $1 AND status = 'open'",
        table_id,
    )
    if existing:
        return existing

    try:
        return await conn.fetchrow(
            """
            INSERT INTO table_lobbies (id, venue_id, table_id, status)
            VALUES ($1, $2, $3, 'open')
            RETURNING id, host_phone_id, created_at
            """,
            str(uuid.uuid4()), venue_id, table_id,
        )
    except asyncpg.UniqueViolationError:
        # Another phone's tap created the lobby in the gap between our
        # SELECT and INSERT — use theirs instead of erroring out.
        return await conn.fetchrow(
            "SELECT id, host_phone_id, created_at FROM table_lobbies WHERE table_id = $1 AND status = 'open'",
            table_id,
        )


async def _join_lobby_phone(conn, lobby_id, phone_id: str) -> int:
    await conn.execute(
        """
        INSERT INTO table_lobby_phones (id, lobby_id, phone_id)
        VALUES ($1, $2, $3)
        ON CONFLICT (lobby_id, phone_id) DO NOTHING
        """,
        str(uuid.uuid4()), lobby_id, phone_id,
    )
    return await conn.fetchval(
        "SELECT COUNT(*) FROM table_lobby_phones WHERE lobby_id = $1", lobby_id
    )


async def get_lobby_state(conn, lobby_id: str) -> dict | None:
    lobby = await conn.fetchrow(
        "SELECT id, status, host_phone_id, converted_session_id, created_at FROM table_lobbies WHERE id = $1",
        lobby_id,
    )
    if not lobby:
        return None
    phone_count = await conn.fetchval(
        "SELECT COUNT(*) FROM table_lobby_phones WHERE lobby_id = $1", lobby_id
    )
    return {
        "lobby_id": str(lobby["id"]),
        "status": lobby["status"],
        "host_phone_id": lobby["host_phone_id"],
        "converted_session_id": str(lobby["converted_session_id"]) if lobby["converted_session_id"] else None,
        "phone_count": phone_count,
        "created_at": lobby["created_at"].isoformat(),
    }


async def get_lobby(conn, lobby_id: str):
    """Full row (including venue_id/table_id) — needed by start_game, as
    opposed to get_lobby_state's API-response-shaped summary."""
    return await conn.fetchrow(
        "SELECT id, venue_id, table_id, status, host_phone_id FROM table_lobbies WHERE id = $1",
        lobby_id,
    )


async def is_lobby_member(conn, lobby_id: str, phone_id: str) -> bool:
    return await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM table_lobby_phones WHERE lobby_id = $1 AND phone_id = $2)",
        lobby_id, phone_id,
    )


async def claim_host(conn, lobby_id: str, phone_id: str) -> dict:
    """First phone to claim wins — atomic, no read-then-write gap."""
    row = await conn.fetchrow(
        """
        UPDATE table_lobbies SET host_phone_id = $1
        WHERE id = $2 AND status = 'open' AND host_phone_id IS NULL
        RETURNING host_phone_id
        """,
        phone_id, lobby_id,
    )
    if row:
        return {"you_are_host": True, "host_phone_id": phone_id}

    current = await conn.fetchval("SELECT host_phone_id FROM table_lobbies WHERE id = $1", lobby_id)
    return {"you_are_host": current == phone_id, "host_phone_id": current}


async def next_group_label(conn, table_id: str) -> str:
    # "Tonight" = since the most recent 4am, matching the activation-code
    # rotation boundary elsewhere in gamespec.md. Numbers don't recycle
    # within a night even if earlier groups have already ended.
    table_number = await conn.fetchval("SELECT table_number FROM tables WHERE id = $1", table_id)
    count = await conn.fetchval(
        """
        SELECT COUNT(*) FROM game_sessions
        WHERE table_id = $1
        AND created_at >= date_trunc('day', NOW() - INTERVAL '4 hours') + INTERVAL '4 hours'
        """,
        table_id,
    )
    return f"Table {table_number} Group {count + 1}"


async def start_game(
    conn, lobby: dict, phone_id: str, player_count: int,
    player_names: list[str] | None, adults_only: bool, group_label: str | None,
) -> dict:
    if lobby["status"] != "open":
        raise ValueError("lobby_not_open")
    if lobby["host_phone_id"] != phone_id:
        raise PermissionError("not_host")
    if not (MIN_PLAYERS <= player_count <= MAX_PLAYERS):
        raise ValueError("invalid_player_count")

    names = player_names or [f"Player {i + 1}" for i in range(player_count)]
    label = group_label or await next_group_label(conn, lobby["table_id"])

    session_id = str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO game_sessions (id, venue_id, table_id, group_label, player_count, player_names,
                                    adults_only, started_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
        """,
        session_id, lobby["venue_id"], lobby["table_id"], label, player_count, names, adults_only,
    )
    for name in names:
        await conn.execute(
            "INSERT INTO game_players (id, session_id, name) VALUES ($1, $2, $3)",
            str(uuid.uuid4()), session_id, name,
        )
    await conn.execute(
        "UPDATE table_lobbies SET status = 'converted', converted_session_id = $1 WHERE id = $2",
        session_id, lobby["id"],
    )
    return {"session_id": session_id, "group_label": label, "player_count": player_count}


async def join_existing_session(conn, session_id: str, name: str | None) -> dict:
    session = await conn.fetchrow(
        "SELECT id FROM game_sessions WHERE id = $1 AND ended_at IS NULL", session_id
    )
    if not session:
        raise LookupError("session_not_found_or_ended")

    player_id = str(uuid.uuid4())
    player_name = name or "New Player"
    await conn.execute(
        "INSERT INTO game_players (id, session_id, name) VALUES ($1, $2, $3)",
        player_id, session_id, player_name,
    )
    return {"session_id": session_id, "player_id": player_id, "name": player_name}
