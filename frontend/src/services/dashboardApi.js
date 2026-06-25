// Empty/unset => same-origin relative calls (the Vercel deploy serves the API
// at /api on the same host). Local dev sets the absolute LAN URL.
const BASE = import.meta.env.VITE_API_URL || ''
const KEY = import.meta.env.VITE_API_KEY
const h = (token) => ({
  'Content-Type': 'application/json',
  'X-API-Key': KEY,
  ...(token ? { Authorization: `Bearer ${token}` } : {}),
})

// Dev-only — issues a stub session token for a seeded clerk_user_id.
// Replaced by real Clerk sign-in once a Clerk dev instance is wired in.
export const devLogin = (clerkUserId) =>
  fetch(`${BASE}/api/auth/dev-login`, {
    method: 'POST',
    headers: h(),
    body: JSON.stringify({ clerk_user_id: clerkUserId }),
  }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

export const fetchMe = (token) =>
  fetch(`${BASE}/api/dashboard/me`, { headers: h(token) }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

export const fetchVenue = (token) =>
  fetch(`${BASE}/api/dashboard/venue`, { headers: h(token) }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

export const fetchOverview = (token) =>
  fetch(`${BASE}/api/dashboard/overview`, { headers: h(token) }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

export const fetchTables = (token) =>
  fetch(`${BASE}/api/dashboard/tables`, { headers: h(token) }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

export const pairTag = (token, tagUid, tableNumber) =>
  fetch(`${BASE}/api/dashboard/pair-tag`, {
    method: 'POST',
    headers: h(token),
    body: JSON.stringify({ tag_uid: tagUid, table_number: tableNumber }),
  }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

// Dev-only — ends every active session/lobby at a table so the next tap
// starts completely fresh. 404s outside DEV_MODE.
export const devResetTable = (token, tableNumber) =>
  fetch(`${BASE}/api/dashboard/dev-reset-table`, {
    method: 'POST',
    headers: h(token),
    body: JSON.stringify({ table_number: tableNumber }),
  }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

export const fetchTableDetail = (token, tableId) =>
  fetch(`${BASE}/api/dashboard/tables/${tableId}`, { headers: h(token) }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

export const fetchInsights = (token, range = 'tonight') =>
  fetch(`${BASE}/api/dashboard/insights?range=${range}`, { headers: h(token) }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

export const fetchSettings = (token) =>
  fetch(`${BASE}/api/dashboard/settings`, { headers: h(token) }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

export const patchSettings = (token, body) =>
  fetch(`${BASE}/api/dashboard/settings`, {
    method: 'PATCH',
    headers: h(token),
    body: JSON.stringify(body),
  }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

export const fetchBilling = (token) =>
  fetch(`${BASE}/api/dashboard/billing`, { headers: h(token) }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

/**
 * Push the venue's latest invoice to Stripe for payment.
 * Calls POST /api/dashboard/billing/sync (auth-gated, venue_owner only).
 * Returns { customer_id, stripe_invoice_id, line_items, mode, skipped? }.
 * Throws on network error (fetch rejects) or HTTP error (non-2xx).
 */
export const syncBillingToStripe = (token) =>
  fetch(`${BASE}/api/dashboard/billing/sync`, {
    method: 'POST',
    headers: h(token),
  }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

export const fetchThemes = (token) =>
  fetch(`${BASE}/api/dashboard/themes`, { headers: h(token) }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

export const fetchActiveTheme = (token) =>
  fetch(`${BASE}/api/dashboard/theme`, { headers: h(token) }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

export const setTheme = (token, themeKey) =>
  fetch(`${BASE}/api/dashboard/theme`, {
    method: 'POST',
    headers: h(token),
    body: JSON.stringify({ theme_key: themeKey }),
  }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

// First-run venue setup for a newly-provisioned owner with no venue yet.
export const setupVenue = (token, body) =>
  fetch(`${BASE}/api/dashboard/setup-venue`, {
    method: 'POST',
    headers: h(token),
    body: JSON.stringify(body),
  }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

// Keyless address autocomplete (Photon/OSM, proxied by the backend).
export const geoAutocomplete = (token, q) =>
  fetch(`${BASE}/api/dashboard/geo/autocomplete?q=${encodeURIComponent(q)}`, { headers: h(token) })
    .then(async (r) => {
      if (!r.ok) return { suggestions: [] }
      return r.json()
    })

export const cancelVenue = (token, reason) =>
  fetch(`${BASE}/api/dashboard/cancel`, {
    method: 'POST',
    headers: h(token),
    body: JSON.stringify({ reason }),
  }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

export const reactivateVenue = (token) =>
  fetch(`${BASE}/api/dashboard/reactivate`, {
    method: 'POST',
    headers: h(token),
    body: JSON.stringify({}),
  }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

// Redeem a venue invite code. Returns { invite: { venue_name, address, lat, lng, place_id } }.
export const redeemInvite = (token, code) =>
  fetch(`${BASE}/api/dashboard/redeem-invite`, {
    method: 'POST',
    headers: h(token),
    body: JSON.stringify({ code }),
  }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })
