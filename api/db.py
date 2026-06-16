import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()

_pool = None


async def get_pool():
    global _pool
    if _pool is None:
        # Neon (dev/prod) requires SSL. CI uses a plain local Postgres
        # service container, which has none — DATABASE_SSL=disable lets
        # that connect without weakening the default for real deployments.
        ssl_mode = os.environ.get("DATABASE_SSL", "require")
        _pool = await asyncpg.create_pool(
            dsn=os.environ["DATABASE_URL"],
            min_size=1,
            max_size=5,
            ssl=None if ssl_mode == "disable" else ssl_mode,
        )
    return _pool
