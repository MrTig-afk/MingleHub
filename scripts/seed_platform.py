import asyncio
import asyncpg
import os
import sys
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
load_dotenv(os.path.join(ROOT, 'api', '.env'))

from api.dev_fixtures import (  # noqa: E402
    VENUE_A_ID, VENUE_B_ID, VENUE_A_TABLE_ID, VENUE_A_TABLE_2_ID, VENUE_B_TABLE_ID,
    OWNER_A_ID, STAFF_A_ID, OWNER_B_ID, ADMIN_ID,
    OWNER_A_CLERK_ID, STAFF_A_CLERK_ID, OWNER_B_CLERK_ID, ADMIN_CLERK_ID,
)

# Two separate venues so BOLA (venue isolation) tests have real cross-venue
# data to prove isolation against — not just a single-tenant happy path.
VENUES = [
    (VENUE_A_ID, "Fifty Five Bar", "fifty-five-bar", "bar",
     "55 Elizabeth Street, Melbourne VIC 3000", -37.8169, 144.9648),
    (VENUE_B_ID, "The Last Chance", "the-last-chance", "pub",
     "238 Victoria Street, Melbourne VIC 3000", -37.8076, 144.9712),
]

TABLES = [
    (VENUE_A_TABLE_ID, VENUE_A_ID, 1),
    (VENUE_A_TABLE_2_ID, VENUE_A_ID, 2),
    (VENUE_B_TABLE_ID, VENUE_B_ID, 1),
]

# (id, clerk_user_id, venue_id, role)
USERS = [
    (OWNER_A_ID, OWNER_A_CLERK_ID, VENUE_A_ID, "venue_owner"),
    (STAFF_A_ID, STAFF_A_CLERK_ID, VENUE_A_ID, "venue_staff"),
    (OWNER_B_ID, OWNER_B_CLERK_ID, VENUE_B_ID, "venue_owner"),
    (ADMIN_ID, ADMIN_CLERK_ID, None, "admin"),
]


async def seed(conn):
    """Upserts the dev fixture rows using an already-open connection.

    Reused by the test suite (api/tests/conftest.py) so tests and the
    manual `python scripts/seed_platform.py` CLI share one definition
    instead of two copies drifting apart.
    """
    for id_, name, slug, venue_type, address, lat, lng in VENUES:
        await conn.execute(
            """
            INSERT INTO venues (id, name, slug, venue_type, address, latitude, longitude)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (id) DO UPDATE SET name = $2, slug = $3, venue_type = $4,
                address = $5, latitude = $6, longitude = $7
            """,
            id_, name, slug, venue_type, address, lat, lng,
        )
    print(f"OK {len(VENUES)} dev venues seeded")

    for id_, venue_id, table_number in TABLES:
        await conn.execute(
            """
            INSERT INTO tables (id, venue_id, table_number)
            VALUES ($1, $2, $3)
            ON CONFLICT (id) DO UPDATE SET venue_id = $2, table_number = $3
            """,
            id_, venue_id, table_number,
        )
    print(f"OK {len(TABLES)} dev tables seeded")

    for id_, clerk_user_id, venue_id, role in USERS:
        await conn.execute(
            """
            INSERT INTO users (id, clerk_user_id, venue_id, role)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (id) DO UPDATE SET clerk_user_id = $2, venue_id = $3, role = $4
            """,
            id_, clerk_user_id, venue_id, role,
        )
    print(f"OK {len(USERS)} dev users seeded")


async def _connect_and_seed():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        await seed(conn)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(_connect_and_seed())
