import os
import secrets
import traceback
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.auth import CurrentUser, get_current_user, require_role
from api.db import get_pool
from api.security import limiter, verify_api_key
from api.services.notify import notify_error
from api.services.nfc_crypto import encrypt_tag_key
from api.services.session_service import compute_retap_state

router = APIRouter(prefix="/api/dashboard", dependencies=[Depends(verify_api_key)])

# "Tonight" rolls over at 4am local time. A single venue timezone for now — per-venue
# timezone is a later slice (gamespec: "server TZ; per-venue TZ later").
VENUE_TIMEZONE = "Australia/Melbourne"


@router.get("/me")
@limiter.limit("60/minute")
async def me(request: Request, current_user: CurrentUser = Depends(get_current_user)):
    return current_user


@router.get("/venue")
@limiter.limit("60/minute")
async def venue(
    request: Request,
    current_user: CurrentUser = Depends(require_role("venue_owner", "venue_staff")),
):
    # venue_id always comes from the authenticated user, never from the request —
    # this is the BOLA-safe pattern every venue-scoped dashboard route should follow.
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, name, slug, venue_type FROM venues WHERE id = $1",
                current_user.venue_id,
            )
    except Exception:
        await notify_error("GET /dashboard/venue failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")

    if not row:
        raise HTTPException(status_code=404, detail="Venue not found")
    return dict(row)


@router.get("/tables")
@limiter.limit("60/minute")
async def list_tables(
    request: Request,
    current_user: CurrentUser = Depends(require_role("venue_owner", "venue_staff")),
):
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT t.id, t.table_number, t.content_ceiling,
                       EXISTS (
                           SELECT 1 FROM nfc_tags n
                           WHERE n.table_id = t.id AND n.status = 'active'
                       ) AS tag_paired
                FROM tables t
                WHERE t.venue_id = $1
                ORDER BY t.table_number
                """,
                current_user.venue_id,
            )
    except Exception:
        await notify_error("GET /dashboard/tables failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")

    return [dict(row) for row in rows]


@router.get("/tags")
@limiter.limit("60/minute")
async def list_tags(
    request: Request,
    current_user: CurrentUser = Depends(require_role("venue_owner", "venue_staff")),
):
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # aes_key_encrypted never selected — must never leave the backend (security.md).
            rows = await conn.fetch(
                """
                SELECT n.id, n.tag_uid, n.status, n.paired_at, t.table_number
                FROM nfc_tags n
                JOIN tables t ON t.id = n.table_id
                WHERE n.venue_id = $1
                ORDER BY t.table_number
                """,
                current_user.venue_id,
            )
    except Exception:
        await notify_error("GET /dashboard/tags failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")

    return [dict(row) for row in rows]


@router.get("/overview")
@limiter.limit("60/minute")
async def overview(
    request: Request,
    current_user: CurrentUser = Depends(require_role("venue_owner", "venue_staff")),
):
    # venue_id always comes from the authenticated user, never from the request (BOLA).
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Compute the "last 4am" boundary in Postgres, not Python: the DB always
            # carries the full tz database (a bare Windows/serverless Python runtime may
            # not), so this is correct and DST-aware regardless of the server process's
            # timezone. started_at is stored as UTC wall-clock, so the local 4am boundary
            # is converted back to UTC for the comparison below.
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

            totals_row = await conn.fetchrow(
                """
                SELECT
                    COUNT(DISTINCT gs.table_id) FILTER (WHERE gs.ended_at IS NULL)
                        AS active_tables,
                    COALESCE(SUM(gs.total_rounds), 0) AS rounds_tonight,
                    COUNT(*) AS sessions_tonight
                FROM game_sessions gs
                JOIN tables t ON t.id = gs.table_id
                WHERE t.venue_id = $1
                  AND gs.started_at >= $2
                """,
                current_user.venue_id,
                tonight_boundary,
            )

            players_tonight = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM game_players gp
                JOIN game_sessions gs ON gs.id = gp.session_id
                JOIN tables t ON t.id = gs.table_id
                WHERE t.venue_id = $1
                  AND gs.started_at >= $2
                  AND gp.left_early = FALSE
                """,
                current_user.venue_id,
                tonight_boundary,
            )

            session_rows = await conn.fetch(
                """
                SELECT
                    gs.id AS session_id,
                    t.table_number,
                    gs.group_label,
                    gs.started_at,
                    gs.created_at,
                    gs.current_round_number,
                    EXTRACT(EPOCH FROM NOW() - gs.started_at) AS seconds_active,
                    EXTRACT(EPOCH FROM NOW() - COALESCE(gs.last_activity_at, gs.created_at))
                        AS idle_seconds,
                    COALESCE(v.retap_interval_minutes, 15) * 60 AS threshold_seconds,
                    (SELECT COUNT(*) FROM game_players gp
                     WHERE gp.session_id = gs.id AND gp.left_early = FALSE) AS player_count,
                    (SELECT r.round_type FROM rounds r
                     WHERE r.session_id = gs.id
                     ORDER BY r.created_at DESC LIMIT 1) AS current_round_type
                FROM game_sessions gs
                JOIN tables t ON t.id = gs.table_id
                JOIN venues v ON v.id = t.venue_id
                WHERE t.venue_id = $1
                  AND gs.ended_at IS NULL
                ORDER BY t.table_number, gs.group_label
                """,
                current_user.venue_id,
            )

        state_map = {
            "active": "active",
            "prompt": "active",
            "paused": "paused",
            "expired": "idle",
        }

        active_sessions = []
        for row in session_rows:
            if row["started_at"] is None:
                status = "lobby"
                secs_active = 0
            else:
                retap = compute_retap_state(
                    float(row["idle_seconds"]),
                    int(row["threshold_seconds"]),
                )
                status = state_map.get(retap["state"], "active")
                secs_active = int(row["seconds_active"])

            active_sessions.append({
                "session_id": str(row["session_id"]),
                "table_number": row["table_number"],
                "group_label": row["group_label"],
                "player_count": int(row["player_count"]),
                "current_round_number": row["current_round_number"],
                "current_round_type": row["current_round_type"],
                "seconds_active": secs_active,
                "status": status,
            })

        return {
            "tonight": {
                "active_tables": int(totals_row["active_tables"]),
                "players_tonight": int(players_tonight),
                "rounds_tonight": int(totals_row["rounds_tonight"]),
                "sessions_tonight": int(totals_row["sessions_tonight"]),
            },
            "active_sessions": active_sessions,
        }
    except Exception:
        await notify_error("GET /dashboard/overview failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")


