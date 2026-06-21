import asyncio
import os
import uuid

import asyncpg
import pytest

from api.dev_fixtures import OWNER_B_CLERK_ID, STAFF_A_CLERK_ID
from api.tests.conftest import dev_login, pair_tag, simulate_tap


@pytest.fixture(autouse=True)
def _cleanup_test_tags():
    """nfc_tags isn't covered by fresh_table's teardown (a tag can outlive
    the table it was last paired to) — same prefix-based cleanup as the
    other test modules."""
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


def _set_name(client, api_key_header, lobby_id, phone_id, name):
    resp = client.post(
        f"/api/patron/lobby/{lobby_id}/set-name",
        headers=api_key_header,
        json={"phone_id": phone_id, "name": name},
    )
    return resp


def _start(client, api_key_header, lobby_id, phone_id, **kwargs):
    return client.post(
        f"/api/patron/lobby/{lobby_id}/start",
        headers=api_key_header,
        json={"phone_id": phone_id, **kwargs},
    )


def test_first_tap_with_no_session_creates_a_lobby(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    body = _tap_with_phone(
        client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"], tag_uid, 1, _fresh_phone()
    )

    state = body["table_state"]
    assert state["phase"] == "lobby"
    assert state["phone_count"] == 1
    assert state["host_phone_id"] is None


def test_second_phone_tapping_joins_the_same_lobby(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    venue_slug, table_number = fresh_table["venue_slug"], fresh_table["table_number"]
    phone_a, phone_b = _fresh_phone(), _fresh_phone()

    first = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 1, phone_a)
    second = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 2, phone_b)

    assert first["table_state"]["lobby_id"] == second["table_state"]["lobby_id"]
    assert second["table_state"]["phone_count"] == 2


def test_retapping_same_phone_does_not_double_count(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    venue_slug, table_number = fresh_table["venue_slug"], fresh_table["table_number"]
    phone_id = _fresh_phone()

    first = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 1, phone_id)
    second = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 2, phone_id)

    assert first["table_state"]["phone_count"] == 1
    assert second["table_state"]["phone_count"] == 1


def test_claim_host_first_wins_second_is_told_who_won(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    venue_slug, table_number = fresh_table["venue_slug"], fresh_table["table_number"]
    phone_a, phone_b = _fresh_phone(), _fresh_phone()
    body = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 1, phone_a)
    _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 2, phone_b)
    lobby_id = body["table_state"]["lobby_id"]

    assert _claim_host(client, api_key_header, lobby_id, phone_a) == {"you_are_host": True, "host_phone_id": phone_a}
    assert _claim_host(client, api_key_header, lobby_id, phone_b) == {"you_are_host": False, "host_phone_id": phone_a}


def test_claim_host_rejects_phone_not_in_lobby(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    body = _tap_with_phone(
        client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"], tag_uid, 1, _fresh_phone()
    )
    lobby_id = body["table_state"]["lobby_id"]

    resp = client.post(
        f"/api/patron/lobby/{lobby_id}/claim-host", headers=api_key_header, json={"phone_id": _fresh_phone()}
    )
    assert resp.status_code == 403


def test_start_requires_host(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    venue_slug, table_number = fresh_table["venue_slug"], fresh_table["table_number"]
    phone_a, phone_b = _fresh_phone(), _fresh_phone()
    body = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 1, phone_a)
    _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 2, phone_b)
    lobby_id = body["table_state"]["lobby_id"]
    _claim_host(client, api_key_header, lobby_id, phone_a)

    resp = _start(client, api_key_header, lobby_id, phone_b)
    assert resp.status_code == 403


def test_start_rejects_out_of_range_player_count(client, api_key_header, owner_a_token, fresh_table):
    # Only 1 phone joined — server derives count=1, below MIN_PLAYERS.
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    phone_id = _fresh_phone()
    body = _tap_with_phone(
        client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"], tag_uid, 1, phone_id
    )
    lobby_id = body["table_state"]["lobby_id"]
    _claim_host(client, api_key_header, lobby_id, phone_id)

    resp = _start(client, api_key_header, lobby_id, phone_id)
    assert resp.status_code == 422


