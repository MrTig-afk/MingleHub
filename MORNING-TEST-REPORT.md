# MingleHub — Overnight Build Report (for Kaushik)

*Two features built autonomously overnight via the plan → code → test → adversarial-review pipeline. **Nothing is merged or deployed.** Both branches are pushed and CI-green, waiting for your review.*

---

## 0. TL;DR

| Build | Branch | Stacked on | Commits | CI | Tests |
|---|---|---|---|---|---|
| **#1 — Venue invites + admin security** | `feature/venue-invites-admin-security` | `feature/theme-engine` (e1a9e73) | 6 | ✅ green | backend **411 passed**; +qrcode.react frontend |
| **#2 — Billing lifecycle** | `feature/billing-lifecycle` | Build #1 | 4 | ✅ green | backend **451 passed, 4 skipped** |

- 🚫 **No merges. No deploys. No pushes to `main`.** Every branch is left for you.
- The 4 skipped tests are Stripe **webhook signature** tests — they skip when `STRIPE_WEBHOOK_SECRET` is unset (true in dev + CI by design). The dunning/auto-reactivate *logic* is tested directly without them.
- One known LOCAL-only flaky test (`test_admin_overview_cross_venue`, wants ≥2 live sessions on the shared dev DB) **passes on CI** (fresh seeded DB). Not a real failure.
- Both `.claude/system-design-learning.md` and `COFOUNDER-REPORT.md` were updated per the project directive.

To review a branch: `git checkout feature/venue-invites-admin-security` (or `feature/billing-lifecycle`).

---

## 1. How to run the dev stack (needed for all manual tests)

1. **Backend** (from repo root), run WITH `--reload` so code changes take effect:
   ```
   DEV_MODE=true ./venv/Scripts/python.exe -m uvicorn api.index:app --host 0.0.0.0 --port 8000 \
     --ssl-keyfile=192.168.1.108-key.pem --ssl-certfile=192.168.1.108.pem --reload
   ```
2. **Apply migrations** first if you switched branches: `DEV_MODE=true PYTHONPATH=. PYTHONIOENCODING=utf-8 ./venv/Scripts/python.exe scripts/migrate.py`
3. **Frontend**: Vite on `https://192.168.1.108:5173` (you usually start this).
4. **Reset game state** between runs: `DEV_MODE=true PYTHONPATH=. ./venv/Scripts/python.exe scripts/dev_reset.py`
5. **Seeded accounts** (DEV_MODE dev-login, `POST /api/auth/dev-login` with `{"clerk_user_id": "..."}`):
   - `dev_admin` → admin (admin dashboard `/admin`)
   - `dev_owner_a` → owner of **fifty-five-bar** (Venue A)
   - `dev_owner_b` → owner of **last-chance** (Venue B)
   - `dev_staff_a` → staff of Venue A (no billing)
   - Patron URL example: `https://192.168.1.108:5173/fifty-five-bar/1`

> Note: NEW owner accounts created for the invite test are simulated via dev-login with a brand-new `clerk_user_id` (e.g. `dev_newvenue_1`) — that gives you a signed-in owner with **no venue**, which is exactly the state the invite flow targets.

---

## 2. BUILD #1 — Venue invites + admin security hardening

### 2.1 What shipped
- **`venue_invites`** table: 43-char high-entropy single-use code (`secrets.token_urlsafe`), 24h expiry, `invited_email` + `signup_email` (both stored, mismatch allowed), geo prefill (`venue_name`/`address`/`lat`/`lng`/`place_id`), status `active|used|revoked|expired`.
- **`admin_audit_log`** table (actor / action / target / IP / JSONB detail / time) — written on invite create, invite revoke, and venue config override.
- **`users.email`** column, populated on Clerk auto-provision.
- **Admin endpoints** (admin-only, rate-limited): `POST/GET /api/admin/invites`, `POST /api/admin/invites/revoke`.
- **Owner endpoint**: `POST /api/dashboard/redeem-invite` (atomic single-use claim), and `/api/dashboard/me` now returns `has_redeemed_invite` + `invite_prefill`.
- **Admin UI "Invite a Venue"**: email + name→address autocomplete (reused from VenueSetup) + client-side QR (`qrcode.react`) + outstanding list with revoke.
- **No-invite gate**: a signed-in owner with no venue and no redeemed invite is locked to a "Contact us" screen, not the setup wizard.
- **Security hardening**: `/docs`+`/redoc`+`/openapi.json` disabled when `DEV_MODE` off; `/admin` excluded from schema + `robots.txt`; `extra="forbid"` + bounds on all admin/invite models; in-memory rate limits (Redis-swap TODO marked); a **CI guard test** that fails the build on `eval`/`exec`/`pickle`/`os.system`/`subprocess`/f-string-SQL.

