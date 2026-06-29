import re
import traceback
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.db import get_pool
from api.security import limiter, verify_api_key
from api.services import (
    chooser_service, lobby_service, realtime_auth, round_service,
    roulette_service, session_service, trivia_service,
)
from api.services.session_service import compute_retap_state
from api.services.theme_service import resolve_active_theme
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
    tag_uid: Optional[str] = Query(None),
    counter: Optional[int] = Query(None, ge=0),
    sig: Optional[str] = Query(None),
    phone_id: Optional[str] = Query(None, max_length=64),
    new_game: bool = Query(False),
):
    """Resolves an NFC tap into a venue/table.

    Supports two paths:
    - Signed path (tag_uid + counter + sig all present): verifies the NTAG 424
      DNA SUN signature and counter monotonicity before resolving the table.
    - Plain-tag path (any of the three is absent): skips crypto verification.
      Used by plain NTAG 213 tags with a static URL. The venue and table must
      still exist — only the NFC crypto check is bypassed.

    Public route — derives venue_id from the slug via a public lookup
    only. Never touches the users table (security.md). A generic 404/401
    is returned so a bad request can't probe which venue/table/tag exists.
    """
    if not _SLUG_RE.match(venue_slug):
        raise HTTPException(status_code=404, detail="Not found")

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            venue = await conn.fetchrow(
                "SELECT id, name, slug, status, restrict_adult_content FROM venues WHERE slug = $1",
                venue_slug,
            )
            if not venue:
                raise HTTPException(status_code=404, detail="Not found")

            # Venue exists but isn't active (cancelled / suspended): surface the
            # informative "venue not active" screen instead of a generic 404
            # "tap didn't go through". Short-circuit here so the result is the
            # same with or without a phone_id, and so we skip the tag crypto and
            # presence logging that only matter for live play. (resolve_table_state
            # carries the same gate, but it only runs when phone_id is present.)
            if venue["status"] != "active":
                return {
                    "venue_name": venue["name"],
                    "venue_slug": venue["slug"],
                    "table_number": table_number,
                    "table_state": {"phase": "venue_inactive", "venue_status": venue["status"]},
                }

            table = await conn.fetchrow(
                "SELECT id, content_ceiling FROM tables WHERE venue_id = $1 AND table_number = $2",
                venue["id"], table_number,
            )
            if not table:
                raise HTTPException(status_code=404, detail="Not found")

            # Only verify NFC crypto when all three signature params are present.
            # A partial or absent set means a plain (unsigned) tag — skip verification.
            is_signed = tag_uid is not None and counter is not None and sig is not None

            if is_signed:
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

                # NFC counter must strictly increase — anything else is a replay.
                last_seen = tag["counter_last_seen"]
                if last_seen is not None and counter <= last_seen:
                    raise HTTPException(status_code=401, detail="Invalid or expired tag")

                await conn.execute(
                    "UPDATE nfc_tags SET counter_last_seen = $1 WHERE tag_uid = $2",
                    counter, tag_uid,
                )

            # Lobby/session resolution runs for both paths when phone_id is provided.
            table_state = None
            if phone_id:
                # Record tap as proof of physical presence — used by POST
                # /sessions/{id}/join to enforce the BOLA presence check.
                await conn.execute(
                    "INSERT INTO table_tap_log (id, table_id, phone_id) VALUES ($1, $2, $3)",
                    str(uuid.uuid4()), str(table["id"]), phone_id,
                )
                table_state = await lobby_service.resolve_table_state(
                    conn, str(venue["id"]), str(table["id"]), table_number, phone_id,
                    force_new=new_game,
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
        "table_id": str(table["id"]),
    }
    if table_state:
        response["table_state"] = table_state
    return response


class PhoneIdBody(BaseModel):
    phone_id: str = Field(min_length=1, max_length=64)


class DrawCardRequest(PhoneIdBody):
    player_id: str = Field(min_length=1)


class StartGameRequest(PhoneIdBody):
    adults_only: bool = False
    group_label: Optional[str] = Field(None, max_length=100)


class SetNameRequest(PhoneIdBody):
    name: str = Field(min_length=1, max_length=60)


class JoinSessionRequest(BaseModel):
    phone_id: str = Field(min_length=1, max_length=64)
    name: Optional[str] = Field(None, max_length=60)


class ChannelAuthRequest(PhoneIdBody):
    table_id: str = Field(min_length=1)


class VoteLoserRequest(PhoneIdBody):
    voted_player_id: str = Field(min_length=1)


class AnswerRequest(PhoneIdBody):
    question_index: int = Field(ge=0)
    selected_option: str = Field(pattern="^[ABCD]$")
    # Client-measured time from displaying the question to answering (self-paced
    # timer). Correctness is still checked server-side; only the points bucket
    # uses this. Defaults to 0 (treated as "before timer").
    time_to_answer_ms: int = Field(default=0, ge=0)


@router.get("/lobby/{lobby_id}")
@limiter.limit("60/minute")
async def poll_lobby(
    request: Request,
    lobby_id: str,
    phone_id: Optional[str] = Query(None, max_length=64),
):
    """Polled by every phone in the lobby (no realtime infra yet) to learn
    when a host is chosen and when the host starts the game."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            state = await lobby_service.get_lobby_state(
                conn, lobby_id, caller_phone_id=phone_id
            )
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


@router.post("/lobby/{lobby_id}/set-name")
@limiter.limit("30/minute")
async def set_lobby_name(request: Request, lobby_id: str, body: SetNameRequest):
    """Patron sets their own name in the lobby before the game starts."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            if not await lobby_service.is_lobby_member(conn, lobby_id, body.phone_id):
                raise HTTPException(status_code=403, detail="Not in this lobby")
            try:
                result = await lobby_service.set_lobby_phone_name(
                    conn, lobby_id, body.phone_id, body.name,
                )
            except LookupError:
                raise HTTPException(status_code=404, detail="Not found")
    except HTTPException:
        raise
    except Exception:
        await notify_error("POST /patron/lobby/set-name failed", traceback.format_exc()[:500])
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
                    conn, lobby, body.phone_id, body.adults_only, body.group_label,
                )
            except PermissionError:
                raise HTTPException(status_code=403, detail="Only the host can start the game")
            except ValueError as e:
                if str(e) == "venue_inactive":
                    raise HTTPException(status_code=409, detail="Venue is not active")
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


@router.post("/channel-auth")
@limiter.limit("30/minute")
async def channel_auth(request: Request, body: ChannelAuthRequest):
    """BOLA-guarded: returns a short-lived Realtime token scoped to the
    table's broadcast channel, only if this phone belongs to an open lobby
    or active session at the table.

    Returns {"realtime_enabled": false} (200) when SUPABASE_* env vars
    are unset. Returns 403 if the phone is not a member.

    The BOLA membership check runs BEFORE is_enabled() so a non-member
    always gets 403 regardless of env configuration.
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # BOLA check: phone must be in an open lobby OR active session at this table.
            # Branch 1 — pre-game open lobby. No game_players rows exist yet.
            is_member = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM table_lobby_phones tlp
                    JOIN table_lobbies tl ON tl.id = tlp.lobby_id
                    WHERE tl.table_id = $1 AND tl.status = 'open' AND tlp.phone_id = $2
                )
                """,
                body.table_id, body.phone_id,
            )
            if not is_member:
                # Branch 2 — session origin. Belt-and-suspenders: verify not left early.
                is_member = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM game_sessions gs
                        JOIN game_players gp ON gp.session_id = gs.id AND gp.phone_id = $2
                        WHERE gs.table_id = $1 AND gs.ended_at IS NULL
                          AND gs.origin_phone_id = $2
                          AND gp.left_early = FALSE
                    )
                    """,
                    body.table_id, body.phone_id,
                )
            if not is_member:
                # Branch 3 — converted lobby member. Critical fix: reject left phones.
                is_member = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM game_sessions gs
                        JOIN table_lobbies tl ON tl.converted_session_id = gs.id
                        JOIN table_lobby_phones tlp ON tlp.lobby_id = tl.id
                        JOIN game_players gp ON gp.session_id = gs.id AND gp.phone_id = tlp.phone_id
                        WHERE gs.table_id = $1 AND gs.ended_at IS NULL
                          AND tlp.phone_id = $2
                          AND gp.left_early = FALSE
                    )
                    """,
                    body.table_id, body.phone_id,
                )
            if not is_member:
                raise HTTPException(status_code=403, detail="Not a member at this table")

        # BOLA passed. Check if realtime is enabled.
        if not realtime_auth.is_enabled():
            return {"realtime_enabled": False}

        channel = f"table:{body.table_id}"
        result = realtime_auth.mint_channel_token(channel, body.phone_id)
        return {"realtime_enabled": True, **result}
    except HTTPException:
        raise
    except Exception:
        await notify_error("POST /patron/channel-auth failed", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")


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
            except ValueError as e:
                if str(e) == "venue_inactive":
                    raise HTTPException(status_code=409, detail="Venue is not active")
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
            # BOLA: phone must have tapped the table this session belongs to.
            # Re-tap idempotency: an existing player already proved presence,
            # so the game_players check bypasses the tap-log requirement.
            existing_player = False
            if body.phone_id:
                existing_player = await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM game_players "
                    "WHERE session_id = $1 AND phone_id = $2)",
                    session_id, body.phone_id,
                )
            if not existing_player:
                has_presence = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM table_tap_log ttl
                        JOIN game_sessions gs ON gs.table_id = ttl.table_id
                        WHERE gs.id = $1 AND ttl.phone_id = $2
                    )
                    """,
                    session_id, body.phone_id,
                )
                if not has_presence:
                    raise HTTPException(
                        status_code=403, detail="Tap the table tag first"
                    )
            try:
                result = await lobby_service.join_existing_session(
                    conn, session_id, body.name, body.phone_id
                )
            except LookupError:
                raise HTTPException(status_code=404, detail="Not found")
            except ValueError:
                raise HTTPException(status_code=409, detail="Session full")
    except HTTPException:
        raise
    except Exception:
        await notify_error(
            "POST /patron/sessions/join failed",
            traceback.format_exc()[:500],
        )
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


@router.post("/sessions/{session_id}/draw-card")
@limiter.limit("60/minute")
async def draw_card(request: Request, session_id: str, body: DrawCardRequest):
    """gamespec.md: Chooser round -- draw a card for the hot-seat player.

    Only the session-origin phone may call this (BOLA guard in service layer).
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            try:
                result = await chooser_service.draw_card(
                    conn, session_id, body.player_id, body.phone_id
                )
            except PermissionError:
                raise HTTPException(status_code=403, detail="Only the table device can draw cards")
            except ValueError as e:
                if str(e) == "session_ended":
                    raise HTTPException(status_code=409, detail="Session has ended")
                if str(e) == "no_active_players":
                    raise HTTPException(status_code=409, detail="No active players")
                raise HTTPException(status_code=409, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        await notify_error("POST /patron/sessions/draw-card failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")
    return result


@router.post("/rounds/{round_id}/complete")
@limiter.limit("60/minute")
async def complete_round(request: Request, round_id: str, body: PhoneIdBody):
    """gamespec.md: Complete a Chooser round -- awards +5 pts to the hot-seat player."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            try:
                result = await chooser_service.complete_round(conn, round_id, body.phone_id)
            except PermissionError:
                raise HTTPException(status_code=403, detail="Only the table device can complete rounds")
            except ValueError as e:
                if str(e) == "round_already_resolved":
                    raise HTTPException(status_code=409, detail="Round already resolved")
                if str(e) == "wrong_round_type":
                    raise HTTPException(status_code=422, detail="Wrong round type")
                raise HTTPException(status_code=409, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        await notify_error("POST /patron/rounds/complete failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")
    return result


@router.post("/rounds/{round_id}/skip")
@limiter.limit("60/minute")
async def skip_round(request: Request, round_id: str, body: PhoneIdBody):
    """gamespec.md: Skip a Chooser round -- 0 pts, no penalty."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            try:
                result = await chooser_service.skip_round(conn, round_id, body.phone_id)
            except PermissionError:
                raise HTTPException(status_code=403, detail="Only the table device can skip rounds")
            except ValueError as e:
                if str(e) == "round_already_resolved":
                    raise HTTPException(status_code=409, detail="Round already resolved")
                if str(e) == "wrong_round_type":
                    raise HTTPException(status_code=422, detail="Wrong round type")
                raise HTTPException(status_code=409, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        await notify_error("POST /patron/rounds/skip failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")
    return result


@router.post("/rounds/{round_id}/redraw")
@limiter.limit("60/minute")
async def redraw_round(request: Request, round_id: str, body: PhoneIdBody):
    """gamespec.md: Redraw a Chooser card -- same category, free twice then -1 pt."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            try:
                result = await chooser_service.redraw(conn, round_id, body.phone_id)
            except PermissionError:
                raise HTTPException(status_code=403, detail="Only the table device can redraw")
            except ValueError as e:
                if str(e) == "round_already_resolved":
                    raise HTTPException(status_code=409, detail="Round already resolved")
                raise HTTPException(status_code=409, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        await notify_error("POST /patron/rounds/redraw failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")
    return result


# ---------------------------------------------------------------------------
# Trivia round (gamespec.md: Round Type 2 -- Trivia). Multi-phone; every player
# answers on their own device. correct_option is checked server-side only and
# never sent to the browser before the phone answers.
# ---------------------------------------------------------------------------

async def _run_trivia(label: str, coro):
    """Shared error mapping for trivia service calls: LookupError -> 404,
    PermissionError -> 403, ValueError -> 409 (detail = the service's reason)."""
    try:
        return await coro
    except PermissionError:
        raise HTTPException(status_code=403, detail="Not allowed for this phone")
    except LookupError:
        raise HTTPException(status_code=404, detail="Not found")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/sessions/{session_id}/trivia/start")
@limiter.limit("30/minute")
async def trivia_start(request: Request, session_id: str, body: PhoneIdBody):
    """Origin opens the Trivia gather phase (picks 5 questions, auto-joins)."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            result = await _run_trivia(
                "start", trivia_service.start_trivia(conn, session_id, body.phone_id)
            )
    except HTTPException:
        raise
    except Exception:
        await notify_error("POST /patron/trivia/start failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")
    return result


@router.post("/trivia/{round_id}/join")
@limiter.limit("60/minute")
async def trivia_join(request: Request, round_id: str, body: PhoneIdBody):
    """A session-member phone joins the gather (idempotent)."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            result = await _run_trivia(
                "join", trivia_service.join_trivia(conn, round_id, body.phone_id)
            )
    except HTTPException:
        raise
    except Exception:
        await notify_error("POST /patron/trivia/join failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")
    return result


@router.post("/trivia/{round_id}/begin")
@limiter.limit("30/minute")
async def trivia_begin(request: Request, round_id: str, body: PhoneIdBody):
    """Origin taps "Start Trivia": gather -> first question (needs >=2 joined)."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            result = await _run_trivia(
                "begin", trivia_service.begin_trivia(conn, round_id, body.phone_id)
            )
    except HTTPException:
        raise
    except Exception:
        await notify_error("POST /patron/trivia/begin failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")
    return result


@router.post("/trivia/{round_id}/answer")
@limiter.limit("120/minute")
async def trivia_answer(request: Request, round_id: str, body: AnswerRequest):
    """A participant answers the current question. Server-side check only."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            result = await _run_trivia(
                "answer",
                trivia_service.submit_answer(
                    conn, round_id, body.phone_id, body.question_index,
                    body.selected_option, body.time_to_answer_ms,
                ),
            )
    except HTTPException:
        raise
    except Exception:
        await notify_error("POST /patron/trivia/answer failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")
    return result


@router.post("/trivia/{round_id}/finish")
@limiter.limit("30/minute")
async def trivia_finish(request: Request, round_id: str, body: PhoneIdBody):
    """Origin finishes after the last question -> leaderboard."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            result = await _run_trivia(
                "finish", trivia_service.finish_trivia(conn, round_id, body.phone_id)
            )
    except HTTPException:
        raise
    except Exception:
        await notify_error("POST /patron/trivia/finish failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")
    return result


@router.post("/trivia/{round_id}/abandon")
@limiter.limit("30/minute")
async def trivia_abandon(request: Request, round_id: str, body: PhoneIdBody):
    """Origin abandons during gather (fewer than 2 phones joined)."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            result = await _run_trivia(
                "abandon", trivia_service.abandon_trivia(conn, round_id, body.phone_id)
            )
    except HTTPException:
        raise
    except Exception:
        await notify_error("POST /patron/trivia/abandon failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")
    return result


@router.get("/sessions/{session_id}/trivia/current")
@limiter.limit("120/minute")
async def trivia_current(request: Request, session_id: str, phone_id: str = Query(..., max_length=64)):
    """Poll fallback for joined phones — current Trivia view for this phone
    (no correct_option for the live question)."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            state = await trivia_service.get_current_state(conn, session_id, phone_id)
    except Exception:
        await notify_error("GET /patron/trivia/current failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")
    if state is None:
        raise HTTPException(status_code=404, detail="Not found")
    return state


@router.get("/sessions/{session_id}/leaderboard")
@limiter.limit("120/minute")
async def session_leaderboard(request: Request, session_id: str):
    """Per-player session leaderboard (between-rounds screen)."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            result = await trivia_service.session_leaderboard(conn, session_id)
    except Exception:
        await notify_error("GET /patron/leaderboard failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")
    return result


@router.post("/sessions/{session_id}/leave")
@limiter.limit("30/minute")
async def leave_session(request: Request, session_id: str, body: PhoneIdBody):
    """Leave the game. If the caller is the origin phone, triggers host
    migration (or end-game if no active players remain)."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Re-verify origin from DB -- never trust the client for BOLA
            origin = await conn.fetchval(
                "SELECT origin_phone_id FROM game_sessions "
                "WHERE id = $1 AND ended_at IS NULL",
                session_id,
            )
            if origin is None:
                raise HTTPException(status_code=404, detail="Not found")

            if origin == body.phone_id:
                # Host leave -> migration path. Route through _run_trivia so its
                # LookupError/PermissionError/ValueError map to 404/403/409 instead
                # of a blanket 500 (e.g. the session ending between the check above
                # and migrate_host's own guard).
                result = await _run_trivia(
                    "migrate_host",
                    session_service.migrate_host(conn, session_id, body.phone_id),
                )
            else:
                # Non-host leave -> existing path
                result = await _run_trivia(
                    "leave",
                    trivia_service.leave_session(conn, session_id, body.phone_id),
                )
    except HTTPException:
        raise
    except Exception:
        await notify_error(
            "POST /patron/sessions/leave failed",
            traceback.format_exc()[:500],
        )
        raise HTTPException(status_code=500, detail="Internal error")
    return result


@router.post("/sessions/{session_id}/rejoin")
@limiter.limit("30/minute")
async def rejoin_session(request: Request, session_id: str, body: PhoneIdBody):
    """Rejoin after leaving: clear this phone's left_early flag (score kept)."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            result = await _run_trivia(
                "rejoin", trivia_service.rejoin_session(conn, session_id, body.phone_id)
            )
    except HTTPException:
        raise
    except Exception:
        await notify_error("POST /patron/sessions/rejoin failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")
    return result


