// Resolve the API base URL once, for every service to share.
//
// Priority:
//   1. VITE_API_URL, if explicitly set — honored as an override (e.g. point dev
//      at a remote box, or pin a value in CI).
//   2. Dev with no override — talk to the SAME host the page was loaded from, on
//      the backend port. So an NFC tap from ANY Wi-Fi/IP reaches the laptop's API
//      at that same address automatically; no per-network .env edit on each move.
//   3. Otherwise (production build) — '' for same-origin relative calls, which is
//      how the Vercel deploy serves the API at /api/*.
//
// Service paths already include the `/api/...` prefix, so this is just the origin.
const DEV_API_PORT = 8000

function resolveApiBase() {
  const explicit = import.meta.env.VITE_API_URL
  if (explicit) return explicit
  if (import.meta.env.DEV && typeof window !== 'undefined') {
    return `${window.location.protocol}//${window.location.hostname}:${DEV_API_PORT}`
  }
  return ''
}

export const API_BASE = resolveApiBase()
