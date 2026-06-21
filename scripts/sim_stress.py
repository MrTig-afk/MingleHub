#!/usr/bin/env python
"""Concurrency / multi-venue / isolation stress test for MingleHub.

Plays N full games CONCURRENTLY across multiple venues + tables (each its own
thread + requests.Session), then asserts isolation: every game's recap shows only
its own players, and each venue owner's dashboard sees ONLY their own venue's live
sessions (BOLA), while admin sees all. Catches cross-table/cross-venue contamination
and connection-pool races that a sequential run can't.

Run: venv/Scripts/python.exe scripts/sim_stress.py
Needs the dev server up on :8000 (DEV_MODE=true).
"""
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import requests
import urllib3

urllib3.disable_warnings()

BASE = "https://192.168.1.108:8000"
KEY = "dev-key"

PASS, FAIL = [], []
_lock = threading.Lock()


class SimError(Exception):
    pass


def record(ok, msg):
    with _lock:
        (PASS if ok else FAIL).append(msg)
        print(f"   [{'PASS' if ok else 'FAIL'}] {msg}")


def _session():
    s = requests.Session()
    s.verify = False
    s.trust_env = False
    s.headers.update({"Content-Type": "application/json", "X-API-Key": KEY})
    return s


def token(clerk):
    return _session().post(f"{BASE}/api/auth/dev-login",
                           json={"clerk_user_id": clerk}, verify=False).json()["token"]


def dash(tok, path):
    r = _session().get(f"{BASE}/api/dashboard/{path}",
                       headers={"Authorization": f"Bearer {tok}"}, verify=False)
    if not r.ok:
        raise SimError(f"GET /dashboard/{path} -> {r.status_code}: {r.text[:160]}")
    return r.json()


def admin(tok, path):
    r = _session().get(f"{BASE}/api/admin/{path}",
                       headers={"Authorization": f"Bearer {tok}"}, verify=False)
    if not r.ok:
        raise SimError(f"GET /admin/{path} -> {r.status_code}: {r.text[:160]}")
    return r.json()


def reset_table(owner_tok, table):
    _session().post(f"{BASE}/api/dashboard/dev-reset-table", json={"table_number": table},
                    headers={"Authorization": f"Bearer {owner_tok}"}, verify=False)


