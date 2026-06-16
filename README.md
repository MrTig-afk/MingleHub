# MingleHub

MingleHub is a tap-to-play group game platform for bars, pubs, breweries, and social venues. Patrons tap an NFC tag (or scan a QR fallback) at their table — no app install, no login — and play a round of **Chooser**, **Trivia**, or **Roulette** straight from the browser. Venues manage tables, themes, and billing from a dashboard; admins manage the platform across all venues.

The full product spec — game rules, scoring, NFC verification, theme system, multi-phone Trivia flow, and database schema — lives in `.claude/gamespec.md`. That document is the source of truth; this README covers what's implemented so far and how to run it locally.

---

## Current Status

| Area | Status |
|---|---|
| Chooser round (card draw, complete/skip/redraw, scoring) | ✅ Implemented |
| Card decks (Icebreakers, Truth, Dares, Compliments, Dirty, Deep, Party, + university decks) | ✅ Implemented |
| Finger picker | ✅ Implemented |
| Platform schema (`venues`, `users`, `tables`) | ✅ Implemented |
| Venue-scoped auth, role gating (venue_owner / venue_staff / admin) | ✅ Implemented — **dev-only stub auth**, real Clerk integration pending |
| NFC tag pairing (`nfc_tags` schema, pair-tag endpoint, `/dashboard/pair-tags`) | ✅ Implemented — AES keys are dev-generated placeholders until real factory key provisioning is built |
| NFC tap verification (`/api/patron/tap`, public landing route) | ✅ Implemented — **dev-stub HMAC signature** standing in for real NTAG 424 DNA SDM/CMAC, see [NFC Tap Verification](#nfc-tap-verification) |
| QR fallback, lobby, Trivia, Roulette, theming, billing, dashboards | ⏳ Not started |

See `.claude/gamespec.md` → "What Needs To Be Built" for the full remaining scope.

---

## Tech Stack

| | Layer | Technology |
|---|---|---|
| <img src="https://cdn.simpleicons.org/react/61DAFB" width="20"/> | Frontend | React 19, Vite 8, Tailwind CSS v4 |
| <img src="https://cdn.simpleicons.org/pwa/5A0FC8" width="20"/> | PWA | vite-plugin-pwa, Workbox |
| <img src="https://cdn.simpleicons.org/fastapi/009688" width="20"/> | Backend | FastAPI (Python), Mangum (ASGI → serverless) |
| <img src="https://cdn.simpleicons.org/postgresql/4169E1" width="20"/> | Database | Neon (PostgreSQL), asyncpg |
| <img src="https://cdn.simpleicons.org/vercel/ffffff" width="20"/> | Hosting | Vercel (static + Python serverless functions) |

---

## Local Development

### Prerequisites

- Python 3.11+
- Node 18+
- A Neon (Postgres) project — dev/test branch, never production data

### 1. Environment variables

```bash
cp api/.env.example api/.env
cp frontend/.env.local.example frontend/.env.local
```

Fill in `api/.env`:

| Variable | Notes |
|---|---|
| `DATABASE_URL` | Your dev Neon connection string |
| `API_KEY` | Shared key the frontend sends as `X-API-Key` |
| `DEV_MODE` | `true` locally — relaxes CORS and enables `/api/auth/dev-login` |
| `SESSION_SECRET` | Random value signing dev session tokens (see [Platform Foundation](#platform-foundation)) |
| `NFC_KEY_ENCRYPTION_SECRET` | Encrypts `nfc_tags.aes_key_encrypted` at rest. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `NTFY_*` | Optional — ntfy.sh topics for error/security/payment alerts |
| `STRIPE_*` | Optional — only needed for checkout/webhook testing |

### 2. Database setup

```bash
python scripts/migrate.py          # creates all tables
python scripts/seed.py              # loads card decks from content/*.json
python scripts/seed_platform.py     # seeds dev venues/users for auth testing
```

### 3. Run the backend

```bash
python -m uvicorn api.index:app --reload
```

### 4. Run the frontend

```bash
cd frontend
npm install
npm run dev
```

### Testing on a phone over your LAN

`frontend/vite.config.js` serves HTTPS using mkcert certs (PWA features and the dashboard's bearer-token auth both need HTTPS). To test from a phone on the same Wi-Fi network:

```bash
mkcert -cert-file <your-lan-ip>.pem -key-file <your-lan-ip>-key.pem <your-lan-ip> localhost 127.0.0.1
```

Update `vite.config.js`'s cert paths to match, set `VITE_API_URL` in `frontend/.env.local` to `https://<your-lan-ip>:8000`, run the backend with `--host 0.0.0.0 --ssl-keyfile=... --ssl-certfile=...`, and the frontend with `npm run dev -- --host`. Visit `https://<your-lan-ip>:5173` from your phone.

### Running tests

```bash
python -m pytest api/tests/ -v
```

---

## Platform Foundation

The DB and auth layer every other feature depends on:

- **Schema**: `venues`, `users`, `tables` (full columns per `gamespec.md`), plus `packs`/`cards`/`premium_interest` from the carried-over card-game foundation.
- **Auth**: `api/auth.py` provides `get_current_user`/`require_role` — `venue_id` and `role` are always derived server-side from the authenticated user, never accepted from the client (BOLA-safe by construction).
- **Dev-only stub**: until a Clerk dev instance is wired in, sessions are HMAC-signed tokens issued by `POST /api/auth/dev-login` (404s outside `DEV_MODE`). The dependency contract (`get_current_user`/`require_role`) is the permanent pattern — swapping in real Clerk verification later won't change route handlers.
- **Proof endpoints**: `GET /api/dashboard/me`, `GET /api/dashboard/venue` (venue_owner/venue_staff), `GET /api/admin/ping` (admin only).
- **Dev-login UI**: visit `/dashboard` in the frontend to sign in as a seeded dev user and see the above endpoints' responses live.
- **Tests**: `api/tests/test_auth.py` — missing/invalid tokens, cross-venue isolation, role gating in both directions.

---

## NFC Tag Pairing

Ties a physical tag's UID to a table — the prerequisite for every patron-facing flow in `gamespec.md` (no tap, no session).

- **Schema**: `nfc_tags` (`venue_id`, `table_id`, `tag_uid`, `aes_key_encrypted`, `status`, `counter_last_seen`, `paired_at`).
- **Encryption at rest**: `api/services/nfc_crypto.py` encrypts each tag's AES key with Fernet before it touches the DB; the key is never returned by any API response. **Dev note**: real NTAG 424 DNA tags arrive factory-programmed with their own AES key — until an admin/ops provisioning flow exists to load those, `pair_tag` generates a random placeholder key for any UID it hasn't seen before.
- **Endpoints** (`api/routers/dashboard_router.py`): `GET /api/dashboard/tables`, `GET /api/dashboard/tags` (venue_owner/venue_staff), `POST /api/dashboard/pair-tag` (venue_owner only — table must belong to the caller's own venue; a `tag_uid` already paired to a *different* venue is rejected with 409 rather than re-pointed, per the BOLA pattern in `security.md`).
- **No NFC hardware needed to test locally**: visit `/dashboard/pair-tags` in the frontend, sign in as a seeded dev owner, pick a table, and hit **"Simulate Tap (dev)"** — it generates a fake UID client-side and exercises the exact same backend pairing logic a real tap would. The real Web NFC read button (`Tap Tag to Pair`) only appears in browsers that support `NDEFReader` (Chrome on Android with a real tag).
- **Tests**: `api/tests/test_nfc_pairing.py` — role gating, table-not-in-venue, re-pairing within a venue, cross-venue UID theft (BOLA), tag list never leaks `aes_key_encrypted`.

---

## NFC Tap Verification

The actual patron-facing flow: a tap resolves to a venue + table only if its signature and counter check out — proving physical presence at the table.

- **Public route**: `GET /api/patron/tap?venue_slug=&table_number=&tag_uid=&counter=&sig=` (`api/routers/patron_router.py`). Derives the venue from the slug via a public lookup only — never touches the `users` table. Every failure mode (unknown venue/table/tag, wrong signature, replayed/lower counter, revoked tag, tag/table venue mismatch) returns a generic 404/401 so a bad request can't be used to probe what exists.
- **Replay protection**: `nfc_tags.counter_last_seen` must strictly increase on every successful tap — equal or lower is rejected outright.
- **Signature scheme** (`api/services/nfc_verify.py`): **DEV-STUB** — HMAC-SHA256 over `tag_uid:counter` keyed by the tag's decrypted AES key. Has the same security property as the real thing (unforgeable without the key, replay-proof counter) but isn't byte-compatible with a real NTAG 424 DNA tag's Secure Dynamic Messaging output (AES-CBC + CMAC per NXP AN12196) — swapping that in only touches this file once real tag output is available to validate against.
- **No NFC hardware needed to test locally**: on `/dashboard/pair-tags`, after pairing a tag, hit **"Open Game (tap #N)"** — it calls the dev-only `POST /api/dev/simulate-tap` (stands in for the physical tag, computing a real valid signature; 404s outside `DEV_MODE`) and opens the actual public landing route in a new tab, running the full verification path for real. **"Replay tap #N-1 (expect rejected)"** re-sends the previous counter to demonstrate the rejection path.
- **Frontend**: `PatronLanding.jsx` — the public route itself (`/{venue-slug}/{table-number}`), parses the tap's query params and shows venue branding ("Playing at {Venue} 🍺") on success or an error otherwise. No lobby/session yet — that's the next slice.
- **Tests**: `api/tests/test_patron_tap.py` — valid tap, replay/lower-counter rejection, wrong signature, unknown tag, cross-venue tag/table mismatch, revoked tag, malformed/unknown venue slug → 404.

---

## Card Decks (Chooser round content)

| | Name | Description |
|---|---|---|
| 🌊 | **Icebreakers** | Easy prompts to warm up the room |
| 🔍 | **Truth** | Honest questions and real answers |
| 🔥 | **Dares** | Physical and social challenges |
| 💛 | **Compliments** | Wholesome cards for good vibes |
| 💋 | **Dirty** | For the brave ones |
| 🌌 | **Deep** | Meaningful questions for actual conversations |
| 🎉 | **Party** | Chaotic cards for louder rounds |
| ⚡ | **Debate** | 30 debatable statements — argue a side |
| 🎒 | **Freshers** | 35 first-year icebreaker prompts |
| 🌶️ | **Hot Takes** | 30 spicy campus opinions |
| 🤔 | **Would You Rather** | 26 dilemmas |

### Round mechanics

- **Finger picker** — players place a finger on screen; the app randomly selects who goes next using `crypto.getRandomValues` with rejection sampling (no modulo bias); with 3+ players the previous winner is excluded from immediate re-selection.
- **Complete, skip, or redraw** — see `gamespec.md`'s scoring/redraw-penalty rules for how this maps to the full game once wired into a real session.
