import os
import sys
import traceback
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
sys.path.insert(0, os.path.dirname(__file__))

# --- Error monitoring (Sentry) — optional; fully skipped when SENTRY_DSN unset.
# Initialised before the app + routers so it instruments everything.
_sentry_dsn = os.getenv("SENTRY_DSN", "")
if _sentry_dsn:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.starlette import StarletteIntegration
        from sentry_sdk.integrations.fastapi import FastApiIntegration

        def _scrub_event(event, hint):
            # Never ship auth tokens, API keys, or cookies to Sentry (security.md).
            req = event.get("request") or {}
            headers = req.get("headers")
            if isinstance(headers, dict):
                for h in list(headers):
                    if h.lower() in ("authorization", "x-api-key", "cookie"):
                        headers[h] = "[scrubbed]"
            return event

        sentry_sdk.init(
            dsn=_sentry_dsn,
            environment="development" if os.getenv("DEV_MODE") == "true" else "production",
            integrations=[StarletteIntegration(), FastApiIntegration()],
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
            send_default_pii=False,
            before_send=_scrub_event,
        )
    except ImportError:
        print("WARNING: SENTRY_DSN set but sentry-sdk not installed — error monitoring disabled", file=sys.stderr)

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mangum import Mangum
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from api.routers import packs
from api.routers import stripe_router
from api.routers import interest_router
from api.routers import dev_auth_router
from api.routers import dev_nfc_router
from api.routers import dashboard_router
from api.routers import admin_router
from api.routers import patron_router
from api.security import limiter, get_client_ip
from api.services.notify import notify_error, notify_security

_dev = os.getenv("DEV_MODE") == "true"
app = FastAPI(
    docs_url="/docs" if _dev else None,
    redoc_url="/redoc" if _dev else None,
    openapi_url="/openapi.json" if _dev else None,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

if os.getenv("DEV_MODE") == "true":
    origins = [
        "http://localhost:5173",
        "https://localhost:5173",
        "https://localhost:5174",
        "https://192.168.1.108:5173",
        "https://192.168.1.108:5174",
    ]
else:
    allowed = os.environ.get("ALLOWED_ORIGINS", "")
    if not allowed:
        raise RuntimeError("ALLOWED_ORIGINS must be set in production")
    origins = [allowed]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["X-API-Key", "Content-Type", "Authorization"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


_NOISE_PATHS = {"/favicon.ico", "/robots.txt", "/sitemap.xml", "/.well-known"}


@app.exception_handler(404)
async def not_found(request: Request, exc):
    path = request.url.path
    if not any(path.startswith(p) for p in _NOISE_PATHS):
        await notify_security("404 hit 🔒", f"Path: {path}", get_client_ip(request))
    return JSONResponse(status_code=404, content={"detail": "Not found"})


@app.exception_handler(Exception)
async def server_error(request: Request, exc):
    await notify_error("500 error 🚨", traceback.format_exc()[:500])
    return JSONResponse(status_code=500, content={"detail": "Internal error"})


app.include_router(packs.router)
app.include_router(stripe_router.router)
app.include_router(interest_router.router)
app.include_router(dev_auth_router.router)
app.include_router(dev_nfc_router.router)
app.include_router(dashboard_router.router)
app.include_router(admin_router.router)
app.include_router(patron_router.router)
handler = Mangum(app)
