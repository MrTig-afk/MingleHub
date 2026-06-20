import re
import traceback
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.db import get_pool
from api.security import limiter, verify_api_key
from api.services import chooser_service, lobby_service, realtime_auth, round_service, trivia_service
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


class AnswerRequest(PhoneIdBody):
    question_index: int = Field(ge=0)
    selected_option: str = Field(pattern="^[ABCD]$")
    # Client-measured time from displaying the question to answering (self-paced
    # timer). Correctness is still checked server-side; only the points bucket
    # uses this. Defaults to 0 (treated as "before timer").
    time_to_answer_ms: int = Field(default=0, ge=0)


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
                is_member = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM game_sessions
                        WHERE table_id = $1 AND ended_at IS NULL AND origin_phone_id = $2
                    )
                    """,
                    body.table_id, body.phone_id,
                )
            if not is_member:
                is_member = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM game_sessions gs
                        JOIN table_lobbies tl ON tl.converted_session_id = gs.id
                        JOIN table_lobby_phones tlp ON tlp.lobby_id = tl.id
                        WHERE gs.table_id = $1 AND gs.ended_at IS NULL AND tlp.phone_id = $2
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
                result = await lobby_service.join_existing_session(conn, session_id, body.name, body.phone_id)
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
    """Basic leave-mid-session: mark this phone's player left_early."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            result = await _run_trivia(
                "leave", trivia_service.leave_session(conn, session_id, body.phone_id)
            )
    except HTTPException:
        raise
    except Exception:
        await notify_error("POST /patron/sessions/leave failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")
    return result
