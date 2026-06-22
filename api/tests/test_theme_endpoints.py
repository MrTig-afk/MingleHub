"""Tests for the owner theme endpoints: GET /themes, GET/POST /theme."""
import asyncio
import os

import asyncpg

from api.dev_fixtures import (
    ADMIN_CLERK_ID,
    OWNER_A_CLERK_ID,
    OWNER_B_CLERK_ID,
    STAFF_A_CLERK_ID,
    VENUE_A_ID,
    VENUE_B_ID,
)
from api.tests.conftest import dev_login


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def _clear_theme(venue_id):
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute(
                "DELETE FROM nightly_theme_selections WHERE venue_id = $1", venue_id)
        finally:
            await conn.close()
    asyncio.run(_q())


def test_list_themes_includes_named_and_test(client, api_key_header):
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    resp = client.get("/api/dashboard/themes", headers={**api_key_header, **auth_header(token)})
    assert resp.status_code == 200
    keys = {t["theme_key"] for t in resp.json()["themes"]}
    assert "random" in keys
    assert "all_trivia" in keys           # the single-type test theme
    test_themes = [t for t in resp.json()["themes"] if t["is_test"]]
    assert {t["theme_key"] for t in test_themes} == {"all_trivia", "all_roulette", "all_chooser"}


def test_list_themes_staff_ok_admin_forbidden(client, api_key_header):
    staff = dev_login(client, api_key_header, STAFF_A_CLERK_ID)
    assert client.get("/api/dashboard/themes",
                      headers={**api_key_header, **auth_header(staff)}).status_code == 200
    admin = dev_login(client, api_key_header, ADMIN_CLERK_ID)
    assert client.get("/api/dashboard/themes",
                      headers={**api_key_header, **auth_header(admin)}).status_code == 403


def test_get_active_theme_defaults_to_random(client, api_key_header):
    _clear_theme(VENUE_A_ID)
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    resp = client.get("/api/dashboard/theme", headers={**api_key_header, **auth_header(token)})
    assert resp.status_code == 200
    assert resp.json()["theme_key"] == "random"


def test_set_theme_owner_then_get(client, api_key_header):
    try:
        token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        headers = {**api_key_header, **auth_header(token)}
        resp = client.post("/api/dashboard/theme", headers=headers, json={"theme_key": "all_trivia"})
        assert resp.status_code == 200
        got = client.get("/api/dashboard/theme", headers=headers)
        assert got.json()["theme_key"] == "all_trivia"
    finally:
        _clear_theme(VENUE_A_ID)


def test_set_theme_staff_forbidden(client, api_key_header):
    token = dev_login(client, api_key_header, STAFF_A_CLERK_ID)
    resp = client.post("/api/dashboard/theme",
                       headers={**api_key_header, **auth_header(token)}, json={"theme_key": "party_night"})
    assert resp.status_code == 403


def test_set_theme_unknown_404(client, api_key_header):
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    resp = client.post("/api/dashboard/theme",
                       headers={**api_key_header, **auth_header(token)}, json={"theme_key": "nope_theme"})
    assert resp.status_code == 404


def test_set_theme_extra_field_422(client, api_key_header):
    token = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
    resp = client.post("/api/dashboard/theme",
                       headers={**api_key_header, **auth_header(token)},
                       json={"theme_key": "random", "venue_id": VENUE_B_ID})
    assert resp.status_code == 422


def test_set_theme_bola(client, api_key_header):
    """Owner B setting their theme must not change Venue A's."""
    try:
        _clear_theme(VENUE_A_ID)
        token_b = dev_login(client, api_key_header, OWNER_B_CLERK_ID)
        client.post("/api/dashboard/theme",
                    headers={**api_key_header, **auth_header(token_b)}, json={"theme_key": "party_night"})
        token_a = dev_login(client, api_key_header, OWNER_A_CLERK_ID)
        got_a = client.get("/api/dashboard/theme", headers={**api_key_header, **auth_header(token_a)})
        assert got_a.json()["theme_key"] == "random"   # A untouched
    finally:
        _clear_theme(VENUE_A_ID)
        _clear_theme(VENUE_B_ID)