def test_host_start_creates_session_and_converts_lobby(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    venue_slug, table_number = fresh_table["venue_slug"], fresh_table["table_number"]
    phone_a, phone_b, phone_c = _fresh_phone(), _fresh_phone(), _fresh_phone()
    body = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 1, phone_a)
    _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 2, phone_b)
    _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 3, phone_c)
    lobby_id = body["table_state"]["lobby_id"]
    _set_name(client, api_key_header, lobby_id, phone_a, "Kaushik")
    _set_name(client, api_key_header, lobby_id, phone_b, "Sarah")
    _set_name(client, api_key_header, lobby_id, phone_c, "James")
    _claim_host(client, api_key_header, lobby_id, phone_a)

    start = _start(client, api_key_header, lobby_id, phone_a)
    assert start.status_code == 200, start.text
    assert start.json()["player_count"] == 3
    assert start.json()["group_label"] == f"Table {fresh_table['table_number']} Group 1"

    poll = client.get(f"/api/patron/lobby/{lobby_id}", headers=api_key_header)
    assert poll.status_code == 200
    assert poll.json()["status"] == "converted"
    assert poll.json()["converted_session_id"] == start.json()["session_id"]


def test_tap_after_session_active_shows_join_or_new(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    venue_slug, table_number = fresh_table["venue_slug"], fresh_table["table_number"]
    host_phone, second_phone = _fresh_phone(), _fresh_phone()
    body = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 1, host_phone)
    _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 2, second_phone)
    lobby_id = body["table_state"]["lobby_id"]
    _claim_host(client, api_key_header, lobby_id, host_phone)
    _start(client, api_key_header, lobby_id, host_phone)

    late = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 3, _fresh_phone())
    assert late["table_state"]["phase"] == "join_or_new"
    assert len(late["table_state"]["groups"]) == 1
    assert late["table_state"]["table_id"] == fresh_table["table_id"]


def test_join_existing_session_adds_a_player(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    venue_slug, table_number = fresh_table["venue_slug"], fresh_table["table_number"]
    host_phone, second_phone = _fresh_phone(), _fresh_phone()
    body = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 1, host_phone)
    _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 2, second_phone)
    lobby_id = body["table_state"]["lobby_id"]
    _claim_host(client, api_key_header, lobby_id, host_phone)
    start = _start(client, api_key_header, lobby_id, host_phone)
    session_id = start.json()["session_id"]

    # BOLA fix: joining phone must tap the table first to prove physical presence.
    joining_phone = _fresh_phone()
    _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 3, joining_phone)

    join = client.post(
        f"/api/patron/sessions/{session_id}/join",
        headers=api_key_header,
        json={"phone_id": joining_phone, "name": "Late Arrival"},
    )
    assert join.status_code == 200
    assert join.json()["name"] == "Late Arrival"


def test_join_rejects_ended_session(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    venue_slug, table_number = fresh_table["venue_slug"], fresh_table["table_number"]
    host_phone, second_phone = _fresh_phone(), _fresh_phone()
    body = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 1, host_phone)
    _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 2, second_phone)
    lobby_id = body["table_state"]["lobby_id"]
    _claim_host(client, api_key_header, lobby_id, host_phone)
    start = _start(client, api_key_header, lobby_id, host_phone)
    session_id = start.json()["session_id"]

    async def _end():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute("UPDATE game_sessions SET ended_at = NOW() WHERE id = $1", session_id)
        finally:
            await conn.close()
    asyncio.run(_end())

    # BOLA fix: the joining phone must tap first so the presence check passes.
    # The ended-session 404 is enforced by join_existing_session (LookupError).
    joining_phone = _fresh_phone()
    _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 3, joining_phone)
    join = client.post(
        f"/api/patron/sessions/{session_id}/join",
        headers=api_key_header,
        json={"phone_id": joining_phone},
    )
    assert join.status_code == 404


def test_new_group_creates_second_lobby_while_first_session_active(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    venue_slug, table_number = fresh_table["venue_slug"], fresh_table["table_number"]
    host_phone, second_phone = _fresh_phone(), _fresh_phone()
    body = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 1, host_phone)
    _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 2, second_phone)
    lobby_id = body["table_state"]["lobby_id"]
    _claim_host(client, api_key_header, lobby_id, host_phone)
    _start(client, api_key_header, lobby_id, host_phone)

    new_group = client.post(
        f"/api/patron/table/{fresh_table['table_id']}/new-group",
        headers=api_key_header, json={"phone_id": _fresh_phone()},
    )
    assert new_group.status_code == 200
    assert new_group.json()["lobby_id"] != lobby_id


