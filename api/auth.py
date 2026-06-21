"""Platform authentication — venue/role identity for /dashboard and /admin routes.

DEV-ONLY STUB: gamespec.md specifies Clerk for real auth (2FA, sessions,
roles). Until a Clerk dev instance is wired in, sessions are simple
HMAC-signed tokens issued by POST /api/auth/dev-login (DEV_MODE only).

The contract below (get_current_user / require_role) is the permanent
pattern every dashboard/admin route should depend on. Swapping in real
Clerk JWT/JWKS verification later only touches _verify_token — route
handlers and require_role don't change.
"""
import base64
import hashlib
import hmac
import os
import time
from typing import Optional

from fastapi import Depends, Header, HTTPException
import httpx
import jwt
from jwt import PyJWKClient
from pydantic import BaseModel

from api.db import get_pool

SESSION_SECRET = os.environ["SESSION_SECRET"]
TOKEN_TTL_SECONDS = 60 * 60 * 12  # 12 hours — dev convenience only

# Clerk mode: set CLERK_JWKS_URL (+ CLERK_ISSUER) to verify real Clerk RS256 JWTs.
# Unset -> falls back to the dev-login HMAC token, so dev/CI are unaffected and prod
# "just works" once these env vars exist (the swap this module was designed for).
CLERK_JWKS_URL = os.getenv("CLERK_JWKS_URL")
CLERK_ISSUER = os.getenv("CLERK_ISSUER")
CLERK_SECRET_KEY = os.getenv("CLERK_SECRET_KEY")
# Auto-provisioning allowlist: a first-login user whose email is here becomes an
# 'admin'; everyone else becomes a 'venue_owner' (no venue yet -> setup wizard).
ADMIN_EMAILS = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}
_jwk_client = None


def _get_jwk_client():
    global _jwk_client
    if _jwk_client is None and CLERK_JWKS_URL:
        _jwk_client = PyJWKClient(CLERK_JWKS_URL)
    return _jwk_client


def _verify_clerk_jwt(token: str, signing_key) -> str:
    """Verify a Clerk RS256 JWT against signing_key, return its `sub` (Clerk user id)."""
    claims = jwt.decode(
        token, signing_key, algorithms=["RS256"],
        issuer=CLERK_ISSUER, options={"require": ["exp", "sub"], "verify_aud": False},
    )
    return claims["sub"]


class CurrentUser(BaseModel):
    id: str
    clerk_user_id: str
    venue_id: Optional[str]
    role: str


def issue_dev_token(clerk_user_id: str) -> str:
    expires_at = int(time.time()) + TOKEN_TTL_SECONDS
    payload = f"{clerk_user_id}:{expires_at}"
    signature = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}:{signature}".encode()).decode()


def _verify_token(token: str) -> str:
    """Returns clerk_user_id if the token is valid and unexpired, else raises 401.
    Clerk RS256 JWT (via JWKS) when CLERK_JWKS_URL is set; dev HMAC token otherwise."""
    client = _get_jwk_client()
    if client is not None:
        try:
            key = client.get_signing_key_from_jwt(token).key
            return _verify_clerk_jwt(token, key)
        except Exception:
            # In DEV_MODE, keep accepting dev-login HMAC tokens alongside Clerk (sim
            # tools + the dashboard dev-login). In production, Clerk is the only path.
            if os.getenv("DEV_MODE") != "true":
                raise HTTPException(status_code=401, detail="Invalid token")
    try:
        payload, expires_at, signature = base64.urlsafe_b64decode(token.encode()).decode().rsplit(":", 2)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=401, detail="Invalid token")

    expected = hmac.new(SESSION_SECRET.encode(), f"{payload}:{expires_at}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid token")
    if int(expires_at) < time.time():
        raise HTTPException(status_code=401, detail="Token expired")

    return payload


async def _fetch_clerk_email(clerk_user_id: str) -> Optional[str]:
    """Primary email of a Clerk user via the Backend API (needs the secret key)."""
    if not CLERK_SECRET_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"https://api.clerk.com/v1/users/{clerk_user_id}",
                headers={"Authorization": f"Bearer {CLERK_SECRET_KEY}"},
            )
        if r.status_code != 200:
            return None
        data = r.json()
        primary = data.get("primary_email_address_id")
        emails = data.get("email_addresses", [])
        for e in emails:
            if e.get("id") == primary:
                return e.get("email_address")
        return emails[0].get("email_address") if emails else None
    except Exception:
        return None


async def _provision_user(conn, clerk_user_id: str) -> Optional[dict]:
    """First-login auto-provision: create the users row. 'admin' if the email is in
    ADMIN_EMAILS, else 'venue_owner' with no venue (-> the venue-setup wizard)."""
    email = await _fetch_clerk_email(clerk_user_id)
    if not email:
        # Couldn't resolve a real Clerk user (e.g. a dev/unknown id, or Clerk
        # unreachable) -> don't provision; the caller returns 401.
        return None
    role = "admin" if email.lower() in ADMIN_EMAILS else "venue_owner"
    await conn.execute(
        "INSERT INTO users (id, clerk_user_id, role, venue_id) "
        "VALUES (gen_random_uuid(), $1, $2, NULL) "
        "ON CONFLICT (clerk_user_id) DO NOTHING",
        clerk_user_id, role,
    )
    return await conn.fetchrow(
        "SELECT id, clerk_user_id, venue_id, role FROM users WHERE clerk_user_id = $1",
        clerk_user_id,
    )


async def get_current_user(authorization: str = Header(...)) -> CurrentUser:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    clerk_user_id = _verify_token(authorization.removeprefix("Bearer "))

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, clerk_user_id, venue_id, role FROM users WHERE clerk_user_id = $1",
            clerk_user_id,
        )
        # First login under Clerk -> auto-provision (admin if allowlisted, else a
        # venue_owner with no venue; the dashboard then shows the setup wizard).
        if not row and _get_jwk_client() is not None:
            row = await _provision_user(conn, clerk_user_id)
    if not row:
        raise HTTPException(status_code=401, detail="User not found")

    return CurrentUser(
        id=str(row["id"]),
        clerk_user_id=row["clerk_user_id"],
        venue_id=str(row["venue_id"]) if row["venue_id"] else None,
        role=row["role"],
    )


def require_role(*roles: str):
    """Dependency factory: 403s unless the authenticated user has one of `roles`."""
    async def dependency(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.role not in roles:
            raise HTTPException(status_code=403, detail="Forbidden")
        return current_user

    return dependency