# ---------------------------------------------------------------------------
# Roulette round (gamespec.md: Round Type 3 -- Roulette). Group challenge:
# the whole table plays, then votes on who lost. Origin drives start/skip/reveal;
# every active phone votes via vote-loser. correct_option not applicable here.
# ---------------------------------------------------------------------------

@router.post("/sessions/{session_id}/roulette/start")
@limiter.limit("30/minute")
async def roulette_start(request: Request, session_id: str, body: PhoneIdBody):
    """Origin opens a Roulette round (picks a challenge card, broadcasts)."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            result = await _run_trivia(
                "roulette_start",
                roulette_service.start_roulette(conn, session_id, body.phone_id),
            )
    except HTTPException:
        raise
    except Exception:
        await notify_error("POST /patron/roulette/start failed", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")
    return result


@router.post("/rounds/{round_id}/vote-loser")
@limiter.limit("60/minute")
async def vote_loser(request: Request, round_id: str, body: VoteLoserRequest):
    """Any active player votes for who lost the Roulette challenge."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            result = await _run_trivia(
                "vote_loser",
                roulette_service.cast_vote(conn, round_id, body.phone_id, body.voted_player_id),
            )
    except HTTPException:
        raise
    except Exception:
        await notify_error("POST /patron/rounds/vote-loser failed", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")
    return result


@router.post("/rounds/{round_id}/roulette/reveal")
@limiter.limit("30/minute")
async def roulette_reveal(request: Request, round_id: str, body: PhoneIdBody):
    """Origin force-tallies with votes cast so far (partial votes OK)."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            result = await _run_trivia(
                "roulette_reveal",
                roulette_service.tally_roulette(conn, round_id, body.phone_id),
            )
    except HTTPException:
        raise
    except Exception:
        await notify_error("POST /patron/roulette/reveal failed", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")
    return result


@router.post("/rounds/{round_id}/roulette/skip")
@limiter.limit("30/minute")
async def roulette_skip(request: Request, round_id: str, body: PhoneIdBody):
    """Origin skips the Roulette round -- 0 points, move on."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            result = await _run_trivia(
                "roulette_skip",
                roulette_service.skip_roulette(conn, round_id, body.phone_id),
            )
    except HTTPException:
        raise
    except Exception:
        await notify_error("POST /patron/roulette/skip failed", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")
    return result


@router.post("/sessions/{session_id}/end-game")
@limiter.limit("30/minute")
async def end_game(request: Request, session_id: str, body: PhoneIdBody):
    """Origin-only: end the game for everyone, broadcast game_ended."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            result = await _run_trivia(
                "end_game",
                session_service.end_game(conn, session_id, body.phone_id),
            )
    except HTTPException:
        raise
    except Exception:
        await notify_error("POST /patron/sessions/end-game failed", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")
    return result


@router.get("/sessions/{session_id}/recap")
@limiter.limit("120/minute")
async def get_recap(request: Request, session_id: str):
    """Recap stats for an ended session -- session-scoped, no secrets."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            result = await _run_trivia(
                "recap",
                session_service.get_recap(conn, session_id),
            )
    except HTTPException:
        raise
    except Exception:
        await notify_error("GET /patron/sessions/recap failed", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")
    return result


@router.get("/table/{table_id}/new-game")
@limiter.limit("60/minute")
async def check_new_game(
    request: Request,
    table_id: str,
    after_session: Optional[str] = Query(None, max_length=64),
):
    """Read-only: is a new game forming at this table?
    Returns lobby_id and/or session_id (both nullable). No side effects.
    BOLA: returns only IDs, no phone_ids or host info (redaction pattern)."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Validate table exists (prevents probing arbitrary UUIDs)
            exists = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM tables WHERE id = $1)", table_id
            )
            if not exists:
                raise HTTPException(status_code=404, detail="Not found")
            result = await lobby_service.check_new_game_at_table(
                conn, table_id, after_session
            )
    except HTTPException:
        raise
    except Exception:
        await notify_error(
            "GET /patron/table/new-game failed",
            traceback.format_exc()[:500],
        )
        raise HTTPException(status_code=500, detail="Internal error")
    return result


@router.get("/sessions/{session_id}/current-round")
@limiter.limit("120/minute")
async def current_round(request: Request, session_id: str):
    """Server-authoritative round number + active player count + retap state.
    Used by RoundOrigin on mount (and periodic poll) to seed cadence + overlay."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT gs.current_round_number, gs.ended_at, gs.id, gs.venue_id,
                       EXTRACT(EPOCH FROM NOW() - COALESCE(gs.last_activity_at, gs.created_at))
                           AS idle_seconds,
                       COALESCE(v.retap_interval_minutes, 15) * 60
                           AS threshold_seconds
                FROM game_sessions gs
                JOIN venues v ON v.id = gs.venue_id
                WHERE gs.id = $1
                """,
                session_id,
            )
            if not row:
                raise HTTPException(status_code=404, detail="Not found")
            active_count = await conn.fetchval(
                "SELECT COUNT(*) FROM game_players "
                "WHERE session_id = $1 AND left_early = FALSE",
                session_id,
            )
            # Theme-weighted round selection: the origin phone picks each round's
            # type from these weights (deterministic per round). Resolved here so
            # the whole session uses the venue's theme for tonight.
            theme = await resolve_active_theme(conn, row["venue_id"])
            theme_key = theme.get("theme_key")
            round_type_weights = (theme.get("weighting") or {}).get("round_types", {})
            retap = compute_retap_state(
                float(row["idle_seconds"]),
                int(row["threshold_seconds"]),
            )
            # Lazy expire: end the session if expired and not already ended.
            if retap["state"] == "expired" and row["ended_at"] is None:
                await session_service.idle_end_session(
                    conn, session_id, reason='retap_expired',
                )
                return {
                    "session_id": session_id,
                    "current_round_number": row["current_round_number"],
                    "active_count": int(active_count),
                    "ended": True,
                    "retap": retap,
                    "theme_key": theme_key,
                    "round_type_weights": round_type_weights,
                }
    except HTTPException:
        raise
    except Exception:
        await notify_error(
            "GET /patron/sessions/current-round failed",
            traceback.format_exc()[:500],
        )
        raise HTTPException(status_code=500, detail="Internal error")
    return {
        "session_id": session_id,
        "current_round_number": row["current_round_number"],
        "active_count": int(active_count),
        "ended": row["ended_at"] is not None,
        "retap": retap,
        "theme_key": theme_key,
        "round_type_weights": round_type_weights,
    }
