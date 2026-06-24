# MingleHub — Production / Launch Write-Up

**Audience:** founders. **Purpose:** what happens on deploy, how NFC/venues/auth work in production, and the exact gaps to close before going live (so you can decide on domain, hardware, etc.).

**Current state:** 4 feature branches stacked, all CI-green, **none merged to main** (39 commits ahead). Stack order: `onboarding → billing → dashboard-perf → theme-engine`. We're using the **Clerk dev instance** on purpose (fine for now; production hardening is a later, separate step).

---

## 1. What happens if we merge to main → push → Vercel deploys

- The repo is configured for **one Vercel project** (`vercel.json`, region `syd1`/Sydney): it builds the Vite SPA to `frontend/dist`, and runs the FastAPI backend as a **serverless function** (`api/index.py` + Mangum). Routing: `/api/*` → the Python function, everything else → the SPA. So **API and app live on the same domain** (no CORS headaches).
- On push to `main`, Vercel auto-builds + deploys. The code is green, so the **build will succeed**. But the app only *works* if three things are configured — and **none of them come from the repo**:

### ⚠️ The 3 things that must be set up (or it breaks)
1. **Environment variables in Vercel** (not your local `.env`): `DATABASE_URL` (Neon **pooled**), `API_KEY`, `ADMIN_EMAILS`, Clerk keys (`VITE_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`), the NFC `SECRET_SEED`, Supabase keys, Stripe keys (optional). Miss one → 500s.
2. **A production database.** You've been on a shared **dev** Neon DB. Prod should be its own Neon DB, **migrated + seeded** (see §5). Don't point prod at the dev DB.
3. **Content-Security-Policy fix for Clerk.** 🚨 **This one will bite you.** The CSP in `vercel.json` currently allows `js.stripe.com`, Supabase, and ntfy — but **not Clerk's domains**. Clerk's JS + API calls would be **blocked in production** (they aren't in dev because Vite doesn't apply these headers). **Fix before deploy:** add your Clerk frontend-API domain to `script-src` *and* `connect-src` (e.g. `https://*.clerk.accounts.dev` for the dev instance, or `https://clerk.<your-domain>` once you have a prod instance). ~2-line change, but the app's sign-in is dead without it.

**Bottom line:** merging + deploying is a **config exercise, not a code one.** The build works; you wire env vars + a prod DB + the CSP line, and it's live.

---

## 2. What URL to program into the **current** (plain) NFC tags

Today's tags are plain (NTAG 213, no crypto). The URL is just:

```
https://<your-domain>/<venue-slug>/<table-number>
```
e.g. `https://minglehub.com/fifty-five-bar/1`

- The SPA route `/:slug/:table` resolves the venue + table server-side and drops the patron straight into the lobby.
- **No same-Wi-Fi requirement, no cert warnings** once hosted (that was a dev-only LAN artifact). Any phone, anywhere, just taps and plays.
- Each venue gets its own slug at onboarding; each table is numbered. So a new bar's tags are `https://<domain>/<their-slug>/1`, `/2`, etc.

---

## 3. How it changes for **new (signed)** NFC tags — and 4. the code

**The whole verification *framework* is already built + tested** (`api/routers/patron_router.py`): it branches signed-vs-plain, looks up the tag's **AES key (stored encrypted)** + last counter, **verifies the signature**, enforces **counter-must-increase** (replay protection), and updates it. Pairing, key storage, replay defense — all done.

**What's stubbed:** only the crypto *algorithm* in `api/services/nfc_verify.py`. It uses HMAC-SHA256 as a stand-in for real **NTAG 424 DNA SDM** (AES-CBC-decrypt the PICC data + AES-CMAC, per NXP AN12196). The file is written so the swap is isolated.

