"""Tests for Re-Tap to Continue (spec: .pipeline/spec.md).

Mirrors test_endgame.py / test_host_migration.py fixtures/helpers exactly:
_cleanup_test_tags autouse fixture, _fresh_phone(), _tap(), _set_name(),
_setup_session() helpers, TestClient (HTTP) for all assertions, asyncpg.connect
for direct DB helpers (statement_cache_size=0 for pooler compat).

Runs CI-equivalent (SUPABASE_* unset -> realtime publish is a no-op), so
assertions only depend on the HTTP/DB layer, never on a delivered broadcast.
"""
import asyncio
import os
import uuid

import asyncpg
import pytest

from api.services.session_service import compute_retap_state
from api.tests.conftest import pair_tag, simulate_tap


@pytest.fixture(autouse=True)
def _cleanup_test_tags():
    """Delete test nfc_tags after each test so tag_uid collisions can't happen."""
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


def _tap(client, api_key_header, venue_slug, table_number, tag_uid, counter, phone_id):
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


def _set_name(client, api_key_header, lobby_id, phone_id, name):
    resp = client.post(
        f"/api/patron/lobby/{lobby_id}/set-name",
        headers=api_key_header,
        json={"phone_id": phone_id, "name": name},
    )
    assert resp.status_code == 200, resp.text


def _setup_session(client, api_key_header, owner_a_token, table_info,
                   num_phones=2, adults_only=False):
    """Tap `num_phones` phones, name them, host=first, start. Returns dict with
    session_id, table_id, origin phone, and the full ordered phones list."""
    tag_uid = pair_tag(client, api_key_header, owner_a_token, table_info["table_number"])
    phones = [_fresh_phone() for _ in range(num_phones)]
    first_body = None
    for i, phone in enumerate(phones):
        body = _tap(
            client, api_key_header, table_info["venue_slug"], table_info["table_number"],
            tag_uid, i + 1, phone,
        )
        if i == 0:
            first_body = body
    lobby_id = first_body["table_state"]["lobby_id"]
    table_id = first_body["table_id"]
    for i, phone in enumerate(phones):
        _set_name(client, api_key_header, lobby_id, phone, f"Player {i + 1}")

    resp = client.post(
        f"/api/patron/lobby/{lobby_id}/claim-host",
        headers=api_key_header, json={"phone_id": phones[0]},
    )
    assert resp.status_code == 200, resp.text

    start = client.post(
        f"/api/patron/lobby/{lobby_id}/start",
        headers=api_key_header, json={"phone_id": phones[0], "adults_only": adults_only},
    )
    assert start.status_code == 200, start.text
    return {
        "session_id": start.json()["session_id"],
        "table_id": table_id,
        "origin": phones[0],
        "phones": phones,
        "tag_uid": tag_uid,
    }


# --- HTTP helpers ---

def _current_round(client, h, session_id):
    return client.get(
        f"/api/patron/sessions/{session_id}/current-round",
        headers=h,
    )


def _poll_state(client, h, session_id, phone_id):
    return client.get(
        f"/api/patron/sessions/{session_id}/trivia/current",
        headers=h,
        params={"phone_id": phone_id},
    )


def _select_hot_seat(client, h, session_id, phone):
    return client.post(
        f"/api/patron/sessions/{session_id}/select-hot-seat",
        headers=h, json={"phone_id": phone},
    )


def _draw_card(client, h, session_id, phone, player_id):
    return client.post(
        f"/api/patron/sessions/{session_id}/draw-card",
        headers=h, json={"phone_id": phone, "player_id": player_id},
    )


def _complete_round(client, h, round_id, phone):
    return client.post(
        f"/api/patron/rounds/{round_id}/complete",
        headers=h, json={"phone_id": phone},
    )


def _age_session(client, h, session_id, minutes):
    """Call the dev endpoint to wind last_activity_at back by N minutes."""
    return client.post(
        "/api/dev/age-session",
        headers=h,
        json={"session_id": session_id, "minutes": minutes},
    )


# --- DB helpers ---

