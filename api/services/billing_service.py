"""Usage billing — duration-based, per the gamespec billing model.

A venue is billed per **block of active play**. One block = 15 minutes of active
play (`BLOCK_SECONDS`), priced at the venue's `billing_unit` ($3 by default).

Two time measures are frozen on each session when it ends:
  - active_span_seconds: started_at -> last_activity_at. Excludes the dead idle
    tail before an auto-end (we stop the clock at the last real action, not at
    ended_at). This is the BILLING basis.
  - active_play_seconds: true play time with idle gaps > IDLE_CUTOFF_SECONDS
    (2 min) removed. ANALYTICS only — lets us check/revise the block size later.

billable_blocks = floor(active_span / 15 min), but only if the session actually
played >= 1 resolved round (a lobby-only sitting bills nothing).

Invoices roll up per venue per month; line items roll up per table per play-date
(4am-boundary night), capped per table per night at nightly_cap / billing_unit
blocks. is_test venues are excluded from invoices entirely.
"""
import uuid

BLOCK_SECONDS = 15 * 60        # one billable block = 15 minutes of active play
IDLE_CUTOFF_SECONDS = 2 * 60   # gaps longer than this are "stepped away", not play

# Shared SQL: per (table, play_date) aggregation with snapshot fallback.
# Parameters: $1=month_start_utc, $2=next_month_start_utc, $3=venue_id,
#             $4=tz, $5=fallback_unit, $6=fallback_cap_wd, $7=fallback_cap_we
#
# (array_agg(col ORDER BY started_at))[1] takes the night's FIRST session's
# snapshot. A pre-migration first session has NULL snapshots, so the COALESCE
# falls back to the venue's current values passed as $5/$6/$7 (today's rule).
NIGHT_BLOCKS_SQL = """
    SELECT
        gs.table_id,
        date_trunc('day', (gs.started_at AT TIME ZONE 'UTC' AT TIME ZONE $4)
            - INTERVAL '4 hours')::date AS play_date,
        EXTRACT(DOW FROM date_trunc('day', (gs.started_at AT TIME ZONE 'UTC'
            AT TIME ZONE $4) - INTERVAL '4 hours'))::int AS dow,
        COALESCE(SUM(gs.billable_blocks), 0)::int AS raw_blocks,
        COALESCE(
            (array_agg(gs.snap_billing_unit ORDER BY gs.started_at))[1],
            $5
        ) AS eff_unit,
        COALESCE(
            (array_agg(gs.snap_nightly_cap_weekday ORDER BY gs.started_at))[1],
            $6
        ) AS eff_cap_wd,
        COALESCE(
            (array_agg(gs.snap_nightly_cap_weekend ORDER BY gs.started_at))[1],
            $7
        ) AS eff_cap_we
    FROM game_sessions gs
    WHERE gs.venue_id = $3
      AND gs.started_at >= $1 AND gs.started_at < $2
      AND gs.ended_at IS NOT NULL
    GROUP BY gs.table_id, play_date, dow
    HAVING COALESCE(SUM(gs.billable_blocks), 0) > 0
"""


def night_terms(row) -> tuple:
    """(unit, cap_blocks) for one NIGHT_BLOCKS_SQL row. Shared by the invoice and
    the owner's billing estimate so the two can never disagree."""
    unit = row["eff_unit"]
    cap = row["eff_cap_we"] if row["dow"] in (0, 6) else row["eff_cap_wd"]  # 0=Sun, 6=Sat
    return unit, cap_blocks(cap, unit)


