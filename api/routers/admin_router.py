import traceback

from fastapi import APIRouter, Depends, HTTPException, Request

from api.auth import CurrentUser, get_current_user, require_role
from api.db import get_pool
from api.security import limiter, verify_api_key
from api.services.notify import notify_error
from api.routers.dashboard_router import VENUE_TIMEZONE

router = APIRouter(prefix="/api/admin", dependencies=[Depends(verify_api_key)])


@router.get("/ping")
@limiter.limit("60/minute")
async def ping(request: Request, current_user: CurrentUser = Depends(require_role("admin"))):
    return {"status": "ok", "admin": current_user.clerk_user_id}


@router.get("/me")
@limiter.limit("60/minute")
async def admin_me(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
):
    # No role enforcement here — the frontend reads role and routes accordingly.
    # get_current_user raises 401 on an invalid/missing token; no DB query needed.
    return current_user


@router.get("/overview")
@limiter.limit("60/minute")
async def admin_overview(
    request: Request,
    current_user: CurrentUser = Depends(require_role("admin")),
):
    # Admin sees cross-venue data — no venue_id filter by design (inverse-BOLA).
    # venue_owner and venue_staff get 403 from require_role before reaching here.
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # "Last 4am" boundary in Postgres — keeps DST handling in the DB
            # where the full tz database lives (same SQL as dashboard_router).
            tonight_boundary = await conn.fetchval(
                """
                SELECT (
                    (date_trunc('day', (NOW() AT TIME ZONE $1) - INTERVAL '4 hours')
                        + INTERVAL '4 hours')
                    AT TIME ZONE $1
                ) AT TIME ZONE 'UTC'
                """,
                VENUE_TIMEZONE,
            )

            # Platform totals (is_test venues excluded per security.md line 145).
            totals_row = await conn.fetchrow(
                """
                SELECT
                    (SELECT COUNT(*) FROM venues WHERE is_test = FALSE) AS total_venues,
                    COUNT(DISTINCT gs.venue_id) FILTER (WHERE gs.ended_at IS NULL) AS active_venues_now,
                    COUNT(*) AS sessions_tonight,
                    COALESCE(SUM(gs.total_rounds), 0) AS rounds_tonight
                FROM game_sessions gs
                JOIN venues v ON v.id = gs.venue_id
                WHERE v.is_test = FALSE
                  AND gs.started_at >= $1
                """,
                tonight_boundary,
            )

            players_tonight = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM game_players gp
                JOIN game_sessions gs ON gs.id = gp.session_id
                JOIN venues v ON v.id = gs.venue_id
                WHERE v.is_test = FALSE
                  AND gs.started_at >= $1
                  AND gp.left_early = FALSE
                """,
                tonight_boundary,
            )

            active_sessions_now = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM game_sessions gs
                JOIN venues v ON v.id = gs.venue_id
                WHERE v.is_test = FALSE
                  AND gs.ended_at IS NULL
                """,
            )

            # Per-venue live breakdown (is_test excluded).
            venue_rows = await conn.fetch(
                """
                SELECT
                    v.id AS venue_id,
                    v.name,
                    v.slug,
                    (SELECT COUNT(*) FROM game_sessions gs2
                     WHERE gs2.venue_id = v.id AND gs2.ended_at IS NULL) AS active_sessions,
                    (SELECT COUNT(*) FROM game_sessions gs3
                     WHERE gs3.venue_id = v.id AND gs3.started_at >= $1) AS sessions_tonight,
                    (SELECT COUNT(*) FROM game_players gp
                     JOIN game_sessions gs4 ON gs4.id = gp.session_id
                     WHERE gs4.venue_id = v.id AND gs4.started_at >= $1
                       AND gp.left_early = FALSE) AS players_tonight
                FROM venues v
                WHERE v.is_test = FALSE
                ORDER BY active_sessions DESC, v.name
                """,
                tonight_boundary,
            )

        # PostgreSQL COUNT(*) always returns a row (even over zero rows), so
        # totals_row will not be None. Cast to int for safety.
        return {
            "platform": {
                "total_venues": int(totals_row["total_venues"]),
                "active_venues_now": int(totals_row["active_venues_now"]),
                "active_sessions_now": int(active_sessions_now),
                "sessions_tonight": int(totals_row["sessions_tonight"]),
                "players_tonight": int(players_tonight),
                "rounds_tonight": int(totals_row["rounds_tonight"]),
            },
            "per_venue": [
                {
                    "venue_id": str(row["venue_id"]),
                    "name": row["name"],
                    "slug": row["slug"],
                    "active_sessions": int(row["active_sessions"]),
                    "sessions_tonight": int(row["sessions_tonight"]),
                    "players_tonight": int(row["players_tonight"]),
                }
                for row in venue_rows
            ],
        }
    except HTTPException:
        raise
    except Exception:
        await notify_error("GET /admin/overview failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")


@router.get("/venues")
@limiter.limit("60/minute")
async def admin_venues(
    request: Request,
    current_user: CurrentUser = Depends(require_role("admin")),
):
    # Admin sees ALL venues (including is_test) — flagged so the UI can highlight them.
    # venue_owner and venue_staff get 403 from require_role before reaching here.
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            tonight_boundary = await conn.fetchval(
                """
                SELECT (
                    (date_trunc('day', (NOW() AT TIME ZONE $1) - INTERVAL '4 hours')
                        + INTERVAL '4 hours')
                    AT TIME ZONE $1
                ) AT TIME ZONE 'UTC'
                """,
                VENUE_TIMEZONE,
            )

            rows = await conn.fetch(
                """
                SELECT
                    v.id,
                    v.name,
                    v.slug,
                    v.venue_type,
                    v.status,
                    v.is_test,
                    (SELECT COUNT(*) FROM tables t WHERE t.venue_id = v.id) AS table_count,
                    (SELECT COUNT(*) FROM game_sessions gs
                     WHERE gs.venue_id = v.id AND gs.ended_at IS NULL) AS active_sessions,
                    (SELECT COUNT(*) FROM game_sessions gs2
                     WHERE gs2.venue_id = v.id AND gs2.started_at >= $1) AS sessions_tonight
                FROM venues v
                ORDER BY v.name
                """,
                tonight_boundary,
            )

        return {
            "venues": [
                {
                    "id": str(row["id"]),
                    "name": row["name"],
                    "slug": row["slug"],
                    "venue_type": row["venue_type"],
                    "status": row["status"],
                    "is_test": bool(row["is_test"]),
                    "table_count": int(row["table_count"]),
                    "active_sessions": int(row["active_sessions"]),
                    "sessions_tonight": int(row["sessions_tonight"]),
                }
                for row in rows
            ]
        }
    except HTTPException:
        raise
    except Exception:
        await notify_error("GET /admin/venues failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")
