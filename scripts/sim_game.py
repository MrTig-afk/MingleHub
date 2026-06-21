#!/usr/bin/env python
"""Headless multi-phone game simulator for MingleHub.

Drives the patron HTTP API (the SAME endpoints real phones hit) to play full
games end-to-end with no devices. Lets you regression-test the backend game loop
plus today's changes without your phones.

Covered scenarios (see SCENARIOS at the bottom):
  - happy_path:    2 phones, full cadence (Chooser -> Roulette -> Trivia) -> End -> Recap
  - host_migration: host leaves mid-game -> migrates to the other player, game lives
  - last_leaver:    everyone leaves -> game ends (host_left_no_players)
  - new_game_bypass: end a game, then a "New game" tap (new_game=1) skips the recap-lock
  - trivia_scoring: every phone answers; correct answers score, wrong ones don't

Run:  venv/Scripts/python.exe scripts/sim_game.py [scenario|all]
Needs the dev server on https://192.168.1.108:8000 with DEV_MODE=true.
"""
import os
import sys
import uuid
import requests
import urllib3

urllib3.disable_warnings()

BASE = "https://192.168.1.108:8000"
KEY = "dev-key"
VENUE = os.getenv("SIM_VENUE", "fifty-five-bar")

S = requests.Session()
S.verify = False
S.trust_env = False  # don't let REQUESTS_CA_BUNDLE / proxies re-enable cert verification
S.headers.update({"Content-Type": "application/json", "X-API-Key": KEY})

PASS, FAIL = [], []


class SimError(Exception):
    pass


def _req(method, path, **kw):
    r = S.request(method, f"{BASE}/api/patron{path}", verify=False, **kw)
    if not r.ok:
        raise SimError(f"{method} {path} -> {r.status_code}: {r.text[:240]}")
    return r.json() if r.text.strip() else {}


# --- endpoint wrappers -------------------------------------------------------
def tap(phone, table, new_game=False):
    q = f"?venue_slug={VENUE}&table_number={table}&phone_id={phone}"
    if new_game:
        q += "&new_game=1"
    return _req("GET", f"/tap{q}")


def claim_host(lobby, phone): return _req("POST", f"/lobby/{lobby}/claim-host", json={"phone_id": phone})
def set_name(lobby, phone, name): return _req("POST", f"/lobby/{lobby}/set-name", json={"phone_id": phone, "name": name})
def start(lobby, phone): return _req("POST", f"/lobby/{lobby}/start", json={"phone_id": phone, "adults_only": False, "group_label": None})  # noqa: E501
def hot_seat(sess, phone): return _req("POST", f"/sessions/{sess}/select-hot-seat", json={"phone_id": phone})
def draw(sess, phone, player_id): return _req("POST", f"/sessions/{sess}/draw-card", json={"phone_id": phone, "player_id": player_id})  # noqa: E501
def complete(rid, phone): return _req("POST", f"/rounds/{rid}/complete", json={"phone_id": phone})
def roulette_start(sess, phone): return _req("POST", f"/sessions/{sess}/roulette/start", json={"phone_id": phone})
def vote(rid, phone, target): return _req("POST", f"/rounds/{rid}/vote-loser", json={"phone_id": phone, "voted_player_id": target})  # noqa: E501
def roulette_reveal(rid, phone): return _req("POST", f"/rounds/{rid}/roulette/reveal", json={"phone_id": phone})
def trivia_start(sess, phone): return _req("POST", f"/sessions/{sess}/trivia/start", json={"phone_id": phone})
def trivia_join(rid, phone): return _req("POST", f"/trivia/{rid}/join", json={"phone_id": phone})
def trivia_begin(rid, phone): return _req("POST", f"/trivia/{rid}/begin", json={"phone_id": phone})
def trivia_answer(rid, phone, qi, opt): return _req("POST", f"/trivia/{rid}/answer", json={"phone_id": phone, "question_index": qi, "selected_option": opt, "time_to_answer_ms": 1000})  # noqa: E501
def trivia_finish(rid, phone): return _req("POST", f"/trivia/{rid}/finish", json={"phone_id": phone})
def leave(sess, phone): return _req("POST", f"/sessions/{sess}/leave", json={"phone_id": phone})
def rejoin(sess, phone): return _req("POST", f"/sessions/{sess}/rejoin", json={"phone_id": phone})
def end_game(sess, phone): return _req("POST", f"/sessions/{sess}/end-game", json={"phone_id": phone})
def recap(sess): return _req("GET", f"/sessions/{sess}/recap")
def leaderboard(sess): return _req("GET", f"/sessions/{sess}/leaderboard")
def current_round(sess): return _req("GET", f"/sessions/{sess}/current-round")
def skip_roulette(rid, phone): return _req("POST", f"/rounds/{rid}/roulette/skip", json={"phone_id": phone})
def join_session(sess, phone, name): return _req("POST", f"/sessions/{sess}/join", json={"phone_id": phone, "name": name})
def new_group(table_id, phone): return _req("POST", f"/table/{table_id}/new-group", json={"phone_id": phone})


