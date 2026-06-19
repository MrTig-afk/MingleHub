"""Seed data for the trivia_questions table (gamespec.md: Round Type 2 -- Trivia).

Uses deterministic UUIDs via uuid.uuid5 (same pattern as seed_bar_cards.py) so
re-running is idempotent via ON CONFLICT DO NOTHING.

correct_option is stored server-side only and is NEVER sent to the browser
before a phone answers (security.md / coding-practices MingleHub Rules).

Entry point: async def seed(conn) -- called from migrate.py and conftest.py.
Category keys match gamespec Analytics (trivia_category):
general | pop_culture | music | sport | food_drink | aussie | adults_only.
"""
import asyncio
import os
import sys
import uuid

import asyncpg
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
load_dotenv(os.path.join(ROOT, 'api', '.env'))

# Deterministic namespace UUID for trivia_questions seed.
_NS = uuid.UUID("17171a00-0000-0000-0000-000000000000")


def _qid(key: str) -> str:
    return str(uuid.uuid5(_NS, key))


# (key, question, a, b, c, d, correct_option, category, is_adults_only)
QUESTIONS = [
    # General Knowledge
    ("general-1", "How many continents are there on Earth?",
     "5", "6", "7", "8", "C", "general", False),
    ("general-2", "What is the largest planet in our solar system?",
     "Earth", "Saturn", "Jupiter", "Neptune", "C", "general", False),
    ("general-3", "What is the chemical symbol for gold?",
     "Go", "Gd", "Au", "Ag", "C", "general", False),
    ("general-4", "How many sides does a hexagon have?",
     "five", "six", "seven", "eight", "B", "general", False),
    ("general-5", "Which ocean is the largest?",
     "Atlantic", "Indian", "Arctic", "Pacific", "D", "general", False),
    # Pop Culture
    ("pop-1", "In the Harry Potter series, what position does Harry play in Quidditch?",
     "Keeper", "Seeker", "Beater", "Chaser", "B", "pop_culture", False),
    ("pop-2", "What is the name of the coffee shop in the TV show Friends?",
     "Central Perk", "Java Joe's", "The Grind", "Cafe Nervosa", "A", "pop_culture", False),
    ("pop-3", "Which superhero is known as the 'Caped Crusader'?",
     "Superman", "Spider-Man", "Batman", "Iron Man", "C", "pop_culture", False),
    ("pop-4", "What colour is the ogre Shrek?",
     "Blue", "Green", "Purple", "Grey", "B", "pop_culture", False),
    # Music
    ("music-1", "Which band released the album 'Abbey Road'?",
     "The Rolling Stones", "The Beatles", "Led Zeppelin", "The Who", "B", "music", False),
    ("music-2", "How many strings does a standard guitar have?",
     "four", "five", "six", "seven", "C", "music", False),
    ("music-3", "Which artist is known as the 'Queen of Pop'?",
     "Madonna", "Beyonce", "Adele", "Whitney Houston", "A", "music", False),
    ("music-4", "What instrument has 88 keys?",
     "Organ", "Harpsichord", "Piano", "Accordion", "C", "music", False),
    # Sport
    ("sport-1", "How many players are on a soccer team on the field at one time?",
     "9", "10", "11", "12", "C", "sport", False),
    ("sport-2", "In which sport would you perform a 'slam dunk'?",
     "Volleyball", "Basketball", "Tennis", "Cricket", "B", "sport", False),
    ("sport-3", "How many rings are on the Olympic flag?",
     "four", "five", "six", "seven", "B", "sport", False),
    ("sport-4", "In tennis, what is a score of zero called?",
     "Nil", "Love", "Duck", "Blank", "B", "sport", False),
    # Food & Drink
    ("food-1", "What is the main ingredient in guacamole?",
     "Tomato", "Avocado", "Pea", "Cucumber", "B", "food_drink", False),
    ("food-2", "Which fruit is traditionally used to make wine?",
     "Apple", "Grape", "Pear", "Cherry", "B", "food_drink", False),
    ("food-3", "What spirit is the base of a Mojito?",
     "Vodka", "Gin", "Rum", "Tequila", "C", "food_drink", False),
    ("food-4", "Espresso originates from which country?",
     "France", "Italy", "Spain", "Portugal", "B", "food_drink", False),
    # Aussie Specific
    ("aussie-1", "What is the capital city of Australia?",
     "Sydney", "Melbourne", "Canberra", "Brisbane", "C", "aussie", False),
    ("aussie-2", "What Australian animal lays eggs but is a mammal?",
     "Koala", "Wombat", "Platypus", "Dingo", "C", "aussie", False),
    ("aussie-3", "Which Australian sport is played with an oval ball on an oval field?",
     "Rugby League", "Aussie Rules Football", "Cricket", "Netball", "B", "aussie", False),
    ("aussie-4", "What is the name of Australia's largest coral reef system?",
     "Ningaloo Reef", "Great Barrier Reef", "Coral Sea Reef", "Whitsunday Reef",
     "B", "aussie", False),
    # Adults Only (only used when the session has Adults Only ON)
    ("adults-1", "What is the recommended standard drink count Australia uses to measure alcohol?",
     "Standard drinks", "Units", "Shots", "Pours", "A", "adults_only", True),
    ("adults-2", "A classic Margarita is traditionally served with what on the rim?",
     "Sugar", "Salt", "Chilli", "Nothing", "B", "adults_only", True),
]


async def seed(conn):
    """Upsert trivia_questions seed rows. Safe to call multiple times."""
    for key, q, a, b, c, d, correct, category, adults in QUESTIONS:
        await conn.execute(
            """
            INSERT INTO trivia_questions
                (id, question, option_a, option_b, option_c, option_d,
                 correct_option, category, is_adults_only)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (id) DO NOTHING
            """,
            _qid(key), q, a, b, c, d, correct, category, adults,
        )
    print(f"OK {len(QUESTIONS)} trivia_questions seeded")


async def _connect_and_seed():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        await seed(conn)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(_connect_and_seed())