**Code change to go real (contained, ~1 day with real tags):**
1. Replace the HMAC in `nfc_verify.py` with real **AES-CBC + AES-CMAC** (using a crypto lib).
2. Small tweak to the tap endpoint params: a signed tag emits `?picc_data=…&cmac=…` (the tag's chip appends these on each tap), not `tag_uid/counter/sig` — so decrypt `picc_data` → UID+counter, verify the CMAC.
3. Tests.

**Signed-tag URL** (programmed into the chip's SDM config — base URL, chip appends the rest):
```
https://<your-domain>/<venue-slug>/<table-number>?picc_data=<enc>&cmac=<mac>
```

**Hardware caveat:** you **can't finish/validate the real crypto without real tags** producing sample SUN URLs. So: order tags → program SDM + AES keys → capture samples → we implement against them.

**Do you need signed tags to launch? No.** Plain NTAG 213 tags work today and are cheaper. Signed tags only add *cryptographic proof of physical presence* (stops someone sharing/forging a table URL to play remotely). Since billing is **duration-based + capped per table/night**, the abuse upside is tiny. **Recommendation: launch on plain tags; add SUN as a later hardening pass.** Don't buy the pricier 424 DNA tags for launch.

---

## 5. Migrating the new code (database)

- `scripts/migrate.py` is **idempotent** (`CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`) and also **seeds** themes, bar cards, trivia questions, roulette cards.
- You run it **once against the production `DATABASE_URL`** (and again after any future schema change). It is **not** auto-run on Vercel deploy — it's a manual step you trigger (locally pointed at prod, or as a one-off CI/job step).
- After it runs, the prod DB has every table + the game content. You then create venues via the onboarding flow (or seed a first one).

---

## 6. Fresh NFC at a real venue — the pitch scenario

You delete all test users, keep only admin emails, walk into a bar, and the owner signs up. Here's the full flow, end to end:

1. Owner goes to `https://<your-domain>/dashboard`, **signs up via Clerk** (Google / email).
2. **First login auto-provisions** them (`api/auth.py` → `_provision_user`): their Clerk email is **not** in `ADMIN_EMAILS`, so they're created as a **`venue_owner` with no venue**.
3. With no venue, they're routed to the **"Set up your venue" wizard** → name, type, address (autocomplete), table count, 18+ toggle → it creates the **venue (with a slug) + the tables**.
4. They go to **Pair Tags**, and each tag gets `https://<domain>/<their-slug>/<table#>` written to it (or you pre-program a kit for them).
5. Patrons tap → lobby → play. The owner sees only **their** venue's dashboard/billing; **you (admin)** see all venues.

So a fresh tag just needs the **venue-slug + table-number URL** — which only exists *after* the owner onboards and creates the venue. The tag is the last step, not the first.

---

## 7. Admin vs Owner — how it's decided

Purely an **allowlist**: `ADMIN_EMAILS` (a Vercel env var, comma-separated). On first login:
- email **in** `ADMIN_EMAILS` → **admin** (no venue, sees everything, `/admin`).
- email **not in** it → **venue_owner** (→ setup wizard → their own venue, `/dashboard`).
- `venue_staff` is created separately (owner invites them — that feature is still on the to-build list).

So you control who's an admin by editing one env var. Everyone else who signs up is a venue owner. There is **no open door to admin** — a random signup can never become admin.

---

## 8. Is Clerk the only authentication?

For **dashboard + admin: yes** — Clerk JWTs, verified server-side (`api/auth.py` → `_verify_token` via Clerk's JWKS). (A dev-only HMAC token path exists for local testing; it's `DEV_MODE`-gated and off in prod.) Identity, sessions, sign-up, bot protection all come from Clerk.

**Patron routes are public** (no login — that's the point: tap and play). They're gated only by a shared **API key**. ⚠️ Note: that API key ships in the **frontend bundle**, so it's effectively **public** — it's a basic gate, not a real secret. The real trust boundary for any sensitive action is **Clerk + the per-venue isolation** (every dashboard query derives `venue_id` from the authenticated user; cross-venue access returns 403/404 — tested).

---

## 9 & 10. Rate limiting (signups + API)

**Per-endpoint limits exist** in code (`slowapi`, e.g. 60/min on reads, 10–30/min on writes, 15/min on geo). **But there's a real production caveat:**

🚨 **The limiter is in-memory** (`Limiter(key_func=get_client_ip)`, no shared store). On Vercel's **serverless** runtime (ephemeral, many short-lived instances), an in-memory counter is **per-instance** — so the limits **don't hold globally** in production. Effectively, rate limiting is **not enforced** once deployed serverless.

- **Signups:** we don't rate-limit provisioning ourselves, but **Clerk rate-limits sign-up/auth on their side** (plus bot protection), so that path is covered by Clerk.
- **API GET/POST:** to make our own limits actually bite in production, the limiter needs a **shared backend — Upstash Redis** (already named in the security plan, free tier, no card). That's a small wiring job (`storage_uri=...`). Until then, treat API rate limiting as **advisory in prod**.

---

## Pre-launch checklist (the actionable list)

**Before deploy:**
- [ ] **CSP fix** — add Clerk domains to `script-src` + `connect-src` in `vercel.json` (🚨 sign-in dead without it).
- [ ] Create a **production Neon DB** (separate from dev); run `migrate.py` against it.
- [ ] Set **all env vars** in Vercel (DB, Clerk, API_KEY, ADMIN_EMAILS, SECRET_SEED, Supabase, Stripe).
- [ ] **Domain** — buy it, point it at Vercel. (Dev Clerk works on it; a *production* Clerk instance is a later step and needs the domain anyway.)
- [ ] Merge the stack to `main` in order (onboarding → billing → dashboard-perf → theme-engine).

**Soon after (hardening, not blockers):**
- [ ] **Upstash Redis** for real rate limiting (+ optional caching).
- [ ] **Signed NFC tags** (order 424 DNA, implement the contained SDM crypto) — only if/when you want physical-presence proof; plain tags launch fine.
- [ ] **Staff invites**, **QR fallback** (`/play`), **per-venue timezone** (only if non-Melbourne venues).
- [ ] Rotate the **Clerk secret key** (it was pasted in chat during dev).

**What's genuinely ready right now:** the whole app — onboarding, multi-venue isolation, games, billing (with the rollups + Stripe stub), the theme engine. The launch work is **config + 3 small hardening items**, not feature-building.
