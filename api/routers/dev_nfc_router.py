import os
import traceback

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.db import get_pool
from api.security import limiter, verify_api_key
from api.services.notify import notify_error
from api.services.nfc_crypto import decrypt_tag_key
from api.services.nfc_verify import sign

# Dev-only — stands in for a physical NTAG 424 DNA tag, which would
# normally compute this signature in hardware. Always 404s outside
# DEV_MODE so it never works in production even if accidentally deployed.
# See api/services/nfc_verify.py for why this is HMAC-based rather than
# real SDM/CMAC.
router = APIRouter(prefix="/api/dev", dependencies=[Depends(verify_api_key)])


class SimulateTapPayload(BaseModel):
    tag_uid: str
    counter: int


@router.post("/simulate-tap")
@limiter.limit("30/minute")
async def simulate_tap(request: Request, body: SimulateTapPayload):
    if os.getenv("DEV_MODE") != "true":
        raise HTTPException(status_code=404, detail="Not found")

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            tag = await conn.fetchrow(
                "SELECT aes_key_encrypted FROM nfc_tags WHERE tag_uid = $1",
                body.tag_uid,
            )
    except Exception:
        await notify_error("POST /dev/simulate-tap failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")

    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found — pair it first")

    raw_key = decrypt_tag_key(tag["aes_key_encrypted"])
    return {"tag_uid": body.tag_uid, "counter": body.counter, "sig": sign(raw_key, body.tag_uid, body.counter)}


class AgeSessionBody(BaseModel):
    session_id: str = Field(min_length=1)
    minutes: int = Field(gt=0)


@router.post("/age-session")
async def age_session(request: Request, body: AgeSessionBody):
    """DEV ONLY: wind last_activity_at back by N minutes for retap testing."""
    if os.getenv("DEV_MODE") != "true":
        raise HTTPException(status_code=404, detail="Not found")

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE game_sessions SET last_activity_at = NOW() - $1 * INTERVAL '1 minute' WHERE id = $2",
                body.minutes, body.session_id,
            )
    except Exception:
        await notify_error("POST /dev/age-session failed", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")
    return {"ok": True}