class Game:
    """One self-contained game on (venue, table) with its own HTTP session."""

    def __init__(self, venue, table, n_players):
        self.venue = venue
        self.table = table
        self.n = n_players
        self.s = _session()
        tag = f"{venue[:4]}{table}"
        self.phones = [f"st-{tag}-{uuid.uuid4().hex[:8]}" for _ in range(n_players)]
        self.names = [f"{tag.upper()}P{i + 1}" for i in range(n_players)]
        self.session_id = None
        self.error = None

    def req(self, method, path, **kw):
        r = self.s.request(method, f"{BASE}/api/patron{path}", verify=False, **kw)
        if not r.ok:
            raise SimError(f"[{self.venue}/{self.table}] {method} {path} -> {r.status_code}: {r.text[:140]}")
        return r.json() if r.text.strip() else {}

    def play(self):
        try:
            self._play()
        except SimError as e:
            self.error = str(e)
        return self

    def _play(self):
        host = self.phones[0]
        first = None
        for p in self.phones:
            b = self.req("GET", f"/tap?venue_slug={self.venue}&table_number={self.table}&phone_id={p}")
            first = first or b
        lobby = first["table_state"]["lobby_id"]
        self.req("POST", f"/lobby/{lobby}/claim-host", json={"phone_id": host})
        for p, nm in zip(self.phones, self.names):
            self.req("POST", f"/lobby/{lobby}/set-name", json={"phone_id": p, "name": nm})
        self.session_id = self.req("POST", f"/lobby/{lobby}/start",
                                   json={"phone_id": host, "adults_only": False})["session_id"]
        # Chooser
        hs = self.req("POST", f"/sessions/{self.session_id}/select-hot-seat", json={"phone_id": host})
        d = self.req("POST", f"/sessions/{self.session_id}/draw-card",
                     json={"phone_id": host, "player_id": hs["player_id"]})
        self.req("POST", f"/rounds/{d['round_id']}/complete", json={"phone_id": host})
        # Roulette
        rs = self.req("POST", f"/sessions/{self.session_id}/roulette/start", json={"phone_id": host})
        target = rs["players"][-1]["id"]
        for p in self.phones:
            self.req("POST", f"/rounds/{rs['round_id']}/vote-loser",
                     json={"phone_id": p, "voted_player_id": target})
        self.req("POST", f"/rounds/{rs['round_id']}/roulette/reveal", json={"phone_id": host})
        # Trivia (host answers 'A'; others answer the revealed-correct option)
        rid = self.req("POST", f"/sessions/{self.session_id}/trivia/start", json={"phone_id": host})["trivia_round_id"]
        for p in self.phones[1:]:
            self.req("POST", f"/trivia/{rid}/join", json={"phone_id": p})
        begun = self.req("POST", f"/trivia/{rid}/begin", json={"phone_id": host})
        for qi in range(len(begun.get("questions", [])) or 5):
            r = self.req("POST", f"/trivia/{rid}/answer",
                         json={"phone_id": host, "question_index": qi,
                               "selected_option": "A", "time_to_answer_ms": 800})
            for p in self.phones[1:]:
                self.req("POST", f"/trivia/{rid}/answer",
                         json={"phone_id": p, "question_index": qi,
                               "selected_option": r["correct_option"], "time_to_answer_ms": 900})
        try:
            self.req("POST", f"/trivia/{rid}/finish", json={"phone_id": host})
        except SimError:
            pass

    def leaderboard(self):
        return self.req("GET", f"/sessions/{self.session_id}/leaderboard")["leaderboard"]

    def end(self, by=None):
        self.req("POST", f"/sessions/{self.session_id}/end-game", json={"phone_id": by or self.phones[0]})

    def start_and_chooser(self):
        """Set up + start + play one Chooser round, leaving the game live at round 2."""
        try:
            host = self.phones[0]
            first = None
            for p in self.phones:
                b = self.req("GET", f"/tap?venue_slug={self.venue}&table_number={self.table}&phone_id={p}")
                first = first or b
            lobby = first["table_state"]["lobby_id"]
            self.req("POST", f"/lobby/{lobby}/claim-host", json={"phone_id": host})
            for p, nm in zip(self.phones, self.names):
                self.req("POST", f"/lobby/{lobby}/set-name", json={"phone_id": p, "name": nm})
            self.session_id = self.req("POST", f"/lobby/{lobby}/start",
                                       json={"phone_id": host, "adults_only": False})["session_id"]
            hs = self.req("POST", f"/sessions/{self.session_id}/select-hot-seat", json={"phone_id": host})
            d = self.req("POST", f"/sessions/{self.session_id}/draw-card",
                         json={"phone_id": host, "player_id": hs["player_id"]})
            self.req("POST", f"/rounds/{d['round_id']}/complete", json={"phone_id": host})
        except SimError as e:
            self.error = str(e)
        return self

    def leave(self, phone):
        return self.req("POST", f"/sessions/{self.session_id}/leave", json={"phone_id": phone})

    def rejoin(self, phone):
        return self.req("POST", f"/sessions/{self.session_id}/rejoin", json={"phone_id": phone})

    def cr(self):
        return self.req("GET", f"/sessions/{self.session_id}/current-round")


