import asyncio
import os
import random
import sys
import uuid

import asyncpg
import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
load_dotenv(os.path.join(ROOT, "api", ".env"))

from api.dev_fixtures import OWNER_A_CLERK_ID, VENUE_A_ID  # noqa: E402
from scripts.seed_bar_cards import seed as seed_bar_cards  # noqa: E402
from scripts.seed_platform import seed as seed_platform  # noqa: E402
from scripts.seed_roulette_cards import seed as seed_roulette_cards  # noqa: E402
from scripts.seed_trivia_questions import seed as seed_trivia_questions  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _seed_dev_fixtures():
    """Upserts dev venues/users/tables before the suite runs.

    Uses a standalone connection (connect -> seed -> close) rather than
    the app's pool, so it doesn't bind to whatever event loop the
    TestClient ends up using for requests.
    """
    async def _run():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await seed_platform(conn)
            await seed_bar_cards(conn)
            await seed_trivia_questions(conn)
            await seed_roulette_cards(conn)
        finally:
            await conn.close()

    asyncio.run(_run())


@pytest.fixture(scope="module")
def client():
    from api.index import app
    with TestClient(app) as c:
        yield c
    # The asyncpg pool (api.db._pool) is bound to this module's event loop,
    # which TestClient just tore down. Reset it so the next test module's
    # TestClient (a new event loop) creates a fresh pool instead of reusing
    # a dead one — otherwise every DB call fails with "Event loop is closed".
    import api.db
    api.db._pool = None
    # api.security.limiter is a process-wide singleton, so rate-limit
    # counters would otherwise carry over between test modules (e.g.
    # test_lobby.py's many /api/patron/tap calls exhausting the 30/minute
    # budget before test_patron_tap.py's own tap tests run).
    from api.security import limiter
    limiter.reset()


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """Reset the rate-limiter before every test so per-test tap volume
    never exhausts the module-level budget (e.g. simulate-tap 30/minute).

    This runs before the test body, giving each test a clean slate.
    The module-scoped teardown in `client` also resets, but that only
    fires between modules, not between individual tests.
    """
    from api.security import limiter
    limiter.reset()


@pytest.fixture
def api_key_header():
    return {"X-API-Key": os.environ["API_KEY"]}


def dev_login(client, api_key_header, clerk_user_id):
    resp = client.post(
        "/api/auth/dev-login",
        headers=api_key_header,
        json={"clerk_user_id": clerk_user_id},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def fresh_tag_uid():
    # A real tag UID is fixed in hardware — tests use a random one each run
    # so reseeding/rerunning the suite never collides with a previous tag_uid.
    return f"test-tag-{uuid.uuid4()}"


@pytest.fixture(scope="module")
def owner_a_token(client):
    # One login reused by every test needing it in a module — logging in
    # fresh per test trips dev-login's 20/minute rate limit once combined
    # with other test modules' own dev-logins in the same run.
    return dev_login(client, {"X-API-Key": os.environ["API_KEY"]}, OWNER_A_CLERK_ID)


def pair_tag(client, api_key_header, token, table_number):
    """Pairs a fresh tag to a table and returns its tag_uid."""
    headers = {**api_key_header, "Authorization": f"Bearer {token}"}
    tag_uid = fresh_tag_uid()
    resp = client.post(
        "/api/dashboard/pair-tag",
        headers=headers,
        json={"tag_uid": tag_uid, "table_number": table_number},
    )
    assert resp.status_code == 200, resp.text
    return tag_uid


def simulate_tap(client, api_key_header, tag_uid, counter):
    resp = client.post(
        "/api/dev/simulate-tap",
        headers=api_key_header,
        json={"tag_uid": tag_uid, "counter": counter},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["sig"]


def _create_table(content_ceiling="standard"):
    """A brand-new table on venue A, isolated to one test.

    Lobby/session tests can't safely share table 1/2 across test
    functions — leftover lobbies and active sessions from one test would
    leak into the next (no per-test transaction rollback here, see
    `client`/`_seed_dev_fixtures` above). Each test gets its own
    table_number instead, torn down afterwards.
    """
    table_number = 100_000 + random.randint(0, 899_999)  # well clear of seeded tables 1/2
    table_id = str(uuid.uuid4())

    async def _create():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute(
                "INSERT INTO tables (id, venue_id, table_number, content_ceiling) VALUES ($1, $2, $3, $4)",
                table_id, VENUE_A_ID, table_number, content_ceiling,
            )
        finally:
            await conn.close()
    asyncio.run(_create())
    return {"table_id": table_id, "table_number": table_number, "venue_slug": "fifty-five-bar"}


def _teardown_table(table_id):
    async def _teardown():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            # Order matters: table_lobbies.converted_session_id references
            # game_sessions, so lobbies must go before sessions. Likewise
            # game_sessions.last_hot_seat_player_id references game_players,
            # so that must be cleared before game_players rows can be deleted.
            # rounds.selected_player_id references game_players, so rounds
            # must be deleted before game_players.
            await conn.execute(
                "UPDATE game_sessions SET last_hot_seat_player_id = NULL WHERE table_id = $1", table_id
            )
            # Trivia rows reference game_players / game_sessions / trivia_rounds,
            # so they must be cleared before those parent rows can be deleted.
            sessions_subq = "SELECT id FROM game_sessions WHERE table_id = $1"
            await conn.execute(
                f"DELETE FROM trivia_answers WHERE trivia_round_id IN "
                f"(SELECT id FROM trivia_rounds WHERE session_id IN ({sessions_subq}))",
                table_id,
            )
            await conn.execute(
                f"DELETE FROM trivia_participants WHERE trivia_round_id IN "
                f"(SELECT id FROM trivia_rounds WHERE session_id IN ({sessions_subq}))",
                table_id,
            )
            await conn.execute(
                f"DELETE FROM trivia_rounds WHERE session_id IN ({sessions_subq})",
                table_id,
            )
            await conn.execute(
                "DELETE FROM roulette_votes WHERE round_id IN "
                "(SELECT id FROM rounds WHERE session_id IN (SELECT id FROM game_sessions WHERE table_id = $1))",
                table_id,
            )
            await conn.execute(
                """
                DELETE FROM rounds
                WHERE session_id IN (SELECT id FROM game_sessions WHERE table_id = $1)
                """,
                table_id,
            )
            await conn.execute(
                "DELETE FROM game_players WHERE session_id IN (SELECT id FROM game_sessions WHERE table_id = $1)",
                table_id,
            )
            await conn.execute(
                "DELETE FROM table_lobby_phones WHERE lobby_id IN (SELECT id FROM table_lobbies WHERE table_id = $1)",
                table_id,
            )
            await conn.execute("DELETE FROM table_lobbies WHERE table_id = $1", table_id)
            await conn.execute("DELETE FROM game_sessions WHERE table_id = $1", table_id)
            await conn.execute("DELETE FROM nfc_tags WHERE table_id = $1", table_id)
            await conn.execute("DELETE FROM table_tap_log WHERE table_id = $1", table_id)
            await conn.execute("DELETE FROM tables WHERE id = $1", table_id)
        finally:
            await conn.close()
    asyncio.run(_teardown())


@pytest.fixture
def fresh_table():
    """A standard-ceiling table — Adults Only can never be enabled here."""
    info = _create_table("standard")
    yield info
    _teardown_table(info["table_id"])


@pytest.fixture
def adults_allowed_table():
    """A table whose content ceiling permits Adults Only, for testing the
    Setup screen's toggle gating (gamespec: Adults Only Content Controls)."""
    info = _create_table("adults_allowed")
    yield info
    _teardown_table(info["table_id"])


@pytest.fixture
def venue_a_restricts_adult_content():
    """Temporarily flips venue A's global "Restrict adult content" switch
    ON, to test that it overrides a table's content_ceiling (gamespec:
    venue-wide toggle is layer 1, takes precedence over everything)."""
    async def _set(value):
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute("UPDATE venues SET restrict_adult_content = $1 WHERE id = $2", value, VENUE_A_ID)
        finally:
            await conn.close()
    asyncio.run(_set(True))
    yield
    asyncio.run(_set(False))
