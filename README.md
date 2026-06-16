# FirstMove

A social card game PWA for groups. Everyone places a finger on the screen, the app picks someone, and they draw a prompt from the deck.

**[Play it here](https://first-move-one.vercel.app/)**

## What It Does

- **Finger picker** — everyone puts a finger down and FirstMove randomly chooses who goes next.
- **Deck-based prompts** — pick a category, draw a card, and keep the round moving.
- **Complete, skip, or redraw** — no awkward waiting around.
- **Game summary** — see completed cards, skipped cards, and who got picked the most.
- **Built for phones** — made for quick group play straight from the browser.

### Decks

| | Name | Description |
|---|---|---|
| 🌊 | **Icebreakers** | Easy prompts to warm up the room |
| 🔍 | **Truth** | Honest questions and real answers |
| 🔥 | **Dares** | Physical and social challenges |
| 💛 | **Compliments** | Wholesome cards for good vibes |
| 💋 | **Dirty** | For the brave ones |
| 🌌 | **Deep** | Meaningful questions for actual conversations |
| 🎉 | **Party** | Chaotic cards for louder rounds |

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

## Branch: `PWA_polished_single` — Free Tier + Mode System

### Free tier limits

| # | Feature | Detail |
|---|---|---|
| 1 | **Haptic on finger reveal** | 400ms vibration when the chosen finger is revealed. Android only — iOS does not support the Vibration API. |
| 2 | **10-second card timer** | Countdown badge top-right of card screen, coloured to match the active category. Goes red at ≤3s. On expiry: auto-skips if skip is available; force-completes if skip already used. Toast notification fades in above the buttons. Timer has no enforcement in dev mode. |
| 3 | **20-card session limit** | After 20 cards the upgrade modal appears. Count resets when app is closed or backgrounded. |
| 4 | **1 skip per round** | Skip available once per card. Using it (manually or via timer auto-skip) locks it for that round. Completing a card grants a fresh skip for the next. |
| 5 | **Per-pack card counter** | `1/20` badge inside the glass card (top-right). Increments per deck, resets when switching to a new pack. |
| 6 | **Locked decks** | Unlocked decks depend on mode (see below). All others are greyed out with 🔒. Tapping a locked deck shows the upgrade modal. |
| 7 | **Upgrade prompt** | Two-screen modal: feature list → email capture. Email stored in Neon `premium_interest` table and notified via ntfy. |
| 8 | **Session reset on close** | All session state fully resets on background/close — navigates back to home screen so next open starts fresh. |

### Party / Uni modes

A **Party / Uni toggle pill** is always visible in the top-right corner of the home screen. Defaults to Party on open.

| Mode | Unlocked decks |
|---|---|
| 🔥 Party | Icebreakers, Dares, Compliments |
| 🎓 Uni | Debate, Freshers, Would You Rather |

### Upgrade prompt

When the card limit or a locked deck is tapped, a two-screen modal appears:

**Screen 1 — Features:** full premium feature list + "Upgrade to Premium →" CTA.

**Screen 2 — Notify:** email input. Submitting calls `POST /api/interest`, stores the email in the `premium_interest` table on Neon, and fires an ntfy notification to the owner. Shows success, duplicate, or error state inline.

Tapping the prompt also fires a background ntfy notification on mount (trigger reason + mode) as a backup signal. All ntfy channels send with Content-Type: text/plain so notifications arrive as readable messages, not file attachments. Payment infrastructure (Stripe) is wired on the backend but not connected to the CTA — the interest list comes first.

#### Uni content decks
Four university-themed decks are included for Uni mode:

| | Deck | Description |
|---|---|---|
| ⚡ | **Debate** | 30 debatable statements — argue a side |
| 🎒 | **Freshers** | 35 first-year icebreaker prompts |
| 🌶️ | **Hot Takes** | 30 spicy campus opinions |
| 🤔 | **Would You Rather** | 26 uni-themed dilemmas |

---

## Latest updates

- **Deck mixing** — select two or more decks and play a combined shuffled session; category header updates per card to show which pack it came from
- **Shareable game recap** — share session stats after the game ends (dev mode only; free tier sees upgrade prompt)
- **Card screen centred layout** — card vertically centred between header and buttons on mobile; category badge sits inside the centred group so badge + card move as one unit
- **Redraw is dev-only** — free tier never sees the redraw button; redraw is unlimited in dev mode
- **Dev mode toast suppression** — timer countdown and all notifications fully suppressed in dev mode; buttons never blocked by toast state
- **Finger picker: 2-finger minimum** — countdown only starts when at least 2 players have a finger down; a single finger shows "Add at least one more player"
- **Finger picker: fair selection** — uses `crypto.getRandomValues` with rejection sampling to eliminate modulo bias; with 3+ players the previous winner is excluded from the pool to prevent immediate back-to-back picks
- **Finger picker: countdown clamped** — countdown display can never show a negative number; race condition between the tick interval and the selection timeout is fully guarded
