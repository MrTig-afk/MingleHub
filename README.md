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
| Lobby + Join-or-New chooser (`game_sessions`/`game_players`/`table_lobbies` schema, host election, up to 3 groups per table) | ✅ Implemented — session/membership routing only, see [Lobby + Join-or-New](#lobby--join-or-new) for the scope boundary |
| Setup screen + Adults Only toggle (player count/names, group label, server-enforced content gating) | ✅ Implemented — see [Setup Screen + Adults Only Toggle](#setup-screen--adults-only-toggle) |
| QR fallback, Trivia, Roulette, theming, billing, dashboards | ⏳ Not started |

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

`-v` prints every test name with its own pass/fail line (see [CI](#ci) below for the same thing running automatically on every push).

### Linting

```bash
python -m flake8 api scripts   # backend
cd frontend && npm run lint    # frontend
```

---

## CI

`.github/workflows/ci.yml` runs on every push and pull request:

- **backend job**: `flake8` (config in `.flake8`), then `pytest -v` against an ephemeral `postgres:16` service container — no real Neon credentials needed. `DATABASE_SSL=disable` (see `api/db.py`) lets the app connect to that plain local container; real deployments are untouched and still default to `ssl=require`.
- **frontend job**: `eslint .`, then `npm run build`.

Both jobs' full output (every file/test, not just a summary) is visible in the Actions tab for each run.

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
- **No NFC hardware needed to test locally**: on `/dashboard/pair-tags`, after pairing a tag, hit **"Open Game (tap #N)"** — it calls the dev-only `POST /api/dev/simulate-tap` (stands in for the physical tag, computing a real valid signature; 404s outside `DEV_MODE`) and opens the actual public landing route in a new tab, running the full verification path for real.
  - Each click of **"Open Game"** advances to the next counter and should always succeed (`Playing at {Venue} 🍺`).
  - **"Replay tap #N-1 (expect rejected)"** deliberately re-sends the *previous* counter — this one is supposed to fail (`Tap didn't go through` / `Invalid or expired tag`). If a fresh "Open Game" tap ever shows that error, something's actually wrong; if it's the Replay button, that's the rejection path working correctly.
  - A guard in `PairTags.jsx` prevents a double-tap from firing the same counter twice (which would otherwise show one success and one confusing "expired" rejection for what looks like the same tap).
- **Frontend**: `PatronLanding.jsx` — the public route itself (`/{venue-slug}/{table-number}`), parses the tap's query params and shows venue branding ("Playing at {Venue} 🍺") on success or an error otherwise. No lobby/session yet — that's the next slice.
- **Tests**: `api/tests/test_patron_tap.py` — valid tap, replay/lower-counter rejection, wrong signature, unknown tag, cross-venue tag/table mismatch, revoked tag, malformed/unknown venue slug → 404.
- **Testing on your phone**: same LAN setup as [Testing on a phone over your LAN](#testing-on-a-phone-over-your-lan) — visit `https://<your-lan-ip>:<frontend-port>/dashboard/pair-tags`, sign in, pair a tag, then use the buttons above. "Open Game" opens the public landing route in a new tab on your phone, exactly as a real tap would.

---

## Lobby + Join-or-New

Once a tap verifies, it has to resolve to *something*: an empty table starts a lobby, a table mid-game offers join-or-new, a table with 3 active groups says it's full. This slice builds that routing and the membership plumbing underneath it — not the round engine itself.

- **Scope boundary**: this is session/membership routing only. Once a session starts, the patron lands on a placeholder "Game started 🎉" screen — Chooser/Trivia/Roulette round UI is separate, later backlog work (#18/#19/#20). The host Setup screen (player count/names, group label, Adults Only toggle) lives inline in `Lobby.jsx` once a host is chosen — see [Setup Screen + Adults Only Toggle](#setup-screen--adults-only-toggle).
- **Schema** (added in `scripts/migrate.py`, after `nfc_tags`):
  - `table_lobbies` — one row per "table is waiting to start a game" window. `status` is `open` → `converted` (a session started) or `expired`. A partial unique index (`one_open_lobby_per_table`) enforces at most one *open* lobby per table at the DB level — a safety net behind the app-level race handling below.
  - `table_lobby_phones` — which phones have tapped into a given lobby (`UNIQUE (lobby_id, phone_id)`, so a re-tap from the same phone is a no-op, not a duplicate).
  - `game_sessions` / `game_players` — the actual game, exactly per `gamespec.md`'s schema (group label, player count/names, adults_only, theme, round counters, scores). A lobby's `converted_session_id` points here once a host starts the game.
- **Patron flow** (`api/services/lobby_service.py`, wired into `api/routers/patron_router.py`):
  1. `GET /api/patron/tap` now takes an optional `phone_id` — when present, the response includes `table_state` describing what this phone should see: `lobby` (join/host an open lobby), `join_or_new` (1-2 active groups — show the chooser), or `table_full` (3 active groups, the gamespec-specified cap).
  2. `GET /api/patron/lobby/{lobby_id}` — polled every 2s by the frontend; returns phone count, host, and status (flips to `converted` once a host starts the game).
  3. `POST /api/patron/lobby/{lobby_id}/claim-host` — first phone to call this becomes host (atomic `UPDATE ... WHERE host_phone_id IS NULL`); everyone else gets told who already won the race.
  4. `POST /api/patron/lobby/{lobby_id}/start` — host-only, validates player count (2-8 per `gamespec.md`), creates the `game_sessions`/`game_players` rows, marks the lobby `converted`.
  5. `POST /api/patron/table/{table_id}/new-group` — starts a second/third lobby at a table that already has an active session (rejected once 3 groups already exist).
  6. `POST /api/patron/sessions/{session_id}/join` — adds a player to an already-started session (the "join their game" path from the chooser).
- **Concurrency**: two phones tapping the same empty table within milliseconds of each other both race to create the lobby — `_get_or_create_open_lobby()` catches the resulting `UniqueViolationError` from the partial index and re-queries instead of erroring. Host election is a single atomic `UPDATE`, not a read-then-write, so there's no window for two phones to both become host.
- **Phone identity**: each browser generates a `phone_id` (`crypto.randomUUID()`) once and persists it in `localStorage`, so reloads/re-taps from the same phone are recognized rather than treated as a new participant. A `?phone_id=` URL override exists purely for dev/testing (see below) — a real tag's NDEF payload never carries one.
- **No NFC hardware or second phone needed to test locally**: pair a tag on `/dashboard/pair-tags`, then click **"Open Game"** — each click mints a fresh `phone_id` and opens the public landing route in a new tab, simulating a *different* phone tapping the same table. Open it 2-3 times to watch a lobby fill up, claim host from one tab, set player count, and start — the other tabs' polling picks up the `converted` state within ~2s. Tap again after a session is active to see the join-or-new chooser, and a 4th simulated group to see "This table is full."
- **Tests**: `api/tests/test_lobby.py` (16 tests) — lobby creation on first tap, second phone joining the same lobby, idempotent re-tap, host-claim race, start validation (host-only, player count bounds), join-or-new and table-full responses (including that `table_id` is present for the new-group call), joining an existing session, rejecting a join to an ended session, the 3-groups-per-table cap, and the Adults Only gating covered below. Uses a function-scoped `fresh_table` fixture (`api/tests/conftest.py`) so stateful session/lobby tests don't interfere with each other.

---

## Setup Screen + Adults Only Toggle

Once a phone claims host in the lobby, the same screen (`Lobby.jsx`) becomes the Setup screen from `gamespec.md` Step 4 — player count, optional names, an optional custom group label, and the Adults Only toggle, all submitted together to `POST /api/patron/lobby/{lobby_id}/start`.

- **Adults Only gating** (`lobby_service.adults_only_allowed`, gamespec: *Adults Only Content Controls*) — two layers, checked **server-side** on start, not just hidden client-side:
  1. `venues.restrict_adult_content` ON overrides everything — the toggle never appears and any `adults_only: true` is rejected regardless of the table.
  2. `tables.content_ceiling` must be `adults_allowed`, not the default `standard`.
  A patron can choose less than the table's ceiling allows, never more. The frontend computes the same check (`!venue.restrict_adult_content && venue.content_ceiling === 'adults_allowed'`) from the fields `GET /api/patron/tap` already returns, so the toggle is hidden entirely rather than shown-then-rejected — but the backend re-validates regardless, the same BOLA-safe pattern used everywhere else (never trust a client-supplied flag for something access-controlled).
- **Group label**: optional free-text field; left blank, `start_game` auto-generates "Table N Group M" via `next_group_label()` (existing Lobby + Join-or-New logic).
- **No NFC hardware needed to test locally**: same multi-tab simulation as [Lobby + Join-or-New](#lobby--join-or-new) — claim host from one tab, you'll see the player-count slider, names field, group-label field, and (only on a table whose `content_ceiling` is `adults_allowed`) the Adults Only checkbox. The seeded dev tables (`lions-den` table 1/2) are `standard` ceiling, so the toggle won't appear on them by design — it's exercised in tests against a dedicated `adults_allowed_table` fixture instead.
- **Tests**: `test_start_rejects_adults_only_on_standard_table`, `test_start_allows_adults_only_on_adults_allowed_table`, `test_start_rejects_adults_only_when_venue_restricts_even_if_table_allows` (in `api/tests/test_lobby.py`) — the last one proves the venue-wide switch overrides a table that would otherwise allow it.

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
