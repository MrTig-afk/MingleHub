import re
import traceback

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from api.db import get_pool
from api.security import limiter, verify_api_key
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
    except HTTPException:
        raise
    except Exception:
        await notify_error("GET /patron/tap failed 🚨", traceback.format_exc()[:500])
        raise HTTPException(status_code=500, detail="Internal error")

    return {
        "venue_name": venue["name"],
        "venue_slug": venue["slug"],
        "table_number": table_number,
        "content_ceiling": table["content_ceiling"],
        "restrict_adult_content": venue["restrict_adult_content"],
    }
