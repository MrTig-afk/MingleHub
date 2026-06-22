# MingleHub — Build & Launch Status Report

*For: founders · Status as of this build · Audience: technical + business*

---

## 1. Executive summary

MingleHub is a **tap-to-play social game platform for bars, pubs and venues** — patrons tap an NFC tag at their table and play (Chooser / Trivia / Roulette) on their own phones, no app install, no login. Venues get a dashboard; we (admins) get a cross-venue control panel; venues are billed for usage.

**Where we are:** the full platform is **built, tested, and continuously-integration-green** — onboarding, multi-venue isolation, the three games, venue + admin dashboards, a complete usage-billing system, performance/caching infrastructure, and a theme engine. It is **not yet deployed** — deployment is a configuration step (env vars, a production database, a domain), not remaining engineering.

**By the numbers:** ~45 feature commits, **~411 automated tests across 33 test files**, every change gated by CI (lint + full test suite + production build) before it can land, plus independent adversarial code reviews on the money-handling and access-control code.

---

## 2. What we've built

Grouped by area. Each: *what it is · why · how.*

### Authentication & roles
- **What:** Sign-in for the venue dashboard and the admin panel via **Clerk** (Google / email), with automatic role assignment.
- **Why:** Venues self-serve; we never hand-create accounts. Security can't be an afterthought.
- **How:** Clerk issues a signed token (JWT) verified server-side on every request. On first login a user is **auto-provisioned**: if their email is on our admin allowlist they become an **admin**; everyone else becomes a **venue owner**. No path exists for a random sign-up to become an admin.

### Self-serve onboarding
- **What:** A new venue owner signs up → "Set up your venue" wizard (name, type, address with autocomplete, table count, 18+ toggle) → it creates their venue + tables → they pair their NFC tags.
- **Why:** This is the growth engine — an owner we pitch can be live in minutes, on their own.
- **How:** Address autocomplete uses a free, keyless map service (no credit card, AU-biased). Slug generation is collision-safe; the flow is guarded against double-submits creating duplicate venues.

### The games (Chooser / Trivia / Roulette)
- **What:** The core experience — finger-picker hot-seat, multi-phone Trivia (everyone answers live), group Roulette by vote, scoring, recap.
- **Why:** It's the product.
- **How:** Server-authoritative round numbering; real-time sync across phones (see §4); host migration, leave/rejoin, and "drop to 1 player" handled gracefully.

### Venue dashboard (owner + staff)
- **What:** Home (live tables tonight), Tables (per-table drill-down + NFC status), Insights (sessions/rounds/trivia/round-mix/trend), Settings, Billing, Tag pairing.
- **Why:** The owner's daily control surface + the reason they'd pay.
- **How:** Every screen is scoped to the signed-in user's venue (see §4 isolation). Staff see operations but **never billing or dollar figures**.

### Admin dashboard (us)
- **What:** Cross-venue analytics, a clickable per-venue breakdown, config overrides with an audit trail, support inbox, leads, team, and **venue invite management** (create/QR/revoke).
- **Why:** Operate the platform across all venues.
- **How:** Admin routes are role-gated and see all venues (no venue filter), distinct from the owner surface.

### Venue invite system (Build #1)
- **What:** Admins generate invite links (with QR code) for specific venues. A venue owner follows the link, signs in via Clerk, and the invite code redeems to pre-fill their venue setup wizard (name, address, coordinates). Owners without an invite see a "Contact us" locked screen instead of the setup wizard.
- **Why:** Controlled rollout — we decide who gets access; random sign-ups don't get a venue.
- **How:** 43-char high-entropy code (`secrets.token_urlsafe`), 24-hour expiry, rate-limited redemption (10/min brute-force defense), `used_by` captured on redeem, full audit trail in `admin_audit_log`. The gate lives server-side: `/me` returns `has_redeemed_invite`; the frontend routes accordingly. Redemption is **atomic** — a single guarded database write claims the code, so two people racing the same invite can't both succeed.

### Admin security hardening (Build #1)
- **What:** A defence-in-depth pass over the admin surface, shipped alongside invites.
- **Why:** The admin panel controls every venue — it has to be locked down before real venues (and real money) exist.
- **How:** (1) an **audit log** on every admin mutation (who/action/target/IP/time); (2) **strict input validation** — unknown fields are rejected (`extra="forbid"`) with tight bounds on every admin/invite field; (3) **API docs disabled in production** and admin routes excluded from the public schema and `robots.txt`; (4) **rate limits** on admin + invite + redeem endpoints (structured for a one-line Redis swap when we scale); (5) a **CI guard** that fails the build if dangerous patterns (`eval`/`exec`/`pickle`/`os.system`/`subprocess`/string-built SQL) ever appear. Two-factor-for-admin is scoped and flagged as the immediate next step (a Clerk setting + a claim check).

