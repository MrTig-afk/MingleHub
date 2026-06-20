"""Seed data for roulette_cards table.

Uses deterministic UUIDs via uuid.uuid5 (same pattern as seed_bar_cards.py)
so re-running is idempotent via ON CONFLICT DO NOTHING.

Entry point: async def seed(conn) -- called from migrate.py and conftest.py.
"""
import asyncio
import asyncpg
import os
import sys
import uuid
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
load_dotenv(os.path.join(ROOT, 'api', '.env'))

# Deterministic namespace UUID for roulette_cards seed
_NS = uuid.UUID("40a1e77e-0000-0000-0000-000000000000")


def _card_id(key: str) -> str:
    return str(uuid.uuid5(_NS, key))


# (id, prompt_text, content_tier, drink_consequence_standard, drink_consequence_adults)
CARDS = [
    (
        _card_id("roulette-1"),
        "Going around the table, name a yellow thing -- first to hesitate or repeat loses.",
        "standard", "Take a sip", "Skull your drink",
    ),
    (
        _card_id("roulette-2"),
        "Everyone do your best impression of a random celebrity -- group votes the worst.",
        "standard", "Take a sip", "Take a drink",
    ),
    (
        _card_id("roulette-3"),
        "Rock paper scissors tournament -- everyone pairs up, losers are eliminated. Last one standing wins.",
        "standard", "Losers take a sip", "Losers take a drink",
    ),
    (
        _card_id("roulette-4"),
        "Everyone puts a finger on the table. Go around saying 'Never have I ever...' -- last finger down loses.",
        "standard", "Take a sip", "Finish your drink",
    ),
    (
        _card_id("roulette-5"),
        "Staring contest -- everyone pairs up. First to blink or laugh loses.",
        "standard", "Take a sip", "Take a drink",
    ),
    (
        _card_id("roulette-6"),
        "Name countries in Asia going around the table -- first to repeat or hesitate loses.",
        "standard", "Take a sip", "Take a drink",
    ),
    (
        _card_id("roulette-7"),
        "Everyone has 10 seconds to strike their best pose -- group votes the worst.",
        "standard", "Take a sip", "Buy a round",
    ),
    (
        _card_id("roulette-8"),
        "Go around the table naming a song by the same artist -- first to fail loses.",
        "standard", "Take a sip", "Take a drink",
    ),
    (
        _card_id("roulette-9"),
        "Thumb war bracket -- pair up and play. Last one undefeated wins.",
        "standard", "Losers take a sip", "Losers finish their drink",
    ),
    (
        _card_id("roulette-10"),
        "Most likely to... The table votes on who is most likely to cry at a movie. Most votes loses.",
        "standard", "Take a sip", "Take a drink",
    ),
    (
        _card_id("roulette-11"),
        "Confess your most embarrassing moment -- group votes whose was worst. That person loses.",
        "adults_allowed", "Take a drink", "Finish your drink",
    ),
    (
        _card_id("roulette-12"),
        "Rate each other's dance moves -- worst dancer loses.",
        "adults_allowed", "Take a drink", "Skull your drink",
    ),
]


async def seed(conn):
    """Upsert roulette_cards seed rows. Safe to call multiple times."""
    for card_id, prompt_text, content_tier, drink_std, drink_adults in CARDS:
        await conn.execute(
            """
            INSERT INTO roulette_cards
                (id, prompt_text, content_tier, drink_consequence_standard, drink_consequence_adults)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (id) DO NOTHING
            """,
            card_id, prompt_text, content_tier, drink_std, drink_adults,
        )
    print(f"OK {len(CARDS)} roulette_cards seeded")


async def _connect_and_seed():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        await seed(conn)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(_connect_and_seed())
