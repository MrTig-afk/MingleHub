"""Alerting transport.

Two channels, by design (see COFOUNDER-REPORT / the alerting plan):
  - **Errors** -> Sentry. `notify_error` captures the live exception (full
    traceback + request context, grouped/deduped) on top of a Slack ping.
  - **Curated events** (security / payments / premium-interest) -> Slack.

Slack delivery uses incoming webhooks: one default sink for everything, with
optional per-category overrides so each kind can fan out to its own channel.
A category falls back to SLACK_WEBHOOK_URL; if neither is set the alert is
silently dropped (same fail-soft contract as before -- a notification must
never break the request flow).
"""
import os
import sys
import time
from datetime import datetime, timezone

import httpx

# Sentry is optional at import time: a missing package leaves this None and the
# capture calls become no-ops, so local dev runs fine without it installed.
try:
    import sentry_sdk
except ImportError:  # pragma: no cover - only when sentry-sdk isn't installed
    sentry_sdk = None

_DEFAULT_WEBHOOK = os.getenv("SLACK_WEBHOOK_URL", "")
_WEBHOOKS = {
    "errors": os.getenv("SLACK_WEBHOOK_ERRORS", "") or _DEFAULT_WEBHOOK,
    "security": os.getenv("SLACK_WEBHOOK_SECURITY", "") or _DEFAULT_WEBHOOK,
    "payments": os.getenv("SLACK_WEBHOOK_PAYMENTS", "") or _DEFAULT_WEBHOOK,
    "interest": os.getenv("SLACK_WEBHOOK_INTEREST", "") or _DEFAULT_WEBHOOK,
}

if not any(_WEBHOOKS.values()):
    print("WARNING: no SLACK_WEBHOOK_URL set -- Slack alerts will be silently dropped", file=sys.stderr)

_first_request_today = {"date": None}
_security_cooldown: dict[str, float] = {}
_COOLDOWN_SECONDS = 60
_MAX_COOLDOWN_ENTRIES = 1000


async def notify_error(title: str, body: str, priority: str = "high"):
    # Sentry owns errors. capture_exception() with no arg grabs the exception
    # currently being handled -- every notify_error call site is inside an
    # `except` block -- so Sentry gets the real exception object + traceback,
    # grouped and deduped. No-op when sentry-sdk is absent or SENTRY_DSN unset.
    if sentry_sdk is not None:
        sentry_sdk.capture_exception()
    await _post("errors", title, body)


async def notify_security(title: str, body: str, ip: str = ""):
    now = time.monotonic()
    last = _security_cooldown.get(ip, 0)
    if now - last < _COOLDOWN_SECONDS:
        return
    if len(_security_cooldown) >= _MAX_COOLDOWN_ENTRIES:
        _security_cooldown.clear()
    _security_cooldown[ip] = now
    await _post("security", title, f"{body}\nIP: {ip}")


async def notify_payment(title: str, body: str):
    await _post("payments", title, body)


async def notify_interest(email: str, mode: str, trigger: str):
    await _post(
        "interest",
        "💰 Premium interest!",
        f"Email: {email}\nMode: {mode}\nTrigger: {trigger}",
    )


async def notify_cold_start():
    await _post("errors", "Cold start", "MingleHub serverless function woke up")


async def notify_daily_alive():
    today = datetime.now(timezone.utc).date().isoformat()
    if _first_request_today["date"] != today:
        _first_request_today["date"] = today
        await _post("errors", "Daily alive", f"MingleHub active -- {today}")


async def _post(category: str, title: str, body: str):
    """Post one Slack message for a category. Fail-soft: never raises."""
    url = _WEBHOOKS.get(category, "")
    if not url:
        return
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            await c.post(url, json={"text": f"*{title}*\n{body}"})
    except Exception:
        pass