def test_new_group_rejects_when_table_already_has_three_groups(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    venue_slug, table_number, table_id = (
        fresh_table["venue_slug"], fresh_table["table_number"], fresh_table["table_id"]
    )

    for i in range(3):
        host_phone = _fresh_phone()
        second_phone = _fresh_phone()
        if i == 0:
            body = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, i * 2 + 1, host_phone)
            _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, i * 2 + 2, second_phone)
            lobby_id = body["table_state"]["lobby_id"]
        else:
            resp = client.post(
                f"/api/patron/table/{table_id}/new-group", headers=api_key_header, json={"phone_id": host_phone}
            )
            assert resp.status_code == 200
            lobby_id = resp.json()["lobby_id"]
            # Add a second phone to meet MIN_PLAYERS
            client.post(
                f"/api/patron/table/{table_id}/new-group", headers=api_key_header, json={"phone_id": second_phone}
            )
            # Second phone joins the same lobby by tapping
            _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, i * 10, second_phone)
        _claim_host(client, api_key_header, lobby_id, host_phone)
        _start(client, api_key_header, lobby_id, host_phone)

    full = client.post(
        f"/api/patron/table/{table_id}/new-group", headers=api_key_header, json={"phone_id": _fresh_phone()}
    )
    assert full.status_code == 409

    # The 4th tap should also see table_full, not lobby or join_or_new.
    fourth_tap = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 99, _fresh_phone())
    assert fourth_tap["table_state"]["phase"] == "table_full"
    assert len(fourth_tap["table_state"]["groups"]) == 3


# gamespec.md: Adults Only Content Controls — the toggle is gated by the
# table's content_ceiling and the venue's restrict_adult_content switch,
# enforced server-side rather than trusted from the client.

def test_start_rejects_adults_only_on_standard_table(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    venue_slug, table_number = fresh_table["venue_slug"], fresh_table["table_number"]
    phone_a, phone_b = _fresh_phone(), _fresh_phone()
    body = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 1, phone_a)
    _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 2, phone_b)
    lobby_id = body["table_state"]["lobby_id"]
    _claim_host(client, api_key_header, lobby_id, phone_a)

    resp = _start(client, api_key_header, lobby_id, phone_a, adults_only=True)
    assert resp.status_code == 422


def test_start_allows_adults_only_on_adults_allowed_table(client, api_key_header, owner_a_token, adults_allowed_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, adults_allowed_table["table_number"])
    venue_slug, table_number = adults_allowed_table["venue_slug"], adults_allowed_table["table_number"]
    phone_a, phone_b = _fresh_phone(), _fresh_phone()
    body = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 1, phone_a)
    _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 2, phone_b)
    lobby_id = body["table_state"]["lobby_id"]
    _claim_host(client, api_key_header, lobby_id, phone_a)

    resp = _start(client, api_key_header, lobby_id, phone_a, adults_only=True)
    assert resp.status_code == 200, resp.text


def test_start_rejects_adults_only_when_venue_restricts_even_if_table_allows(
    client, api_key_header, owner_a_token, adults_allowed_table, venue_a_restricts_adult_content
):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, adults_allowed_table["table_number"])
    venue_slug, table_number = adults_allowed_table["venue_slug"], adults_allowed_table["table_number"]
    phone_a, phone_b = _fresh_phone(), _fresh_phone()
    body = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 1, phone_a)
    _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 2, phone_b)
    lobby_id = body["table_state"]["lobby_id"]
    _claim_host(client, api_key_header, lobby_id, phone_a)

    resp = _start(client, api_key_header, lobby_id, phone_a, adults_only=True)
    assert resp.status_code == 422


# Dev-only convenience for local testing — ends active sessions/lobbies at
# a table so the next tap starts fresh, rather than resuming whatever an
# earlier test round left active.

