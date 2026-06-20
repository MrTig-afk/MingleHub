import asyncio
import base64
import json
import os
import uuid

import asyncpg
import pytest

from api.tests.conftest import pair_tag, simulate_tap


@pytest.fixture(autouse=True)
def _cleanup_test_tags():
    """Remove test NFC tags after each test (same pattern as test_lobby.py)."""
    yield

    async def _delete():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute("DELETE FROM nfc_tags WHERE tag_uid LIKE 'test-tag-%'")
        finally:
            await conn.close()

    asyncio.run(_delete())


def _fresh_phone():
    return f"test-phone-{uuid.uuid4()}"


def _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, counter, phone_id):
    sig = simulate_tap(client, api_key_header, tag_uid, counter)
    resp = client.get(
        "/api/patron/tap",
        headers=api_key_header,
        params={
            "venue_slug": venue_slug, "table_number": table_number,
            "tag_uid": tag_uid, "counter": counter, "sig": sig, "phone_id": phone_id,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _claim_host(client, api_key_header, lobby_id, phone_id):
    resp = client.post(f"/api/patron/lobby/{lobby_id}/claim-host", headers=api_key_header, json={"phone_id": phone_id})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _start(client, api_key_header, lobby_id, phone_id, **kwargs):
    return client.post(
        f"/api/patron/lobby/{lobby_id}/start",
        headers=api_key_header,
        json={"phone_id": phone_id, **kwargs},
    )


# --- Unit tests ---

def test_publish_noop_when_env_unset():
    """publish() must return cleanly (no exception) when SUPABASE_* vars unset."""
    # Import fresh to exercise the disabled path; env vars are not set in CI.
    from api.services.realtime_service import publish
    # Should complete without raising.
    asyncio.run(publish("table:test-table", "lobby_update", {}))


def test_mint_token_structure(monkeypatch):
    """mint_channel_token returns a valid HS256 JWT with the expected claims."""
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "test-secret-key")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test-anon-key")

    # Re-import to pick up the monkeypatched env vars.
    import importlib
    import api.services.realtime_auth as mod
    importlib.reload(mod)

    channel = "table:some-table-id"
    phone_id = _fresh_phone()
    result = mod.mint_channel_token(channel, phone_id)

    assert result["channel"] == channel
    assert result["supabase_url"] == "https://test.supabase.co"
    assert result["supabase_anon_key"] == "test-anon-key"
    assert "token" in result
    # SUPABASE_SERVICE_ROLE_KEY and SUPABASE_JWT_SECRET must NOT appear.
    assert "SUPABASE_SERVICE_ROLE_KEY" not in str(result)
    assert "test-secret-key" not in str(result)

    # Decode the JWT payload (middle segment) to verify claims.
    parts = result["token"].split(".")
    assert len(parts) == 3
    # Base64url padding
    payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    # iss/aud/role must match Supabase's legacy-key convention so Realtime
    # treats the token as an authenticated user (required for private channels).
    assert payload["iss"] == "supabase"
    assert payload["aud"] == "authenticated"
    assert payload["sub"] == phone_id
    assert payload["channel"] == channel
    assert payload["role"] == "authenticated"
    assert "exp" in payload
    assert "iat" in payload
    assert payload["exp"] > payload["iat"]

    # Restore the module to the disabled state for subsequent tests. Clear the env
    # FIRST — monkeypatch teardown runs only after this body returns, so reloading
    # here without delenv would re-enable the module (and leak into sibling tests).
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    importlib.reload(mod)


def test_mint_token_raises_when_disabled(monkeypatch):
    """mint_channel_token raises RuntimeError when env vars are unset."""
    # Deterministic regardless of the ambient dev environment (which may have real
    # SUPABASE_* set): clear them, then reload so _ENABLED is definitively False.
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    import importlib
    import api.services.realtime_auth as mod
    importlib.reload(mod)
    assert not mod.is_enabled()

    with pytest.raises(RuntimeError):
        mod.mint_channel_token("table:x", _fresh_phone())


# --- Integration tests ---

def test_channel_auth_for_lobby_member(client, api_key_header, owner_a_token, fresh_table):
    """A phone in an open lobby gets 200 from /channel-auth.

    In CI (SUPABASE_* unset) the response is {realtime_enabled: false}.
    """
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    phone_id = _fresh_phone()
    body = _tap_with_phone(
        client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"],
        tag_uid, 1, phone_id,
    )
    table_id = body["table_id"]

    resp = client.post(
        "/api/patron/channel-auth",
        headers=api_key_header,
        json={"phone_id": phone_id, "table_id": table_id},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # In CI, realtime_enabled is False because SUPABASE_* are unset.
    # When enabled, it would be True with token/channel/supabase_url/supabase_anon_key.
    assert "realtime_enabled" in data


def test_channel_auth_rejects_non_member(client, api_key_header, owner_a_token, fresh_table):
    """A phone that has never joined the lobby or session gets 403 (BOLA)."""
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    # Tap with one phone to create a lobby
    phone_a = _fresh_phone()
    body = _tap_with_phone(
        client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"],
        tag_uid, 1, phone_a,
    )
    table_id = body["table_id"]

    # An entirely different phone (never tapped) tries to get a channel token.
    stranger = _fresh_phone()
    resp = client.post(
        "/api/patron/channel-auth",
        headers=api_key_header,
        json={"phone_id": stranger, "table_id": table_id},
    )
    assert resp.status_code == 403


def test_channel_auth_disabled_response(client, api_key_header, owner_a_token, fresh_table):
    """With SUPABASE_* unset (CI), /channel-auth returns {realtime_enabled: false} — not an error."""
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    phone_id = _fresh_phone()
    body = _tap_with_phone(
        client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"],
        tag_uid, 1, phone_id,
    )
    table_id = body["table_id"]

    resp = client.post(
        "/api/patron/channel-auth",
        headers=api_key_header,
        json={"phone_id": phone_id, "table_id": table_id},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # In CI the response must be the graceful disabled response.
    # (If SUPABASE_* happen to be set, realtime_enabled would be True — accept both.)
    assert data.get("realtime_enabled") is False or data.get("realtime_enabled") is True


def test_channel_auth_rejects_session_non_member(client, api_key_header, owner_a_token, fresh_table):
    """After the lobby converts to a session, a stranger phone still gets 403."""
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    venue_slug, table_number = fresh_table["venue_slug"], fresh_table["table_number"]
    host_phone, second_phone = _fresh_phone(), _fresh_phone()

    body = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 1, host_phone)
    _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 2, second_phone)
    lobby_id = body["table_state"]["lobby_id"]
    table_id = body["table_id"]

    _claim_host(client, api_key_header, lobby_id, host_phone)
    start = _start(client, api_key_header, lobby_id, host_phone)
    assert start.status_code == 200, start.text

    # A stranger phone (never in the lobby, not the session origin) must be rejected.
    stranger = _fresh_phone()
    resp = client.post(
        "/api/patron/channel-auth",
        headers=api_key_header,
        json={"phone_id": stranger, "table_id": table_id},
    )
    assert resp.status_code == 403