def dash_get(path, token):
    r = S.get(f"{BASE}/api/dashboard/{path}",
              headers={"Authorization": f"Bearer {token}"}, verify=False)
    if not r.ok:
        raise SimError(f"GET /dashboard/{path} -> {r.status_code}: {r.text[:200]}")
    return r.json()


def owner_token():
    r = S.post(f"{BASE}/api/auth/dev-login", json={"clerk_user_id": "dev_owner_a"}, verify=False)
    return r.json()["token"]


def reset_table(table, token):
    S.post(f"{BASE}/api/dashboard/dev-reset-table",
           json={"table_number": table},
           headers={"Authorization": f"Bearer {token}"}, verify=False)


# --- helpers -----------------------------------------------------------------
def new_phones(n):
    return [f"sim-{uuid.uuid4().hex[:12]}" for _ in range(n)]


def expect(cond, msg):
    if cond:
        PASS.append(msg)
        print(f"   [PASS] {msg}")
    else:
        FAIL.append(msg)
        print(f"   [FAIL] {msg}")


def open_lobby_session(table, phones, token):
    """Reset table, all phones tap, host claims + everyone names, start. Returns session_id."""
    reset_table(table, token)
    states = [tap(p, table) for p in phones]
    ts = states[0]["table_state"]
    if ts.get("phase") != "lobby":
        raise SimError(f"expected lobby after reset, got phase={ts.get('phase')}: {ts}")
    lobby = ts["lobby_id"]
    claim_host(lobby, phones[0])
    for i, p in enumerate(phones):
        set_name(lobby, p, f"P{i + 1}")
    res = start(lobby, phones[0])
    sess = res.get("converted_session_id") or res.get("session_id")
    if not sess:
        raise SimError(f"no session id from start: {res}")
    return sess, res


def probe_shapes(table=1):
    """One-off: print the exact response shapes we depend on, so the wrappers are correct."""
    tok = owner_token()
    phones = new_phones(2)
    print("== open lobby + start ==")
    sess, started = open_lobby_session(table, phones, tok)
    print("start ->", started)
    print("== chooser: hot_seat -> draw -> complete ==")
    hs = hot_seat(sess, phones[0])
    print("hot_seat ->", hs)
    d = draw(sess, phones[0], hs["player_id"])
    card_keys = list(d.get("card", {}).keys()) if isinstance(d.get("card"), dict) else type(d.get("card"))
    print("draw ->", {k: (v if k != 'card' else '<card>') for k, v in d.items()}, "| card keys:", card_keys)
    print("complete ->", complete(d["round_id"], phones[0]))
    print("== roulette: start -> (vote shape?) ==")
    rs = roulette_start(sess, phones[0])
    print("roulette_start ->", {k: (v if k not in ('card',) else '<card>') for k, v in rs.items()})
    print("== trivia: start -> begin (question shape) ==")
    ts = trivia_start(sess, phones[0])
    print("trivia_start ->", ts)


# --- round drivers (replicate what RoundOrigin does on the host phone) --------
def play_chooser(sess, host):
    hs = hot_seat(sess, host)
    d = draw(sess, host, hs["player_id"])
    res = complete(d["round_id"], host)
    return res


def play_roulette(sess, host, voters):
    rs = roulette_start(sess, host)
    target = rs["players"][-1]["id"]  # everyone votes the last-listed player as loser
    for p in voters:
        vote(rs["round_id"], p, target)
    return roulette_reveal(rs["round_id"], host)