def test_dev_reset_table_ends_active_sessions_and_unblocks_a_fresh_lobby(
    client, api_key_header, owner_a_token, fresh_table
):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    venue_slug, table_number = fresh_table["venue_slug"], fresh_table["table_number"]
    host_phone, second_phone = _fresh_phone(), _fresh_phone()
    body = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 1, host_phone)
    _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 2, second_phone)
    lobby_id = body["table_state"]["lobby_id"]
    _claim_host(client, api_key_header, lobby_id, host_phone)
    _start(client, api_key_header, lobby_id, host_phone)

    headers = {**api_key_header, "Authorization": f"Bearer {owner_a_token}"}
    reset = client.post(
        "/api/dashboard/dev-reset-table", headers=headers, json={"table_number": fresh_table["table_number"]}
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["sessions_ended"] == 1

    fresh_tap = _tap_with_phone(
        client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"], tag_uid, 3, _fresh_phone()
    )
    assert fresh_tap["table_state"]["phase"] == "lobby"


def test_dev_reset_table_requires_venue_owner(client, api_key_header, fresh_table):
    staff_token = dev_login(client, api_key_header, STAFF_A_CLERK_ID)
    headers = {**api_key_header, "Authorization": f"Bearer {staff_token}"}

    resp = client.post(
        "/api/dashboard/dev-reset-table", headers=headers, json={"table_number": fresh_table["table_number"]}
    )
    assert resp.status_code == 403


def test_dev_reset_table_rejects_table_not_owned_by_caller(client, api_key_header, fresh_table):
    owner_b_token = dev_login(client, api_key_header, OWNER_B_CLERK_ID)
    headers = {**api_key_header, "Authorization": f"Bearer {owner_b_token}"}

    resp = client.post(
        "/api/dashboard/dev-reset-table", headers=headers, json={"table_number": fresh_table["table_number"]}
    )
    assert resp.status_code == 404


# --- New tests for per-person name entry ---

def test_set_name_stores_name_in_lobby(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    phone_a = _fresh_phone()
    body = _tap_with_phone(
        client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"], tag_uid, 1, phone_a
    )
    lobby_id = body["table_state"]["lobby_id"]

    resp = _set_name(client, api_key_header, lobby_id, phone_a, "Kaushik")
    assert resp.status_code == 200, resp.text

    poll = client.get(f"/api/patron/lobby/{lobby_id}", headers=api_key_header)
    assert poll.status_code == 200
    phones = poll.json()["phones"]
    assert phones[0]["name"] == "Kaushik"


def test_set_name_rejects_phone_not_in_lobby(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    phone_a = _fresh_phone()
    body = _tap_with_phone(
        client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"], tag_uid, 1, phone_a
    )
    lobby_id = body["table_state"]["lobby_id"]

    resp = _set_name(client, api_key_header, lobby_id, _fresh_phone(), "Intruder")
    assert resp.status_code == 403


def test_retap_does_not_erase_name(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    venue_slug, table_number = fresh_table["venue_slug"], fresh_table["table_number"]
    phone_a = _fresh_phone()
    body = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 1, phone_a)
    lobby_id = body["table_state"]["lobby_id"]

    _set_name(client, api_key_header, lobby_id, phone_a, "Kaushik")
    # Re-tap same phone with incremented counter — passes name=None to _join_lobby_phone.
    _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 2, phone_a)

    poll = client.get(f"/api/patron/lobby/{lobby_id}", headers=api_key_header)
    phones = poll.json()["phones"]
    assert phones[0]["name"] == "Kaushik"


def test_set_name_updates_on_resubmit(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    phone_a = _fresh_phone()
    body = _tap_with_phone(
        client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"], tag_uid, 1, phone_a
    )
    lobby_id = body["table_state"]["lobby_id"]

    _set_name(client, api_key_header, lobby_id, phone_a, "Kaushik")
    _set_name(client, api_key_header, lobby_id, phone_a, "K")

    poll = client.get(f"/api/patron/lobby/{lobby_id}", headers=api_key_header)
    phones = poll.json()["phones"]
    assert phones[0]["name"] == "K"


def test_start_derives_players_from_lobby_phones(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    venue_slug, table_number = fresh_table["venue_slug"], fresh_table["table_number"]
    phone_a, phone_b = _fresh_phone(), _fresh_phone()
    body = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 1, phone_a)
    _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 2, phone_b)
    lobby_id = body["table_state"]["lobby_id"]
    _set_name(client, api_key_header, lobby_id, phone_a, "Kaushik")
    _set_name(client, api_key_header, lobby_id, phone_b, "Sarah")
    _claim_host(client, api_key_header, lobby_id, phone_a)

    start = _start(client, api_key_header, lobby_id, phone_a)
    assert start.status_code == 200, start.text
    assert start.json()["player_count"] == 2

    # Verify game_players rows have the correct names.
    session_id = start.json()["session_id"]

    async def _fetch_names():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            rows = await conn.fetch(
                "SELECT name FROM game_players WHERE session_id = $1 ORDER BY id", session_id
            )
            return [r["name"] for r in rows]
        finally:
            await conn.close()

    names = asyncio.run(_fetch_names())
    assert sorted(names) == ["Kaushik", "Sarah"]


def test_start_falls_back_to_player_n_for_unnamed_phones(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    venue_slug, table_number = fresh_table["venue_slug"], fresh_table["table_number"]
    phone_a, phone_b = _fresh_phone(), _fresh_phone()
    body = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 1, phone_a)
    _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 2, phone_b)
    lobby_id = body["table_state"]["lobby_id"]
    _set_name(client, api_key_header, lobby_id, phone_a, "Kaushik")
    # phone_b does not set a name
    _claim_host(client, api_key_header, lobby_id, phone_a)

    start = _start(client, api_key_header, lobby_id, phone_a)
    assert start.status_code == 200, start.text
    assert start.json()["player_count"] == 2

    session_id = start.json()["session_id"]

    async def _fetch_names():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            rows = await conn.fetch(
                "SELECT name FROM game_players WHERE session_id = $1 ORDER BY id", session_id
            )
            return [r["name"] for r in rows]
        finally:
            await conn.close()

    names = asyncio.run(_fetch_names())
    # phone_a joined first → "Kaushik"; phone_b joined second → "Player 2"
    assert "Kaushik" in names
    assert "Player 2" in names


def test_start_rejects_fewer_than_min_players(client, api_key_header, owner_a_token, fresh_table):
    # Only 1 phone tapped — server derives count=1, below MIN_PLAYERS.
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    phone_id = _fresh_phone()
    body = _tap_with_phone(
        client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"], tag_uid, 1, phone_id
    )
    lobby_id = body["table_state"]["lobby_id"]
    _set_name(client, api_key_header, lobby_id, phone_id, "Solo")
    _claim_host(client, api_key_header, lobby_id, phone_id)

    resp = _start(client, api_key_header, lobby_id, phone_id)
    assert resp.status_code == 422


# --- Re-tap session resume tests ---

def test_origin_retap_resumes_as_origin(client, api_key_header, owner_a_token, fresh_table):
    """Origin phone re-tapping an active session resumes as is_origin=True."""
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    venue_slug, table_number = fresh_table["venue_slug"], fresh_table["table_number"]
    host_phone, second_phone = _fresh_phone(), _fresh_phone()

    body = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 1, host_phone)
    _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 2, second_phone)
    lobby_id = body["table_state"]["lobby_id"]
    _claim_host(client, api_key_header, lobby_id, host_phone)
    start = _start(client, api_key_header, lobby_id, host_phone)
    session_id = start.json()["session_id"]
    expected_adults_only = start.json()["adults_only"]
    expected_player_count = start.json()["player_count"]

    retap = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 3, host_phone)
    state = retap["table_state"]
    assert state["phase"] == "resume"
    assert state["is_origin"] is True
    assert state["session_id"] == session_id
    assert state["adults_only"] == expected_adults_only
    assert state["player_count"] == expected_player_count


