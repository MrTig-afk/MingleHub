"""Mint short-lived Supabase-compatible JWTs for Realtime channel access.

The token is a standard HS256 JWT signed with SUPABASE_JWT_SECRET. The
service-role key and JWT secret never leave the backend.

Production note: subscribe-side channel authorization requires enabling
Supabase private/authorized channels with an RLS policy on
realtime.messages that checks the JWT `channel` claim. The JWTs minted
here already carry that claim, so the foundation is in place -- enabling
authorized channels in the Supabase dashboard and adding the RLS policy
is the remaining step to close the foundation→production gap.
"""
import base64
import hashlib
import hmac
import json
import os
import time

_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET", "")
_SUPABASE_URL = os.getenv("SUPABASE_URL", "")
_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
_ENABLED = bool(_JWT_SECRET and _SUPABASE_URL and _ANON_KEY)

TOKEN_TTL_SECONDS = 300  # 5 minutes -- short-lived; client re-fetches on reconnect


def is_enabled() -> bool:
    """Returns True if all required SUPABASE_* env vars are set."""
    return _ENABLED


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _mint_jwt(claims: dict) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps(claims).encode())
    sig_input = f"{header}.{payload}".encode()
    signature = hmac.new(
        _JWT_SECRET.encode(), sig_input, hashlib.sha256
    ).digest()
    return f"{header}.{payload}.{_b64url(signature)}"


def mint_channel_token(channel: str, phone_id: str) -> dict:
    """Mint a short-lived JWT scoped to the given channel and phone.

    Returns {
        "token": "<jwt>",
        "channel": channel,
        "supabase_url": _SUPABASE_URL,
        "supabase_anon_key": _ANON_KEY,
    }.

    Raises RuntimeError if called when not enabled (caller must check
    is_enabled() first).

    Never returns SUPABASE_SERVICE_ROLE_KEY or SUPABASE_JWT_SECRET.
    """
    if not _ENABLED:
        raise RuntimeError("Supabase realtime is not configured")

    now = int(time.time())
    claims = {
        # iss + aud + role must match Supabase's own legacy keys: a token
        # self-signed with the shared JWT secret acts as the project's auth
        # issuer. iss="supabase" and aud="authenticated" are required for
        # Realtime to accept the token as an authenticated user and deliver
        # broadcasts on private channels (RLS `to authenticated` policies).
        "iss": "supabase",
        "aud": "authenticated",
        "sub": phone_id,
        "channel": channel,
        "iat": now,
        "exp": now + TOKEN_TTL_SECONDS,
        "role": "authenticated",
    }
    token = _mint_jwt(claims)
    return {
        "token": token,
        "channel": channel,
        "supabase_url": _SUPABASE_URL,
        "supabase_anon_key": _ANON_KEY,
    }
