import os
import secrets
import traceback
import uuid
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
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
                       ) AS tag_paired,
                       (
                           SELECT COUNT(*) FROM game_sessions gs
                           WHERE gs.table_id = t.id AND gs.ended_at IS NULL
                       ) AS active_session_count
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


@router.get("/tables/{table_id}")
@limiter.limit("60/minute")
async def table_detail(
    table_id: str,
    request: Request,
    current_user: CurrentUser = Depends(require_role("venue_owner", "venue_staff")),
):
    # BOLA: the table must belong to the token's venue — never trust client IDs.
    try:
        validated_id = str(uuid.UUID(table_id))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="Table not found")

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            table = await conn.fetchrow(
                "SELECT id, table_number, content_ceiling FROM tables WHERE id = $1 AND venue_id = $2",
                validated_id, current_user.venue_id,
            )
            if not table:
                raise HTTPException(status_code=404, detail="Table not found")

            tag = await conn.fetchrow(
                """
                SELECT tag_uid, status, paired_at
                FROM nfc_tags
                WHERE table_id = $1 AND venue_id = $2
                ORDER BY paired_at DESC NULLS LAST
                LIMIT 1
                """,
                validated_id, current_user.venue_id,
            )

            # Reuse the "last 4am local -> UTC" boundary for recent ended sessions.
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

            session_rows = await conn.fetch(
                """
                SELECT
                    gs.id AS session_id,
                    gs.group_label,
                    gs.started_at,
                    gs.created_at,
                    gs.current_round_number,
                    gs.origin_phone_id,
                    EXTRACT(EPOCH FROM NOW() - gs.started_at) AS seconds_active,
                    EXTRACT(EPOCH FROM NOW() - COALESCE(gs.last_activity_at, gs.created_at))
                        AS idle_seconds,
                    COALESCE(v.retap_interval_minutes, 15) * 60 AS threshold_seconds
                FROM game_sessions gs
                JOIN venues v ON v.id = gs.venue_id
                WHERE gs.table_id = $1
                  AND gs.venue_id = $2
                  AND gs.ended_at IS NULL
                ORDER BY gs.created_at
                """,
                validated_id, current_user.venue_id,
            )

            state_map = {
                "active": "active",
                "prompt": "active",
                "paused": "paused",
                "expired": "idle",
            }

            active_sessions = []
            for row in session_rows:
                session_id = str(row["session_id"])

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

                leaderboard_rows = await conn.fetch(
                    """
                    SELECT name, score, left_early
                    FROM game_players
                    WHERE session_id = $1
                    ORDER BY left_early ASC, score DESC, name ASC
                    """,
                    session_id,
                )

                round_rows = await conn.fetch(
                    """
                    SELECT round_number, round_type, result, score_awarded
                    FROM rounds
                    WHERE session_id = $1
                    ORDER BY created_at
                    """,
                    session_id,
                )

                # Derive current round type from the last round row — no extra query.
                current_round_type = round_rows[-1]["round_type"] if round_rows else None

                host_name = None
                if row["origin_phone_id"] is not None:
                    host_row = await conn.fetchrow(
                        """
                        SELECT name FROM game_players
                        WHERE session_id = $1 AND phone_id = $2 AND left_early = FALSE
                        LIMIT 1
                        """,
                        session_id, str(row["origin_phone_id"]),
                    )
                    if host_row:
                        host_name = host_row["name"]

                active_sessions.append({
                    "session_id": session_id,
                    "group_label": row["group_label"],
                    "status": status,
                    "current_round_number": row["current_round_number"],
                    "current_round_type": current_round_type,
                    "seconds_active": secs_active,
                    "host_name": host_name,
                    "leaderboard": [
                        {
                            "name": r["name"],
                            "score": int(r["score"]),
                            "left_early": r["left_early"],
                        }
                        for r in leaderboard_rows
                    ],
                    "round_history": [
                        {
                            "round_number": r["round_number"],
                            "round_type": r["round_type"],
                            "result": r["result"],
                            "score_awarded": int(r["score_awarded"]) if r["score_awarded"] is not None else 0,
                        }
                        for r in round_rows
                    ],
                })

            recent_rows = await conn.fetch(
                """
                SELECT
                    gs.id AS session_id,
                    gs.group_label,
                    gs.player_count,
                    gs.total_score,
                    gs.total_rounds,
                    gs.ended_at,
                    gs.end_reason
                FROM game_sessions gs
                WHERE gs.table_id = $1
                  AND gs.venue_id = $2
                  AND gs.ended_at IS NOT NULL
                  AND gs.started_at >= $3
                ORDER BY gs.ended_at DESC
                """,
                validated_id, current_user.venue_id, tonight_boundary,
            )

        return {
            "table": {
                "id": str(table["id"]),
                "table_number": table["table_number"],
                "content_ceiling": table["content_ceiling"],
            },
            "tag": {
                "tag_uid": tag["tag_uid"],
                "status": tag["status"],
                "paired_at": tag["paired_at"].isoformat() if tag["paired_at"] else None,
            } if tag else None,
            "active_sessions": active_sessions,
            "recent_sessions": [
                {
                    "session_id": str(r["session_id"]),
                    "group_label": r["group_label"],
                    "player_count": r["player_count"],
                    "total_score": int(r["total_score"]) if r["total_score"] is not None else 0,
                    "total_rounds": int(r["total_rounds"]) if r["total_rounds"] is not None else 0,
                    "ended_at": r["ended_at"].isoformat() if r["ended_at"] else None,
                    "end_reason": r["end_reason"],
                }
                for r in recent_rows
            ],
        }
    except HTTPException:
        raise
    except Exception:
        await notify_error("GET /dashboard/tables/{id} failed \U0001f6a8", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")


@router.get("/insights")
@limiter.limit("60/minute")
async def insights(
    request: Request,
    range_param: Literal["tonight", "7d", "30d"] = Query("tonight", alias="range"),
    current_user: CurrentUser = Depends(require_role("venue_owner", "venue_staff")),
):
    # venue_id always comes from the authenticated user, never from the request (BOLA).
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

            if range_param == "tonight":
                range_start = tonight_boundary
            elif range_param == "7d":
                range_start = tonight_boundary - timedelta(days=6)
            else:  # 30d
                range_start = tonight_boundary - timedelta(days=29)

            totals_row = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) AS session_count,
                    COALESCE(SUM(gs.total_rounds), 0) AS total_rounds,
                    ROUND(AVG(gs.player_count)::numeric, 1) AS avg_players
                FROM game_sessions gs
                JOIN tables t ON t.id = gs.table_id
                WHERE t.venue_id = $1
                  AND gs.started_at >= $2
                """,
                current_user.venue_id, range_start,
            )

            avg_minutes_val = await conn.fetchval(
                """
                SELECT
                    ROUND(AVG(EXTRACT(EPOCH FROM gs.ended_at - gs.started_at) / 60.0)::numeric, 1)
                FROM game_sessions gs
                JOIN tables t ON t.id = gs.table_id
                WHERE t.venue_id = $1
                  AND gs.started_at >= $2
                  AND gs.ended_at IS NOT NULL
                  AND gs.started_at IS NOT NULL
                """,
                current_user.venue_id, range_start,
            )

            player_count = await conn.fetchval(
                """
                SELECT COUNT(*) AS player_count
                FROM game_players gp
                JOIN game_sessions gs ON gs.id = gp.session_id
                JOIN tables t ON t.id = gs.table_id
                WHERE t.venue_id = $1
                  AND gs.started_at >= $2
                  AND gp.left_early = FALSE
                """,
                current_user.venue_id, range_start,
            )

            round_mix_rows = await conn.fetch(
                """
                SELECT r.round_type, COUNT(*) AS count
                FROM rounds r
                JOIN game_sessions gs ON gs.id = r.session_id
                JOIN tables t ON t.id = gs.table_id
                WHERE t.venue_id = $1
                  AND gs.started_at >= $2
                GROUP BY r.round_type
                """,
                current_user.venue_id, range_start,
            )

            trivia_row = await conn.fetchrow(
                """
                SELECT
                    COALESCE(SUM(gs.trivia_correct), 0) AS trivia_correct,
                    COALESCE(SUM(gs.trivia_wrong), 0) AS trivia_wrong
                FROM game_sessions gs
                JOIN tables t ON t.id = gs.table_id
                WHERE t.venue_id = $1
                  AND gs.started_at >= $2
                """,
                current_user.venue_id, range_start,
            )

            roulette_row = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) FILTER (WHERE r.round_type = 'roulette' AND r.result = 'completed')
                        AS roulette_completed,
                    COUNT(*) FILTER (WHERE r.card_type = 'drink')
                        AS drink_rounds
                FROM rounds r
                JOIN game_sessions gs ON gs.id = r.session_id
                JOIN tables t ON t.id = gs.table_id
                WHERE t.venue_id = $1
                  AND gs.started_at >= $2
                """,
                current_user.venue_id, range_start,
            )

            trend_rows = await conn.fetch(
                """
                SELECT
                    date_trunc('day', (gs.started_at AT TIME ZONE 'UTC' AT TIME ZONE $3)
                        - INTERVAL '4 hours')::date AS play_night,
                    COUNT(*) AS session_count
                FROM game_sessions gs
                JOIN tables t ON t.id = gs.table_id
                WHERE t.venue_id = $1
                  AND gs.started_at >= $2
                GROUP BY play_night
                ORDER BY play_night
                """,
                current_user.venue_id, range_start, VENUE_TIMEZONE,
            )

        round_mix = {"chooser": 0, "roulette": 0, "trivia": 0}
        for row in round_mix_rows:
            if row["round_type"] in round_mix:
                round_mix[row["round_type"]] = int(row["count"])

        trivia_correct = int(trivia_row["trivia_correct"])
        trivia_wrong = int(trivia_row["trivia_wrong"])
        total_trivia = trivia_correct + trivia_wrong
        trivia_accuracy = round(trivia_correct / total_trivia, 3) if total_trivia > 0 else None

        trend = [
            {"date": str(row["play_night"]), "count": int(row["session_count"])}
            for row in trend_rows
        ]

        return {
            "range": range_param,
            "totals": {
                "sessions": int(totals_row["session_count"]),
                "players": int(player_count),
                "rounds": int(totals_row["total_rounds"]),
                "avg_session_minutes": float(avg_minutes_val) if avg_minutes_val is not None else None,
                "avg_players": float(totals_row["avg_players"]) if totals_row["avg_players"] is not None else None,
            },
            "round_mix": round_mix,
            "trivia": {
                "correct": trivia_correct,
                "wrong": trivia_wrong,
                "accuracy": trivia_accuracy,
            },
            "roulette_and_drinks": {
                "roulette_completed": int(roulette_row["roulette_completed"]),
                "drink_rounds": int(roulette_row["drink_rounds"]),
            },
            "trend": trend,
        }
    except Exception:
        await notify_error("GET /dashboard/insights failed \U0001f6a8", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")


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
