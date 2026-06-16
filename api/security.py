import os
import secrets
from fastapi import Header, HTTPException, Request
from slowapi import Limiter
from api.services.notify import notify_security

API_KEY = os.environ["API_KEY"]


def get_client_ip(request: Request) -> str:
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


limiter = Limiter(key_func=get_client_ip)


async def verify_api_key(request: Request, x_api_key: str = Header(...)):
    if not secrets.compare_digest(x_api_key, API_KEY):
        await notify_security(
            "Wrong API key 🔒",
            f"Endpoint: {request.url.path}",
            get_client_ip(request),
        )
        raise HTTPException(status_code=401, detail="Unauthorized")
