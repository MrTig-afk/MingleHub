"""Analytics rollup — pre-aggregate per-venue per-day session stats into
venue_daily_stats so dashboard insights/overview can read small summaries
instead of scanning raw game_sessions. Same shape as the billing rollup:
idempotent, recomputed by a nightly job.

A "day" is the 4am-boundary play-date (a session that runs past midnight counts
on the night it started), matching the dashboard's "tonight" boundary.
"""
from datetime import timedelta

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


_RANGE_DAYS = {"tonight": 0, "7d": 6, "30d": 29}


async def range_totals(conn, venue_id, range_param: str) -> dict:
    """Session-level totals + per-day trend for a dashboard range, read from
    venue_daily_stats for COMPLETED days plus a small live query for TODAY (which
    isn't rolled up yet). Equivalent to scanning raw game_sessions over the range,
    but only the current day is scanned live — history is pre-aggregated.

    Returns raw sums/counts so the caller derives averages with its own rounding.
    """
    days = _RANGE_DAYS[range_param]
    bounds = await conn.fetchrow(
        """
        SELECT
            (date_trunc('day', (NOW() AT TIME ZONE $1) - INTERVAL '4 hours'))::date AS today,
            ((date_trunc('day', (NOW() AT TIME ZONE $1) - INTERVAL '4 hours')
                + INTERVAL '4 hours') AT TIME ZONE $1) AT TIME ZONE 'UTC' AS today_start
        """,
        VENUE_TIMEZONE,
    )
    today = bounds["today"]
    today_start = bounds["today_start"]
    range_start_date = today - timedelta(days=days)

    # Completed days: pre-aggregated rows (stat_date strictly before today).
    closed = await conn.fetchrow(
        """
        SELECT
            COALESCE(SUM(session_count), 0)        AS sessions,
            COALESCE(SUM(total_rounds), 0)         AS rounds,
            COALESCE(SUM(sum_player_count), 0)     AS sum_player,
            COALESCE(SUM(ended_count), 0)          AS ended,
            COALESCE(SUM(sum_duration_seconds), 0) AS duration,
            COALESCE(SUM(trivia_correct), 0)       AS trivia_correct,
            COALESCE(SUM(trivia_wrong), 0)         AS trivia_wrong
        FROM venue_daily_stats
        WHERE venue_id = $1 AND stat_date >= $2 AND stat_date < $3
        """,
        venue_id, range_start_date, today,
    )

    # Today: live aggregate (the only day scanned from raw sessions).
    live = await conn.fetchrow(
        """
        SELECT
            COUNT(*)                                                  AS sessions,
            COALESCE(SUM(total_rounds), 0)                            AS rounds,
            COALESCE(SUM(player_count), 0)                            AS sum_player,
            COUNT(*) FILTER (WHERE ended_at IS NOT NULL)              AS ended,
            COALESCE(SUM(EXTRACT(EPOCH FROM ended_at - started_at))
                FILTER (WHERE ended_at IS NOT NULL), 0)::bigint       AS duration,
            COALESCE(SUM(trivia_correct), 0)                          AS trivia_correct,
            COALESCE(SUM(trivia_wrong), 0)                            AS trivia_wrong
        FROM game_sessions
        WHERE venue_id = $1 AND started_at >= $2
        """,
        venue_id, today_start,
    )

    # Per-day trend: completed days from the rollup + today's live count.
    trend_rows = await conn.fetch(
        """
        SELECT stat_date AS d, session_count AS c
        FROM venue_daily_stats
        WHERE venue_id = $1 AND stat_date >= $2 AND stat_date < $3 AND session_count > 0
        ORDER BY stat_date
        """,
        venue_id, range_start_date, today,
    )
    trend = [{"date": str(r["d"]), "count": int(r["c"])} for r in trend_rows]
    if int(live["sessions"]) > 0:
        trend.append({"date": str(today), "count": int(live["sessions"])})

    def _sum(key):
        return int(closed[key]) + int(live[key])

    return {
        "sessions": _sum("sessions"),
        "rounds": _sum("rounds"),
        "sum_player": _sum("sum_player"),
        "ended": _sum("ended"),
        "duration_seconds": _sum("duration"),
        "trivia_correct": _sum("trivia_correct"),
        "trivia_wrong": _sum("trivia_wrong"),
        "trend": trend,
    }
