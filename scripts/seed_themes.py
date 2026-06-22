"""Seed the theme recipes (gamespec "Theme System"). Idempotent: ON CONFLICT
updates the recipe so tweaks land on re-run.

weighting = {"round_types": {chooser, roulette, trivia}, "card_categories": {...}}
Weights are relative (the picker normalises). card_categories bias the Chooser
card draw; trivia_category_bias biases Trivia question selection.

TEST themes (is_test=TRUE) force a single round type so a tester can play exactly
one game on repeat — and watch the billing block counters tick.
"""
import json
import uuid

_NS = uuid.NAMESPACE_DNS


def _id(key):
    return str(uuid.uuid5(_NS, f"minglehub.theme.{key}"))


# (theme_key, display_name, round_types, card_categories, trivia_bias, is_test)
_CATS = ["icebreaker", "truth", "dare", "compliment", "challenge", "drink", "flirty"]


def _cats(**overrides):
    base = {c: 1 for c in _CATS}
    base.update(overrides)
    return base


THEMES = [
    ("random", "Random Alternation",
     {"chooser": 1, "roulette": 1, "trivia": 1}, _cats(flirty=0), None, False),
    ("last_drinks", "Last Drinks",
     {"chooser": 3, "roulette": 4, "trivia": 1}, _cats(drink=5, dare=2, flirty=0), None, False),
    ("party_night", "Party Night",
     {"chooser": 3, "roulette": 5, "trivia": 1}, _cats(dare=4, challenge=3, flirty=0), None, False),
    ("date_night", "Date Night",
     {"chooser": 5, "roulette": 1, "trivia": 1}, _cats(icebreaker=4, compliment=4, flirty=4, dare=0), None, False),
    ("sunday_sesh", "Sunday Sesh",
     {"chooser": 4, "roulette": 1, "trivia": 2}, _cats(icebreaker=3, challenge=3, drink=0, flirty=0), None, False),
    ("aussie_night", "Aussie Night",
     {"chooser": 2, "roulette": 1, "trivia": 4}, _cats(challenge=3, flirty=0),
     {"aussie": 5, "general": 2, "sport": 2, "music": 1, "pop_culture": 1, "food_drink": 1}, False),
    ("pop_culture_night", "Pop Culture Night",
     {"chooser": 2, "roulette": 1, "trivia": 4}, _cats(flirty=0),
     {"pop_culture": 6, "music": 3, "general": 2, "sport": 1, "aussie": 1, "food_drink": 1}, False),
    ("mingle_night", "Mingle Night",
     {"chooser": 5, "roulette": 1, "trivia": 1}, _cats(icebreaker=5, truth=4, dare=0, flirty=0), None, False),
    ("birthday_bash", "Birthday Bash",
     {"chooser": 3, "roulette": 4, "trivia": 1}, _cats(dare=4, challenge=4, drink=2, flirty=0), None, False),
    # --- TEST themes: force a single round type (is_test) ---
    ("all_trivia", "All Trivia (test)",
     {"chooser": 0, "roulette": 0, "trivia": 1}, _cats(), None, True),
    ("all_roulette", "All Roulette (test)",
     {"chooser": 0, "roulette": 1, "trivia": 0}, _cats(), None, True),
    ("all_chooser", "All Chooser (test)",
     {"chooser": 1, "roulette": 0, "trivia": 0}, _cats(), None, True),
]


async def seed(conn):
    for key, name, rounds, cats, bias, is_test in THEMES:
        weighting = json.dumps({"round_types": rounds, "card_categories": cats})
        bias_json = json.dumps(bias) if bias is not None else None
        await conn.execute(
            """
            INSERT INTO themes (id, theme_key, display_name, weighting, trivia_category_bias, is_test)
            VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6)
            ON CONFLICT (theme_key) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                weighting = EXCLUDED.weighting,
                trivia_category_bias = EXCLUDED.trivia_category_bias,
                is_test = EXCLUDED.is_test
            """,
            _id(key), key, name, weighting, bias_json, is_test,
        )
