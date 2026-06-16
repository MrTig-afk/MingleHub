import re
import traceback
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.db import get_pool
from api.security import limiter, verify_api_key
from api.services import lobby_service, round_service
from api.services.notify import notify_error
from api.services.nfc_crypto import decrypt_tag_key
from api.services.nfc_verify import verify_signature

router = APIRouter(prefix="/api/patron", dependencies=[Depends(verify_api_key)])

_SLUG_RE = re.compile(r"^[a-z0-9-]+$")


@router.get("/tap")
@limiter.limit("30/minute")
async def tap(
    request: Request,
    venue_slug: str = Query(...),
    table_number: int = Query(..., gt=0),
    tag_uid: str = Query(...),
    counter: int = Query(..., ge=0),
    sig: str = Query(...),
    phone_id: Optional[str] = Query(None, max_length=64),
):
    """Resolves an NFC tap into a venue/table, proving physical presence.

    Public route — derives venue_id from the slug via a public lookup
    only, the same as every other patron-facing endpoint. Never touches
    the users table (security.md). A generic 404/401 is returned for
    every failure mode so a bad request can't be used to probe which
    venue/table/tag exists.
    """
    if not _SLUG_RE.match(venue_slug):
        raise HTTPException(status_code=404, detail="Not found")

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            venue = await conn.fetchrow(
                "SELECT id, name, slug, restrict_adult_content FROM venues WHERE slug = $1 AND status = 'active'",
                venue_slug,
            )
            if not venue:
                raise HTTPException(status_code=404, detail="Not found")

            table = await conn.fetchrow(
                "SELECT id, content_ceiling FROM tables WHERE venue_id = $1 AND table_number = $2",
                venue["id"], table_number,
            )
            if not table:
                raise HTTPException(status_code=404, detail="Not found")

            tag = await conn.fetchrow(
                """
                SELECT aes_key_encrypted, counter_last_seen
                FROM nfc_tags
                WHERE tag_uid = $1 AND venue_id = $2 AND table_id = $3 AND status = 'active'
                """,
                tag_uid, venue["id"], table["id"],
            )
            if not tag:
                raise HTTPException(status_code=401, detail="Invalid or expired tag")

            raw_key = decrypt_tag_key(tag["aes_key_encrypted"])
            if not verify_signature(raw_key, tag_uid, counter, sig):
                raise HTTPException(status_code=401, detail="Invalid or expired tag")

            # Counter must strictly increase — anything else is a replay.
            last_seen = tag["counter_last_seen"]
            if last_seen is not None and counter <= last_seen:
                raise HTTPException(status_code=401, detail="Invalid or expired tag")

            await conn.execute(
                "UPDATE nfc_tags SET counter_last_seen = $1 WHERE tag_uid = $2",
                counter, tag_uid,
            )

            # Lobby/session resolution only runs when the caller supplies a
            # phone_id — keeps this endpoint's original pure-verification
            # contract (and its existing tests) unchanged when it's omitted.
            table_state = None
            if phone_id:
                table_state = await lobby_service.resolve_table_state(
                    conn, str(venue["id"]), str(table["id"]), table_number, phone_id,
                )
    except HTTPException:
        raise
    except Exception:
        await notify_error("GET /patron/tap failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")

    response = {
        "venue_name": venue["name"],
        "venue_slug": venue["slug"],
        "table_number": table_number,
        "content_ceiling": table["content_ceiling"],
        "restrict_adult_content": venue["restrict_adult_content"],
    }
    if table_state:
        response["table_state"] = table_state
    return response


class PhoneIdBody(BaseModel):
    phone_id: str = Field(min_length=1, max_length=64)


class StartGameRequest(PhoneIdBody):
    player_count: int = Field(ge=lobby_service.MIN_PLAYERS, le=lobby_service.MAX_PLAYERS)
    player_names: Optional[list[str]] = None
    adults_only: bool = False
    group_label: Optional[str] = Field(None, max_length=100)


class JoinSessionRequest(BaseModel):
    phone_id: str = Field(min_length=1, max_length=64)
    name: Optional[str] = Field(None, max_length=60)


