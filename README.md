# MingleHub

**Tap-to-play social games for bars, pubs and venues.** Patrons tap the NFC tag on their table and play together on their own phones. No app install, no account, no login. Venues get a live dashboard and analytics; the platform operator gets a cross-venue admin panel and usage-based billing.

MingleHub is an extension of **FirstMove**, the original social icebreaker game, grown into an end-to-end business: the game itself, plus self-serve venue onboarding, multi-tenant dashboards, invite-controlled rollout, a theme engine, and a complete metered billing system (15-minute active-play blocks, capped per table per night).

---

## Try it right now

You don't need to install anything. The live deployment has demo venues seeded, so just open this on your phone:

**<https://mingle-hub.vercel.app/fifty-five-bar/1>**

Get a friend (or a second phone) to open the same link, enter names, and start a game from the host phone. Every phone on that link is "at the same table" and stays in sync live. You'll rotate through three games: **Chooser** (hot-seat card questions), **Trivia** (everyone answers live, with scores), and **Roulette** (group-vote dares and prompts). More demo tables if you want separate groups:

- `https://mingle-hub.vercel.app/fifty-five-bar/2`
- `https://mingle-hub.vercel.app/the-last-chance/1`
- `https://mingle-hub.vercel.app/the-last-chance/2`

That link is exactly what an NFC tap opens at a real venue table.

---

## How it works

The whole trick is deliberately simple: **an NFC tag is just a URL carrier.**

Every table's tag holds one link:

```
https://<domain>/<venue-slug>/<table-number>
```

Tapping the tag opens that page in the phone's browser. The page joins the phone to that table's live session, and everyone at the table plays together in real time. That's it, which means **you don't need NFC at all to use MingleHub**. Anything that gets that URL onto a phone works identically:

- **Tap an NFC tag** (the premium physical experience at a venue)
- **Scan a QR code** of the same URL
- **Type or share the link** directly

### The games

| Game | What happens |
|---|---|
| **Chooser** | A finger-picker puts one player in the hot seat, and they answer the card the group draws |
| **Trivia** | Everyone answers the same questions live on their own phone, with per-player scoring and a recap at the end |
| **Roulette** | The wheel lands on a prompt and the group votes on the outcome |

Chooser and Roulette draw from **11 card packs** (300+ cards): Icebreakers, Party, Truth, Dares, Deep, Compliments, Would You Rather, Hot Takes, Debate, Freshers, and Dirty (18+ venues only; venues can restrict adult content).

Rounds are drawn by the venue's **theme** (Party Night, Date Night, and so on), which weights which game types and card packs appear. Host hand-off, players leaving and rejoining, and idle timeout are all handled server-side.

---

## Run it yourself

Want your own instance instead of the hosted one? Here's the full local setup.

### Prerequisites

