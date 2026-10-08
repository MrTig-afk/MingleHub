"""Nightly billing rollup — recompute the current month's invoices from finalized
sessions, run dunning, then the month tail: recompute LAST month's still-pending
invoices for the first few days after it closes. Idempotent: safe to run
repeatedly ('paid' and final invoices are left untouched; is_test venues excluded).

Run from a scheduler (cron / Vercel Cron hitting a protected endpoint / GitHub
Action). Local:
    DEV_MODE=true PYTHONPATH=. python scripts/rollup_billing.py
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone

import asyncpg
from dotenv import load_dotenv

load_dotenv("api/.env")
from api.services.billing_service import recompute_invoices, sweep_abandoned_sessions  # noqa: E402
from api.services.venue_lifecycle_service import check_dunning_suspensions  # noqa: E402


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        swept = await sweep_abandoned_sessions(conn)
        print(f"Abandoned sweep: {swept} session(s) ended, all ended sessions finalized")
        async with conn.transaction():
            summary = await recompute_invoices(conn)
        print(
            f"Billing rollup OK for {summary['period_start']}: "
            f"{summary['invoices']} invoice(s), {summary['line_items']} line item(s), "
            f"{summary['skipped_paid']} skipped (paid/final)"
        )
        suspended_count = await check_dunning_suspensions(conn)
        print(f"Dunning sweep: {suspended_count} venue(s) newly suspended")
        # Month tail (owner, 2026-10-08): the month 3 days ago is LAST month on the
        # runs of the 1st-4th, so sessions that ended after the turnover (or a run
        # missed on the 1st) still reach its invoice; otherwise it is the current
        # month again. Only still-pending invoices: one already sent to Stripe or
        # failed is never rewritten. Own transaction, last, so a failure here never
        # undoes tonight's billing or dunning.
        async with conn.transaction():
            tail = await recompute_invoices(
                conn, ref_ts=datetime.now(timezone.utc) - timedelta(days=3), pending_only=True)
        print(
            f"Month-tail rollup OK for {tail['period_start']}: "
            f"{tail['invoices']} invoice(s), {tail['line_items']} line item(s), "
            f"{tail['skipped_paid']} skipped (paid/final/sent/failed)"
        )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
