const BASE = import.meta.env.VITE_API_URL
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

export const fetchAdminPing = (token) =>
  fetch(`${BASE}/api/admin/ping`, { headers: h(token) }).then(async (r) => {
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
