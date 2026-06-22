"""Tests for the theme weighting engine: deterministic weighted round-type
selection + active-theme resolution.
"""
import asyncio
import os
import uuid

import asyncpg

from api.dev_fixtures import VENUE_A_ID
from api.services.theme_service import pick_round_type, resolve_active_theme

ALL_TRIVIA = {"round_types": {"chooser": 0, "roulette": 0, "trivia": 1}}
ALL_ROULETTE = {"round_types": {"chooser": 0, "roulette": 1, "trivia": 0}}
RANDOM = {"round_types": {"chooser": 1, "roulette": 1, "trivia": 1}}
SID = "00000000-0000-0000-0000-000000000abc"


def test_all_trivia_theme_always_trivia():
    picks = {pick_round_type(ALL_TRIVIA, n, SID, active_count=4) for n in range(1, 200)}
    assert picks == {"trivia"}


def test_all_roulette_theme_always_roulette():
    picks = {pick_round_type(ALL_ROULETTE, n, SID, active_count=4) for n in range(1, 200)}
    assert picks == {"roulette"}


def test_multi_player_types_fall_back_to_chooser_when_solo():
    # all-trivia but only 1 player -> trivia ineligible -> chooser
    assert pick_round_type(ALL_TRIVIA, 1, SID, active_count=1) == "chooser"
    assert pick_round_type(ALL_ROULETTE, 5, SID, active_count=1) == "chooser"


def test_exclude_trivia_never_picks_trivia():
    picks = {pick_round_type(RANDOM, n, SID, active_count=4, exclude_trivia=True)
             for n in range(1, 200)}
    assert "trivia" not in picks


def test_deterministic_same_inputs_same_output():
    a = pick_round_type(RANDOM, 7, SID, active_count=4)
    b = pick_round_type(RANDOM, 7, SID, active_count=4)
    assert a == b


def test_random_theme_distribution_is_balanced():
    counts = {"chooser": 0, "roulette": 0, "trivia": 0}
    n = 900
    for r in range(1, n + 1):
        counts[pick_round_type(RANDOM, r, SID, active_count=4)] += 1
    # Each ~1/3; allow a generous band so it isn't flaky.
    for t in counts:
        assert 0.25 < counts[t] / n < 0.42, counts


def test_zero_weights_fall_back_to_chooser():
    assert pick_round_type({"round_types": {"chooser": 0, "roulette": 0, "trivia": 0}},
                           1, SID, active_count=4) == "chooser"


# --- resolve_active_theme (DB) ---

def _run(coro_fn):
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await coro_fn(conn)
        finally:
            await conn.close()
    return asyncio.run(_q())


def _set_theme(venue_id, theme_key):
    async def _q(conn):
        await conn.execute(
            """INSERT INTO nightly_theme_selections (id, venue_id, selected_date, theme_key)
               VALUES ($1, $2, (date_trunc('day',(NOW() AT TIME ZONE 'Australia/Melbourne')
                   - INTERVAL '4 hours'))::date, $3)
               ON CONFLICT (venue_id, selected_date) DO UPDATE SET theme_key = $3""",
            str(uuid.uuid4()), venue_id, theme_key)
    _run(_q)


def _clear_theme(venue_id):
    _run(lambda c: c.execute("DELETE FROM nightly_theme_selections WHERE venue_id = $1", venue_id))


def test_resolve_defaults_to_random_when_unset():
    _clear_theme(VENUE_A_ID)
    theme = _run(lambda c: resolve_active_theme(c, VENUE_A_ID))
    assert theme["theme_key"] == "random"
    assert "round_types" in theme["weighting"]


def test_resolve_returns_selected_theme():
    try:
        _set_theme(VENUE_A_ID, "all_trivia")
        theme = _run(lambda c: resolve_active_theme(c, VENUE_A_ID))
        assert theme["theme_key"] == "all_trivia"
        # weighting usable by the picker (dict, not raw JSON string)
        assert pick_round_type(theme["weighting"], 1, SID, active_count=4) == "trivia"
    finally:
        _clear_theme(VENUE_A_ID)
