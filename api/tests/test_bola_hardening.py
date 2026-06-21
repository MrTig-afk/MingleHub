"""BOLA Hardening tests -- Slice 6.

Covers all three security fixes:
  Item 1 -- channel-auth rejects phones with left_early=TRUE.
  Item 2 -- POST /sessions/{id}/join requires a prior table tap (presence proof).
  Item 3 -- GET /lobby/{id} response contains no raw phone_id anywhere.
"""
import asyncio
import os
import uuid

import asyncpg
import pytest

from api.tests.conftest import pair_tag, simulate_tap


# ---------------------------------------------------------------------------
# Autouse NFC tag cleanup (same pattern as all other patron test modules)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _cleanup_test_tags():
    """Remove test NFC tags after each test."""
    yield

    async def _delete():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute("DELETE FROM nfc_tags WHERE tag_uid LIKE 'test-tag-%'")
        finally:
            await conn.close()

    asyncio.run(_delete())


# ---------------------------------------------------------------------------
# Shared helpers (mirrors test_lobby.py / test_realtime.py exactly)
# ---------------------------------------------------------------------------

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
    resp = client.post(
        f"/api/patron/lobby/{lobby_id}/claim-host",
        headers=api_key_header,
        json={"phone_id": phone_id},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _set_name(client, api_key_header, lobby_id, phone_id, name):
    resp = client.post(
        f"/api/patron/lobby/{lobby_id}/set-name",
        headers=api_key_header,
        json={"phone_id": phone_id, "name": name},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _start(client, api_key_header, lobby_id, phone_id, **kwargs):
    resp = client.post(
        f"/api/patron/lobby/{lobby_id}/start",
        headers=api_key_header,
        json={"phone_id": phone_id, **kwargs},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _setup_session(client, api_key_header, owner_a_token, table_info, num_phones=2):
    """Tap num_phones phones, claim host on first, start. Returns session_id,
    table_id, phones list, tag_uid, lobby_id."""
    tag_uid = pair_tag(client, api_key_header, owner_a_token, table_info["table_number"])
    phones = [_fresh_phone() for _ in range(num_phones)]
    first_body = None
    for i, phone in enumerate(phones):
        body = _tap_with_phone(
            client, api_key_header, table_info["venue_slug"], table_info["table_number"],
            tag_uid, i + 1, phone,
        )
        if i == 0:
            first_body = body
    lobby_id = first_body["table_state"]["lobby_id"]
    table_id = first_body["table_id"]
    _claim_host(client, api_key_header, lobby_id, phones[0])
    result = _start(client, api_key_header, lobby_id, phones[0])
    return {
        "session_id": result["session_id"],
        "table_id": table_id,
        "phones": phones,
        "tag_uid": tag_uid,
        "lobby_id": lobby_id,
    }


def _channel_auth(client, api_key_header, phone_id, table_id):
    return client.post(
        "/api/patron/channel-auth",
        headers=api_key_header,
        json={"phone_id": phone_id, "table_id": table_id},
    )


# ---------------------------------------------------------------------------
# Item 1: channel-auth rejects phones with left_early=TRUE
# ---------------------------------------------------------------------------

def test_channel_auth_rejects_left_phone(client, api_key_header, owner_a_token, fresh_table):
    """A phone that left the session is rejected by /channel-auth (left_early=TRUE
    means it is no longer a member even though it still has a table_lobby_phones row).
    The active host must still pass."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    session_id = s["session_id"]
    table_id = s["table_id"]
    host_phone = s["phones"][0]
    second_phone = s["phones"][1]

    # Non-host phone leaves.
    leave = client.post(
        f"/api/patron/sessions/{session_id}/leave",
        headers=api_key_header,
        json={"phone_id": second_phone},
    )
    assert leave.status_code == 200, leave.text
    assert leave.json().get("left") is True

    # Left phone must be rejected (Item 1 fix: branch 3 now joins game_players
    # with left_early=FALSE).
    resp_left = _channel_auth(client, api_key_header, second_phone, table_id)
    assert resp_left.status_code == 403, (
        f"Left phone must get 403; got {resp_left.status_code}: {resp_left.text}"
    )

    # Active origin phone (host) must still pass.
    resp_host = _channel_auth(client, api_key_header, host_phone, table_id)
    assert resp_host.status_code == 200, (
        f"Active host must get 200; got {resp_host.status_code}: {resp_host.text}"
    )
    assert "realtime_enabled" in resp_host.json()


def test_channel_auth_allows_rejoined_phone(client, api_key_header, owner_a_token, fresh_table):
    """A phone that left and subsequently rejoined (left_early reset to FALSE)
    must pass /channel-auth again."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    session_id = s["session_id"]
    table_id = s["table_id"]
    second_phone = s["phones"][1]

    # Leave then rejoin.
    leave = client.post(
        f"/api/patron/sessions/{session_id}/leave",
        headers=api_key_header,
        json={"phone_id": second_phone},
    )
    assert leave.status_code == 200, leave.text

    rejoin = client.post(
        f"/api/patron/sessions/{session_id}/rejoin",
        headers=api_key_header,
        json={"phone_id": second_phone},
    )
    assert rejoin.status_code == 200, rejoin.text

    # Rejoined phone must pass channel-auth.
    resp = _channel_auth(client, api_key_header, second_phone, table_id)
    assert resp.status_code == 200, (
        f"Rejoined phone must get 200; got {resp.status_code}: {resp.text}"
    )
    assert "realtime_enabled" in resp.json()


def test_channel_auth_open_lobby_member_unaffected_by_item1(
    client, api_key_header, owner_a_token, fresh_table
):
    """Branch 1 (open lobby, pre-game) is unchanged by Item 1: a lobby member
    still gets 200 from /channel-auth without any game_players row."""
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    phone_id = _fresh_phone()
    body = _tap_with_phone(
        client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"],
        tag_uid, 1, phone_id,
    )
    table_id = body["table_id"]

    resp = _channel_auth(client, api_key_header, phone_id, table_id)
    assert resp.status_code == 200, (
        f"Open-lobby member must pass; got {resp.status_code}: {resp.text}"
    )
    assert "realtime_enabled" in resp.json()


# ---------------------------------------------------------------------------
# Item 2: join-presence BOLA -- /sessions/{id}/join requires a prior tap
# ---------------------------------------------------------------------------

def test_join_rejects_phone_without_presence(client, api_key_header, owner_a_token, fresh_table):
    """A phone that NEVER tapped the table's NFC tag cannot join an active session.
    The router returns 403 with detail 'Tap the table tag first'."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    session_id = s["session_id"]

    stranger = _fresh_phone()
    resp = client.post(
        f"/api/patron/sessions/{session_id}/join",
        headers=api_key_header,
        json={"phone_id": stranger, "name": "Crasher"},
    )
    assert resp.status_code == 403, (
        f"Untapped phone must get 403; got {resp.status_code}: {resp.text}"
    )
    assert "tap" in resp.json().get("detail", "").lower(), (
        f"Expected 'Tap the table tag first' in detail; got: {resp.json()}"
    )


def test_join_allows_phone_with_tap_presence(client, api_key_header, owner_a_token, fresh_table):
    """A phone that tapped the table (table_tap_log row exists) can join the
    active session via POST /sessions/{id}/join."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    session_id = s["session_id"]
    tag_uid = s["tag_uid"]

    # Third phone taps first — this inserts the table_tap_log row.
    third_phone = _fresh_phone()
    _tap_with_phone(
        client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"],
        tag_uid, 3, third_phone,
    )

    resp = client.post(
        f"/api/patron/sessions/{session_id}/join",
        headers=api_key_header,
        json={"phone_id": third_phone, "name": "Late Arrival"},
    )
    assert resp.status_code == 200, (
        f"Tapped phone must join OK; got {resp.status_code}: {resp.text}"
    )
    data = resp.json()
    assert "session_id" in data
    assert data.get("name") == "Late Arrival"


def test_join_existing_player_bypasses_tap_check(client, api_key_header, owner_a_token, fresh_table):
    """An existing game_players row (re-tap resume path) bypasses the tap-log
    check entirely — the player already proved presence when they first joined."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    session_id = s["session_id"]
    existing_phone = s["phones"][1]  # Was in the lobby, now a game_player.

    # Re-join without a fresh tap — the existing_player check must bypass presence.
    resp = client.post(
        f"/api/patron/sessions/{session_id}/join",
        headers=api_key_header,
        json={"phone_id": existing_phone, "name": "Player 2"},
    )
    assert resp.status_code == 200, (
        f"Existing player re-join must bypass tap check; got {resp.status_code}: {resp.text}"
    )
    assert resp.json()["session_id"] == session_id


# ---------------------------------------------------------------------------
# Item 3: lobby response redaction -- no raw phone_id anywhere
# ---------------------------------------------------------------------------

def _has_phone_id_key(obj) -> bool:
    """Recursively check whether 'phone_id' or 'host_phone_id' appears
    anywhere in the JSON structure."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("phone_id", "host_phone_id"):
                return True
            if _has_phone_id_key(v):
                return True
    elif isinstance(obj, list):
        for item in obj:
            if _has_phone_id_key(item):
                return True
    return False


def test_lobby_state_does_not_leak_phone_ids(client, api_key_header, owner_a_token, fresh_table):
    """GET /lobby/{id} must not include any raw phone_id or host_phone_id in the
    response, at any nesting level."""
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    venue_slug, table_number = fresh_table["venue_slug"], fresh_table["table_number"]
    phone_a = _fresh_phone()
    phone_b = _fresh_phone()

    body = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 1, phone_a)
    _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 2, phone_b)
    lobby_id = body["table_state"]["lobby_id"]

    poll = client.get(
        f"/api/patron/lobby/{lobby_id}",
        headers=api_key_header,
        params={"phone_id": phone_a},
    )
    assert poll.status_code == 200, poll.text
    data = poll.json()

    # No phone_id or host_phone_id must appear anywhere in the response.
    assert not _has_phone_id_key(data), (
        f"Raw phone_id leaked in lobby response: {data}"
    )

    # Root must have is_host and host_name.
    assert "is_host" in data
    assert "host_name" in data

    # Each phone entry must have slot_id and is_self, but NOT phone_id.
    assert "phones" in data
    assert len(data["phones"]) == 2
    for p in data["phones"]:
        assert "phone_id" not in p, f"phone_id leaked in phone entry: {p}"
        assert "slot_id" in p
        assert "is_self" in p

    # Exactly one phone has is_self=True (the caller, phone_a).
    self_entries = [p for p in data["phones"] if p["is_self"]]
    assert len(self_entries) == 1


def test_lobby_state_is_self_matches_caller(client, api_key_header, owner_a_token, fresh_table):
    """is_self is True for exactly the polling phone and False for the other."""
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    venue_slug, table_number = fresh_table["venue_slug"], fresh_table["table_number"]
    phone_a = _fresh_phone()
    phone_b = _fresh_phone()

    body = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 1, phone_a)
    _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 2, phone_b)
    lobby_id = body["table_state"]["lobby_id"]

    _set_name(client, api_key_header, lobby_id, phone_a, "Alice")
    _set_name(client, api_key_header, lobby_id, phone_b, "Bob")

    # Poll as phone_a.
    poll_a = client.get(
        f"/api/patron/lobby/{lobby_id}",
        headers=api_key_header,
        params={"phone_id": phone_a},
    )
    assert poll_a.status_code == 200, poll_a.text
    data_a = poll_a.json()
    self_a = [p for p in data_a["phones"] if p["is_self"]]
    assert len(self_a) == 1
    assert self_a[0]["name"] == "Alice"

    # Poll as phone_b.
    poll_b = client.get(
        f"/api/patron/lobby/{lobby_id}",
        headers=api_key_header,
        params={"phone_id": phone_b},
    )
    assert poll_b.status_code == 200, poll_b.text
    data_b = poll_b.json()
    self_b = [p for p in data_b["phones"] if p["is_self"]]
    assert len(self_b) == 1
    assert self_b[0]["name"] == "Bob"


