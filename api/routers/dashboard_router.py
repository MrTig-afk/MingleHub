import traceback

from fastapi import APIRouter, Depends, HTTPException, Request

from api.auth import CurrentUser, get_current_user, require_role
from api.db import get_pool
from api.security import limiter, verify_api_key
from api.services.notify import notify_error

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
