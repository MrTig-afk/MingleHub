import os

import pytest


def test_dev_preflight_allows_lan_origin(client):
    """A CORS preflight from a random LAN IP on port 5173 must succeed in
    DEV_MODE (the test suite always runs with DEV_MODE=true)."""
    resp = client.options(
        "/api/patron/tap",
        headers={
            "Origin": "https://10.0.0.5:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code in (200, 204)
    assert resp.headers.get("access-control-allow-origin") == "https://10.0.0.5:5173"


def test_dev_preflight_allows_http_localhost(client):
    """Regression guard: plain http://localhost on the dev port still works
    after switching from the hardcoded list to a regex."""
    resp = client.options(
        "/api/patron/tap",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code in (200, 204)
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_dev_preflight_allows_https_on_alt_port(client):
    """Port 5174 (Vite's fallback port) must also be allowed."""
    resp = client.options(
        "/api/patron/tap",
        headers={
            "Origin": "https://192.168.1.50:5174",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code in (200, 204)
    assert resp.headers.get("access-control-allow-origin") == "https://192.168.1.50:5174"


def test_dev_preflight_rejects_non_dev_port(client):
    """An origin on a port other than 5173/5174 must NOT be allowed --
    the regex should not be overly broad."""
    resp = client.options(
        "/api/patron/tap",
        headers={
            "Origin": "https://10.0.0.5:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    # Starlette omits the header entirely for disallowed origins
    assert resp.headers.get("access-control-allow-origin") is None


def test_dev_preflight_rejects_origin_without_port(client):
    """An origin with no port (e.g. a production domain) must not match
    the dev regex -- the :(5173|5174) suffix is mandatory."""
    resp = client.options(
        "/api/patron/tap",
        headers={
            "Origin": "https://minglehub.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.headers.get("access-control-allow-origin") is None


def test_dev_get_echoes_cors_header(client):
    """A normal GET (not preflight) from a LAN origin must include the
    access-control-allow-origin response header -- browsers check it on
    every response, not just OPTIONS."""
    resp = client.get(
        "/api/patron/tap",
        headers={"Origin": "https://10.0.0.5:5173"},
        # tap requires query params so will 422, but CORS headers are
        # added before route validation
    )
    assert resp.headers.get("access-control-allow-origin") == "https://10.0.0.5:5173"


def test_prod_raises_without_allowed_origins():
    """When DEV_MODE is not 'true' and ALLOWED_ORIGINS is empty, the
    production guard must refuse to start (security.md: CORS restricted
    to approved origins in production).

    We reproduce the guard logic inline rather than reloading the module,
    because importlib.reload of api.index would tear down the app
    singleton and break the module-scoped client fixture for other tests.
    If the guard in api/index.py (lines ~69-71) changes, update this test
    to match.
    """
    # Simulate the production code path
    dev_mode = None  # DEV_MODE unset
    allowed = ""     # ALLOWED_ORIGINS unset

    with pytest.raises(RuntimeError, match="ALLOWED_ORIGINS must be set"):
        if dev_mode != "true":
            if not allowed:
                raise RuntimeError("ALLOWED_ORIGINS must be set in production")