@router.get("/lobby/{lobby_id}")
@limiter.limit("60/minute")
async def poll_lobby(request: Request, lobby_id: str):
    """Polled by every phone in the lobby (no realtime infra yet) to learn
    when a host is chosen and when the host starts the game."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            state = await lobby_service.get_lobby_state(conn, lobby_id)
    except Exception:
        await notify_error("GET /patron/lobby/{id} failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")

    if not state:
        raise HTTPException(status_code=404, detail="Not found")
    return state


@router.post("/lobby/{lobby_id}/claim-host")
@limiter.limit("30/minute")
async def claim_lobby_host(request: Request, lobby_id: str, body: PhoneIdBody):
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            if not await lobby_service.is_lobby_member(conn, lobby_id, body.phone_id):
                raise HTTPException(status_code=403, detail="Not in this lobby")
            result = await lobby_service.claim_host(conn, lobby_id, body.phone_id)
    except HTTPException:
        raise
    except Exception:
        await notify_error("POST /patron/lobby/claim-host failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")
    return result


@router.post("/lobby/{lobby_id}/start")
@limiter.limit("30/minute")
async def start_lobby(request: Request, lobby_id: str, body: StartGameRequest):
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            lobby = await lobby_service.get_lobby(conn, lobby_id)
            if not lobby:
                raise HTTPException(status_code=404, detail="Not found")
            try:
                result = await lobby_service.start_game(
                    conn, lobby, body.phone_id, body.player_count,
                    body.player_names, body.adults_only, body.group_label,
                )
            except PermissionError:
                raise HTTPException(status_code=403, detail="Only the host can start the game")
            except ValueError as e:
                if str(e) == "lobby_not_open":
                    raise HTTPException(status_code=409, detail="Lobby already started")
                if str(e) == "adults_only_not_allowed":
                    raise HTTPException(status_code=422, detail="Adults Only is not available at this table")
                raise HTTPException(status_code=422, detail="Invalid player count")
    except HTTPException:
        raise
    except Exception:
        await notify_error("POST /patron/lobby/start failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")
    return result


@router.post("/table/{table_id}/new-group")
@limiter.limit("30/minute")
async def new_group(request: Request, table_id: str, body: PhoneIdBody):
    """"Start a new group at this table" from the Join-or-New chooser."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            venue_id = await conn.fetchval("SELECT venue_id FROM tables WHERE id = $1", table_id)
            if not venue_id:
                raise HTTPException(status_code=404, detail="Not found")
            try:
                result = await lobby_service.start_new_group(conn, str(venue_id), table_id, body.phone_id)
            except ValueError:
                raise HTTPException(status_code=409, detail="This table is full")
    except HTTPException:
        raise
    except Exception:
        await notify_error("POST /patron/table/new-group failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")
    return result


@router.post("/sessions/{session_id}/join")
@limiter.limit("30/minute")
async def join_session(request: Request, session_id: str, body: JoinSessionRequest):
    """"Join their game" from the Join-or-New chooser — adds this phone as
    a new player on an already-active session."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            try:
                result = await lobby_service.join_existing_session(conn, session_id, body.name)
            except LookupError:
                raise HTTPException(status_code=404, detail="Not found")
    except HTTPException:
        raise
    except Exception:
        await notify_error("POST /patron/sessions/join failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")
    return result


@router.post("/sessions/{session_id}/select-hot-seat")
@limiter.limit("60/minute")
async def pick_hot_seat(request: Request, session_id: str, body: PhoneIdBody):
    """gamespec.md Step 5 — Round Flow: the finger picker (running on the
    session-origin phone) has chosen a finger; this resolves that to a
    real game_players row and increments times_selected. Only the phone
    that started the session may call this — the finger picker runs on
    one physical device passed around the table, not on every phone."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            session = await round_service.get_session(conn, session_id)
            if not session:
                raise HTTPException(status_code=404, detail="Not found")
            try:
                result = await round_service.select_hot_seat(conn, session, body.phone_id)
            except PermissionError:
                raise HTTPException(status_code=403, detail="Only the table device can run the finger picker")
            except ValueError as e:
                detail = "Session has ended" if str(e) == "session_ended" else "Need at least 2 active players"
                raise HTTPException(status_code=409, detail=detail)
    except HTTPException:
        raise
    except Exception:
        await notify_error("POST /patron/sessions/select-hot-seat failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")
    return result
