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

router = APIRouter(prefix="/api/dashboard", dependencies=[Depends(verify_api_key)])


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
