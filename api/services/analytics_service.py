"""Analytics rollup — pre-aggregate per-venue per-day session stats into
venue_daily_stats so dashboard insights/overview can read small summaries
instead of scanning raw game_sessions. Same shape as the billing rollup:
idempotent, recomputed by a nightly job.

A "day" is the 4am-boundary play-date (a session that runs past midnight counts
on the night it started), matching the dashboard's "tonight" boundary.
"""
VENUE_TIMEZONE = "Australia/Melbourne"  # mirrors dashboard_router.VENUE_TIMEZONE

DEFAULT_WINDOW_DAYS = 35  # covers the 30d insights range + buffer for late-ending sessions


async def recompute_daily_stats(conn, ref_ts=None, window_days: int = DEFAULT_WINDOW_DAYS) -> dict:
    """Recompute venue_daily_stats for the last `window_days` of play-dates from
    game_sessions. Idempotent: re-running overwrites the same rows (sessions can
    end after a day's first rollup, so recent days are recomputed each run).
    Includes all venues (test + real). Returns a small summary for logging.
    """
    result = await conn.fetchrow(
        """
        WITH ref AS (SELECT COALESCE($1::timestamptz, NOW()) AS r),
        win AS (
            SELECT (
                (date_trunc('day', (r AT TIME ZONE $2) - INTERVAL '4 hours')
                    - make_interval(days => $3) + INTERVAL '4 hours')
                AT TIME ZONE $2
            ) AT TIME ZONE 'UTC' AS window_start
            FROM ref
        ),
        agg AS (
            INSERT INTO venue_daily_stats AS vds (
                venue_id, stat_date, session_count, ended_count, total_rounds,
                sum_player_count, sum_duration_seconds, trivia_correct, trivia_wrong,
                cards_completed, cards_skipped, total_score, updated_at)
            SELECT
                gs.venue_id,
                date_trunc('day', (gs.started_at AT TIME ZONE 'UTC' AT TIME ZONE $2)
                    - INTERVAL '4 hours')::date AS stat_date,
                COUNT(*),
                COUNT(*) FILTER (WHERE gs.ended_at IS NOT NULL),
                COALESCE(SUM(gs.total_rounds), 0),
                COALESCE(SUM(gs.player_count), 0),
                COALESCE(SUM(EXTRACT(EPOCH FROM gs.ended_at - gs.started_at))
                    FILTER (WHERE gs.ended_at IS NOT NULL), 0)::bigint,
                COALESCE(SUM(gs.trivia_correct), 0),
                COALESCE(SUM(gs.trivia_wrong), 0),
                COALESCE(SUM(gs.cards_completed), 0),
                COALESCE(SUM(gs.cards_skipped), 0),
                COALESCE(SUM(gs.total_score), 0),
                NOW()
            FROM game_sessions gs, win
            WHERE gs.started_at >= win.window_start AND gs.started_at IS NOT NULL
            GROUP BY gs.venue_id, stat_date
            ON CONFLICT (venue_id, stat_date) DO UPDATE SET
                session_count        = EXCLUDED.session_count,
                ended_count          = EXCLUDED.ended_count,
                total_rounds         = EXCLUDED.total_rounds,
                sum_player_count     = EXCLUDED.sum_player_count,
                sum_duration_seconds = EXCLUDED.sum_duration_seconds,
                trivia_correct       = EXCLUDED.trivia_correct,
                trivia_wrong         = EXCLUDED.trivia_wrong,
                cards_completed      = EXCLUDED.cards_completed,
                cards_skipped        = EXCLUDED.cards_skipped,
                total_score          = EXCLUDED.total_score,
                updated_at           = NOW()
            RETURNING vds.venue_id, vds.stat_date
        )
        SELECT COUNT(*) AS rows_upserted,
               COUNT(DISTINCT venue_id) AS venues
        FROM agg
        """,
        ref_ts, VENUE_TIMEZONE, window_days,
    )
    return {"rows_upserted": result["rows_upserted"], "venues": result["venues"],
            "window_days": window_days}
