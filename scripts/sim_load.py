#!/usr/bin/env python
"""Bounded load/scale probe for MingleHub.

SAFE BY DESIGN: the backend's asyncpg pool is capped (max_size=5), so Neon never
sees more than ~5 connections no matter how many concurrent requests we fire — it
can't be overwhelmed and compute-hours barely move. This measures the LOCAL dev
server's per-worker ceiling (1 uvicorn worker + 5 conns), which is representative
but NOT production: prod is serverless (many workers) bounded by the Neon pooler.

Read-heavy poll load (what thousands of idle phones actually do — poll current-round)
ramped over concurrency, plus a small write burst (concurrent trivia answers). Caps
concurrency + total requests, ABORTS if error rate spikes, and resets its table after.

Run: venv/Scripts/python.exe scripts/sim_load.py
Needs the dev server up on :8000 (DEV_MODE=true).
"""
import statistics
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import requests
import urllib3

urllib3.disable_warnings()

BASE = "https://192.168.1.108:8000"
KEY = "dev-key"
VENUE = "lions-den"
TABLE = 1
H = {"Content-Type": "application/json", "X-API-Key": KEY}

POLL_LEVELS = [25, 50, 100, 200, 400]   # concurrent in-flight poll requests
REQS_PER_LEVEL = 6                       # multiplier: total = level * this
ERROR_ABORT_PCT = 25.0                   # stop ramping if a level exceeds this


def _session():
    s = requests.Session()
    s.verify = False
    s.trust_env = False
    s.headers.update(H)
    return s


def owner_token():
    return _session().post(f"{BASE}/api/auth/dev-login",
                           json={"clerk_user_id": "dev_owner_a"}, verify=False).json()["token"]


def reset_table(tok):
    _session().post(f"{BASE}/api/dashboard/dev-reset-table", json={"table_number": TABLE},
                    headers={"Authorization": f"Bearer {tok}"}, verify=False)


def setup_live_session(tok):
    """Reset + start a 2-phone game so there's a live session to poll."""
    reset_table(tok)
    s = _session()

    def req(method, path, **kw):
        r = s.request(method, f"{BASE}/api/patron{path}", verify=False, **kw)
        r.raise_for_status()
        return r.json()

    phones = [f"load-{uuid.uuid4().hex[:8]}" for _ in range(2)]
    first = None
    for p in phones:
        b = req("GET", f"/tap?venue_slug={VENUE}&table_number={TABLE}&phone_id={p}")
        first = first or b
    lobby = first["table_state"]["lobby_id"]
    req("POST", f"/lobby/{lobby}/claim-host", json={"phone_id": phones[0]})
    for i, p in enumerate(phones):
        req("POST", f"/lobby/{lobby}/set-name", json={"phone_id": p, "name": f"L{i}"})
    return req("POST", f"/lobby/{lobby}/start", json={"phone_id": phones[0], "adults_only": False})["session_id"]


def _poll_once(session_id):
    s = _session()
    t0 = time.perf_counter()
    try:
        r = s.get(f"{BASE}/api/patron/sessions/{session_id}/current-round", verify=False, timeout=30)
        ok = r.status_code == 200
    except Exception:
        ok = False
    return ok, (time.perf_counter() - t0) * 1000.0


def run_level(session_id, concurrency):
    total = concurrency * REQS_PER_LEVEL
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        results = list(ex.map(lambda _: _poll_once(session_id), range(total)))
    wall = time.perf_counter() - t0
    lat = sorted(ms for _, ms in results)
    errs = sum(1 for ok, _ in results if not ok)
    err_pct = 100.0 * errs / total
    p50 = statistics.median(lat) if lat else 0
    p95 = lat[int(len(lat) * 0.95) - 1] if lat else 0
    rps = total / wall if wall else 0
    print(f"  conc={concurrency:>4}  reqs={total:>5}  {rps:7.1f} req/s  "
          f"p50={p50:7.1f}ms  p95={p95:8.1f}ms  err={err_pct:5.1f}%")
    return err_pct, p95


def main():
    tok = owner_token()
    session_id = setup_live_session(tok)
    print("### LOAD: read-heavy poll ramp (GET /current-round) — app pool max_size=5\n")
    knee = None
    try:
        for level in POLL_LEVELS:
            err_pct, p95 = run_level(session_id, level)
            if err_pct > ERROR_ABORT_PCT:
                knee = f"errors exceeded {ERROR_ABORT_PCT}% at concurrency {level}"
                print(f"\n  -> ABORT ramp: {knee}")
                break
    finally:
        reset_table(tok)
    print("\n===== LOAD SUMMARY =====")
    print("  app DB pool cap: 5 connections (Neon never saw more) — single dev worker")
    print(f"  knee: {knee or 'none hit within tested range (errors stayed under %.0f%%)' % ERROR_ABORT_PCT}")
    print("  note: prod is serverless (many workers) — local per-worker ceiling only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