class PairTagRequest(BaseModel):
    tag_uid: str = Field(min_length=4, max_length=64)
    table_number: int = Field(gt=0)


@router.post("/pair-tag")
@limiter.limit("30/minute")
async def pair_tag(
    request: Request,
    body: PairTagRequest,
    current_user: CurrentUser = Depends(require_role("venue_owner")),
):
    """Ties a physical NFC tag's UID to one of the owner's own tables.

    venue_id always comes from the authenticated user — the table being
    paired must belong to their venue, and a tag_uid already paired to a
    *different* venue is rejected rather than silently re-pointed (BOLA).
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            table = await conn.fetchrow(
                "SELECT id FROM tables WHERE venue_id = $1 AND table_number = $2",
                current_user.venue_id, body.table_number,
            )
            if not table:
                raise HTTPException(status_code=404, detail="Table not found")

            existing = await conn.fetchrow(
                "SELECT id, venue_id FROM nfc_tags WHERE tag_uid = $1",
                body.tag_uid,
            )
            if existing and str(existing["venue_id"]) != current_user.venue_id:
                raise HTTPException(status_code=409, detail="Tag already belongs to another venue")

            if existing:
                row = await conn.fetchrow(
                    """
                    UPDATE nfc_tags
                    SET table_id = $1, status = 'active', paired_at = NOW()
                    WHERE id = $2
                    RETURNING id, tag_uid, status, paired_at
                    """,
                    table["id"], existing["id"],
                )
            else:
                # DEV NOTE: real tags ship factory-programmed with their own AES
                # key (see nfc_crypto.py). Until provisioning is wired up, a
                # fresh random key is generated here so pairing — and later SUN
                # verification — has something real to encrypt/decrypt against.
                placeholder_key = encrypt_tag_key(secrets.token_bytes(16))
                row = await conn.fetchrow(
                    """
                    INSERT INTO nfc_tags (id, venue_id, table_id, tag_uid, aes_key_encrypted, status, paired_at)
                    VALUES ($1, $2, $3, $4, $5, 'active', NOW())
                    RETURNING id, tag_uid, status, paired_at
                    """,
                    str(uuid.uuid4()), current_user.venue_id, table["id"], body.tag_uid, placeholder_key,
                )
    except HTTPException:
        raise
    except Exception:
        await notify_error("POST /dashboard/pair-tag failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")

    return {**dict(row), "table_number": body.table_number}


class DevResetTableRequest(BaseModel):
    table_number: int = Field(gt=0)


@router.post("/dev-reset-table")
@limiter.limit("30/minute")
async def dev_reset_table(
    request: Request,
    body: DevResetTableRequest,
    current_user: CurrentUser = Depends(require_role("venue_owner")),
):
    """Dev-only convenience for local testing: ends every active session
    and expires any open lobby at one of the caller's own tables, so the
    very next tap starts completely fresh instead of resuming whatever
    groups a previous test round left active. 404s outside DEV_MODE —
    never reachable in production."""
    if os.getenv("DEV_MODE") != "true":
        raise HTTPException(status_code=404, detail="Not found")

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            table = await conn.fetchrow(
                "SELECT id FROM tables WHERE venue_id = $1 AND table_number = $2",
                current_user.venue_id, body.table_number,
            )
            if not table:
                raise HTTPException(status_code=404, detail="Table not found")

            ended = await conn.fetch(
                """
                UPDATE game_sessions SET ended_at = NOW(), end_reason = 'dev_reset'
                WHERE table_id = $1 AND ended_at IS NULL
                RETURNING id
                """,
                table["id"],
            )
            await conn.execute(
                "UPDATE table_lobbies SET status = 'expired' WHERE table_id = $1 AND status = 'open'",
                table["id"],
            )
    except HTTPException:
        raise
    except Exception:
        await notify_error("POST /dashboard/dev-reset-table failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")

    return {"table_number": body.table_number, "sessions_ended": len(ended)}