def test_participant_retap_resumes_as_participant(client, api_key_header, owner_a_token, fresh_table):
    """Non-origin phone (was in the converted lobby) re-tapping resumes as is_origin=False."""
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    venue_slug, table_number = fresh_table["venue_slug"], fresh_table["table_number"]
    host_phone, participant_phone = _fresh_phone(), _fresh_phone()

    body = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 1, host_phone)
    _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 2, participant_phone)
    lobby_id = body["table_state"]["lobby_id"]
    _claim_host(client, api_key_header, lobby_id, host_phone)
    start = _start(client, api_key_header, lobby_id, host_phone)
    session_id = start.json()["session_id"]

    retap = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 3, participant_phone)
    state = retap["table_state"]
    assert state["phase"] == "resume"
    assert state["is_origin"] is False
    assert state["session_id"] == session_id


def test_stranger_phone_sees_join_or_new_not_resume(client, api_key_header, owner_a_token, fresh_table):
    """A phone with no connection to an active session still sees join_or_new."""
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    venue_slug, table_number = fresh_table["venue_slug"], fresh_table["table_number"]
    host_phone, second_phone = _fresh_phone(), _fresh_phone()

    body = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 1, host_phone)
    _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 2, second_phone)
    lobby_id = body["table_state"]["lobby_id"]
    _claim_host(client, api_key_header, lobby_id, host_phone)
    _start(client, api_key_header, lobby_id, host_phone)

    stranger = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 3, _fresh_phone())
    state = stranger["table_state"]
    assert state["phase"] == "join_or_new"
    assert state["phase"] != "resume"