def cap_blocks(nightly_cap, billing_unit) -> int:
    """Max billable blocks per table per night = cap dollars / unit price.
    e.g. $30 cap / $3 unit = 10 blocks. Guards a zero/None cap or unit."""
    if not nightly_cap or nightly_cap <= 0:
        return 0
    if not billing_unit or billing_unit <= 0:
        return 0
    return int(nightly_cap // billing_unit)


async def finalize_session_billing(conn, session_id: str) -> None:
    """Freeze the billing measures for a session at end. Idempotent: the
    `billing_finalized_at IS NULL` guard means a second call (idle + manual end
    racing, repeated poll-driven idle ends) is a harmless no-op.

    Active-play time is derived from the session's event timestamps (start, each
    round, last activity) so no per-event accounting was needed during play —
    and it stays re-derivable with a different cutoff later.
    """
    await conn.execute(
        """
        WITH ev AS (
            SELECT started_at AS ts FROM game_sessions
                WHERE id = $1 AND started_at IS NOT NULL
            UNION ALL
            SELECT created_at FROM rounds WHERE session_id = $1
            UNION ALL
            SELECT last_activity_at FROM game_sessions
                WHERE id = $1 AND last_activity_at IS NOT NULL
        ),
        ordered AS (
            SELECT ts, LAG(ts) OVER (ORDER BY ts) AS prev FROM ev
        ),
        play AS (
            SELECT COALESCE(SUM(
                CASE WHEN EXTRACT(EPOCH FROM ts - prev) BETWEEN 0 AND $2
                     THEN EXTRACT(EPOCH FROM ts - prev) ELSE 0 END
            ), 0)::int AS secs
            FROM ordered WHERE prev IS NOT NULL
        )
        UPDATE game_sessions gs
        SET active_play_seconds = (SELECT secs FROM play),
            active_span_seconds = GREATEST(
                0, EXTRACT(EPOCH FROM gs.last_activity_at - gs.started_at))::int,
            billable_blocks = CASE
                WHEN gs.total_rounds >= 1 AND gs.started_at IS NOT NULL
                     AND gs.last_activity_at IS NOT NULL
                THEN GREATEST(0, FLOOR(
                    EXTRACT(EPOCH FROM gs.last_activity_at - gs.started_at) / $3))::int
                ELSE 0 END,
            billing_finalized_at = NOW()
        WHERE gs.id = $1 AND gs.billing_finalized_at IS NULL
        """,
        session_id, IDLE_CUTOFF_SECONDS, BLOCK_SECONDS,
    )


async def sweep_abandoned_sessions(conn) -> int:
    """Finalize all sessions idle past the retap-expired threshold.

    Called by rollup_billing.py before recompute_invoices so sessions whose
    host disappeared (never got ended_at set) appear in the monthly rollup.

    Threshold mirrors the lazy path in lobby_service.py:
        COALESCE(last_activity_at, started_at) < NOW() - (retap_interval * 60 + grace + pause)

    Idempotent: ended_at IS NULL in the WHERE clause means a second run is a no-op.
    Then finalizes every ended-but-unfinalized session, so a run interrupted
    between the two steps (here or in the lazy end paths) heals on the next run
    instead of billing $0 forever.
    Returns the count of sessions the sweep ended.
    """
    # Local import avoids circular dependency (session_service imports finalize_session_billing)
    from api.services.session_service import RETAP_GRACE_SECONDS, RETAP_PAUSE_SECONDS

    rows = await conn.fetch(
        """
        UPDATE game_sessions gs
        SET ended_at = NOW(), end_reason = 'retap_expired'
        FROM venues v
        WHERE gs.venue_id = v.id
          AND gs.ended_at IS NULL
          AND gs.started_at IS NOT NULL
          AND COALESCE(gs.last_activity_at, gs.started_at)
              < NOW() - INTERVAL '1 second' * (v.retap_interval_minutes * 60 + $1 + $2)
        RETURNING gs.id
        """,
        RETAP_GRACE_SECONDS, RETAP_PAUSE_SECONDS,
    )
    unfinalized = await conn.fetch(
        "SELECT id FROM game_sessions WHERE ended_at IS NOT NULL AND billing_finalized_at IS NULL"
    )
    for row in unfinalized:
        await finalize_session_billing(conn, str(row["id"]))
    return len(rows)


async def _period_window(conn, ref_ts, tz: str):
    """Return (month_start_utc, next_month_start_utc, period_start_date,
    period_end_date) for the calendar month containing ref_ts (or NOW()),
    using the venue's 4am night boundary so a session that runs past midnight
    counts on the night it started."""
    return await conn.fetchrow(
        """
        WITH ref AS (SELECT COALESCE($1::timestamptz, NOW()) AS r),
        local_month AS (
            SELECT date_trunc('month', (r AT TIME ZONE $2) - INTERVAL '4 hours') AS m
            FROM ref
        )
        SELECT
            ((m + INTERVAL '4 hours') AT TIME ZONE $2) AT TIME ZONE 'UTC' AS month_start,
            ((m + INTERVAL '1 month' + INTERVAL '4 hours') AT TIME ZONE $2)
                AT TIME ZONE 'UTC' AS next_month_start,
            (m)::date AS period_start,
            (m + INTERVAL '1 month' - INTERVAL '1 day')::date AS period_end
        FROM local_month
        """,
        ref_ts, tz,
    )


async def recompute_invoices(conn, ref_ts=None) -> dict:
    """Nightly rollup: recompute the current month's invoices from finalized
    sessions. Idempotent — recomputes the whole month from scratch each run, so
    re-running is safe and past months stay correct. 'paid' invoices are never
    touched. is_test venues are skipped (they never pay).

    Loops non-test venues so each venue uses its own timezone for play_date
    bucketing and month-window computation — correct when venues span timezones.

    Returns a summary for logging/visibility.
    """
    # The lock below lives as long as the transaction, so always have one.
    if not conn.is_in_transaction():
        async with conn.transaction():
            return await recompute_invoices(conn, ref_ts)

    # The nightly job and a venue cancellation both delete and re-insert the same
    # invoices' line items; serialize them. Held until the caller's transaction ends.
    # ponytail: one global lock; lock the venue's invoice row instead if cancels
    # ever queue behind the nightly run.
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext('recompute_invoices'))")

    # Fetch all non-test venues for per-venue processing.
    venue_list = await conn.fetch(
        "SELECT id, timezone, billing_unit, nightly_cap_weekday, nightly_cap_weekend"
        " FROM venues WHERE is_test = FALSE"
    )

    summary = {"period_start": None, "venues": 0,
               "invoices": 0, "line_items": 0, "skipped_paid": 0}

    for venue_entry in venue_list:
        venue_id = venue_entry["id"]
        tz = venue_entry["timezone"]

        win = await _period_window(conn, ref_ts, tz)
        month_start, next_month_start = win["month_start"], win["next_month_start"]
        period_start, period_end = win["period_start"], win["period_end"]

        # Record the first period_start seen for the summary (logging only).
        if summary["period_start"] is None:
            summary["period_start"] = str(period_start)

        # Per (table, play-date) sum of blocks for this venue, with snapshot fallback.
        venue_rows = await conn.fetch(
            NIGHT_BLOCKS_SQL,
            month_start, next_month_start, venue_id, tz,
            venue_entry["billing_unit"],
            venue_entry["nightly_cap_weekday"],
            venue_entry["nightly_cap_weekend"],
        )

        if not venue_rows:
            continue

        # Never recompute a paid or final invoice. is_final marks the snapshot
        # issued at cancellation; recomputing it would overwrite the at-cancel total.
        existing = await conn.fetchrow(
            "SELECT id, status, is_final FROM invoices WHERE venue_id = $1 AND period_start = $2",
            venue_id, period_start,
        )
        if existing and (existing["status"] == "paid" or existing.get("is_final")):
            summary["skipped_paid"] += 1
            continue

        # Per-invoice atomic: delete-then-reinsert line items + total update never
        # half-applies, regardless of whether the caller wraps the whole run.
        # (asyncpg nests this as a savepoint if the caller already has a txn.)
        async with conn.transaction():
            if existing:
                invoice_id = existing["id"]
                await conn.execute(
                    "DELETE FROM invoice_line_items WHERE invoice_id = $1", invoice_id)
            else:
                invoice_id = uuid.uuid4()
                await conn.execute(
                    """INSERT INTO invoices (id, venue_id, period_start, period_end, status)
                       VALUES ($1, $2, $3, $4, 'pending')""",
                    invoice_id, venue_id, period_start, period_end,
                )

            total = 0
            for r in venue_rows:
                eff_unit, cap = night_terms(r)
                raw = r["raw_blocks"]
                units = min(raw, cap)
                amount = eff_unit * units
                total += amount
                await conn.execute(
                    """INSERT INTO invoice_line_items
                           (id, invoice_id, table_id, play_date, units_billed, amount, cap_applied)
                       VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                    uuid.uuid4(), invoice_id, r["table_id"], r["play_date"],
                    units, amount, raw > cap,
                )
                summary["line_items"] += 1

            await conn.execute(
                "UPDATE invoices SET total_amount = $1, updated_at = NOW() WHERE id = $2",
                total, invoice_id,
            )
        summary["venues"] += 1
        summary["invoices"] += 1

    return summary
