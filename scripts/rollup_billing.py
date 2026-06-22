"""Nightly billing rollup — recompute the current month's invoices from finalized
sessions. Idempotent: safe to run repeatedly (recomputes the month from scratch;
'paid' invoices are left untouched; is_test venues excluded).

Run from a scheduler (cron / Vercel Cron hitting a protected endpoint / GitHub
Action). Local:
    DEV_MODE=true PYTHONPATH=. python scripts/rollup_billing.py
"""
import asyncio
import os

import asyncpg
from dotenv import load_dotenv

load_dotenv("api/.env")
from api.services.billing_service import recompute_invoices  # noqa: E402
from api.services.venue_lifecycle_service import check_dunning_suspensions  # noqa: E402


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        async with conn.transaction():
            summary = await recompute_invoices(conn)
        print(
            f"Billing rollup OK for {summary['period_start']}: "
            f"{summary['invoices']} invoice(s), {summary['line_items']} line item(s), "
            f"{summary['skipped_paid']} paid-skipped"
        )
        suspended_count = await check_dunning_suspensions(conn)
        print(f"Dunning sweep: {suspended_count} venue(s) newly suspended")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
