"""Dev-only: wipe all live game state so the very next tap starts game-zero.

Clears sessions, lobbies, joined phones, players, and rounds — i.e. every
table that accumulates *during play*. Deliberately KEEPS venues, tables,
nfc_tags, users, and the bar_cards deck, so `/{venue}/{table}` still
resolves and the seeded demo venue stays intact.

Refuses to run unless DEV_MODE=true, so it can never nuke a production
database by accident. Run between test rounds:

    DEV_MODE=true PYTHONPATH=. ./venv/Scripts/python.exe scripts/dev_reset.py
"""
import asyncio
import os

from dotenv import load_dotenv

from api.db import get_pool

load_dotenv()

# Order doesn't matter with CASCADE, but listing children-first documents intent.
GAME_STATE_TABLES = [
    "trivia_answers",
    "trivia_participants",
    "trivia_rounds",
    "game_players",
    "rounds",
    "table_lobby_phones",
    "table_lobbies",
    "game_sessions",
]


async def main():
    if os.getenv("DEV_MODE") != "true":
        raise SystemExit("Refusing to run: DEV_MODE != 'true' (this wipes ALL game state).")

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE " + ", ".join(GAME_STATE_TABLES) + " RESTART IDENTITY CASCADE"
        )
    await pool.close()
    print("Game state cleared:", ", ".join(GAME_STATE_TABLES))


asyncio.run(main())