def _set_last_activity(session_id, minutes_ago):
    """Set last_activity_at to N minutes in the past."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute(
                "UPDATE game_sessions"
                " SET last_activity_at = NOW() - $1 * INTERVAL '1 minute'"
                " WHERE id = $2",
                minutes_ago, uuid.UUID(session_id),
            )
        finally:
            await conn.close()
    asyncio.run(_q())


def _get_session_end_info(session_id):
    """Return (ended_at, end_reason) for a session."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            row = await conn.fetchrow(
                "SELECT ended_at, end_reason FROM game_sessions WHERE id = $1",
                uuid.UUID(session_id),
            )
            return row["ended_at"], row["end_reason"]
        finally:
            await conn.close()
    return asyncio.run(_q())


def _get_last_activity(session_id):
    """Return last_activity_at datetime for a session."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await conn.fetchval(
                "SELECT last_activity_at FROM game_sessions WHERE id = $1",
                uuid.UUID(session_id),
            )
        finally:
            await conn.close()
    return asyncio.run(_q())


def _get_venue_retap_default():
    """Return the column_default for venues.retap_interval_minutes."""
    async def _q():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await conn.fetchval(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_name = 'venues' AND column_name = 'retap_interval_minutes'"
            )
        finally:
            await conn.close()
    return asyncio.run(_q())


# ---------------------------------------------------------------------------
# 1. Pure function boundary table
# ---------------------------------------------------------------------------

def test_compute_retap_state_boundaries():
    """Pure function: exact boundary table from the spec (threshold=900s)."""
    cases = [
        (0,    900, {"state": "active",  "seconds_left": 0}),
        (899,  900, {"state": "active",  "seconds_left": 0}),
        (900,  900, {"state": "prompt",  "seconds_left": 120}),
        (901,  900, {"state": "prompt",  "seconds_left": 119}),
        (1019, 900, {"state": "prompt",  "seconds_left": 1}),
        (1020, 900, {"state": "paused",  "seconds_left": 300}),
        (1021, 900, {"state": "paused",  "seconds_left": 299}),
        (1319, 900, {"state": "paused",  "seconds_left": 1}),
        (1320, 900, {"state": "expired", "seconds_left": 0}),
        (9999, 900, {"state": "expired", "seconds_left": 0}),
    ]
    for idle, threshold, expected in cases:
        result = compute_retap_state(idle, threshold)
        assert result == expected, (
            f"compute_retap_state({idle}, {threshold}) = {result!r}, "
            f"expected {expected!r}"
        )


# ---------------------------------------------------------------------------
# 2. GET /current-round reflects retap state: active band
# ---------------------------------------------------------------------------

def test_poll_current_round_retap_active(client, api_key_header, owner_a_token, fresh_table):
    """Fresh session -> GET /current-round -> retap.state == 'active', ended == False."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)

    resp = _current_round(client, api_key_header, s["session_id"])
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["ended"] is False
    assert "retap" in data
    assert data["retap"]["state"] == "active"
    assert data["retap"]["seconds_left"] == 0


# ---------------------------------------------------------------------------
# 3. GET /current-round reflects retap state: prompt band
# ---------------------------------------------------------------------------

def test_poll_current_round_retap_prompt(client, api_key_header, owner_a_token, fresh_table):
    """Age last_activity_at 16 min -> poll -> retap.state == 'prompt', seconds_left > 0."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)

    # 16 min = 960 s; threshold = 900 s; prompt ends at 1020 s; seconds_left = 60
    _set_last_activity(s["session_id"], 16)

    resp = _current_round(client, api_key_header, s["session_id"])
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["ended"] is False
    assert data["retap"]["state"] == "prompt"
    assert data["retap"]["seconds_left"] > 0


# ---------------------------------------------------------------------------
# 4. GET /current-round reflects retap state: paused band
# ---------------------------------------------------------------------------

def test_poll_current_round_retap_paused(client, api_key_header, owner_a_token, fresh_table):
    """Age last_activity_at 18 min -> poll -> retap.state == 'paused', seconds_left > 0."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)

    # 18 min = 1080 s; pause starts at 1020 s; pause ends at 1320 s; seconds_left ~ 240
    _set_last_activity(s["session_id"], 18)

    resp = _current_round(client, api_key_header, s["session_id"])
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["ended"] is False
    assert data["retap"]["state"] == "paused"
    assert data["retap"]["seconds_left"] > 0


