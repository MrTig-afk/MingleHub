import asyncio
import asyncpg
import os
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, 'api', '.env'))


async def migrate():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS premium_interest (
                id         SERIAL PRIMARY KEY,
                email      TEXT NOT NULL UNIQUE,
                mode       TEXT,
                trigger    TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        print("OK premium_interest table ready")

        schema = await conn.fetch("""
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_name = 'premium_interest'
            ORDER BY ordinal_position
        """)
        print("\nSchema:")
        for col in schema:
            print(f"  {col['column_name']:15} {col['data_type']:30} nullable={col['is_nullable']} default={col['column_default']}")
    finally:
        await conn.close()


asyncio.run(migrate())
