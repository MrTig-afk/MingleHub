import asyncio
import os
import sys

import asyncpg
import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
load_dotenv(os.path.join(ROOT, "api", ".env"))

from scripts.seed_platform import seed as seed_platform  # noqa: E402


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
        finally:
            await conn.close()

    asyncio.run(_run())


@pytest.fixture(scope="module")
def client():
    from api.index import app
    with TestClient(app) as c:
        yield c


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
