"""Nightly analytics rollup — recompute recent per-venue daily stats into
venue_daily_stats. Idempotent: safe to run repeatedly. Pair it with the billing
rollup on the same nightly schedule.

    DEV_MODE=true PYTHONPATH=. python scripts/rollup_analytics.py
"""
import asyncio
import os

import asyncpg
from dotenv import load_dotenv

load_dotenv("api/.env")
from api.services.analytics_service import recompute_daily_stats  # noqa: E402


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        async with conn.transaction():
            summary = await recompute_daily_stats(conn)
        print(
            f"Analytics rollup OK: {summary['rows_upserted']} day-rows across "
            f"{summary['venues']} venue(s), last {summary['window_days']} days"
        )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