- Python 3.11+
- Node 18+
- A Postgres database. A free [Neon](https://neon.tech) project works out of the box; any Postgres connection string is fine.

### 1. Backend

```bash
git clone https://github.com/MrTig-afk/MingleHub.git
cd MingleHub
python -m venv venv
venv/Scripts/activate        # Windows; use `source venv/bin/activate` on macOS/Linux
pip install -r requirements.txt

# Configure
cp api/.env.example api/.env
# edit api/.env: set DATABASE_URL to your Postgres connection string

# Create tables and seed content (venues, tables, card packs, trivia, themes)
python scripts/migrate.py
python scripts/seed_platform.py
python scripts/seed.py
python scripts/seed_roulette_cards.py
python scripts/seed_trivia_questions.py
python scripts/seed_themes.py

# Run
python -m uvicorn api.index:app --reload --port 8000
```

### 2. Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local   # defaults point at http://localhost:8000
npm run dev
```

### 3. Play on your phones

The seed data creates two demo venues, `fifty-five-bar` and `the-last-chance`, each with tables 1 and 2.

1. The Vite dev server binds all interfaces (`host: true`), so it's reachable on your LAN. Note the **Network** URL it prints, e.g. `http://192.168.1.50:5173`.
2. On each phone (same Wi-Fi as your computer), open:

   ```
   http://<your-lan-ip>:5173/fifty-five-bar/1
   ```

3. Enter a name on each phone and start a game from the host phone.

> Real-time sync uses Supabase Realtime broadcast when configured, and **gracefully falls back to 2-second polling when it isn't**, so local play works with just the database. Even with realtime on, polling remains the source of truth; broadcasts just make it snappy.

### 4. Be the venue owner too

Playing patron is only half the product. On your own instance you can run the whole business side:

1. The owner dashboard lives at `/dashboard` and the admin panel at `/admin`. Both sign in via [Clerk](https://clerk.com) (free tier): add your Clerk publishable key to `frontend/.env.local` and the matching secrets to `api/.env`. Patron gameplay needs no auth at all.
2. Add your email to `ADMIN_EMAILS` (comma-separated) in `api/.env`. On first login that email is auto-provisioned as an **admin**; everyone else becomes a venue owner.
3. Onboarding is invite-gated: from `/admin`, generate a **venue invite** (link + QR), open it, sign in, and the setup wizard creates your venue and tables. An owner without an invite gets a locked "Contact us" screen, which is the controlled-rollout gate working as intended.

As the owner you get the full dashboard: live tables tonight, per-table drill-down, insights (sessions, rounds, round mix, trends), auto-saving settings, and, the fun part, **Billing**. MingleHub meters real usage:

- Active play time is measured **start to last activity**, so idle time is never billed. A lobby where nobody played a round bills $0.
- Play is metered in **15-minute blocks**, priced per block, **capped per table per night** (separate weekday/weekend caps).
- Blocks roll up into a **monthly invoice** automatically, and a play-time analytics view shows **actual vs billed** minutes side by side.
- Invoicing is idempotent end to end: finalizing twice is a no-op, a paid invoice can never regress, and nothing double-sends.

Play a few rounds as a patron at your venue's table URL, then watch your own bill accrue in the Billing tab. Stripe runs in test mode, so a real charge is impossible.

### 5. Optional: real NFC tags

When you want the true tap experience, no code changes are needed:

1. Buy cheap **NTAG213/215** stickers (a few cents each).
2. Install a free writer app, e.g. **NXP TagWriter** (iOS/Android).
3. Write a single URL record: `https://<your-deployment>/<venue-slug>/<table-number>`.
4. Stick it on the table. Done: any modern phone tap-launches the page.

(The backend also supports **signed tags**, NTAG 424 DNA with server-side signature and replay-counter verification, for cryptographic proof of physical presence. That's an ops/provisioning upgrade, not a requirement.)

---

## Deploying your own

The repo deploys as **one Vercel project**: the FastAPI backend runs as a serverless function (`api/index.py` via Mangum) and the Vite SPA is served from the same origin (see `vercel.json`, no CORS setup needed).

1. Import the repo into Vercel.
2. Create a production Postgres (e.g. Neon) and run `scripts/migrate.py` plus the seed scripts against it.
3. Set the environment variables from `api/.env.example` and `frontend/.env.local.example` in Vercel.
4. Point a domain at Vercel (automatic HTTPS). Your NFC tags and QR codes then carry `https://your-domain.com/<venue>/<table>`.

Stripe runs in **test mode** with a stubbed path; real charges are impossible in the current build.

---

## Architecture

- **Backend**: FastAPI (Python) in `api/`, deployed serverless via Mangum; server-authoritative game state, round numbering and billing.
- **Frontend**: React + Vite SPA in `frontend/`; the patron game route is code-split from the dashboard/admin bundles so a tap downloads only game code.
- **Database**: Postgres (Neon in production, pooled connection).
- **Real-time**: Supabase Realtime broadcast for instant multi-phone sync, reconciled by a 2-second poll (eventual consistency with reconciliation).
- **Auth**: Clerk (Google/email) for venue owners, staff and admins, with auto-provisioning and an admin allowlist. Patrons never log in.
- **Billing**: active-play time metered in 15-minute blocks, per-table nightly caps, idempotent invoicing, Stripe (test mode).
- **Multi-tenancy**: every dashboard query derives the venue from the authenticated user, never from the request; cross-venue access is impossible by construction and tested per endpoint.

```
api/
  index.py        # FastAPI app (serverless entry)
  routers/        # patron, dashboard, admin, stripe, dev
  services/       # billing, NFC crypto, game logic
  tests/          # 450+ tests across 34 files
frontend/
  src/components/ # PatronLanding (the game), Dashboard, Admin
  src/hooks/      # realtime session channel
scripts/          # migrate, seed, dev reset, rollups, simulators
content/          # card packs (JSON)
```

Every change lands through CI (lint plus the full test suite plus a production build), with a CI guard that fails the build if dangerous code patterns (`eval`, string-built SQL, and friends) ever appear.

---

## Status

The platform is feature-complete and CI-green: onboarding, the three games, venue and admin dashboards, invite-controlled rollout, theme engine, and the full billing lifecycle (cancel / suspend / dunning / reactivation), all hand-tested end-to-end on real devices.

---

## License

[MIT](LICENSE)
