import os
import re
import secrets
import traceback
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal, Optional

import asyncpg
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from api.auth import CurrentUser, get_current_user, require_role
from api.db import get_pool
from api.security import limiter, verify_api_key
from api.services.notify import notify_error
from api.services.nfc_crypto import encrypt_tag_key
from api.services.session_service import compute_retap_state
from api.services.billing_service import cap_blocks, BLOCK_SECONDS
from api.services.analytics_service import range_totals
from api.services.theme_service import resolve_active_theme
from api.services import stripe_service
from api.services import venue_lifecycle_service

router = APIRouter(prefix="/api/dashboard", dependencies=[Depends(verify_api_key)])

# "Tonight" rolls over at 4am local time. A single venue timezone for now — per-venue
# timezone is a later slice (gamespec: "server TZ; per-venue TZ later").
VENUE_TIMEZONE = "Australia/Melbourne"


@router.get("/me")
@limiter.limit("60/minute")
async def me(request: Request, current_user: CurrentUser = Depends(get_current_user)):
    result = current_user.model_dump()
    if current_user.venue_id is None and current_user.role == "venue_owner":
        pool = await get_pool()
        async with pool.acquire() as conn:
            inv = await conn.fetchrow(
                "SELECT venue_name, address, lat, lng, place_id FROM venue_invites "
                "WHERE used_by = $1 AND status = 'used' ORDER BY used_at DESC LIMIT 1",
                current_user.id,
            )
        result["has_redeemed_invite"] = inv is not None
        # Surface the invite prefill here too so a refresh between redeem and setup
        # completion still pre-fills the wizard (the redeem response otherwise lives
        # only in transient React state).
        result["invite_prefill"] = ({
            "venue_name": inv["venue_name"],
            "address": inv["address"],
            "lat": float(inv["lat"]) if inv["lat"] is not None else None,
            "lng": float(inv["lng"]) if inv["lng"] is not None else None,
            "place_id": inv["place_id"],
        } if inv else None)
    else:
        result["has_redeemed_invite"] = None  # Not applicable
        result["invite_prefill"] = None
    return result


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
                "SELECT id, name, slug, venue_type, address, latitude, longitude "
                "FROM venues WHERE id = $1",
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

            # A2: batch all per-session queries to eliminate the N+1 pattern.
            session_ids = [str(r["session_id"]) for r in session_rows]

            leaderboard_map: defaultdict = defaultdict(list)
            round_map: defaultdict = defaultdict(list)
            host_map: dict = {}

            if session_ids:
                all_leaderboard_rows = await conn.fetch(
                    """
                    SELECT session_id, name, score, left_early
                    FROM game_players
                    WHERE session_id = ANY($1::uuid[])
                    ORDER BY session_id, left_early ASC, score DESC, name ASC
                    """,
                    session_ids,
                )
                for r in all_leaderboard_rows:
                    leaderboard_map[str(r["session_id"])].append(r)

                all_round_rows = await conn.fetch(
                    """
                    SELECT session_id, round_number, round_type, result, score_awarded
                    FROM rounds
                    WHERE session_id = ANY($1::uuid[])
                    ORDER BY session_id, created_at
                    """,
                    session_ids,
                )
                for r in all_round_rows:
                    round_map[str(r["session_id"])].append(r)

                # Batch host lookup: only query sessions that have an origin_phone_id.
                sessions_with_host = [
                    (str(r["session_id"]), str(r["origin_phone_id"]))
                    for r in session_rows
                    if r["origin_phone_id"] is not None
                ]
                if sessions_with_host:
                    host_session_ids = [s for s, _ in sessions_with_host]
                    host_phone_ids = [p for _, p in sessions_with_host]
                    host_rows = await conn.fetch(
                        """
                        SELECT session_id, phone_id, name
                        FROM game_players
                        WHERE session_id = ANY($1::uuid[])
                          AND phone_id = ANY($2::text[])
                          AND left_early = FALSE
                        """,
                        host_session_ids,
                        host_phone_ids,
                    )
                    # Match the exact (session, its own origin phone) pair — the
                    # batch ANY() fetch is a cross-product, so key by the pair to
                    # reproduce the original per-session WHERE session_id AND phone_id.
                    host_by_pair = {
                        (str(r["session_id"]), r["phone_id"]): r["name"] for r in host_rows
                    }
                    host_map = {
                        sid: host_by_pair.get((sid, pid)) for sid, pid in sessions_with_host
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

                leaderboard_rows = leaderboard_map.get(session_id, [])
                round_rows = round_map.get(session_id, [])
                # Derive current round type from the last round row.
                current_round_type = round_rows[-1]["round_type"] if round_rows else None
                host_name = host_map.get(session_id)

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

            # Session-level totals + trend: read pre-aggregated rollup for
            # completed days + a live query for today (only today scans raw
            # sessions). Equivalent to the old full-range scan — see test_insights_*.
            agg = await range_totals(conn, current_user.venue_id, range_param)

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

        round_mix = {"chooser": 0, "roulette": 0, "trivia": 0}
        for row in round_mix_rows:
            if row["round_type"] in round_mix:
                round_mix[row["round_type"]] = int(row["count"])

        trivia_correct = agg["trivia_correct"]
        trivia_wrong = agg["trivia_wrong"]
        total_trivia = trivia_correct + trivia_wrong
        trivia_accuracy = round(trivia_correct / total_trivia, 3) if total_trivia > 0 else None

        trend = agg["trend"]

        # ROUND_HALF_UP + exact Decimal arithmetic matches Postgres ROUND(numeric,1)
        # so the rollup-derived averages equal the old live AVG()s exactly.
        def _avg1(total, count):
            if not count:
                return None
            return float((Decimal(total) / Decimal(count)).quantize(Decimal("0.1"), ROUND_HALF_UP))

        return {
            "range": range_param,
            "totals": {
                "sessions": agg["sessions"],
                "players": int(player_count),
                "rounds": agg["rounds"],
                "avg_session_minutes": _avg1(agg["duration_seconds"], agg["ended"] * 60),
                "avg_players": _avg1(agg["sum_player"], agg["sessions"]),
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

            # A1: players_tonight merged into totals_row as a scalar subquery
            # to save one DB round-trip.
            totals_row = await conn.fetchrow(
                """
                SELECT
                    COUNT(DISTINCT gs.table_id) FILTER (WHERE gs.ended_at IS NULL)
                        AS active_tables,
                    COALESCE(SUM(gs.total_rounds), 0) AS rounds_tonight,
                    COUNT(*) AS sessions_tonight,
                    (SELECT COUNT(*)
                     FROM game_players gp
                     JOIN game_sessions gs2 ON gs2.id = gp.session_id
                     JOIN tables t2 ON t2.id = gs2.table_id
                     WHERE t2.venue_id = $1
                       AND gs2.started_at >= $2
                       AND gp.left_early = FALSE
                    ) AS players_tonight
                FROM game_sessions gs
                JOIN tables t ON t.id = gs.table_id
                WHERE t.venue_id = $1
                  AND gs.started_at >= $2
                """,
                current_user.venue_id,
                tonight_boundary,
            )

            # A1: correlated scalar subqueries for player_count and current_round_type
            # replaced with LEFT JOIN LATERAL — semantically identical, more efficient.
            session_rows = await conn.fetch(
                """
                SELECT
                    gs.id AS session_id,
                    t.id AS table_id,
                    t.table_number,
                    gs.group_label,
                    gs.started_at,
                    gs.created_at,
                    gs.current_round_number,
                    EXTRACT(EPOCH FROM NOW() - gs.started_at) AS seconds_active,
                    EXTRACT(EPOCH FROM NOW() - COALESCE(gs.last_activity_at, gs.created_at))
                        AS idle_seconds,
                    COALESCE(v.retap_interval_minutes, 15) * 60 AS threshold_seconds,
                    COALESCE(pc.cnt, 0) AS player_count,
                    lr.round_type AS current_round_type
                FROM game_sessions gs
                JOIN tables t ON t.id = gs.table_id
                JOIN venues v ON v.id = t.venue_id
                LEFT JOIN LATERAL (
                    SELECT COUNT(*) AS cnt FROM game_players gp
                    WHERE gp.session_id = gs.id AND gp.left_early = FALSE
                ) pc ON TRUE
                LEFT JOIN LATERAL (
                    SELECT r.round_type FROM rounds r
                    WHERE r.session_id = gs.id
                    ORDER BY r.created_at DESC LIMIT 1
                ) lr ON TRUE
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
                "table_id": str(row["table_id"]),
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
                "players_tonight": int(totals_row["players_tonight"]),
                "rounds_tonight": int(totals_row["rounds_tonight"]),
                "sessions_tonight": int(totals_row["sessions_tonight"]),
            },
            "active_sessions": active_sessions,
        }
    except Exception:
        await notify_error("GET /dashboard/overview failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")


class PatchSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = None
    restrict_adult_content: Optional[bool] = None


@router.get("/settings")
@limiter.limit("60/minute")
async def get_settings(
    request: Request,
    current_user: CurrentUser = Depends(require_role("venue_owner")),
):
    # venue_id always comes from the authenticated user — never from the request (BOLA).
    # Only venue_owner role may read settings; venue_staff gets 403.
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT name, restrict_adult_content,
                       retap_interval_minutes, billing_unit,
                       nightly_cap_weekday, nightly_cap_weekend,
                       status, cancelled_at, suspended_at
                FROM venues WHERE id = $1
                """,
                current_user.venue_id,
            )
        if not row:
            raise HTTPException(status_code=404, detail="Venue not found")
        cancelled_at = row["cancelled_at"]
        can_reactivate = (
            row["status"] == "cancelled"
            and cancelled_at is not None
            and (datetime.now(timezone.utc).replace(tzinfo=None) - cancelled_at.replace(tzinfo=None)).days < 7
        )
        return {
            "editable": {
                "name": row["name"],
                "restrict_adult_content": row["restrict_adult_content"],
            },
            "read_only": {
                "retap_interval_minutes": int(row["retap_interval_minutes"]),
                "billing_unit": str(row["billing_unit"]),
                "nightly_cap_weekday": str(row["nightly_cap_weekday"]),
                "nightly_cap_weekend": str(row["nightly_cap_weekend"]),
            },
            "venue_status": {
                "status": row["status"],
                "cancelled_at": cancelled_at.isoformat() if cancelled_at else None,
                "suspended_at": row["suspended_at"].isoformat() if row["suspended_at"] else None,
                "can_reactivate": can_reactivate,
            },
        }
    except HTTPException:
        raise
    except Exception:
        await notify_error("GET /dashboard/settings failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")


@router.patch("/settings")
@limiter.limit("60/minute")
async def patch_settings(
    request: Request,
    body: PatchSettingsRequest,
    current_user: CurrentUser = Depends(require_role("venue_owner")),
):
    # venue_id always comes from the authenticated user — never from the request (BOLA).
    # Only venue_owner role may edit settings; venue_staff gets 403.
    try:
        if body.name is None and body.restrict_adult_content is None:
            raise HTTPException(status_code=400, detail="No fields to update")

        if body.name is not None:
            stripped = body.name.strip()
            if len(stripped) == 0 or len(stripped) > 120:
                raise HTTPException(status_code=422, detail="Name must be 1-120 characters")
            body.name = stripped

        pool = await get_pool()
        async with pool.acquire() as conn:
            # Static queries per field combination — no f-string SQL.
            # Column names are hardcoded string literals; values are $N parameters.
            if body.name is not None and body.restrict_adult_content is not None:
                row = await conn.fetchrow(
                    """
                    UPDATE venues SET name=$1, restrict_adult_content=$2, updated_at=NOW()
                    WHERE id=$3
                    RETURNING name, restrict_adult_content,
                              retap_interval_minutes, billing_unit,
                              nightly_cap_weekday, nightly_cap_weekend
                    """,
                    body.name, body.restrict_adult_content, current_user.venue_id,
                )
            elif body.name is not None:
                row = await conn.fetchrow(
                    """
                    UPDATE venues SET name=$1, updated_at=NOW()
                    WHERE id=$2
                    RETURNING name, restrict_adult_content,
                              retap_interval_minutes, billing_unit,
                              nightly_cap_weekday, nightly_cap_weekend
                    """,
                    body.name, current_user.venue_id,
                )
            else:
                # restrict_adult_content only
                # restrict_adult_content change applies to NEW sessions only
                row = await conn.fetchrow(
                    """
                    UPDATE venues SET restrict_adult_content=$1, updated_at=NOW()
                    WHERE id=$2
                    RETURNING name, restrict_adult_content,
                              retap_interval_minutes, billing_unit,
                              nightly_cap_weekday, nightly_cap_weekend
                    """,
                    body.restrict_adult_content, current_user.venue_id,
                )

        return {
            "editable": {
                "name": row["name"],
                "restrict_adult_content": row["restrict_adult_content"],
            },
            "read_only": {
                "retap_interval_minutes": int(row["retap_interval_minutes"]),
                "billing_unit": str(row["billing_unit"]),
                "nightly_cap_weekday": str(row["nightly_cap_weekday"]),
                "nightly_cap_weekend": str(row["nightly_cap_weekend"]),
            },
        }
    except HTTPException:
        raise
    except Exception:
        await notify_error("PATCH /dashboard/settings failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")


@router.get("/billing")
@limiter.limit("60/minute")
async def get_billing(
    request: Request,
    current_user: CurrentUser = Depends(require_role("venue_owner")),
):
    # venue_id always comes from the authenticated user — never from the request (BOLA).
    # venue_staff must never see billing or dollar figures; only venue_owner allowed.
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            venue_row = await conn.fetchrow(
                """
                SELECT billing_unit, nightly_cap_weekday, nightly_cap_weekend,
                       stripe_customer_id, is_test, status AS venue_status
                FROM venues WHERE id = $1
                """,
                current_user.venue_id,
            )

            # Month start: first 4am of the current local calendar month, in UTC.
            month_start = await conn.fetchval(
                """
                SELECT (
                    (date_trunc('month', (NOW() AT TIME ZONE $1) - INTERVAL '4 hours')
                        + INTERVAL '4 hours')
                    AT TIME ZONE $1
                ) AT TIME ZONE 'UTC'
                """,
                VENUE_TIMEZONE,
            )

            # Tonight's play-date (4am-boundary local date) to pick out of the nights.
            tonight_date = await conn.fetchval(
                "SELECT (date_trunc('day', (NOW() AT TIME ZONE $1) - INTERVAL '4 hours'))::date",
                VENUE_TIMEZONE,
            )

            # Per (table, night) sum of billable blocks from FINALIZED sessions.
            # In-progress sessions aren't billed until they end (blocks NULL).
            block_rows = await conn.fetch(
                """
                SELECT
                    gs.table_id,
                    date_trunc('day', (gs.started_at AT TIME ZONE 'UTC' AT TIME ZONE $3)
                        - INTERVAL '4 hours')::date AS play_date,
                    EXTRACT(DOW FROM date_trunc('day', (gs.started_at AT TIME ZONE 'UTC'
                        AT TIME ZONE $3) - INTERVAL '4 hours'))::int AS dow,
                    COALESCE(SUM(gs.billable_blocks), 0)::int AS raw_blocks
                FROM game_sessions gs
                WHERE gs.venue_id = $1
                  AND gs.started_at >= $2
                  AND gs.ended_at IS NOT NULL
                GROUP BY gs.table_id, play_date, dow
                """,
                current_user.venue_id, month_start, VENUE_TIMEZONE,
            )

            # Play-time analytics: billed span vs true (idle-excluded) play.
            stats_row = await conn.fetchrow(
                """
                SELECT
                    COALESCE(SUM(active_span_seconds), 0)::int AS span_secs,
                    COALESCE(SUM(active_play_seconds), 0)::int AS play_secs,
                    COALESCE(SUM(billable_blocks), 0)::int AS blocks,
                    COUNT(*) FILTER (WHERE billable_blocks > 0) AS billable_sessions
                FROM game_sessions
                WHERE venue_id = $1 AND started_at >= $2 AND ended_at IS NOT NULL
                """,
                current_user.venue_id, month_start,
            )

            invoice_rows = await conn.fetch(
                """
                SELECT period_start, period_end, total_amount, status
                FROM invoices WHERE venue_id = $1
                ORDER BY period_start DESC LIMIT 12
                """,
                current_user.venue_id,
            )

        unit = venue_row["billing_unit"]
        cap_wd = cap_blocks(venue_row["nightly_cap_weekday"], unit)   # blocks/night
        cap_we = cap_blocks(venue_row["nightly_cap_weekend"], unit)
        block_min = BLOCK_SECONDS // 60

        # Aggregate per night (sum tables), applying the per-table-per-night cap.
        nights: dict = {}
        for r in block_rows:
            cap = cap_we if r["dow"] in (0, 6) else cap_wd
            raw = r["raw_blocks"]
            billed = min(raw, cap)
            n = nights.setdefault(r["play_date"], {
                "date": str(r["play_date"]), "tables": 0,
                "blocks_raw": 0, "blocks_billed": 0, "_amount": 0, "cap_applied": False,
            })
            n["tables"] += 1
            n["blocks_raw"] += raw
            n["blocks_billed"] += billed
            n["_amount"] += unit * billed
            if raw > cap:
                n["cap_applied"] = True

        month_total = sum(n["_amount"] for n in nights.values())
        tonight = nights.get(tonight_date)

        span_min = round(stats_row["span_secs"] / 60.0, 1)
        play_min = round(stats_row["play_secs"] / 60.0, 1)
        billed_blocks = stats_row["blocks"]

        def _night_out(n: dict) -> dict:
            return {
                "date": n["date"], "tables": n["tables"],
                "blocks_raw": n["blocks_raw"], "blocks_billed": n["blocks_billed"],
                "amount": f"{n['_amount']:.2f}", "cap_applied": n["cap_applied"],
            }

        return {
            "is_estimate": True,
            "venue_status": venue_row["venue_status"],
            "model": {
                "billing_unit": str(unit),
                "block_minutes": block_min,
                "nightly_cap_weekday": str(venue_row["nightly_cap_weekday"]),
                "nightly_cap_weekend": str(venue_row["nightly_cap_weekend"]),
                "blocks_per_night_cap_weekday": cap_wd,
                "blocks_per_night_cap_weekend": cap_we,
                "currency": "AUD",
            },
            "is_test_venue": bool(venue_row["is_test"]),
            "tonight": {
                "date": str(tonight_date),
                "blocks_billed": tonight["blocks_billed"] if tonight else 0,
                "total": f"{(tonight['_amount'] if tonight else 0):.2f}",
                "cap_applied": bool(tonight["cap_applied"]) if tonight else False,
            },
            "month_estimate": {
                "total": f"{month_total:.2f}",
                "blocks_billed": billed_blocks,
                "nights": [_night_out(nights[d]) for d in sorted(nights)],
            },
            "play_analytics": {
                "billable_sessions": int(stats_row["billable_sessions"]),
                "billed_span_minutes": span_min,
                "actual_play_minutes": play_min,
                "billed_blocks": billed_blocks,
                # billed-span minutes not captured by whole blocks (your "+14 min")
                "unbilled_remainder_minutes": max(0.0, round(span_min - billed_blocks * block_min, 1)),
            },
            "payment_status": "connected" if venue_row["stripe_customer_id"] else "not_connected",
            "invoice_history": [
                {
                    "period_start": str(iv["period_start"]),
                    "period_end": str(iv["period_end"]),
                    "total_amount": f"{iv['total_amount']:.2f}",
                    "status": iv["status"],
                }
                for iv in invoice_rows
            ],
        }
    except Exception:
        await notify_error("GET /dashboard/billing failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")


class CancelVenueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(..., min_length=1, max_length=500)


@router.post("/cancel")
@limiter.limit("5/minute")
async def cancel_venue_endpoint(
    request: Request,
    body: CancelVenueRequest,
    current_user: CurrentUser = Depends(require_role("venue_owner")),
):
    # BOLA: venue_id always from auth, never from the request body.
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            try:
                # One transaction so the service's SELECT ... FOR UPDATE lock holds
                # across the status flip + final-invoice issuance — otherwise asyncpg
                # autocommit releases the lock immediately (double-cancel race + a
                # partial-failure window where the venue is cancelled with no invoice).
                async with conn.transaction():
                    result = await venue_lifecycle_service.cancel_venue(
                        conn, current_user.venue_id, body.reason.strip(),
                    )
            except ValueError as e:
                if str(e) == "venue_suspended":
                    raise HTTPException(
                        status_code=409,
                        detail="Your account is suspended. Please settle your outstanding balance first.",
                    )
                raise HTTPException(status_code=409, detail=str(e))
        return result
    except HTTPException:
        raise
    except Exception:
        await notify_error("POST /dashboard/cancel failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")


@router.post("/reactivate")
@limiter.limit("5/minute")
async def reactivate_venue_endpoint(
    request: Request,
    current_user: CurrentUser = Depends(require_role("venue_owner")),
):
    # BOLA: venue_id always from auth, no body needed.
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            try:
                # Same atomicity reason as cancel: hold the FOR UPDATE lock across
                # the window re-check + status flip.
                async with conn.transaction():
                    result = await venue_lifecycle_service.reactivate_venue(
                        conn, current_user.venue_id,
                    )
            except ValueError as e:
                if str(e) == "reactivation_window_expired":
                    raise HTTPException(
                        status_code=409,
                        detail="Reactivation window has expired. Contact support.",
                    )
                if str(e) == "venue_suspended":
                    raise HTTPException(
                        status_code=409,
                        detail="Your account is suspended. Contact support.",
                    )
                raise HTTPException(status_code=409, detail=str(e))
        return result
    except HTTPException:
        raise
    except Exception:
        await notify_error("POST /dashboard/reactivate failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")


@router.get("/themes")
@limiter.limit("60/minute")
async def list_themes(
    request: Request,
    current_user: CurrentUser = Depends(require_role("venue_owner", "venue_staff")),
):
    """Available themes for the picker. Test themes (is_test) force a single
    round type — handy for isolating a game while testing/billing."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT theme_key, display_name, is_test FROM themes ORDER BY is_test, display_name")
    return {"themes": [
        {"theme_key": r["theme_key"], "display_name": r["display_name"], "is_test": r["is_test"]}
        for r in rows
    ]}


@router.get("/theme")
@limiter.limit("60/minute")
async def get_active_theme(
    request: Request,
    current_user: CurrentUser = Depends(require_role("venue_owner", "venue_staff")),
):
    """The venue's theme for tonight (or the 'random' default)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        theme = await resolve_active_theme(conn, current_user.venue_id)
    return {"theme_key": theme["theme_key"], "display_name": theme["display_name"]}


class SetThemeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    theme_key: str = Field(min_length=1, max_length=64)


@router.post("/theme")
@limiter.limit("30/minute")
async def set_theme(
    request: Request,
    body: SetThemeRequest,
    current_user: CurrentUser = Depends(require_role("venue_owner")),
):
    """Set tonight's theme (owner only). Upserts the venue's selection for the
    current 4am-boundary play-date."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM themes WHERE theme_key = $1", body.theme_key)
        if not exists:
            raise HTTPException(status_code=404, detail="Unknown theme")
        await conn.execute(
            """
            INSERT INTO nightly_theme_selections (id, venue_id, selected_date, theme_key)
            VALUES (gen_random_uuid(), $1,
                (date_trunc('day', (NOW() AT TIME ZONE $2) - INTERVAL '4 hours'))::date, $3)
            ON CONFLICT (venue_id, selected_date) DO UPDATE SET theme_key = $3
            """,
            current_user.venue_id, VENUE_TIMEZONE, body.theme_key)
    return {"theme_key": body.theme_key}


@router.get("/session-billing/{session_id}")
@limiter.limit("120/minute")
async def session_billing(
    request: Request,
    session_id: str,
    current_user: CurrentUser = Depends(require_role("venue_owner")),
):
    """Per-session billing breakdown: rounds by type + active span + blocks + cost
    + time-to-next-block. Answers 'how many rounds / minutes of each game hit a
    15-min block, and what it costs'. Live sessions show a PROVISIONAL block count
    (frozen only at session end). Owner-only, BOLA-checked by venue_id."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT gs.venue_id, gs.started_at, gs.ended_at, gs.total_rounds,
                       gs.billable_blocks, gs.active_span_seconds, gs.active_play_seconds,
                       gs.billing_finalized_at,
                       GREATEST(0, EXTRACT(EPOCH FROM
                           COALESCE(gs.last_activity_at, gs.started_at) - gs.started_at))::int
                           AS live_span_seconds,
                       v.billing_unit
                FROM game_sessions gs JOIN venues v ON v.id = gs.venue_id
                WHERE gs.id = $1 AND gs.venue_id = $2
                """,
                session_id, current_user.venue_id,
            )
            if not row:
                raise HTTPException(status_code=404, detail="Not found")
            type_rows = await conn.fetch(
                "SELECT round_type, COUNT(*) AS c FROM rounds WHERE session_id = $1 GROUP BY round_type",
                session_id,
            )

        rounds_by_type = {"chooser": 0, "trivia": 0, "roulette": 0}
        for r in type_rows:
            if r["round_type"] in rounds_by_type:
                rounds_by_type[r["round_type"]] = int(r["c"])

        finalized = row["billing_finalized_at"] is not None
        span = int(row["active_span_seconds"]) if finalized else int(row["live_span_seconds"])
        played = int(row["total_rounds"]) >= 1
        if finalized:
            blocks = int(row["billable_blocks"] or 0)
        else:
            blocks = (span // BLOCK_SECONDS) if played else 0
        unit = row["billing_unit"]
        amount = unit * blocks
        # Time until the next whole block accrues (only meaningful once a round is played).
        secs_to_next = (BLOCK_SECONDS - (span % BLOCK_SECONDS)) if played else BLOCK_SECONDS

        return {
            "session_id": session_id,
            "finalized": finalized,
            "rounds_by_type": rounds_by_type,
            "total_rounds": int(row["total_rounds"]),
            "active_span_seconds": span,
            "active_span_minutes": round(span / 60.0, 1),
            "active_play_seconds": int(row["active_play_seconds"] or 0) if finalized else None,
            "block_minutes": BLOCK_SECONDS // 60,
            "billable_blocks": blocks,
            "billing_unit": str(unit),
            "amount": f"{amount:.2f}",
            "seconds_to_next_block": secs_to_next,
            "minutes_to_next_block": round(secs_to_next / 60.0, 1),
        }
    except HTTPException:
        raise
    except Exception:
        await notify_error("GET /dashboard/session-billing failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")


@router.post("/billing/sync")
@limiter.limit("10/minute")
async def sync_billing_to_stripe(
    request: Request,
    current_user: CurrentUser = Depends(require_role("venue_owner")),
):
    """Push this venue's latest invoice to Stripe (test mode; stub until real test
    keys are set). Returns the stripe customer + invoice ids. No real charge is
    possible — test keys + test customers only."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        invoice_id = await conn.fetchval(
            "SELECT id FROM invoices WHERE venue_id = $1 ORDER BY period_start DESC LIMIT 1",
            current_user.venue_id)
        if not invoice_id:
            raise HTTPException(status_code=404, detail="No invoice to sync")
        result = await stripe_service.sync_invoice(conn, invoice_id)
    return result


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


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "venue"


_VENUE_INSERT = (
    "INSERT INTO venues (id, name, slug, venue_type, address, latitude, longitude, place_id) "
    "VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, $6, $7) RETURNING id"
)


async def _insert_venue_unique_slug(conn, body):
    """Insert the venue with a unique slug, collision-safe under concurrency: each
    attempt runs in a savepoint, so a UNIQUE(slug) violation rolls back only that
    attempt (not the outer transaction). Bounded, with a random-suffix fallback for a
    very hot base slug."""
    base = _slugify(body.name)

    async def _try(slug):
        return await conn.fetchval(
            _VENUE_INSERT, body.name, slug, body.venue_type,
            body.address, body.latitude, body.longitude, body.place_id,
        )

    for attempt in range(20):
        slug = base if attempt == 0 else f"{base}-{attempt + 1}"
        try:
            async with conn.transaction():  # savepoint
                return await _try(slug)
        except asyncpg.UniqueViolationError:
            continue
    async with conn.transaction():
        return await _try(f"{base}-{secrets.token_hex(3)}")


class SetupVenueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    venue_type: Literal["cafe", "pub", "bar", "brewery", "other"]
    table_count: int = Field(ge=1, le=50)
    allow_adult: bool = False
    address: Optional[str] = Field(default=None, max_length=400)
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    place_id: Optional[str] = Field(default=None, max_length=200)
    # Informational only: links setup to the invite for future analytics.
    # The hard gate is the frontend showing "Contact us" for un-invited owners.
    invite_code: Optional[str] = Field(None, max_length=100)


class RedeemInviteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(..., min_length=10, max_length=100)


@router.post("/redeem-invite")
@limiter.limit("10/minute")
async def redeem_invite(
    request: Request,
    body: RedeemInviteRequest,
    current_user: CurrentUser = Depends(require_role("venue_owner")),
):
    """Redeem an active invite code, marking it used and returning prefill data."""
    if current_user.venue_id is not None:
        raise HTTPException(status_code=409, detail="Venue already set up")
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Fetch the owner's email to record on the invite for analytics.
            user_email = await conn.fetchval(
                "SELECT email FROM users WHERE id = $1", current_user.id,
            )
            # Atomic single-use claim: the UPDATE's own WHERE clause enforces
            # active + unexpired, so two concurrent redeems of the same code can't
            # both win — the loser matches 0 rows and gets the 404 below.
            invite = await conn.fetchrow(
                """
                UPDATE venue_invites
                SET status = 'used', used_by = $1, used_at = NOW(), signup_email = $2
                WHERE code = $3 AND status = 'active' AND expires_at > NOW()
                RETURNING venue_name, address, lat, lng, place_id
                """,
                current_user.id, user_email, body.code,
            )
            if not invite:
                raise HTTPException(status_code=404, detail="Invalid or expired invite code")
        return {
            "invite": {
                "venue_name": invite["venue_name"],
                "address": invite["address"],
                "lat": float(invite["lat"]) if invite["lat"] is not None else None,
                "lng": float(invite["lng"]) if invite["lng"] is not None else None,
                "place_id": invite["place_id"],
            }
        }
    except HTTPException:
        raise
    except Exception:
        await notify_error("POST /dashboard/redeem-invite failed \U0001f6a8", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")


@router.post("/setup-venue")
@limiter.limit("10/minute")
async def setup_venue(
    request: Request,
    body: SetupVenueRequest,
    current_user: CurrentUser = Depends(require_role("venue_owner")),
):
    """First-run setup for a newly-provisioned owner with no venue yet: creates the
    venue + its tables (numbered 1..N) and links them to the owner. One transaction."""
    if current_user.venue_id is not None:  # fast-fail on the cached identity
        raise HTTPException(status_code=409, detail="Venue already set up")
    content_ceiling = "adults_allowed" if body.allow_adult else "standard"
    uid = uuid.UUID(current_user.id)
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Lock the owner row + authoritative re-check INSIDE the tx, so a
                # double-submit (or a stale token) cannot create a second orphan venue.
                urow = await conn.fetchrow(
                    "SELECT venue_id FROM users WHERE id = $1 FOR UPDATE", uid)
                if urow is None:
                    raise HTTPException(status_code=404, detail="User not found")
                if urow["venue_id"] is not None:
                    raise HTTPException(status_code=409, detail="Venue already set up")
                venue_id = await _insert_venue_unique_slug(conn, body)
                for n in range(1, body.table_count + 1):
                    await conn.execute(
                        "INSERT INTO tables (id, venue_id, table_number, content_ceiling) "
                        "VALUES (gen_random_uuid(), $1, $2, $3)",
                        venue_id, n, content_ceiling,
                    )
                await conn.execute(
                    "UPDATE users SET venue_id = $1 WHERE id = $2", venue_id, uid)
                slug = await conn.fetchval("SELECT slug FROM venues WHERE id = $1", venue_id)
        return {"venue_id": str(venue_id), "slug": slug, "table_count": body.table_count}
    except HTTPException:
        raise
    except Exception:
        await notify_error("POST /dashboard/setup-venue failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")


# Bias autocomplete toward the current market (AU) so local results rank first;
# without this Photon ranks globally ("55 Elizabeth St" -> Connecticut before Melbourne).
_GEO_BIAS = {"lat": "-37.8136", "lon": "144.9631"}  # Melbourne CBD

# Small bounded cache so repeated keystrokes / identical queries don't re-hit the free
# public Photon endpoint (protects its politeness budget). Cleared wholesale when full.
_GEO_CACHE = {}
_GEO_CACHE_MAX = 512


def _photon_parts(p: dict):
    """Split Photon (OSM) properties into (name, address, label): name is the POI name
    (empty for a plain address), address omits the name, label is for display."""
    name = p.get("name") or ""
    street = " ".join(x for x in [p.get("housenumber"), p.get("street")] if x)
    out, seen = [], set()
    for c in [street, p.get("city"), p.get("state"), p.get("postcode"), p.get("country")]:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    address = ", ".join(out)
    label = f"{name}, {address}" if (name and address) else (name or address)
    return name, address, label


@router.get("/geo/autocomplete")
@limiter.limit("15/minute")
async def geo_autocomplete(
    request: Request,
    q: str = Query(..., min_length=3, max_length=120),
    current_user: CurrentUser = Depends(require_role("venue_owner", "venue_staff", "admin")),
):
    """Keyless address/venue autocomplete via Photon (OpenStreetMap), AU-biased. Searches
    addresses AND named places, so typing a venue name can surface its address (when it's
    in OSM). Proxied for User-Agent + rate-limit + cache; returns name/address split +
    coords; fails soft to []."""
    key = q.strip().lower()
    if key in _GEO_CACHE:
        return {"suggestions": _GEO_CACHE[key]}
    try:
        async with httpx.AsyncClient(
            timeout=8, headers={"User-Agent": "MingleHub/1.0 (venue onboarding)"}
        ) as client:
            r = await client.get(
                "https://photon.komoot.io/api/", params={"q": q, "limit": 6, **_GEO_BIAS}
            )
        if r.status_code != 200:
            return {"suggestions": []}
        feats = r.json().get("features", [])
    except Exception:
        return {"suggestions": []}
    out = []
    for f in feats:
        p = f.get("properties", {}) or {}
        coords = (f.get("geometry") or {}).get("coordinates") or [None, None]
        name, address, label = _photon_parts(p)
        if label:
            out.append({
                "label": label,
                "name": name,
                "address": address,
                "place_id": str(p["osm_id"]) if p.get("osm_id") else None,
                "longitude": coords[0],
                "latitude": coords[1],
            })
    if len(_GEO_CACHE) >= _GEO_CACHE_MAX:
        _GEO_CACHE.clear()
    _GEO_CACHE[key] = out
    return {"suggestions": out}
