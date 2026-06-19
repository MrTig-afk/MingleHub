import json

import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()

_pool = None


async def _init_connection(conn):
    # asyncpg doesn't serialize Python objects for json/jsonb columns by
    # default — without this, every call site would need its own
    # json.dumps/loads (easy to forget once, on either the read or write
    # side). Registering it once here makes jsonb columns (e.g.
    # game_sessions.player_names) transparent dict/list in and out.
    for type_name in ("json", "jsonb"):
        await conn.set_type_codec(
            type_name,
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
            format="text",
        )


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
            init=_init_connection,
            # Required when DATABASE_URL points at a transaction-mode pooler
            # (Neon's pgBouncer endpoint, used by the Vercel serverless
            # deploy): asyncpg's prepared-statement cache breaks under
            # transaction pooling. Harmless on direct connections.
            statement_cache_size=0,
        )
    return _pool