### 2.2 Manual test — QR invite, end to end
1. Start the stack (§1). Dev-login as **`dev_admin`** and open `https://192.168.1.108:5173/admin`.
2. In the admin nav, click **Invites** → "Invite a Venue".
3. Type an email (e.g. `newbar@example.com`). In the **venue name** field type a real place (e.g. "Fed Square") and pick a suggestion — confirm the **address auto-fills**.
4. Click **Generate**. **Expected:** an invite row appears in the outstanding list and a **QR code** renders. Copy the invite URL (it ends with `?invite=<CODE>`).
5. Open that invite URL but as a **brand-new owner**: dev-login first with a fresh id (`dev_newvenue_1`), then visit `https://192.168.1.108:5173/dashboard?invite=<CODE>`.
   **Expected:** you land on the **setup wizard pre-filled** with the venue name + address from step 3. (Refreshing the page keeps the prefill — it's re-fetched from `/me`.)
6. Finish setup (table count, etc.) → venue is created. **Expected:** redeeming the SAME code again now fails (single-use).
7. Back as `dev_admin` → Invites list: the invite shows **used**.

### 2.3 Manual test — no-invite gate
1. Dev-login as another fresh owner (`dev_newvenue_2`) with no invite. Open `https://192.168.1.108:5173/dashboard`.
   **Expected:** a locked **"Contact us"** screen — NOT the setup wizard.

### 2.4 Manual test — admin security checks
1. **Docs hidden in prod:** with `DEV_MODE=true`, `https://192.168.1.108:8000/docs` is reachable (dev). Restart the backend with `DEV_MODE=false` and reload `/docs` → **Expected: 404** (also `/openapi.json` → 404). (Then put `DEV_MODE=true` back for further testing.)
2. **robots.txt:** open `https://192.168.1.108:5173/robots.txt` → **Expected:** `Disallow: /admin` and `/api/...`.
3. **Auth gates (curl):**
   - `curl -k https://192.168.1.108:8000/api/admin/invites` (no token) → **401**.
   - Dev-login as `dev_owner_a` (a non-admin), call `GET /api/admin/invites` with that token → **403**.
4. **Strict validation:** `POST /api/admin/invites` (as admin) with an extra unknown JSON field → **422**.
5. **Rate limit:** hammer `POST /api/dashboard/redeem-invite` 11× quickly → **Expected:** a `429` appears.
6. **CI code-exec guard:** (optional) add `eval("1")` anywhere under `api/` and run `pytest api/tests/test_code_exec_guard.py` → **Expected: it FAILS**. Remove it.

### 2.5 What's NOT done in Build #1 (intentional)
- **2FA-for-admin**: scoped + flagged as a TODO only (needs a Clerk instance setting + a claim check) — not implemented.
- Rate limiting is **in-memory** (per-instance). Fine for now; needs Redis to be truly distributed in serverless. TODO is in `api/security.py`.
- Email-binding is intentionally **not** enforced (invited_email ≠ signup_email is allowed by design).

---

## 3. BUILD #2 — Billing lifecycle (cancellation / dunning / reactivation)

### 3.1 What shipped
- **Schema:** `venues.cancelled_at / suspended_at / cancellation_reason / suspension_reason`; `invoices.is_final`; `payment_methods` table.
- **New service** `api/services/venue_lifecycle_service.py` holds all transitions.
- **Owner:** `POST /api/dashboard/cancel` (voluntary cancel → status `cancelled`, one **final invoice**, archive payment method stub, idempotent) and `POST /api/dashboard/reactivate` (within **7 days** → active). Both wrapped in a DB transaction so the row lock holds across the status flip + invoice (a review fix — see §5).
- **Admin:** venue status override extended to **suspend / cancel / reactivate** any venue + reason → `admin_audit_log`.
- **Dunning:** an invoice going `failed` starts a **7-day grace**; the nightly sweep (`check_dunning_suspensions`, wired into `scripts/rollup_billing.py`) then sets `suspended` (`suspension_reason='dunning'`). A Stripe webhook `invoice.paid` **auto-reactivates** — but ONLY if the suspension was for dunning (a voluntarily-cancelled or admin-suspended venue is never auto-reactivated by a payment).
- **New-game gate:** new sessions are blocked when a venue isn't `active` (`resolve_table_state` / `start_new_group` / `start_game` + a patron 409). **In-progress sessions are unaffected** — they finish their rounds and end normally.
- **Retain everything:** invoices/audit kept for accounting; `is_final` guards the final invoice from being recomputed by the nightly rollup. `is_test` venues are never real-billed. All transitions idempotent (double-cancel, double-reactivate, replayed webhook).

### 3.2 Manual test — voluntary cancel
1. Dev-login as **`dev_owner_a`** (fifty-five-bar). Open `https://192.168.1.108:5173/dashboard` → **Settings**.
2. Click **Cancel account**, give a reason, confirm.
   **Expected:** status becomes **cancelled**; a status banner appears; Billing still reachable; exactly **one final invoice** is issued (visible on the Billing page / in `invoices` with `is_final=true`).
3. **Patron block:** open `https://192.168.1.108:5173/fifty-five-bar/1` (tap as a patron) → **Expected:** a "venue inactive" screen; you cannot start a NEW game.
4. **In-progress survives:** before cancelling, start a game on a table; THEN cancel the venue; the already-running session should still let you complete rounds and **End Game** normally (this is the key rule — proven by an HTTP-level test too).

### 3.3 Manual test — reactivation window (7 days)
1. Right after the cancel above, in **Settings** click **Reactivate**. **Expected:** status returns to **active**; patrons can play again.
2. **After 7 days:** (to simulate, set `cancelled_at` back >7 days in the DB) → Reactivate → **Expected: denied** ("Reactivation window has expired. Contact support.").

### 3.4 Manual test — dunning → suspend → auto-reactivate
1. Create a `failed` invoice for Venue A dated **>7 days ago** (the tests' `_make_failed_invoice(failed_days_ago=8)` shows the shape; or set an invoice `status='failed'` with an old date).
2. Run the nightly sweep: `DEV_MODE=true PYTHONPATH=. PYTHONIOENCODING=utf-8 ./venv/Scripts/python.exe scripts/rollup_billing.py`.
   **Expected:** Venue A becomes **suspended** with `suspension_reason='dunning'`. (A still-in-grace invoice, <7 days, must NOT suspend.)
3. **Auto-reactivate:** mark that invoice `paid` (in test mode, via the webhook path or directly call `venue_lifecycle_service.auto_reactivate_on_payment`).
   **Expected:** Venue A returns to **active** — but ONLY because it was suspended for dunning. (A voluntarily-cancelled venue would NOT be reactivated by a payment.)

### 3.5 Manual test — admin override
1. Dev-login as **`dev_admin`** → `/admin` → open Venue A's detail.
2. Use the status dropdown to **suspend / cancel / reactivate** with a reason.
   **Expected:** the venue's status changes and an `admin_audit_log` row is written (actor=admin, target=venue, reason). A venue_owner can only act on their OWN venue (BOLA — verified by tests).

### 3.6 What's NOT done in Build #2 (intentional)
- Real Stripe calls are **stubbed** (test mode). The final invoice + payment-method archive record the intent; no money moves.
- The dunning sweep runs **inside the existing nightly job** — no new cron infra was added (per the spec). Scheduling that nightly job in production (Vercel Cron) is still a separate config task.
- The 4 webhook **signature** tests skip without `STRIPE_WEBHOOK_SECRET`; the transition logic they would exercise is covered by direct-call tests.

---

## 4. Decisions & assumptions I made (both builds)

**Build #1**
1. Built `venue_invites` (admin-invites-a-venue) as a NEW table — distinct from gamespec's `admin_invites` (invite-an-admin) and `staff_invites`. The overnight spec's richer field set (geo prefill, dual emails) drove this.
2. The no-invite gate's hard stop is enforced **server-side** (`/me` returns `has_redeemed_invite`); the frontend routes on it. The setup-venue endpoint also carries the invite code as informational-only.
3. `npm install` on this Windows box **prunes Linux-only optional deps** from `package-lock.json`, which broke CI's `npm ci` twice. Fixed deterministically by restoring the known-good lockfile and **hand-adding only `qrcode.react`** (which has no runtime deps), leaving the native-dep cluster intact. Worth knowing for any future frontend dep changes.
4. No-auth tests assert via a **bad token → 401** (the project's `Header(...)` returns 422 on a missing header) — matches existing convention.

**Build #2**
5. "Suspended for non-payment" is distinguished from admin/voluntary suspension via **`suspension_reason='dunning'`** — that's the only state a `paid` webhook auto-reactivates.
6. The new-game gate is applied at session-CREATION points only; round-loop endpoints deliberately don't check status, so live games are never killed mid-play.
7. `payment_methods` was **created** (the table didn't exist yet) rather than altered — documented in the build's changes.
8. The 7-day reactivation window is measured from `cancelled_at` in UTC; the authoritative check is server-side in `reactivate_venue` (the Settings page's "can reactivate" flag is display-only).

**Process**
9. I did NOT merge or deploy anything, per your instruction — every branch is unmerged.
10. The full local suite shows ~43 NFC failures **only when orphan `uvicorn` workers are alive** (they exhaust Neon's pooler) — a known, documented environment artifact. I verified by killing orphans and re-running the exact cluster clean (**123/123 passed**), and CI (isolated Postgres) is green. These are NOT regressions.

---

## 5. Adversarial review findings (applied)

**Build #1** — 1 CRITICAL fixed: a TOCTOU race let two people redeem the same invite; fixed with a single **atomic guarded `UPDATE ... RETURNING`** (single-use enforced by the DB, not read-then-write). Plus: a test that didn't actually assert geo persistence (fixed), prefill lost on refresh (fixed via `/me`), and a `||`→`??` coords bug.

**Build #2** — 1 CRITICAL fixed: the owner cancel/reactivate endpoints used `SELECT ... FOR UPDATE` but **without a surrounding transaction**, so asyncpg autocommit released the lock immediately (double-cancel race + a partial-failure window where a venue is cancelled with no final invoice). Fixed by wrapping both in `async with conn.transaction():`. Plus a deprecated `datetime.utcnow()` fixed. (Two test-hygiene nits were left as-is — harmless, tests pass.)

Both builds: BOLA, auth gating, injection/parameterized-SQL, webhook signature/replay, state-machine integrity, and idempotency were reviewed and found clean.

---

## 6. Garbage collection — theory (you asked; no code written)

You asked how GC is handled in (a) the current platform/runtime and (b) "every venue system." There are **two completely different kinds of "garbage"** here, and only one is automatic.

### 6.1 Runtime / memory GC (automatic — not our concern day-to-day)
- **Backend (CPython on Vercel serverless):** Python reclaims memory by **reference counting** (freed the instant the last reference drops) plus a **generational cyclic collector** for reference cycles. Because the backend runs as **short-lived serverless functions**, each invocation's memory is naturally bounded and the whole instance is recycled by the platform between bursts — so GC pressure is low by construction. We don't tune it.
- **DB connections (asyncpg pool):** the closest thing to a "leak" at runtime is **stale pooled connections** against Neon's pgBouncer pooler. That's exactly the orphan-`uvicorn`-worker / pooler-exhaustion problem we hit during testing — a resource-lifecycle (not memory) issue, solved by recycling the pool / killing orphans, with `statement_cache_size=0` for the pooler.
- **Frontend (V8 in the browser):** automatic mark-sweep GC. The one discipline that matters is **unsubscribing realtime channels and clearing timers on React unmount** — otherwise those are genuine leaks. The codebase already does this in the polling/channel hooks.

**Verdict:** runtime GC needs no work.

### 6.2 Per-venue DATA lifecycle (the real question — and it's manual, by design)
MingleHub does **not** physically delete most rows. It uses **logical expiry / filter-on-read**: a row stops mattering when a query stops selecting it (e.g. `WHERE expires_at > NOW()`), but the row stays. Per venue, the accumulating "garbage" is:

| Data | How it's "expired" today | Reaped? |
|---|---|---|
| `game_sessions` / `rounds` | idle auto-end flips `ended_at`; retained for billing/analytics | **No** (retained on purpose) |
| `table_lobbies` / `_phones` | superseded once a session starts; `dev_reset` truncates in dev | No (ephemeral, small) |
| `venue_invites` (Build #1) | 24h `expires_at`; redeem filters it out | **No** — expired invites accumulate |
| `device_lockouts` / `table_lockouts` | `locked_until` in the past → inert | **No** — accumulate |
| `activation_codes` | daily rotation; old codes filtered by date | **No** — accumulate |
| `admin_audit_log` (Build #1) | append-only | **No, deliberately** (compliance) |
| `invoices` / `invoice_line_items` | `is_final` / `paid` guards | **No, deliberately** (accounting) |
| Supabase realtime channels | TTL'd by Supabase server-side | N/A (no DB rows) |

This is the **correct** default for a billing/audit system — you never want to delete money or audit history, and filter-on-read keeps reads correct. But the **ephemeral** rows (expired invites, expired lockouts, old activation codes, long-closed sessions) grow **unbounded**, which at 10k venues means table + index bloat and slowly degrading scans.

### 6.3 Is optimization required? — plan only (not built)
Not urgent at current scale; **yes** before high scale. The plan, reusing what already exists:

1. **One nightly "reaper" sweep**, added as a step to the **existing** nightly job (`scripts/rollup_billing.py` / `rollup_analytics.py` already run nightly — **no new cron infra**). Idempotent. It would:
   - delete (or archive) `venue_invites` that are `used`/`revoked`/`expired` and older than a retention window (e.g. 30 days);
   - delete `device_lockouts` / `table_lockouts` whose `locked_until` is well in the past;
   - prune `activation_codes` older than N days.
2. **Never** reap `invoices`, `invoice_line_items`, or `admin_audit_log` — instead give them a **retention/cold-storage policy** later (move, don't delete).
3. Add **partial indexes** (e.g. on `venue_invites(code) WHERE status='active'`) so filter-on-read stays fast even before sweeping.
4. At large scale: **time-partition** `game_sessions` / `rounds` by month so old partitions can be dropped cheaply — the database-level analog of generational GC. (System Design Primer: data partitioning / archival.)

Effort: roughly a **half-day**, single nightly function + a couple of indexes. Deliberately deferred — flagged here per your request, no code changed.

### 6.4 Realtime channel lifecycle (not part of the reaper — and that's correct)
The nightly reaper (§6.3) deliberately does **not** touch Supabase Realtime channels, because they aren't database rows — there's nothing for a SQL sweep to delete. Their cleanup runs on two separate paths, both **verified in the code**:

1. **Client-side teardown** — `frontend/src/hooks/useSessionChannel.js` (lines 69–75) returns an effect cleanup that calls `supabase.removeChannel(channel)` on unmount / dependency change; `useMultiTouch.js` follows the same pattern. A phone leaving a table/session unsubscribes itself.
2. **Server-side TTL** — Supabase reaps a Broadcast channel once it's empty, and the signed HS256 channel tokens self-expire via their `exp` claim.

**Nothing accumulates in our database:** broadcast is ephemeral (FastAPI publishes, phones subscribe) — messages are not persisted, so there are no `realtime.messages` rows to sweep. *(If message persistence were ever enabled on `realtime.messages`, that table WOULD need a reaper entry — it currently is not.)*

**One honest residual — flagged enhancement (not built):** there is no **server-initiated** channel teardown when a session idle-ends. Cleanup relies on clients dropping off, so when a patron simply locks their phone (no clean React unmount), `removeChannel` never fires and that subscription lingers until **Supabase's websocket heartbeat timeout** reaps it. That's Supabase-managed, not a leak in our DB. If we want belt-and-suspenders, the fix is an **event, not a reaper row**: on server-side session-end, publish a final `session_closed` broadcast so still-connected clients proactively unsubscribe instead of waiting out the heartbeat. Confirmed not currently implemented (no teardown publish in `api/services/realtime_service.py`). Small enhancement, deferred.

---

## 7. Bottom line
Two features, both adversarially reviewed, both CI-green, **both unmerged**. Review each branch independently; when you're happy, you merge (stack order: invites first, then billing-lifecycle). The garbage-collection answer is in §6 — runtime GC is automatic and fine; the per-venue data lifecycle is intentionally retain-by-default, with a clear (unbuilt) nightly-reaper plan for scale.