def test_ended_session_does_not_resume(client, api_key_header, owner_a_token, fresh_table):
    """Origin phone re-tapping LONG after the session ended falls through to lobby.
    A recently-ended session routes to the recap instead (see test_endgame); here
    we age ended_at well past the recap window so it lands on a fresh lobby."""
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    venue_slug, table_number = fresh_table["venue_slug"], fresh_table["table_number"]
    host_phone, second_phone = _fresh_phone(), _fresh_phone()

    body = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 1, host_phone)
    _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 2, second_phone)
    lobby_id = body["table_state"]["lobby_id"]
    _claim_host(client, api_key_header, lobby_id, host_phone)
    start = _start(client, api_key_header, lobby_id, host_phone)
    session_id = start.json()["session_id"]

    async def _end():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute(
                "UPDATE game_sessions SET ended_at = NOW() - INTERVAL '120 minutes' WHERE id = $1",
                session_id,
            )
        finally:
            await conn.close()
    asyncio.run(_end())

    retap = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 3, host_phone)
    state = retap["table_state"]
    assert state["phase"] != "resume"
    # Ended long ago (past the recap window) → no active session → fresh lobby.
    assert state["phase"] == "lobby"


def test_poll_lobby_returns_phones_with_names(client, api_key_header, owner_a_token, fresh_table):
    tag_uid = pair_tag(client, api_key_header, owner_a_token, fresh_table["table_number"])
    venue_slug, table_number = fresh_table["venue_slug"], fresh_table["table_number"]
    phone_a, phone_b = _fresh_phone(), _fresh_phone()
    body = _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 1, phone_a)
    _tap_with_phone(client, api_key_header, venue_slug, table_number, tag_uid, 2, phone_b)
    lobby_id = body["table_state"]["lobby_id"]
    _set_name(client, api_key_header, lobby_id, phone_a, "Kaushik")
    _set_name(client, api_key_header, lobby_id, phone_b, "Sarah")

    # Poll with caller identity so is_self is populated (Item 3 redaction shape).
    poll = client.get(
        f"/api/patron/lobby/{lobby_id}",
        headers=api_key_header,
        params={"phone_id": phone_a},
    )
    assert poll.status_code == 200
    data = poll.json()
    assert "phones" in data
    assert len(data["phones"]) == 2

    # No raw phone_id must appear in any phone entry.
    for p in data["phones"]:
        assert "phone_id" not in p, f"phone_id leaked in phone entry: {p}"
        assert "slot_id" in p
        assert "is_self" in p

    # No host_phone_id in root response.
    assert "host_phone_id" not in data

    # Exactly one entry has is_self=True (phone_a is the caller).
    self_entries = [p for p in data["phones"] if p["is_self"]]
    assert len(self_entries) == 1
    assert self_entries[0]["name"] == "Kaushik"

    # Both names are present (order is insertion order — slot_id 0 = phone_a).
    names = {p["name"] for p in data["phones"]}
    assert "Kaushik" in names
    assert "Sarah" in names