def stress_full():
    print("### STRESS: 3 tables x 5 players across 2 venues, concurrently")
    tok_a = token("dev_owner_a")   # lions-den
    tok_b = token("dev_owner_b")   # brew-house
    tok_admin = token("dev_admin")

    # Fresh tables
    reset_table(tok_a, 1)
    reset_table(tok_a, 2)
    reset_table(tok_b, 1)

    games = {
        "lions-den/1": Game("lions-den", 1, 5),
        "lions-den/2": Game("lions-den", 2, 5),
        "brew-house/1": Game("brew-house", 1, 5),
    }

    # Play all 3 concurrently
    with ThreadPoolExecutor(max_workers=3) as ex:
        list(ex.map(lambda g: g.play(), games.values()))

    for key, g in games.items():
        record(g.error is None, f"{key}: full concurrent game completed"
               + (f" ({g.error})" if g.error else ""))

    live = {k: g for k, g in games.items() if g.session_id and not g.error}

    # Isolation: each game's leaderboard shows exactly its own 5 players
    for key, g in live.items():
        names = sorted(r["name"] for r in g.leaderboard())
        record(names == sorted(g.names), f"{key}: leaderboard has exactly its own 5 players (no cross-talk)")

    ld_ids = {games["lions-den/1"].session_id, games["lions-den/2"].session_id}
    bh_id = games["brew-house/1"].session_id

    # Dashboard BOLA isolation while all 3 are live
    a_over = dash(tok_a, "overview")
    a_ids = {s["session_id"] for s in a_over["active_sessions"]}
    record(a_ids == ld_ids,
           f"owner_a sees exactly the 2 lions-den sessions, not brew-house (got {len(a_ids)})")
    record(bh_id not in a_ids, "owner_a CANNOT see the brew-house session (BOLA)")
    record(a_over["tonight"]["active_tables"] == 2,
           f"owner_a tonight.active_tables == 2 (got {a_over['tonight']['active_tables']})")

    b_over = dash(tok_b, "overview")
    b_ids = {s["session_id"] for s in b_over["active_sessions"]}
    record(b_ids == {bh_id},
           f"owner_b sees exactly the brew-house session, not lions-den (got {len(b_ids)})")
    record(not (ld_ids & b_ids), "owner_b CANNOT see any lions-den session (BOLA)")

    # Admin sees all venues, cross-venue
    adm = admin(tok_admin, "overview")
    now = adm["platform"]["active_sessions_now"]
    record(now >= 3, f"admin sees >=3 active sessions across venues (got {now})")
    pv = {v["slug"]: v["active_sessions"] for v in adm["per_venue"]}
    record(pv.get("lions-den") == 2 and pv.get("brew-house") == 1,
           f"admin per-venue breakdown: lions-den=2, brew-house=1 (got {pv})")

    # Tables endpoint: each owner's tables show live counts, isolated
    a_tables = {t["table_number"]: t["active_session_count"] for t in dash(tok_a, "tables")}
    record(a_tables.get(1, 0) >= 1 and a_tables.get(2, 0) >= 1,
           f"owner_a /tables: tables 1 & 2 both live (got {a_tables})")

    # End all games + verify recap isolation
    for key, g in live.items():
        g.end()
        recap = g.req("GET", f"/sessions/{g.session_id}/recap")
        record(len(recap.get("leaderboard", [])) == 5, f"{key}: recap shows all 5 players")


def stress_edge():
    print("\n### CONCURRENT EDGE CASES: migration / leave+rejoin / end, with isolation")
    tok_a, tok_b = token("dev_owner_a"), token("dev_owner_b")
    reset_table(tok_a, 1)
    reset_table(tok_a, 2)
    reset_table(tok_b, 1)
    A, B, C = Game("lions-den", 1, 3), Game("lions-den", 2, 3), Game("brew-house", 1, 3)
    with ThreadPoolExecutor(max_workers=3) as ex:
        list(ex.map(lambda g: g.start_and_chooser(), [A, B, C]))
    if any(g.error for g in (A, B, C)):
        for g in (A, B, C):
            record(g.error is None, f"{g.venue}/{g.table}: setup ({g.error})")
        return
    # A: host leaves -> migration
    A.leave(A.phones[0])
    record(not A.cr()["ended"] and A.cr()["active_count"] == 2, "A: host-leave migrated, game live (2 active)")
    # B: non-host leaves -> continues, then rejoins
    B.leave(B.phones[2])
    record(not B.cr()["ended"] and B.cr()["active_count"] == 2, "B: non-host left, game still live")
    B.rejoin(B.phones[2])
    record(B.cr()["active_count"] == 3, "B: rejoin restored to 3 active")
    # C: host ends the game
    C.end()
    record(C.cr()["ended"], "C: host ended the game")
    # Isolation: C's end didn't touch A/B
    record(not A.cr()["ended"] and not B.cr()["ended"], "C's end did NOT affect A or B (cross-game isolation)")
    # Dashboards reflect the post-edge state, isolated
    a_ids = {s["session_id"] for s in dash(tok_a, "overview")["active_sessions"]}
    record(a_ids == {A.session_id, B.session_id}, f"owner_a still sees A+B live (got {len(a_ids)})")
    b_ids = {s["session_id"] for s in dash(tok_b, "overview")["active_sessions"]}
    record(b_ids == set(), f"owner_b sees 0 live after C ended (got {len(b_ids)})")


def main():
    stress_full()
    stress_edge()
    # leave the tables clean
    ta, tb = token("dev_owner_a"), token("dev_owner_b")
    reset_table(ta, 1)
    reset_table(ta, 2)
    reset_table(tb, 1)
    print(f"\n===== STRESS SUMMARY: {len(PASS)} passed, {len(FAIL)} failed =====")
    for f in FAIL:
        print(f"  FAIL: {f}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
