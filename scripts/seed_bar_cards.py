"""Seed data for bar_cards table.

Uses deterministic UUIDs via uuid.uuid5 (same pattern as dev_fixtures.py)
so re-running is idempotent via ON CONFLICT DO NOTHING.

Entry point: async def seed(conn) — called from migrate.py and conftest.py.
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

# Deterministic namespace UUID for bar_cards seed
_NS = uuid.UUID("b4ca4d05-0000-0000-0000-000000000000")


def _card_id(key: str) -> str:
    return str(uuid.uuid5(_NS, key))


CARDS = [
    # icebreaker (5 cards)
    (_card_id("icebreaker-1"), "What's your most controversial food opinion?", "icebreaker", False),
    (_card_id("icebreaker-2"),
     "If you could only eat one cuisine for the rest of your life, what would it be?",
     "icebreaker", False),
    (_card_id("icebreaker-3"), "What's a skill you have that would surprise people at this table?", "icebreaker", False),
    (_card_id("icebreaker-4"), "What's the best purchase you've made under $20?", "icebreaker", False),
    (_card_id("icebreaker-5"), "Would you rather have the ability to fly or be invisible? Why?", "icebreaker", False),
    # truth (5 cards)
    (_card_id("truth-1"), "What's the last lie you told?", "truth", False),
    (_card_id("truth-2"), "What's something you've done that you'd never admit on a first date?", "truth", False),
    (_card_id("truth-3"), "What's your most embarrassing childhood memory?", "truth", False),
    (_card_id("truth-4"), "What's a secret talent or hobby you're too embarrassed to share publicly?", "truth", False),
    (_card_id("truth-5"), "What's the most childish thing you still do as an adult?", "truth", False),
    # dare (5 cards)
    (_card_id("dare-1"), "Speak in an accent until your next turn.", "dare", False),
    (_card_id("dare-2"), "Do your best celebrity impression — the table votes on who it was.", "dare", False),
    (_card_id("dare-3"), "Let the person to your right post something on your social media.", "dare", False),
    (_card_id("dare-4"), "Sing the chorus of the last song you listened to.", "dare", False),
    (_card_id("dare-5"), "Do 10 press-ups or pay a forfeit decided by the table.", "dare", False),
    # compliment (5 cards)
    (_card_id("compliment-1"), "Give the person on your left a genuine compliment.", "compliment", False),
    (_card_id("compliment-2"),
     "Tell everyone at the table one thing you admire about them — go around the circle.",
     "compliment", False),
    (_card_id("compliment-3"), "Give the person who looks the most tired tonight a motivational speech.", "compliment", False),
    (_card_id("compliment-4"), "Tell the person across from you why they'd be a great teammate.", "compliment", False),
    (_card_id("compliment-5"), "Name one thing the person to your right is brilliant at.", "compliment", False),
    # challenge (5 cards)
    (_card_id("challenge-1"), "Name five beers in ten seconds.", "challenge", False),
    (_card_id("challenge-2"),
     "Recite the alphabet backwards — stop the clock if you finish under 15 seconds.",
     "challenge", False),
    (_card_id("challenge-3"), "Name ten countries in Europe in 20 seconds.", "challenge", False),
    (_card_id("challenge-4"), "Stack four beer mats on the back of your hand and catch them in one go.", "challenge", False),
    (_card_id("challenge-5"), "Thumb war against the person to your right — loser buys the next round.", "challenge", False),
    # drink (5 cards)
    (_card_id("drink-1"),
     "Waterfall — everyone starts drinking together, you can only stop when the person to your right stops.",
     "drink", False),
    (_card_id("drink-2"), "Never Have I Ever — one round, everyone take turns.", "drink", False),
    (_card_id("drink-3"), "Two Truths and a Lie — whoever guesses wrong drinks.", "drink", False),
    (_card_id("drink-4"), "Cheers — raise your glasses and down them together.", "drink", False),
    (_card_id("drink-5"), "The Bar Decides — the table votes on who drinks and how much.", "drink", False),
    # flirty / adults-only (3 cards)
    (_card_id("flirty-1"), "Give your best chat-up line.", "flirty", True),
    (_card_id("flirty-2"), "Who at this table would survive a reality dating show and why?", "flirty", True),
    (_card_id("flirty-3"), "Rate everyone at the table's flirting game on a scale of 1-10.", "flirty", True),
]


async def seed(conn):
    """Upsert bar_cards seed rows. Safe to call multiple times."""
    for card_id, content, card_type, is_adults_only in CARDS:
        await conn.execute(
            """
            INSERT INTO bar_cards (id, content, type, is_adults_only)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (id) DO NOTHING
            """,
            card_id, content, card_type, is_adults_only,
        )
    print(f"OK {len(CARDS)} bar_cards seeded")


async def _connect_and_seed():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        await seed(conn)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(_connect_and_seed())