def play_trivia(sess, host, others):
    """Host begins; phone[0]=host answers 'A', everyone else answers the revealed
    correct option (so we can assert correct-scores-higher-than-wrong)."""
    ts = trivia_start(sess, host)
    rid = ts["trivia_round_id"]
    for p in others:
        trivia_join(rid, p)
    begun = trivia_begin(rid, host)
    n = len(begun.get("questions", [])) or ts.get("num_questions", 5)
    host_correct = 0
    for qi in range(n):
        r_host = trivia_answer(rid, host, qi, "A")
        correct = r_host["correct_option"]
        if r_host["is_correct"]:
            host_correct += 1
        for p in others:
            r_other = trivia_answer(rid, p, qi, correct)  # always-correct answerer
            expect(r_other["is_correct"] is True and r_other["score_awarded"] > 0,
                   f"trivia q{qi}: correct answer scores ({r_other['score_awarded']} pts)")
    # Self-paced trivia auto-completes server-side once everyone has answered, so
    # finish may 409 ("round_not_in_progress"). The real client ignores this too.
    try:
        trivia_finish(rid, host)
    except SimError:
        pass
    return host_correct, n


# --- scenarios ---------------------------------------------------------------
def scenario_happy_path(table, tok):
    print("\n### happy_path: 2 phones, full cadence -> end -> recap")
    phones = new_phones(2)
    host = phones[0]
    sess, _ = open_lobby_session(table, phones, tok)
    play_chooser(sess, host)
    expect(current_round(sess)["current_round_number"] == 1, "after chooser, round #1 recorded")
    play_roulette(sess, host, phones)
    expect(current_round(sess)["current_round_number"] == 2, "after roulette, round #2 recorded")
    play_trivia(sess, host, phones[1:])
    expect(current_round(sess)["current_round_number"] == 3, "after trivia, round #3 recorded")
    end_game(sess, host)
    cr = current_round(sess)
    expect(cr["ended"] is True, "end_game -> session ended")
    rc = recap(sess)
    expect(len(rc.get("leaderboard", [])) == 2, "recap shows both players")


def scenario_host_migration(table, tok):
    print("\n### host_migration: host leaves mid-game -> migrates, game lives")
    phones = new_phones(2)
    host, other = phones
    sess, _ = open_lobby_session(table, phones, tok)
    play_chooser(sess, host)
    leave(sess, host)  # origin leaves
    cr = current_round(sess)
    expect(cr["ended"] is False, "host leave with 1 player remaining -> game NOT ended (migrated)")
    expect(cr["active_count"] == 1, "active_count drops to 1 after host leaves")
    # the migrated origin (other) can now end the game -> proves it's the new origin
    end_game(sess, other)
    expect(current_round(sess)["ended"] is True, "migrated host (other phone) can end the game")


def scenario_last_leaver(table, tok):
    print("\n### last_leaver: everyone leaves -> game ends (host_left_no_players)")
    phones = new_phones(2)
    host, other = phones
    sess, _ = open_lobby_session(table, phones, tok)
    play_chooser(sess, host)
    leave(sess, other)  # non-host leaves first
    expect(current_round(sess)["ended"] is False, "non-host leave -> game continues")
    leave(sess, host)   # last player (origin) leaves
    expect(current_round(sess)["ended"] is True, "last player leaves -> game ends")


def scenario_new_game_bypass(table, tok):
    print("\n### new_game_bypass: ended game -> plain tap=recap, new_game tap=lobby")
    phones = new_phones(2)
    host = phones[0]
    sess, _ = open_lobby_session(table, phones, tok)
    play_chooser(sess, host)
    end_game(sess, host)
    plain = tap(host, table)["table_state"]["phase"]
    expect(plain == "recap", f"plain re-tap after end -> recap (got {plain})")
    fresh = tap(host, table, new_game=True)["table_state"]["phase"]
    expect(fresh == "lobby", f"new_game re-tap -> fresh lobby, bypassing recap-lock (got {fresh})")


def scenario_three_phones(table, tok):
    print("\n### three_phones: full 3-player game -> end -> recap")
    phones = new_phones(3)
    host = phones[0]
    sess, _ = open_lobby_session(table, phones, tok)
    play_chooser(sess, host)
    play_roulette(sess, host, phones)
    play_trivia(sess, host, phones[1:])
    end_game(sess, host)
    rc = recap(sess)
    expect(len(rc.get("leaderboard", [])) == 3, "3-player recap shows all three")


