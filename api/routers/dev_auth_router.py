import os

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.auth import issue_dev_token
from api.security import limiter, verify_api_key

# Dev-only session issuer — see api/auth.py for why this exists instead of
# real Clerk verification. Always 404s outside DEV_MODE so it never works
# in production even if accidentally deployed.
router = APIRouter(prefix="/api/auth", dependencies=[Depends(verify_api_key)])


class DevLoginPayload(BaseModel):
    clerk_user_id: str


@router.post("/dev-login")
@limiter.limit("20/minute")
async def dev_login(request: Request, body: DevLoginPayload):
    if os.getenv("DEV_MODE") != "true":
        raise HTTPException(status_code=404, detail="Not found")
    return {"token": issue_dev_token(body.clerk_user_id)}
