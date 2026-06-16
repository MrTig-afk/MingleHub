import httpx
import os
import sys
import time
from datetime import datetime, timezone

NTFY = "https://ntfy.sh"
ERRORS = os.getenv("NTFY_ERRORS_TOPIC", "")
SECURITY = os.getenv("NTFY_SECURITY_TOPIC", "")
PAYMENTS = os.getenv("NTFY_PAYMENTS_TOPIC", "")
INTEREST = os.getenv("NTFY_INTEREST_TOPIC", "")

if not ERRORS:
    print("WARNING: NTFY_ERRORS_TOPIC not set — error alerts will be silently dropped", file=sys.stderr)
if not SECURITY:
    print("WARNING: NTFY_SECURITY_TOPIC not set — security alerts will be silently dropped", file=sys.stderr)
if not PAYMENTS:
    print("WARNING: NTFY_PAYMENTS_TOPIC not set — payment alerts will be silently dropped", file=sys.stderr)

_first_request_today = {"date": None}
_security_cooldown: dict[str, float] = {}
_COOLDOWN_SECONDS = 60
_MAX_COOLDOWN_ENTRIES = 1000


async def notify_error(title: str, body: str, priority: str = "high"):
    await _post(ERRORS, title, body, priority)


async def notify_security(title: str, body: str, ip: str = ""):
    now = time.monotonic()
    last = _security_cooldown.get(ip, 0)
    if now - last < _COOLDOWN_SECONDS:
        return
    if len(_security_cooldown) >= _MAX_COOLDOWN_ENTRIES:
        _security_cooldown.clear()
    _security_cooldown[ip] = now
    await _post(SECURITY, title, f"{body}\nIP: {ip}", "default")


async def notify_payment(title: str, body: str):
    await _post(PAYMENTS, title, body, "high")


async def notify_interest(email: str, mode: str, trigger: str):
    await _post(
        INTEREST,
        "💰 Premium interest!",
        f"Email: {email}\nMode: {mode}\nTrigger: {trigger}",
        "default",
    )


async def notify_cold_start():
    await _post(ERRORS, "Cold start", "FirstMove serverless function woke up", "low")


async def notify_daily_alive():
    today = datetime.now(timezone.utc).date().isoformat()
    if _first_request_today["date"] != today:
        _first_request_today["date"] = today
        await _post(ERRORS, "Daily alive", f"FirstMove active — {today}", "low")


async def _post(topic: str, title: str, body: str, priority: str):
    if not topic:
        return
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            await c.post(
                f"{NTFY}/{topic}",
                content=body,
                headers={"Title": title, "Priority": priority, "Content-Type": "text/plain"},
            )
    except Exception:
        pass