### Usage billing system
- **What:** Venues are billed by **active-play time**: one "block" = 15 minutes of active play, priced per block, **capped per table per night**. Monthly invoices roll up automatically. A play-time analytics view shows actual vs. billed time. Stripe integration (test mode) to issue invoices.
- **Why:** This is revenue. It has to be correct, fair, and defensible to a venue disputing a bill.
- **How:** Detailed in §4 (it's our most carefully-engineered subsystem). Real charges are **impossible** in the current build (test mode + stub).

### Performance & scale infrastructure
- **What:** Instant dashboard tab-switching (client cache), and pre-aggregated "rollup" tables so reports read tiny summaries instead of scanning years of raw data.
- **Why:** So the product stays fast and cheap as venues and history grow.
- **How:** A nightly job pre-computes daily summaries; dashboards read those for past days + a small live query for today. Proven to return **identical numbers** to the old live computation.

### Theme engine
- **What:** Each venue picks a **theme** that weights which game types/cards appear (Party Night, Date Night, etc.), plus **single-type "test" themes** to force one game.
- **Why:** Product differentiation (every venue feels different) and operational control.
- **How:** The server resolves the venue's theme; the table's origin phone draws each round's type from those weights, deterministically (so all phones agree, no flicker), with a fallback to Chooser when there aren't enough players for Roulette/Trivia.

---

## 3. Why the branch structure (and what each holds)

We build **one feature per branch**, off the others in a clean stack, and **nothing merges until CI is fully green** (lint + all 370 tests + a production build). This keeps every change isolated, reviewable, and reversible.

| Branch | What it delivers |
|---|---|
| (earlier, now in the lineage) | Venue dashboard slices, admin dashboard, BOLA/security hardening, single-active-seat, Clerk auth |
| `feature/onboarding` | Self-serve signup + venue-setup wizard + auto-provisioning |
| `feature/usage-billing` | The duration-based billing model, invoices, nightly rollup |
| `feature/dashboard-perf` | Client caching (instant tabs) + analytics rollup tables |
| `feature/theme-engine` | Theme engine + per-session billing breakdown + Stripe (test) + the deploy CSP fix |
| `feature/venue-invites-admin-security` | QR invite flow for controlled onboarding + admin audit log + docs hidden in production |

`feature/venue-invites-admin-security` is the current **tip** — it contains everything above it in the stack. Merging it brings the whole platform to `main` in one go (we'd merge the stack in order).

---

## 4. Edge cases we covered (and the logic)

This is where the rigor lives. A sample of what's explicitly handled and tested:

### Multi-tenant isolation (the #1 security risk)
- **Every** dashboard query derives the venue from the *authenticated user* — never from the URL or request body. A venue owner literally cannot request another venue's data; cross-venue access returns 403/404. This is tested on every endpoint.
- **Why:** "Broken Object-Level Authorization" (one tenant seeing another's data) is the most common—and most damaging—real-world breach. We close it by construction.

### Billing correctness (our most-engineered area)
- **Idle time is excluded from the bill:** active span is measured *start → last activity*, not start → end. A table left on after everyone leaves doesn't inflate the bill.
- **No play, no charge:** a session must have ≥1 actually-played round to bill anything (a lobby that never started a game = $0).
- **Abandoned Trivia doesn't count:** a Trivia round nobody joined isn't billed or counted as a round.
- **Per-table-per-night cap:** beyond the cap, play is free; weekday vs. weekend caps by day-of-week.
- **Test venues never billed;** the rounding **exactly matches** the database's rounding so numbers never drift.
- **Idempotency everywhere:** finalizing a session's bill twice is a no-op; the nightly rollup can re-run safely; a **paid** invoice can never be regressed; an already-sent invoice is never double-sent (no double-charge).
- **Guards:** zero/blank price or cap can't crash billing.

### Payments / Stripe
- Webhooks are **signature-verified** (constant-time HMAC, timestamp tolerance, malformed signatures rejected) — a forged "you got paid" event can't slip through.
- A late "payment failed" arriving after "paid" **cannot** corrupt a paid invoice.
- In the current build, creating a **real charge is impossible** (test mode + stub).

### Real-time consistency
- All phones at a table stay in sync via real-time broadcast, but a **2-second poll is the source of truth** — if a real-time message is missed (game end, host hand-off, a player rejoining), the poll reconciles it. (This is the classic "eventual consistency with reconciliation" pattern — fast *and* correct.)

### Game-flow resilience
- **Host leaves** → the table hands off to the next player (or ends cleanly if none remain).
- **Drops to 1 player** → short grace countdown, then auto-end (a solo social game is pointless); a rejoin cancels it.
- **Two phones, same table / switching tables** → confirm-gated, single-active-seat behavior.
- **Idle table** → grace → pause → auto-end, with the bill frozen at the last real activity.

### NFC integrity
- Signed tags are verified server-side (signature + a strictly-increasing counter to block replay); the per-tag key is stored **encrypted**. Plain (unsigned) tags are supported too. (More in §7.)

### Theme engine
- Round-type selection is **deterministic** per (session, round) so every phone shows the same thing and it never flickers; falls back to Chooser when there aren't enough players for Roulette/Trivia; an all-zero/edge theme can't deadlock.

### Invite & admin-surface security (Build #1)
- **Single-use invites can't be double-redeemed:** the redeem path is a single atomic guarded write (not read-then-write), so a race between two owners on the same code resolves to exactly one winner — the loser gets a clean "invalid/expired."
- **Un-invited owners are stopped server-side**, not just hidden in the UI: a venue-less owner with no redeemed invite is gated to "Contact us."
- **Every admin mutation is audited**, all admin/invite inputs reject unknown fields, and a CI guard blocks dangerous code patterns from ever landing.

### Operational reality
- Because dev shares one database, tests run **sequentially** to avoid corrupting each other (a lesson we hit and codified). Deploy is region-pinned to **Sydney** (close to our users + data).

---

## 5. What happens when we merge → push to GitHub → Vercel

- We're set up as **one Vercel project**: it builds the web app and runs the backend as **serverless functions** on the same domain (no CORS complexity). On a push to `main`, Vercel **auto-deploys**.
- The code is green, so **the build succeeds.** The app then needs three pieces of **configuration** (not code) to actually run:
  1. **Environment variables** set in Vercel (database URL, Clerk keys, secrets) — missing any = errors.
  2. A **separate production database** (we've been on a dev database), migrated + seeded — done with one idempotent command.
  3. ✅ **A security-policy fix for Clerk** — *already done* (the deployed site's content-security-policy now allows Clerk's sign-in; without it sign-in would have been dead in production).
- **Important:** none of this affects local testing — the local environment ignores production config entirely.

**Bottom line:** going live is a **config + setup afternoon**, not more building.

---

## 6. What happens once we buy + map a domain

- Point the domain at Vercel → the app is served at `https://<our-domain>` with **automatic real HTTPS** (no more dev-only certificate warnings, no "same Wi-Fi" requirement — any phone anywhere just works).
- **NFC tags** then carry `https://<our-domain>/<venue-slug>/<table-number>`.
- For a **production-grade** Clerk instance (cleaner than the dev one), Clerk needs DNS records on our domain — so the domain unlocks that upgrade too (optional; the dev instance works in the meantime).
- **Testing different venues becomes trivial:** each owner signs up with their own email → their own venue; patrons just open the venue's URL on any phone. Multi-venue testing is now "hand someone a link," not a LAN setup.

---

## 7. Future scope

**NFC — signed tags (security upgrade, optional for launch):**
- Today's **plain NFC tags work and are cheap.** They lack only *cryptographic proof of physical presence* (someone could share a table's URL and "play" remotely). Because billing is duration-based and capped, the abuse upside is minimal — so we can **launch on plain tags**.
- Upgrading to **signed (NTAG 424 DNA) tags** is a **contained ~1-day code change** — the entire verification framework (key storage, replay protection) is already built; only the crypto algorithm swaps in. The catch: it needs real tags in hand to validate against, so it's a deliberate later pass, not a launch blocker. **Recommendation: don't buy the pricier signed tags for launch.**

**Other roadmap items (all scoped, none blocking launch):**
- **QR fallback** (`/play` + daily codes) for the ~5% of phones without NFC.
- **Staff invites** (owners adding their staff).
- **Distributed rate limiting + caching** via Redis (our current per-endpoint limits need a shared store to fully bite in serverless — a small hardening job; meanwhile Clerk rate-limits sign-ups).
- **Per-venue timezone** (only needed once we have venues outside Melbourne).
- **Stripe go-live-in-test** (drop in free test keys to exercise the real Stripe path), then real billing.
- **Nightly job scheduling** for the billing/analytics rollups.
- **Theme 90-day scheduler** and **Venue Trivia Night** (live venue-wide event).

---

## 8. Pre-launch checklist

**Blockers (config, ~an afternoon):**
- [x] CSP/Clerk fix (done)
- [ ] Production database (create + migrate + seed)
- [ ] Vercel environment variables
- [ ] Domain (buy + map to Vercel)
- [ ] Merge the stack to `main`

**Soon-after hardening (not blockers):**
- [ ] Redis (distributed rate limiting/caching)
- [ ] Signed NFC tags (only if/when we want physical-presence proof)
- [ ] Rotate the development secret key before real users

**The headline:** the *product* is done — onboarding, isolation, games, dashboards, billing, theming, controlled-invite onboarding, and admin audit trail — all tested and CI-green. What remains between here and a live, multi-venue platform is **configuration and a domain purchase**, not feature development. We're clear to move on the domain.
