import traceback
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

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

            # A4: players_tonight and active_sessions_now merged into totals_row
            # as scalar subqueries to save two DB round-trips.
            # Platform totals (is_test venues excluded per security.md line 145).
            totals_row = await conn.fetchrow(
                """
                SELECT
                    (SELECT COUNT(*) FROM venues WHERE is_test = FALSE) AS total_venues,
                    COUNT(DISTINCT gs.venue_id) FILTER (WHERE gs.ended_at IS NULL) AS active_venues_now,
                    COUNT(*) AS sessions_tonight,
                    COALESCE(SUM(gs.total_rounds), 0) AS rounds_tonight,
                    (SELECT COUNT(*)
                     FROM game_players gp
                     JOIN game_sessions gs2 ON gs2.id = gp.session_id
                     JOIN venues v2 ON v2.id = gs2.venue_id
                     WHERE v2.is_test = FALSE
                       AND gs2.started_at >= $1
                       AND gp.left_early = FALSE
                    ) AS players_tonight,
                    (SELECT COUNT(*)
                     FROM game_sessions gs3
                     JOIN venues v3 ON v3.id = gs3.venue_id
                     WHERE v3.is_test = FALSE
                       AND gs3.ended_at IS NULL
                    ) AS active_sessions_now
                FROM game_sessions gs
                JOIN venues v ON v.id = gs.venue_id
                WHERE v.is_test = FALSE
                  AND gs.started_at >= $1
                """,
                tonight_boundary,
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
                "active_sessions_now": int(totals_row["active_sessions_now"]),
                "sessions_tonight": int(totals_row["sessions_tonight"]),
                "players_tonight": int(totals_row["players_tonight"]),
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
        await notify_error("GET /admin/venues failed \U0001f6a8", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")


# ---------------------------------------------------------------------------
# Pydantic models for Slice 5 write endpoints
# ---------------------------------------------------------------------------

class AdminVenueOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=500)
    billing_unit: Optional[float] = Field(None, ge=0)
    retap_interval_minutes: Optional[int] = Field(None, gt=0)
    nightly_cap_weekday: Optional[float] = Field(None, ge=0)
    nightly_cap_weekend: Optional[float] = Field(None, ge=0)
    restrict_adult_content: Optional[bool] = None
    is_test: Optional[bool] = None
    status: Optional[Literal["active", "suspended"]] = None


class PatchSupportMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["open", "resolved"]


class CreateLead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(None, max_length=200)
    email: Optional[str] = Field(None, max_length=300)
    phone: Optional[str] = Field(None, max_length=50)
    venue_name: Optional[str] = Field(None, max_length=200)
    source: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = Field(None, max_length=2000)


# ---------------------------------------------------------------------------
# 2A. GET /api/admin/venues/{venue_id} -- Venue Detail
# ---------------------------------------------------------------------------

@router.get("/venues/{venue_id}")
@limiter.limit("60/minute")
async def admin_venue_detail(
    venue_id: str,
    request: Request,
    current_user: CurrentUser = Depends(require_role("admin")),
):
    try:
        try:
            validated_id = str(uuid.UUID(venue_id))
        except (ValueError, AttributeError):
            raise HTTPException(status_code=404, detail="Venue not found")

        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, name, slug, venue_type, status, billing_unit,
                       retap_interval_minutes, nightly_cap_weekday, nightly_cap_weekend,
                       restrict_adult_content, is_test, created_at, updated_at
                FROM venues WHERE id = $1
                """,
                validated_id,
            )
            if not row:
                raise HTTPException(status_code=404, detail="Venue not found")

            table_count = await conn.fetchval(
                "SELECT COUNT(*) FROM tables WHERE venue_id = $1",
                validated_id,
            )

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

            sessions_tonight = await conn.fetchval(
                "SELECT COUNT(*) FROM game_sessions WHERE venue_id = $1 AND started_at >= $2",
                validated_id,
                tonight_boundary,
            )

            lifetime_sessions = await conn.fetchval(
                "SELECT COUNT(*) FROM game_sessions WHERE venue_id = $1",
                validated_id,
            )

        return {
            "venue": {
                "id": str(row["id"]),
                "name": row["name"],
                "slug": row["slug"],
                "venue_type": row["venue_type"],
                "status": row["status"],
                "billing_unit": str(row["billing_unit"]),
                "retap_interval_minutes": int(row["retap_interval_minutes"]),
                "nightly_cap_weekday": str(row["nightly_cap_weekday"]),
                "nightly_cap_weekend": str(row["nightly_cap_weekend"]),
                "restrict_adult_content": bool(row["restrict_adult_content"]),
                "is_test": bool(row["is_test"]),
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            },
            "table_count": int(table_count),
            "sessions_tonight": int(sessions_tonight),
            "lifetime_sessions": int(lifetime_sessions),
        }
    except HTTPException:
        raise
    except Exception:
        await notify_error("GET /admin/venues/{id} failed \U0001f6a8", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")


# ---------------------------------------------------------------------------
# 2B. PATCH /api/admin/venues/{venue_id} -- Config Override + Audit
# ---------------------------------------------------------------------------

# Static per-field UPDATE SQL. Column names are string literals -- never interpolated.
# Values are parameterised ($1/$2) so no injection is possible.
FIELD_UPDATES = {
    "billing_unit": "UPDATE venues SET billing_unit = $1, updated_at = NOW() WHERE id = $2",
    "retap_interval_minutes": "UPDATE venues SET retap_interval_minutes = $1, updated_at = NOW() WHERE id = $2",
    "nightly_cap_weekday": "UPDATE venues SET nightly_cap_weekday = $1, updated_at = NOW() WHERE id = $2",
    "nightly_cap_weekend": "UPDATE venues SET nightly_cap_weekend = $1, updated_at = NOW() WHERE id = $2",
    "restrict_adult_content": "UPDATE venues SET restrict_adult_content = $1, updated_at = NOW() WHERE id = $2",
    "is_test": "UPDATE venues SET is_test = $1, updated_at = NOW() WHERE id = $2",
    "status": "UPDATE venues SET status = $1, updated_at = NOW() WHERE id = $2",
}


@router.patch("/venues/{venue_id}")
@limiter.limit("60/minute")
async def admin_venue_override(
    venue_id: str,
    request: Request,
    body: AdminVenueOverride,
    current_user: CurrentUser = Depends(require_role("admin")),
):
    try:
        try:
            validated_id = str(uuid.UUID(venue_id))
        except (ValueError, AttributeError):
            raise HTTPException(status_code=404, detail="Venue not found")

        stripped_reason = body.reason.strip()
        if len(stripped_reason) == 0:
            raise HTTPException(status_code=422, detail="Reason must not be blank")

        overridable = {
            "billing_unit": body.billing_unit,
            "retap_interval_minutes": body.retap_interval_minutes,
            "nightly_cap_weekday": body.nightly_cap_weekday,
            "nightly_cap_weekend": body.nightly_cap_weekend,
            "restrict_adult_content": body.restrict_adult_content,
            "is_test": body.is_test,
            "status": body.status,
        }
        provided = {k: v for k, v in overridable.items() if v is not None}

        if len(provided) == 0:
            raise HTTPException(status_code=400, detail="No overridable fields provided")

        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                current = await conn.fetchrow(
                    """
                    SELECT billing_unit, retap_interval_minutes, nightly_cap_weekday,
                           nightly_cap_weekend, restrict_adult_content, is_test, status
                    FROM venues WHERE id = $1 FOR UPDATE
                    """,
                    validated_id,
                )
                if not current:
                    raise HTTPException(status_code=404, detail="Venue not found")

                updated_fields = []
                for field_name, new_value in provided.items():
                    old_value = current[field_name]

                    # Type-coerce Decimal DB values to float for numeric fields
                    if field_name in ("billing_unit", "nightly_cap_weekday", "nightly_cap_weekend"):
                        old_comparable = float(old_value) if old_value is not None else None
                        new_comparable = float(new_value)
                    else:
                        old_comparable = old_value
                        new_comparable = new_value

                    if old_comparable == new_comparable:
                        continue  # No actual change -- skip

                    await conn.execute(FIELD_UPDATES[field_name], new_value, validated_id)

                    await conn.execute(
                        """
                        INSERT INTO venue_config_overrides
                            (id, venue_id, field_name, old_value, new_value, reason, changed_by, created_at)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
                        """,
                        str(uuid.uuid4()),
                        validated_id,
                        field_name,
                        str(old_value) if old_value is not None else None,
                        str(new_value),
                        stripped_reason,
                        current_user.id,
                    )
                    updated_fields.append(field_name)

        return {"updated_fields": updated_fields, "overrides_recorded": len(updated_fields)}
    except HTTPException:
        raise
    except Exception:
        await notify_error("PATCH /admin/venues/{id} failed \U0001f6a8", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")


# ---------------------------------------------------------------------------
# 2C. GET /api/admin/venues/{venue_id}/config-history
# ---------------------------------------------------------------------------

@router.get("/venues/{venue_id}/config-history")
@limiter.limit("60/minute")
async def admin_venue_config_history(
    venue_id: str,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    try:
        try:
            validated_id = str(uuid.UUID(venue_id))
        except (ValueError, AttributeError):
            raise HTTPException(status_code=404, detail="Venue not found")

        pool = await get_pool()
        async with pool.acquire() as conn:
            exists = await conn.fetchval(
                "SELECT id FROM venues WHERE id = $1",
                validated_id,
            )
            if not exists:
                raise HTTPException(status_code=404, detail="Venue not found")

            # A5: total count for pagination metadata (ignores limit/offset).
            total_count = await conn.fetchval(
                "SELECT COUNT(*) FROM venue_config_overrides WHERE venue_id = $1",
                validated_id,
            )

            rows = await conn.fetch(
                """
                SELECT vco.id, vco.field_name, vco.old_value, vco.new_value, vco.reason,
                       vco.changed_by, u.clerk_user_id AS changed_by_clerk_id,
                       vco.created_at
                FROM venue_config_overrides vco
                LEFT JOIN users u ON u.id = vco.changed_by
                WHERE vco.venue_id = $1
                ORDER BY vco.created_at DESC
                LIMIT $2 OFFSET $3
                """,
                validated_id,
                limit,
                offset,
            )

        return {
            "history": [
                {
                    "id": str(row["id"]),
                    "field_name": row["field_name"],
                    "old_value": row["old_value"],
                    "new_value": row["new_value"],
                    "reason": row["reason"],
                    "changed_by": str(row["changed_by"]) if row["changed_by"] else None,
                    "changed_by_clerk_id": row["changed_by_clerk_id"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                }
                for row in rows
            ],
            "total": int(total_count),
            "limit": limit,
            "offset": offset,
        }
    except HTTPException:
        raise
    except Exception:
        await notify_error("GET /admin/venues/{id}/config-history failed \U0001f6a8", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")


# ---------------------------------------------------------------------------
# 2D. GET /api/admin/support
# ---------------------------------------------------------------------------

@router.get("/support")
@limiter.limit("60/minute")
async def admin_support_list(
    request: Request,
    status_filter: Literal["open", "resolved", "all"] = Query("open", alias="status"),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            if status_filter == "all":
                rows = await conn.fetch(
                    """
                    SELECT id, venue_id, name, email, message, status, created_at
                    FROM support_messages
                    ORDER BY created_at DESC
                    """
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT id, venue_id, name, email, message, status, created_at
                    FROM support_messages
                    WHERE status = $1
                    ORDER BY created_at DESC
                    """,
                    status_filter,
                )

        return {
            "messages": [
                {
                    "id": str(row["id"]),
                    "venue_id": str(row["venue_id"]) if row["venue_id"] else None,
                    "name": row["name"],
                    "email": row["email"],
                    "message": row["message"],
                    "status": row["status"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                }
                for row in rows
            ]
        }
    except HTTPException:
        raise
    except Exception:
        await notify_error("GET /admin/support failed \U0001f6a8", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")


# ---------------------------------------------------------------------------
# 2E. PATCH /api/admin/support/{message_id}
# ---------------------------------------------------------------------------

@router.patch("/support/{message_id}")
@limiter.limit("60/minute")
async def admin_support_patch(
    message_id: str,
    request: Request,
    body: PatchSupportMessage,
    current_user: CurrentUser = Depends(require_role("admin")),
):
    try:
        try:
            validated_id = str(uuid.UUID(message_id))
        except (ValueError, AttributeError):
            raise HTTPException(status_code=404, detail="Message not found")

        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE support_messages SET status = $1 WHERE id = $2
                RETURNING id, venue_id, name, email, message, status, created_at
                """,
                body.status,
                validated_id,
            )
        if not row:
            raise HTTPException(status_code=404, detail="Message not found")

        return {
            "id": str(row["id"]),
            "venue_id": str(row["venue_id"]) if row["venue_id"] else None,
            "name": row["name"],
            "email": row["email"],
            "message": row["message"],
            "status": row["status"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        }
    except HTTPException:
        raise
    except Exception:
        await notify_error("PATCH /admin/support/{id} failed \U0001f6a8", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")


# ---------------------------------------------------------------------------
# 2F. GET /api/admin/leads
# ---------------------------------------------------------------------------

@router.get("/leads")
@limiter.limit("60/minute")
async def admin_leads_list(
    request: Request,
    current_user: CurrentUser = Depends(require_role("admin")),
):
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, name, email, phone, venue_name, source, notes, created_at
                FROM leads
                ORDER BY created_at DESC
                """
            )

        return {
            "leads": [
                {
                    "id": str(row["id"]),
                    "name": row["name"],
                    "email": row["email"],
                    "phone": row["phone"],
                    "venue_name": row["venue_name"],
                    "source": row["source"],
                    "notes": row["notes"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                }
                for row in rows
            ]
        }
    except HTTPException:
        raise
    except Exception:
        await notify_error("GET /admin/leads failed \U0001f6a8", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")


# ---------------------------------------------------------------------------
# 2G. POST /api/admin/leads
# ---------------------------------------------------------------------------

@router.post("/leads", status_code=201)
@limiter.limit("60/minute")
async def admin_leads_create(
    request: Request,
    body: CreateLead,
    current_user: CurrentUser = Depends(require_role("admin")),
):
    try:
        name = body.name.strip() if body.name else None
        email = body.email.strip() if body.email else None
        phone = body.phone.strip() if body.phone else None
        venue_name = body.venue_name.strip() if body.venue_name else None
        source = body.source.strip() if body.source else None
        notes = body.notes.strip() if body.notes else None

        # Treat empty strings as None after strip
        name = name or None
        email = email or None
        phone = phone or None
        venue_name = venue_name or None
        source = source or None
        notes = notes or None

        if not name and not email:
            raise HTTPException(status_code=422, detail="At least name or email is required")

        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO leads (id, name, email, phone, venue_name, source, notes, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
                RETURNING id, name, email, phone, venue_name, source, notes, created_at
                """,
                str(uuid.uuid4()),
                name,
                email,
                phone,
                venue_name,
                source,
                notes,
            )

        return {
            "id": str(row["id"]),
            "name": row["name"],
            "email": row["email"],
            "phone": row["phone"],
            "venue_name": row["venue_name"],
            "source": row["source"],
            "notes": row["notes"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        }
    except HTTPException:
        raise
    except Exception:
        await notify_error("POST /admin/leads failed \U0001f6a8", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")


# ---------------------------------------------------------------------------
# 2H. GET /api/admin/team
# ---------------------------------------------------------------------------

@router.get("/team")
@limiter.limit("60/minute")
async def admin_team_list(
    request: Request,
    current_user: CurrentUser = Depends(require_role("admin")),
):
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT u.id, u.clerk_user_id, u.role, u.venue_id, v.name AS venue_name, u.created_at
                FROM users u
                LEFT JOIN venues v ON v.id = u.venue_id
                ORDER BY
                    CASE u.role WHEN 'admin' THEN 0 WHEN 'venue_owner' THEN 1 WHEN 'venue_staff' THEN 2 END,
                    u.created_at
                """
            )

        return {
            "users": [
                {
                    "id": str(row["id"]),
                    "clerk_user_id": row["clerk_user_id"],
                    "role": row["role"],
                    "venue_id": str(row["venue_id"]) if row["venue_id"] else None,
                    "venue_name": row["venue_name"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                }
                for row in rows
            ]
        }
    except HTTPException:
        raise
    except Exception:
        await notify_error("GET /admin/team failed \U0001f6a8", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")