# ---------------------------------------------------------------------------
# 5. GET /current-round: expired lazily ends session
# ---------------------------------------------------------------------------

def test_poll_current_round_retap_expired_ends_session(
    client, api_key_header, owner_a_token, fresh_table
):
    """Age 23 min (past expire at 22 min) -> poll -> ended == True, retap_expired in DB."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)

    # 23 min = 1380 s; expire_start = 900 + 120 + 300 = 1320 s; well expired.
    _set_last_activity(s["session_id"], 23)

    resp = _current_round(client, api_key_header, s["session_id"])
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["ended"] is True
    assert data["retap"]["state"] == "expired"

    ended_at, end_reason = _get_session_end_info(s["session_id"])
    assert ended_at is not None, "ended_at must be set after lazy expire"
    assert end_reason == "retap_expired"


# ---------------------------------------------------------------------------
# 6. GET /trivia/current returns retap field + expires
# ---------------------------------------------------------------------------

def test_trivia_current_returns_retap_and_expires(
    client, api_key_header, owner_a_token, fresh_table
):
    """2-phone session. Age 16 min -> trivia poll shows retap.state == 'prompt'.
    Age to 23 min -> poll shows phase == 'ended', retap.state == 'expired'. DB ok."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    participant = s["phones"][1]

    # Age to prompt band (16 min)
    _set_last_activity(s["session_id"], 16)

    resp = _poll_state(client, api_key_header, s["session_id"], participant)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "retap" in data
    assert data["retap"]["state"] == "prompt"

    # Age further to expired band (23 min)
    _set_last_activity(s["session_id"], 23)

    resp2 = _poll_state(client, api_key_header, s["session_id"], participant)
    assert resp2.status_code == 200, resp2.text
    data2 = resp2.json()
    assert data2["phase"] == "ended"
    assert data2["retap"]["state"] == "expired"

    ended_at, end_reason = _get_session_end_info(s["session_id"])
    assert ended_at is not None
    assert end_reason == "retap_expired"


# ---------------------------------------------------------------------------
# 7. Re-tap within prompt window resumes + bumps clock (origin)
# ---------------------------------------------------------------------------

def test_retap_within_grace_window_resumes(
    client, api_key_header, owner_a_token, fresh_table
):
    """Age to prompt (16 min), re-tap origin -> phase == 'resume', clock reset."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)

    _set_last_activity(s["session_id"], 16)

    tag_uid = s["tag_uid"]
    retap_resp = _tap(
        client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"],
        tag_uid, len(s["phones"]) + 1, s["origin"],
    )
    table_state = retap_resp.get("table_state", {})
    assert table_state.get("phase") == "resume", (
        f"Expected phase='resume' during prompt window, got {table_state}"
    )
    assert table_state.get("is_origin") is True

    # Clock must be reset: last_activity_at should be within ~10 seconds of now.
    # asyncpg returns naive UTC datetimes; compare against datetime.utcnow().
    import datetime
    last_act = _get_last_activity(s["session_id"])
    assert last_act is not None
    now_utc = datetime.datetime.utcnow()
    delta = abs((now_utc - last_act).total_seconds())
    assert delta < 10, (
        f"last_activity_at should be ~now after resume, got delta={delta:.1f}s"
    )

    # Subsequent poll must show 'active' (clock was reset)
    poll_resp = _current_round(client, api_key_header, s["session_id"])
    assert poll_resp.status_code == 200, poll_resp.text
    assert poll_resp.json()["retap"]["state"] == "active"


# ---------------------------------------------------------------------------
# 8. Re-tap within paused window resumes + bumps clock (origin)
# ---------------------------------------------------------------------------

def test_retap_within_pause_window_resumes(
    client, api_key_header, owner_a_token, fresh_table
):
    """Age to paused (18 min), re-tap origin -> phase == 'resume', clock reset."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)

    _set_last_activity(s["session_id"], 18)

    tag_uid = s["tag_uid"]
    retap_resp = _tap(
        client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"],
        tag_uid, len(s["phones"]) + 1, s["origin"],
    )
    table_state = retap_resp.get("table_state", {})
    assert table_state.get("phase") == "resume", (
        f"Expected phase='resume' during paused window, got {table_state}"
    )
    assert table_state.get("is_origin") is True

    import datetime
    last_act = _get_last_activity(s["session_id"])
    assert last_act is not None
    now_utc = datetime.datetime.utcnow()
    delta = abs((now_utc - last_act).total_seconds())
    assert delta < 10, (
        f"last_activity_at should be ~now after resume, got delta={delta:.1f}s"
    )


