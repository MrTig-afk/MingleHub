# MingleHub — Usage Billing: Build & Test Report

**Branch:** `feature/usage-billing` (stacked on `feature/onboarding`) · **Built 2026-06-22** · **Not merged, not deployed** (your call).

Your morning: **(1)** sign in and look at the Billing tab, **(2)** decide on merge + the nightly cron. Everything below is built, tested, reviewed, and green in CI.

> **Cost/limits honoured:** no Stripe wiring (no real charges possible), both test venues set `is_test=TRUE` (rollup + real billing skip them), no destructive DB ops, free CI only.

---

## 1. TL;DR

A **duration-based billing system** is built end-to-end:

- **1 block = 15 min of active play**, priced at `billing_unit` (default **$3**).
- Span is measured **start → last activity** (the dead idle tail before an auto-end is excluded, so a walk-away doesn't inflate the bill).
- A block needs **≥1 resolved round** (a lobby-only sitting bills $0).
- **Metered**: 29 min span = 1 block billed; the extra 14 min is recorded but unbilled.
- **Cap $30/table/night** (= 10 blocks), applied per table per night.
- **Nightly rollup** → `invoices` (per venue/month) + `invoice_line_items` (per table/day), idempotent, `is_test` excluded, paid invoices never recomputed.
- **Play-time analytics** surfaced for you to revise the model later: actual play vs billed span vs unbilled remainder.

---

## 2. Status

| | |
|---|---|
| **CI** | ✅ Green — backend **328 passed**, frontend lint+build (run 27929391774) |
| **Billing tests** | **39 passed** (block math, ≥1-round gate, idle-exclusion, caps by day-of-week, multi-night/multi-table rollup, idempotency, is_test exclusion, paid-skip, BOLA, zero-unit safety, idle-tail exclusion) |
| **Adversarial review** | ✅ SHIP (2 hardening nits applied) |
| **Blast-radius audit** | ✅ Clean — no missed surfaces, no inconsistent admin/insights dollar figures, migration order fine |
| **Merged to main** | ❌ No (your standing rule + billing is approval-gated) |
| **`is_test`** | ✅ Both venues TRUE in the live DB |

---

## 3. Manual test — see it for yourself

Sign in at **`https://192.168.1.108:5174/dashboard`** as the **Fifty Five Bar owner**:
`kaushiknaru2002+fiftyfivebar@gmail.com` · password `VenueTest1!` → open the **Billing** tab.

You'll see: the **Billing Model** card, **Tonight**, a **Play Time** card, **Month to Date**, **Payment** (Stripe: not connected), and **Invoice History**.

### See it populated (recommended)
A *short* manual game won't bill a block — you'd need 15+ min of active play, by design — so to see a realistic populated view, inject a demo session:

```bash
DEV_MODE=true PYTHONPATH=. python .pipeline/demo_billing.py add
```
Refresh the Billing tab → **Tonight: 1 block, $3.00**, **Play Time: actual 22 min / billed span 25 min / unbilled remainder 10 min**. When done:
```bash
DEV_MODE=true PYTHONPATH=. python .pipeline/demo_billing.py clear
```
(The demo is tagged so `clear` only removes demo rows, never real game data.)

### See it from a real game (optional)
Play a game on two phones/tabs, play a few rounds, **End Game**. The Play Time card populates with the real minutes immediately; **blocks bill only once active span ≥ 15 min** (so a 5-min test shows minutes but $0 — that's correct).

### Staff cannot see billing
Sign in as staff (`kaushiknaru2002+staff@gmail.com`) → the Billing tab is hidden, and `/api/dashboard/billing` returns 403. (Owner-only, enforced server-side.)

---

## 4. What I did while you were away

- **+4 edge-case tests** (idle-tail exclusion at finalize; multi-night/multi-table rollup → correct per-(table,night) line items; weekday-vs-weekend cap selection by day-of-week; zero-`billing_unit` endpoint safety). All green.
- **Blast-radius audit** (subagent) of the round-counter change and the billing rollout — see §5.
- **Demo helper** (`.pipeline/demo_billing.py`, gitignored) so the Billing tab isn't empty when you test.
- Pushed; CI re-validated green.

---

## 5. Audit findings (clean)

- **Round-fix is safe.** The trivia `abandoned_at_gather` change correctly stops counting that non-played round toward `total_rounds` while still advancing cadence. Chooser/Roulette always count (they always resolve) — confirmed.
- **One intended effect (not a bug):** a session whose *only* round was an abandoned trivia gather now has `total_rounds=0`, so it bills **$0** — exactly the ≥1-round "nobody really played" rule.
- **Minor, intended:** insights/overview/admin "rounds" counts and the owner table-detail "N rounds" chip read slightly lower after an abandoned gather (the new "played rounds" semantic). Not money figures.
- **Patron recap unaffected** — it doesn't read `total_rounds`.
- **No missed old-model billing surface** anywhere; admin shows no cross-venue revenue at all; the only dollar path is the new block model.

---

## 6. Open decisions (yours)

1. **Merge to main** — when ready: onboarding → main, then billing → main (billing is stacked on onboarding).
2. **Vercel Cron** for the nightly rollup (`scripts/rollup_billing.py`) — deploy-config, needs your approval; not wired.
3. **Holiday cap** — only weekday/weekend caps are wired (by day-of-week); `nightly_cap_holiday` exists in schema but holiday detection isn't implemented.
4. **`is_test` is live-DB only, not in the seed** — so CI/tests keep those venues non-test (admin venue counts depend on it). A full reseed (`seed_platform.py`) reverts it; making it permanent needs test updates.
5. **Stripe** — price-per-block is a business number you set later; no charging logic yet.

---

## 7. Commits & CI

- `7e22a2a` — backend (service + finalize hooks + round-fix + endpoint + migration)
- `101dad1` — dashboard UI (block model + play-time analytics)
- `d4dd8d3` — hardening (null-cap guard, per-invoice atomic rollup)
- `+` overnight commit — edge tests + this report
- CI: https://github.com/MrTig-afk/MingleHub/actions (branch `feature/usage-billing`)
- PR (if you want one, don't merge): https://github.com/MrTig-afk/MingleHub/pull/new/feature/usage-billing
