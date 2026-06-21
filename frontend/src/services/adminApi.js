import { devLogin } from './dashboardApi'

// Re-export devLogin — the POST /api/auth/dev-login endpoint is role-agnostic.
export { devLogin }

const BASE = import.meta.env.VITE_API_URL || ''
const KEY = import.meta.env.VITE_API_KEY
const h = (token) => ({
  'Content-Type': 'application/json',
  'X-API-Key': KEY,
  ...(token ? { Authorization: `Bearer ${token}` } : {}),
})

export const fetchAdminMe = (token) =>
  fetch(`${BASE}/api/admin/me`, { headers: h(token) }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

export const fetchAdminOverview = (token) =>
  fetch(`${BASE}/api/admin/overview`, { headers: h(token) }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

export const fetchAdminVenues = (token) =>
  fetch(`${BASE}/api/admin/venues`, { headers: h(token) }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })
