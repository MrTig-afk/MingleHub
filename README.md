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
| NFC tap activation, QR fallback, lobby, Trivia, Roulette, theming, billing, dashboards | ⏳ Not started |

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
