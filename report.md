# MingleHub — Overnight Test & Build Report

**Branch:** `feature/onboarding` · **Date:** 2026-06-22 (overnight) · **Not merged, not deployed** (your call).

Your morning job is small: **(1)** point one phone's NFC/URL at the second venue (one TODO, below), and **(2)** walk the manual test checklist. Everything else is done, tested, and committed.

> **Cost/limits honoured (your #1 rule):** no Mapbox (free Photon/OSM only), **no new paid Notion pages** (this report lives in the repo), Photon used lightly + now cached, modest DB use (no load/stress sims), and only **free Clerk dev-instance** accounts. Nothing can trigger a paid tier.

---

## 1. What changed tonight

| Area | Change |
|---|---|
| **Venues renamed** | Placeholder `Lion's Den` / `Brew House` → **Fifty Five Bar** (`fifty-five-bar`, 55 Elizabeth St) + **The Last Chance** (`the-last-chance`, 238 Victoria St) across seed, fixtures, tests, sims (96 replacements, 15 files). Seed now stores real **address + coords**. The Last Chance got a **2nd table**. |
| **Bug fixed** | `GET /dashboard/venue` stored the address but never returned it (`SELECT` omitted the new columns) → now returns `address/lat/lng`. |
| **Hardening** (from adversarial review) | **HIGH** slug-collision race → collision-safe insert via savepoints (no 500 on concurrent same-name); **MED** double-submit could create an orphan venue → `FOR UPDATE` re-check on the owner row; **MED** geo endpoint → 30→15/min + bounded query cache so repeated keystrokes don't re-hit Photon. |
| **Address autocomplete** | AU location-bias (so "55 Elizabeth St" returns Melbourne, not Connecticut); **name → address** auto-fill when the venue is in OpenStreetMap. |
| **Post-setup prompt** | After creating a venue the wizard now shows a success screen guiding the owner to **pair NFC tags** (or go to the dashboard). |
| **Cleanup** | Removed a duplicated migration block; fixed a stale "Mapbox" comment → Photon. |

---

## 2. What I tested — and the results

All run sequentially against the live dev stack (parallel agents would corrupt the shared Neon DB).

| Test | Result |
|---|---|
| **Onboarding e2e** (both venues): geo autocomplete → `setup-venue` → venue + tables + address | ✅ Pass (this is how the `/venue` bug was caught + fixed) |
| **Dashboard role-gating matrix** — admin / owner_a / owner_b / staff across 14 endpoints | ✅ **56/56 correct** |
| **Cross-venue isolation (BOLA)** — owner_a vs owner_b vs staff | ✅ Correct (each sees only their own venue) |
| **Games** — full `happy_path` game on **2 tables × 2 venues** | ✅ **40/40 assertions** |
| **Slug dedupe** — two same-named venues | ✅ Distinct slugs (`collision-tavern`, `collision-tavern-2`) |
| **Adversarial code review** (subagent) | Found 1 HIGH + 2 MED → **all fixed**; BOLA/SQL-injection/validation/atomicity confirmed clean |
| **Full backend suite** | _see "CI status" at the bottom — updated after the run finishes_ |

### Role-gating matrix (the evidence)
```
endpoint                            admin   owner_a  owner_b  staff_a
dashboard/me                         200      200      200      200
dashboard/venue                      403      200      200      200
dashboard/tables                     403      200      200      200
dashboard/overview                   403      200      200      200
dashboard/insights                   403      200      200      200
dashboard/tags                       403      200      200      200
dashboard/settings                   403      200      200      403   <- owner-only
dashboard/billing                    403      200      200      403   <- owner-only
dashboard/pair-tag                   403      200      409      403   <- owner-only
dashboard/setup-venue                403      409*     409*     403   (*already has a venue)
dashboard/geo/autocomplete           200      200      200      200
admin/ping|overview|venues           200      403      403      403   <- admin-only
```
Admin can't touch venue dashboards; owners/staff can't touch admin; staff is correctly blocked from owner-only screens. **All correct.**

---

## 3. ⚠️ YOUR ONE TODO — make the two phones two different *venues*

Right now both tap URLs are the same venue, different tables. Change **one** so the phones represent **two venues**:

- **Phone A (NFC 1):** `https://192.168.1.108:5174/fifty-five-bar/1`
- **Phone B (NFC 2):** ~~`…/fifty-five-bar/2`~~ → **change to** `https://192.168.1.108:5174/the-last-chance/1`

(Write that URL to the second NFC tag, or just open it directly on phone B.) Both venues + tables already exist and are game-tested.

---

## 4. Accounts to sign in with (Clerk — the real login)

Sign in at the URLs below with these. **Password for all the `+` accounts: `VenueTest1!`** (pre-verified, so **no email code needed**).

| Role | Email | Where | Sees |
|---|---|---|---|
| **Admin** | `kaushiknaru2002@gmail.com` | `/admin` | Both venues, platform stats |
| **Owner — Fifty Five Bar** | `kaushiknaru2002+fiftyfivebar@gmail.com` | `/dashboard` | Fifty Five Bar only |
| **Owner — The Last Chance** | `kaushiknaru2002+thelastchance@gmail.com` | `/dashboard` | The Last Chance only |
| **Staff — Fifty Five Bar** | `kaushiknaru2002+staff@gmail.com` | `/dashboard` | Fifty Five Bar, **no** settings/billing |
| **Onboarding test** | _a brand-new email you pick_ | `/dashboard` | The **setup wizard** (your stress test) |

---

## 5. Manual test checklist (your walk-through)

### A. Admin dashboard — `https://192.168.1.108:5174/admin`
1. Sign in as **admin**. You should land on the admin overview.
2. ✅ "venues active now" should read **0** (clean state) until games start.
3. ✅ **Per-Venue Breakdown** lists **Fifty Five Bar** + **The Last Chance** (both clickable → venue detail).
4. Click a venue → config, override form, audit history.

### B. Owner dashboard — `https://192.168.1.108:5174/dashboard`
1. Sign in as **Owner — Fifty Five Bar**. You should see the **Fifty Five Bar** dashboard (address shown: 55 Elizabeth St).
2. ✅ **Home** + **Tables** tabs show the venue's tables (clickable → table detail).
3. ✅ **Settings** + **Billing** tabs are visible and load.
4. **Sign out**, sign in as **Owner — The Last Chance** → you should see **only** The Last Chance (never Fifty Five Bar). That's the isolation check.

### C. Staff dashboard — `https://192.168.1.108:5174/dashboard`
1. Sign in as **Staff**. You see Fifty Five Bar's tables/insights…
2. ✅ …but **Settings / Billing / Pair-Tags are hidden** (staff is owner-gated out). If you force the URL `/dashboard/settings`, the API returns 403.

### D. Onboarding (your stress test) — new signup
1. **Incognito window** → `/dashboard` → sign up with a **fresh email**.
2. You should be auto-routed to **"Set up your venue"** (not an empty dashboard).
3. Type a **venue name** — if it's a known pub it may auto-fill the address; otherwise type the **address** ("238 Vic…") and pick a Melbourne suggestion. (Typing just a house number like "238" alone won't suggest — that's normal for any geocoder; include the street.)
4. Set tables + 18+ toggle → **Create my venue** → a **success screen** appears ("🎉 … is set up") with **Pair NFC tags** / **Go to dashboard**. Either lands you in the new venue's dashboard. (Any 1–50 tables works.)

### E. Live games via the two phones
1. Phone A → `…/fifty-five-bar/1`, Phone B → `…/the-last-chance/1` (your TODO).
2. Add players, start a game on each. Play a round or two.
3. While playing, open the **admin** dashboard on a third screen → "sessions active now" should climb, and **each venue's count should reflect only its own game** (isolation, live).
4. End the games → recap → the dashboards settle back to 0 active.

### F. Edge cases worth a poke
- Owner tries to re-run setup (already has a venue) → blocked (409).
- Staff tries `/dashboard/billing` → 403.
- Two phones at the **same** table → second gets the lobby/switch behaviour.
- Tap a different table while in a live game → switch-confirm gate.

---

## 6. Screenshots
`F:\Dev\Projects\Lala\MingleHub\.pipeline\pwtest\shots\` — Clerk sign-in screens captured during automation.
**Honest note:** I could **not** auto-drive the Clerk *sign-in UI* headlessly — Clerk's bot-protection silently blocks automated browsers (stuck on the password step with no error). The real sign-in works fine (you saw it as admin earlier). So the wizard itself is verified via the **API e2e test** (section 2), not a UI screenshot. Your section-D manual run is the UI confirmation.

---

## 7. Not done / needs your decision
- **Merge to `main` + deploy** — yours to call.
- **Per-venue timezone** (hardcoded Melbourne) and **real Stripe billing** — separate future work.
- **Rotate the Clerk secret key** before production (it was pasted in chat).

---

## 8. CI status & final state
- **CI: ✅ GREEN** — commit `8ce0e73` on `feature/onboarding`, backend + frontend both pass.
- **Local full suite:** **317 passed, 0 failed.**
- **State:** clean — Fifty Five Bar + The Last Chance (2 tables each, real addresses), the 4 linked Clerk accounts above, **0 active games**.
- **Servers were up when I finished** (`:8000` backend, `:5174` Vite). If either is down when you wake, restart from the repo root (you can use `! <cmd>` in this session):
  - **Vite:** `cd frontend && npx vite --host 0.0.0.0 --port 5174`
  - **Backend:** `set -a; source api/.env; set +a; DEV_MODE=true ./venv/Scripts/python.exe -m uvicorn api.index:app --host 0.0.0.0 --port 8000 --reload --reload-dir api --ssl-keyfile 192.168.1.108-key.pem --ssl-certfile 192.168.1.108.pem`
- **Reset the env** anytime (clears games, keeps venues/accounts): `DEV_MODE=true PYTHONPATH=. ./venv/Scripts/python.exe scripts/dev_reset.py`