# ---------------------------------------------------------------------------
# 9. Re-tap past expiry window ends session (origin)
# ---------------------------------------------------------------------------

def test_retap_past_window_ends_session(
    client, api_key_header, owner_a_token, fresh_table
):
    """Age 23 min, re-tap origin -> phase == 'recap', end_reason == 'retap_expired'."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)

    _set_last_activity(s["session_id"], 23)

    tag_uid = s["tag_uid"]
    retap_resp = _tap(
        client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"],
        tag_uid, len(s["phones"]) + 1, s["origin"],
    )
    table_state = retap_resp.get("table_state", {})
    assert table_state.get("phase") == "recap", (
        f"Expected phase='recap' after expiry, got {table_state}"
    )
    assert table_state.get("session_id") == s["session_id"]

    ended_at, end_reason = _get_session_end_info(s["session_id"])
    assert ended_at is not None
    assert end_reason == "retap_expired"


# ---------------------------------------------------------------------------
# 10. Re-tap within prompt window resumes (participant branch)
# ---------------------------------------------------------------------------

def test_participant_retap_within_window_resumes(
    client, api_key_header, owner_a_token, fresh_table
):
    """Age to prompt (16 min), participant phone taps -> phase == 'resume', is_origin == False."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    participant = s["phones"][1]

    _set_last_activity(s["session_id"], 16)

    tag_uid = s["tag_uid"]
    retap_resp = _tap(
        client, api_key_header, fresh_table["venue_slug"], fresh_table["table_number"],
        tag_uid, len(s["phones"]) + 1, participant,
    )
    table_state = retap_resp.get("table_state", {})
    assert table_state.get("phase") == "resume", (
        f"Expected phase='resume' for participant during prompt window, got {table_state}"
    )
    assert table_state.get("is_origin") is False


# ---------------------------------------------------------------------------
# 11. Completed Chooser round resets the clock
# ---------------------------------------------------------------------------

def test_completed_round_resets_clock(
    client, api_key_header, owner_a_token, fresh_table
):
    """Draw + complete a Chooser round bumps last_activity_at. Age to 14 min (< 15
    threshold) -> poll shows retap.state == 'active' (not 'prompt')."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)

    # Play one Chooser round: select hot seat, draw, complete.
    hs_resp = _select_hot_seat(client, api_key_header, s["session_id"], s["origin"])
    assert hs_resp.status_code == 200, hs_resp.text
    chosen_player_id = hs_resp.json()["player_id"]

    draw_resp = _draw_card(
        client, api_key_header, s["session_id"], s["origin"], chosen_player_id
    )
    assert draw_resp.status_code == 200, draw_resp.text
    round_id = draw_resp.json()["round_id"]

    complete_resp = _complete_round(client, api_key_header, round_id, s["origin"])
    assert complete_resp.status_code == 200, complete_resp.text

    # Verify last_activity_at was recently bumped by the completed round.
    # asyncpg returns naive UTC datetimes; compare against datetime.utcnow().
    import datetime
    last_act = _get_last_activity(s["session_id"])
    assert last_act is not None
    now_utc = datetime.datetime.utcnow()
    delta = abs((now_utc - last_act).total_seconds())
    assert delta < 15, (
        f"last_activity_at should be ~now after completing a round, got delta={delta:.1f}s"
    )

    # Now age to 14 min (just under the 15-min threshold of 900s).
    # 14 min = 840 s < 900 s threshold -> must remain 'active'.
    _set_last_activity(s["session_id"], 14)

    resp = _current_round(client, api_key_header, s["session_id"])
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["retap"]["state"] == "active", (
        f"Expected 'active' at 14 min with 15-min threshold, got {data['retap']}"
    )


# ---------------------------------------------------------------------------
# 12. Migration default
# ---------------------------------------------------------------------------

def test_migration_default():
    """venues.retap_interval_minutes column default is '15' after migration."""
    default_val = _get_venue_retap_default()
    assert default_val is not None, "column_default must not be NULL after migration"
    assert "15" in str(default_val), (
        f"Expected column default to contain '15', got {default_val!r}"
    )


# ---------------------------------------------------------------------------
# 13. Dev endpoint gating
# ---------------------------------------------------------------------------

def test_dev_endpoint_age_session_works(
    client, api_key_header, owner_a_token, fresh_table
):
    """With DEV_MODE=true (test env), POST /api/dev/age-session returns ok=True
    and actually ages the session."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)

    resp = _age_session(client, api_key_header, s["session_id"], 10)
    assert resp.status_code == 200, resp.text
    assert resp.json().get("ok") is True

    # Confirm the session was aged: poll should no longer be 'active' at 10 min.
    # (10 min = 600 s < 900 s threshold, so it's still active — just verify no error)
    poll_resp = _current_round(client, api_key_header, s["session_id"])
    assert poll_resp.status_code == 200, poll_resp.text
    # At 10 min it's still active (threshold is 15 min)
    assert poll_resp.json()["retap"]["state"] == "active"


