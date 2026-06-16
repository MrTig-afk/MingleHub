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

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS packs (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                description TEXT,
                accent      TEXT NOT NULL,
                icon        TEXT,
                mode        TEXT NOT NULL DEFAULT 'party',
                created_at  TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        print("OK packs table ready")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS cards (
                id      TEXT PRIMARY KEY,
                pack_id TEXT NOT NULL REFERENCES packs(id),
                type    TEXT NOT NULL,
                text    TEXT NOT NULL,
                flavour TEXT
            )
        """)
        print("OK cards table ready")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS venues (
                id                      UUID PRIMARY KEY,
                name                    TEXT NOT NULL,
                slug                    TEXT UNIQUE NOT NULL,
                venue_type              TEXT NOT NULL CHECK (venue_type IN ('cafe', 'pub', 'bar', 'brewery', 'other')),
                billing_unit            NUMERIC NOT NULL DEFAULT 3.00,
                round_time_minutes      INTEGER NOT NULL DEFAULT 20,
                retap_interval_minutes  INTEGER NOT NULL DEFAULT 30,
                nightly_cap_weekday     NUMERIC NOT NULL DEFAULT 30,
                nightly_cap_weekend     NUMERIC NOT NULL DEFAULT 30,
                nightly_cap_holiday     NUMERIC NOT NULL DEFAULT 30,
                stripe_customer_id      TEXT,
                menu_url                TEXT,
                restrict_adult_content  BOOLEAN DEFAULT FALSE,
                is_test                 BOOLEAN DEFAULT FALSE,
                status                  TEXT NOT NULL DEFAULT 'active',
                created_at              TIMESTAMP DEFAULT NOW(),
                updated_at              TIMESTAMP DEFAULT NOW()
            )
        """)
        print("OK venues table ready")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id             UUID PRIMARY KEY,
                clerk_user_id  TEXT UNIQUE NOT NULL,
                venue_id       UUID REFERENCES venues(id),
                role           TEXT NOT NULL CHECK (role IN ('venue_owner', 'venue_staff', 'admin')),
                created_at     TIMESTAMP DEFAULT NOW(),
                -- admin accounts are platform-wide and never tied to a venue
                CHECK ((role = 'admin') = (venue_id IS NULL))
            )
        """)
        print("OK users table ready")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tables (
                id               UUID PRIMARY KEY,
                venue_id         UUID NOT NULL REFERENCES venues(id),
                table_number     INTEGER NOT NULL,
                content_ceiling  TEXT NOT NULL DEFAULT 'standard' CHECK (content_ceiling IN ('standard', 'adults_allowed')),
                created_at       TIMESTAMP DEFAULT NOW(),
                UNIQUE (venue_id, table_number)
            )
        """)
        print("OK tables table ready")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS nfc_tags (
                id                  UUID PRIMARY KEY,
                venue_id            UUID NOT NULL REFERENCES venues(id),
                table_id            UUID REFERENCES tables(id),
                tag_uid             TEXT UNIQUE NOT NULL,
                aes_key_encrypted   TEXT NOT NULL,
                status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked', 'replacement_pending')),
                counter_last_seen   BIGINT,
                paired_at           TIMESTAMP,
                created_at          TIMESTAMP DEFAULT NOW()
            )
        """)
        print("OK nfc_tags table ready")

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