def test_lobby_state_without_phone_id_param(client, api_key_header, owner_a_token, fresh_table):
    """Polling GET /lobby/{id} without a phone_id query param must not crash
    and must return is_host=False and all is_self=False."""
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    venue_slug, table_number = fresh_table["venue_slug"], fresh_table["table_number"]
    phone_a = _fresh_phone()

    body = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 1, phone_a)
    lobby_id = body["table_state"]["lobby_id"]

    poll = client.get(f"/api/patron/lobby/{lobby_id}", headers=api_key_header)
    assert poll.status_code == 200, poll.text
    data = poll.json()

    assert data["is_host"] is False
    for p in data["phones"]:
        assert p["is_self"] is False

    # No phone_id must leak even when no caller identity is provided.
    assert not _has_phone_id_key(data), (
        f"Raw phone_id leaked in anonymous lobby poll: {data}"
    )


def test_lobby_state_is_host_true_for_host(client, api_key_header, owner_a_token, fresh_table):
    """After claim-host, the host's poll returns is_host=True and non-host
    returns is_host=False. Both see the same host_name."""
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    venue_slug, table_number = fresh_table["venue_slug"], fresh_table["table_number"]
    phone_a = _fresh_phone()
    phone_b = _fresh_phone()

    body = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 1, phone_a)
    _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 2, phone_b)
    lobby_id = body["table_state"]["lobby_id"]

    _set_name(client, api_key_header, lobby_id, phone_a, "HostName")
    _claim_host(client, api_key_header, lobby_id, phone_a)

    # Host polls.
    poll_host = client.get(
        f"/api/patron/lobby/{lobby_id}",
        headers=api_key_header,
        params={"phone_id": phone_a},
    )
    assert poll_host.status_code == 200, poll_host.text
    data_host = poll_host.json()
    assert data_host["is_host"] is True
    assert data_host["host_name"] == "HostName"

    # Non-host polls.
    poll_other = client.get(
        f"/api/patron/lobby/{lobby_id}",
        headers=api_key_header,
        params={"phone_id": phone_b},
    )
    assert poll_other.status_code == 200, poll_other.text
    data_other = poll_other.json()
    assert data_other["is_host"] is False
    # Non-host still sees the correct host_name.
    assert data_other["host_name"] == "HostName"


def test_lobby_state_no_host_yet_host_name_is_none(client, api_key_header, owner_a_token, fresh_table):
    """Before any phone claims host, host_name must be None."""
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    phone_a = _fresh_phone()
    body = _tap_with_phone(
        client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"],
        tag_uid, 1, phone_a,
    )
    lobby_id = body["table_state"]["lobby_id"]

    poll = client.get(
        f"/api/patron/lobby/{lobby_id}",
        headers=api_key_header,
        params={"phone_id": phone_a},
    )
    assert poll.status_code == 200, poll.text
    data = poll.json()
    assert data["host_name"] is None
    assert data["is_host"] is False
