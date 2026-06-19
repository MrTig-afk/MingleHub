"""Supabase Realtime broadcast publisher.

Publishes events to Supabase Broadcast channels via the REST endpoint.
No-op when SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY is unset (CI / local
without Supabase). Publish failures never raise into the caller -- the game
flow must not break because a push notification failed.
"""
import logging
import os

import httpx

logger = logging.getLogger(__name__)

_SUPABASE_URL = os.getenv("SUPABASE_URL", "")
_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
_ENABLED = bool(_SUPABASE_URL and _SERVICE_ROLE_KEY)
_WARNED = False


async def publish(channel: str, event: str, payload: dict) -> None:
    """Broadcast an event to a Supabase Realtime channel.

    No-op when SUPABASE_* env vars are unset. Never raises -- a failed
    publish must not break the game flow.
    """
    global _WARNED

    if not _ENABLED:
        if not _WARNED:
            logger.warning("SUPABASE_* env vars unset -- realtime publish disabled")
            _WARNED = True
        return

    try:
        async with httpx.AsyncClient(timeout=3) as c:
            await c.post(
                f"{_SUPABASE_URL}/realtime/v1/api/broadcast",
                # private:true routes the message to PRIVATE channels — without
                # it, subscribers on a `{ config: { private: true } }` channel
                # never receive the broadcast (the channels are private so RLS
                # on realtime.messages scopes who can subscribe; see RLS policy
                # and realtime_auth's aud/role/iss claims).
                json={"messages": [{"topic": channel, "event": event, "payload": payload, "private": True}]},
                headers={
                    "apikey": _SERVICE_ROLE_KEY,
                    "Authorization": f"Bearer {_SERVICE_ROLE_KEY}",
                    "Content-Type": "application/json",
                },
            )
    except Exception as exc:
        # Never log the service-role key. Log only the exception message.
        logger.warning("realtime publish failed: %s", exc)