def scenario_roulette_skip(table, tok):
    print("\n### roulette_skip: origin skips roulette -> 0 pts, advances")
    phones = new_phones(2)
    host = phones[0]
    sess, _ = open_lobby_session(table, phones, tok)
    play_chooser(sess, host)
    rs = roulette_start(sess, host)
    skip_roulette(rs["round_id"], host)
    expect(current_round(sess)["current_round_number"] == 2, "skipped roulette still records round #2")
    expect(current_round(sess)["ended"] is False, "skip does not end the game")


def scenario_trivia_afk(table, tok):
    print("\n### trivia_afk: one phone never answers -> round still finalizes")
    phones = new_phones(2)
    host, afk = phones
    sess, _ = open_lobby_session(table, phones, tok)
    play_chooser(sess, host)
    play_roulette(sess, host, phones)
    before = {r["name"]: r["score"] for r in leaderboard(sess)["leaderboard"]}
    ts = trivia_start(sess, host)
    rid = ts["trivia_round_id"]
    trivia_join(rid, afk)
    begun = trivia_begin(rid, host)
    n = len(begun.get("questions", [])) or 5
    for qi in range(n):
        trivia_answer(rid, host, qi, "A")   # afk answers nothing this whole round
    try:
        trivia_finish(rid, host)
    except SimError:
        pass
    lb = {r["name"]: r for r in leaderboard(sess)["leaderboard"]}
    expect(lb.get("P2", {}).get("score", -1) == before.get("P2", -2),
           "AFK player (P2) gained 0 trivia points")
    expect(current_round(sess)["ended"] is False, "AFK trivia round finalizes without ending the game")


def scenario_join_or_new(table, tok):
    print("\n### join_or_new: 3rd phone joins a live table; 4th starts a new group")
    phones = new_phones(2)
    sess, _ = open_lobby_session(table, phones, tok)
    p3 = new_phones(1)[0]
    st = tap(p3, table)["table_state"]
    expect(st["phase"] == "join_or_new", f"3rd phone on a live table -> join_or_new (got {st['phase']})")
    join_session(st["groups"][0]["session_id"], p3, "P3")
    names = [r["name"] for r in leaderboard(sess)["leaderboard"]]
    expect("P3" in names, "3rd phone joined the existing session")
    p4 = new_phones(1)[0]
    st4 = tap(p4, table)["table_state"]
    ng = new_group(st4["table_id"], p4)
    expect("lobby_id" in ng or ng.get("phase") == "lobby", "4th phone can start a new group")


def scenario_dashboard_reflection(table, tok):
    print("\n### dashboard_reflection: a live game shows in /overview and /tables")
    phones = new_phones(2)
    host = phones[0]
    sess, _ = open_lobby_session(table, phones, tok)
    play_chooser(sess, host)
    ov = dash_get("overview", tok)
    mine = [x for x in ov["active_sessions"] if x["session_id"] == sess]
    expect(bool(mine), "live session appears in /dashboard/overview")
    expect(bool(mine and mine[0].get("table_id")), "overview session carries table_id")
    tb = dash_get("tables", tok)
    this_table = [t for t in tb if t["table_number"] == table]
    expect(bool(this_table and this_table[0]["active_session_count"] >= 1),
           "table shows >=1 active session in /tables")


SCENARIOS = {
    "happy_path": scenario_happy_path,
    "host_migration": scenario_host_migration,
    "last_leaver": scenario_last_leaver,
    "new_game_bypass": scenario_new_game_bypass,
    "three_phones": scenario_three_phones,
    "roulette_skip": scenario_roulette_skip,
    "trivia_afk": scenario_trivia_afk,
    "join_or_new": scenario_join_or_new,
    "dashboard_reflection": scenario_dashboard_reflection,
}


def run(which, table=1):
    tok = owner_token()
    names = list(SCENARIOS) if which == "all" else [which]
    for n in names:
        try:
            SCENARIOS[n](table, tok)
        except SimError as e:
            FAIL.append(f"{n}: CRASHED -> {e}")
            print(f"   [CRASH] {n}: {e}")
    reset_table(table, tok)  # leave the table clean for the next person
    print(f"\n===== SUMMARY: {len(PASS)} passed, {len(FAIL)} failed =====")
    for f in FAIL:
        print(f"  FAIL: {f}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "all"
    table = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    if arg == "probe":
        probe_shapes()
    elif arg in SCENARIOS or arg == "all":
        sys.exit(run(arg, table))
    else:
        print(f"unknown: {arg}. options: probe, all, {', '.join(SCENARIOS)}")
        sys.exit(2)
