import re
import traceback

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.db import get_pool
from api.security import limiter, verify_api_key
from api.services.notify import notify_error, notify_interest

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

router = APIRouter(prefix="/api", dependencies=[Depends(verify_api_key)])


class InterestPayload(BaseModel):
    email: str
    mode: str = "party"
    trigger: str = "card_limit"


@router.post("/interest")
@limiter.limit("5/minute")
async def capture_interest(request: Request, body: InterestPayload):
    if not _EMAIL_RE.match(body.email):
        raise HTTPException(status_code=422, detail="Invalid email")

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            existing = await conn.fetchval(
                "SELECT 1 FROM premium_interest WHERE email = $1",
                body.email,
            )
            await conn.execute(
                """
                INSERT INTO premium_interest (email, mode, trigger)
                VALUES ($1, $2, $3)
                ON CONFLICT (email) DO UPDATE SET created_at = NOW()
                """,
                body.email, body.mode, body.trigger,
            )
    except Exception:
        await notify_error("Interest capture DB error 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="DB error")

    already_registered = existing is not None
    if not already_registered:
        await notify_interest(body.email, body.mode, body.trigger)

    return JSONResponse({"success": True, "already_registered": already_registered})
