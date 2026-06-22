"""Theme weighting engine (gamespec "Theme System"). Replaces the fixed
Chooser->Roulette->Trivia cadence with a theme-weighted draw.

The round-type pick is DETERMINISTIC for a given (session, round_number, theme)
so every phone computes/sees the same type and it never flickers across polls —
it's seeded by a hash of the session id + round number, then drawn from the
active theme's round-type weights. Roulette/Trivia need >= 2 active players (else fall back to Chooser). An optional
exclude_trivia guard implements the "just-abandoned Trivia gather excluded from
the immediate next pick" rule; it's available + tested but not yet wired to the
live abandonment signal (the old cadence didn't implement it either).
"""
import hashlib
import json

VENUE_TIMEZONE = "Australia/Melbourne"  # mirrors dashboard_router.VENUE_TIMEZONE

DEFAULT_THEME_KEY = "random"
MIN_PLAYERS_MULTI = 2  # Roulette + Trivia both require >= 2 active players


def _unit_seed(session_id: str, round_number: int) -> float:
    """Stable pseudo-random fraction in [0, 1) for this exact round."""
    h = hashlib.sha256(f"{session_id}:{round_number}".encode()).hexdigest()
    return (int(h[:15], 16) % 1_000_000) / 1_000_000.0


def pick_round_type(weighting: dict, round_number: int, session_id: str,
                    active_count: int, exclude_trivia: bool = False) -> str:
    """Deterministic theme-weighted round type for this round. Falls back to
    'chooser' when no eligible weighted type remains (too few players, or the
    theme + exclusions zero everything out)."""
    weights = dict((weighting or {}).get("round_types", {}))
    eligible = {}
    for rtype, w in weights.items():
        if w is None or w <= 0:
            continue
        if rtype in ("roulette", "trivia") and active_count < MIN_PLAYERS_MULTI:
            continue
        if rtype == "trivia" and exclude_trivia:
            continue
        eligible[rtype] = float(w)

    if not eligible:
        return "chooser"

    total = sum(eligible.values())
    target = _unit_seed(session_id, round_number) * total
    cumulative = 0.0
    for rtype in sorted(eligible):  # sorted -> stable iteration order
        cumulative += eligible[rtype]
        if target < cumulative:
            return rtype
    return sorted(eligible)[-1]  # float-rounding guard


def pick_card_category(weighting: dict, round_number: int, session_id: str,
                       allowed: list) -> str | None:
    """Theme-weighted Chooser card category, restricted to `allowed` (the
    categories actually available given adult-content rules). Returns None when
    nothing is allowed (caller falls back to its own pool)."""
    weights = dict((weighting or {}).get("card_categories", {}))
    eligible = {c: float(weights.get(c, 1)) for c in allowed if weights.get(c, 1) > 0}
    if not eligible:
        return None
    total = sum(eligible.values())
    # Offset the seed so the category draw is independent of the round-type draw.
    target = _unit_seed(session_id, round_number * 7 + 3) * total
    cumulative = 0.0
    for c in sorted(eligible):
        cumulative += eligible[c]
        if target < cumulative:
            return c
    return sorted(eligible)[-1]


async def resolve_active_theme(conn, venue_id) -> dict:
    """The venue's theme for tonight's play-date (4am boundary), or the default
    'random' theme if none is selected. Returns {theme_key, display_name,
    weighting, trivia_category_bias}."""
    row = await conn.fetchrow(
        """
        SELECT t.theme_key, t.display_name, t.weighting, t.trivia_category_bias
        FROM nightly_theme_selections nts
        JOIN themes t ON t.theme_key = nts.theme_key
        WHERE nts.venue_id = $1
          AND nts.selected_date = (date_trunc('day',
                (NOW() AT TIME ZONE $2) - INTERVAL '4 hours'))::date
        """,
        venue_id, VENUE_TIMEZONE,
    )
    if not row:
        row = await conn.fetchrow(
            "SELECT theme_key, display_name, weighting, trivia_category_bias "
            "FROM themes WHERE theme_key = $1", DEFAULT_THEME_KEY)
    if not row:
        return {
            "theme_key": DEFAULT_THEME_KEY, "display_name": "Random Alternation",
            "weighting": {"round_types": {"chooser": 1, "roulette": 1, "trivia": 1}},
            "trivia_category_bias": None,
        }
    out = dict(row)
    # The pool registers a jsonb codec (dict in/out); a raw asyncpg connection
    # (tests/scripts) does not, so parse strings defensively.
    for key in ("weighting", "trivia_category_bias"):
        if isinstance(out.get(key), str):
            out[key] = json.loads(out[key])
    return out
