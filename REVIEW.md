# Review branch: `review/bola-audit`

Off `feature/bola-hardening` (`61756a8`). Work done autonomously for your review —
**nothing here is merged.** Three things you asked for: adversarial review, automated
test coverage, and a headless game simulator. Plus two clear-cut fixes the review
surfaced.

---

## 1. Adversarial review of today's 8 changes (`946a809..61756a8`)

**Verdict: no Critical or High defects.** BOLA/venue isolation holds, the Trivia
shuffle is provably correct, the drop-to-1 auto-end race is well-guarded.

Findings, by severity:

| ID | Sev | Where | Issue | Status |
|----|-----|-------|-------|--------|
| **M1** | Medium | `DashboardHome.jsx` | `Promise.all([overview, tables])` was all-or-nothing: a **tables**-call failure on first load blanked the whole Home (incl. live sessions overview returned fine). | **FIXED** — overview drives the page; tables is now non-fatal (`.catch(() => [])`). |
| **L1** | Low | `RoundOrigin.jsx handleEndGame` | Manual "End game" showed a raw `session_already_ended` 409 if the session was idle-ended first. | **FIXED** — treats already-ended as success → goes to recap (mirrors the auto-end path). |
| L2 | Low | `lobby_service.py` | `force_new` skips the post-end recap-lock but **not** the lazy-expired recap in `_check_phone_session_resume`. A "New game" tap that coincides with the host's own session crossing idle-expiry still lands on recap (self-corrects on the next tap). | **Left for you** — UX edge, not security. |
| L3 | Low | `RoundOrigin.jsx` (auto-end) | Extremely narrow migration-window race: the leaving phone's 60s timer could fire a doomed `endGame` (403) then show recap locally while the game continues for everyone else. Only affects the phone that's leaving anyway. | **Left for you** — optional guard. |
| N1/N2 | Nit | `AnswerTiles.jsx` | `shuffledOrder` recomputed each render (cheap); empty `phoneId` would collapse order (never happens — both call sites pass a real id). | **Left** — cosmetic. |

Areas actively attacked and found **correct**: BOLA on `new_game`/`table_id`, can't
yank other players out of recap, Trivia shuffle scoring, drop-to-1 double-fire /
flap / migration interaction, host-gate `roundStarted`, dashboard active/idle split.

Full report is in the git history of this branch / the session transcript.

---

## 2. Headless game simulator — `scripts/sim_game.py`

Drives the **patron HTTP API** (same endpoints real phones hit) to play full games
with **no devices** and **no cost** (local server + your Neon DB only — no paid APIs,
no AI, no Stripe).

Run it (dev server must be up on `:8000`, DEV_MODE=true):
```
venv/Scripts/python.exe scripts/sim_game.py all          # all scenarios
venv/Scripts/python.exe scripts/sim_game.py happy_path   # one scenario
venv/Scripts/python.exe scripts/sim_game.py probe         # print API response shapes
```

Scenarios (all self-clean the table afterward):
- `happy_path` — 2 phones, Chooser → Roulette → Trivia → End → Recap; asserts trivia scoring
- `host_migration` — host leaves mid-game → migrates to the other player, game lives
- `last_leaver` — non-host leaves (continues) → last player leaves → game ends
- `new_game_bypass` — ended game: plain tap → recap, `new_game` tap → fresh lobby

**Result: 17/17 assertions pass.** This is your regression net for the game loop
without needing your phones — re-run it any time after backend changes.

---

## 3. Automated tests — `api/tests/test_new_game.py`

New pytest coverage for today's backend changes (4 tests, all pass):
- `test_plain_retap_after_end_shows_recap` — control: recap-lock still works
- `test_new_game_tap_bypasses_recap_lock` — `new_game=1` → fresh lobby
- `test_new_game_does_not_disrupt_active_session` — **security**: `new_game` on a LIVE
  session still resumes (can't hijack/abandon a game in progress)
- `test_overview_active_sessions_include_table_id` — Home click-through data

**Full suite regression run: 308 passed, 0 failed** (14m37s, uvicorn stopped so Neon
wasn't contended). No regressions from today's changes.

Note: the host-gate, drop-to-1 countdown, and trivia shuffle are **frontend-timer/UI**
behaviors — covered by the simulator at the API level and by manual on-device testing,
not by these pytest cases. A Playwright pass for those is the natural next add (not done
here — would need the dev Playwright setup wired up).

---

## How to use this branch
- **Review** the diff vs `feature/bola-hardening`: `git diff feature/bola-hardening..review/bola-audit`
- The only product-code changes are the two fixes (M1, L1). Everything else is new
  test/sim tooling (`scripts/sim_game.py`, `api/tests/test_new_game.py`, this file).
- If you're happy, I can either merge `review/bola-audit` → `feature/bola-hardening`,
  or cherry-pick just the M1/L1 fixes. Your call.
