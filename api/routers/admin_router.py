from fastapi import APIRouter, Depends, Request

from api.auth import CurrentUser, require_role
from api.security import limiter, verify_api_key

router = APIRouter(prefix="/api/admin", dependencies=[Depends(verify_api_key)])


@router.get("/ping")
@limiter.limit("60/minute")
async def ping(request: Request, current_user: CurrentUser = Depends(require_role("admin"))):
    return {"status": "ok", "admin": current_user.clerk_user_id}