def test_dev_endpoint_unknown_session_is_no_op(
    client, api_key_header, owner_a_token, fresh_table
):
    """POST /api/dev/age-session with a non-existent session_id returns ok=True
    (UPDATE with no matching row is a no-op, not an error)."""
    fake_id = str(uuid.uuid4())
    resp = _age_session(client, api_key_header, fake_id, 10)
    # The endpoint does not raise on zero-row UPDATE; it just returns ok.
    assert resp.status_code == 200, resp.text
    assert resp.json().get("ok") is True


# ---------------------------------------------------------------------------
# 14. BOLA / non-member poll still gets retap field
# ---------------------------------------------------------------------------

def test_non_member_poll_returns_retap_field(
    client, api_key_header, owner_a_token, fresh_table
):
    """A phone that is not a member of the session gets is_member=False but the
    retap block is still present (no leaderboard/questions leak)."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    stranger = _fresh_phone()

    resp = _poll_state(client, api_key_header, s["session_id"], stranger)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["is_member"] is False
    assert data["phase"] == "not_member"
    assert "retap" in data
    assert data["retap"]["state"] == "active"
    # No sensitive fields leaked
    assert "leaderboard" not in data
    assert "question" not in data


# ---------------------------------------------------------------------------
# 15. Idempotent lazy expire: both endpoints expire cleanly when already ended
# ---------------------------------------------------------------------------

def test_lazy_expire_idempotent_both_endpoints(
    client, api_key_header, owner_a_token, fresh_table
):
    """Two polls (current-round, then trivia/current) when session is expired.
    Both return ended/expired. The second call must not fail even though
    ended_at is already set (idle_end_session WHERE ended_at IS NULL is idempotent)."""
    s = _setup_session(client, api_key_header, owner_a_token, fresh_table, num_phones=2)
    participant = s["phones"][1]

    _set_last_activity(s["session_id"], 23)

    # First expiry trigger via current-round
    r1 = _current_round(client, api_key_header, s["session_id"])
    assert r1.status_code == 200, r1.text
    assert r1.json()["ended"] is True
    assert r1.json()["retap"]["state"] == "expired"

    # Second trigger via trivia/current — must not 500
    r2 = _poll_state(client, api_key_header, s["session_id"], participant)
    assert r2.status_code == 200, r2.text
    assert r2.json()["phase"] == "ended"
    assert r2.json()["retap"]["state"] == "expired"

    # DB: exactly one end_reason, set once
    ended_at, end_reason = _get_session_end_info(s["session_id"])
    assert ended_at is not None
    assert end_reason == "retap_expired"
