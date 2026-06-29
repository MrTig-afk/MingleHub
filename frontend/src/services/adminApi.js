import { devLogin, geoAutocomplete } from './dashboardApi'
import { API_BASE as BASE } from './apiBase'

// Re-export devLogin and geoAutocomplete — shared utilities used by admin components.
export { devLogin, geoAutocomplete }

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

export const fetchAdminVenueDetail = (token, venueId) =>
  fetch(`${BASE}/api/admin/venues/${venueId}`, { headers: h(token) })
    .then(async (r) => { if (!r.ok) throw new Error((await r.json()).detail || r.status); return r.json() })

export const patchAdminVenue = (token, venueId, body) =>
  fetch(`${BASE}/api/admin/venues/${venueId}`, {
    method: 'PATCH',
    headers: h(token),
    body: JSON.stringify(body),
  }).then(async (r) => { if (!r.ok) throw new Error((await r.json()).detail || r.status); return r.json() })

export const fetchAdminVenueConfigHistory = (token, venueId, { limit = 50, offset = 0 } = {}) =>
  fetch(`${BASE}/api/admin/venues/${venueId}/config-history?limit=${limit}&offset=${offset}`, { headers: h(token) })
    .then(async (r) => { if (!r.ok) throw new Error((await r.json()).detail || r.status); return r.json() })

export const fetchAdminSupport = (token, status = 'open') =>
  fetch(`${BASE}/api/admin/support?status=${status}`, { headers: h(token) })
    .then(async (r) => { if (!r.ok) throw new Error((await r.json()).detail || r.status); return r.json() })

export const patchAdminSupportMessage = (token, messageId, body) =>
  fetch(`${BASE}/api/admin/support/${messageId}`, {
    method: 'PATCH',
    headers: h(token),
    body: JSON.stringify(body),
  }).then(async (r) => { if (!r.ok) throw new Error((await r.json()).detail || r.status); return r.json() })

export const fetchAdminLeads = (token) =>
  fetch(`${BASE}/api/admin/leads`, { headers: h(token) })
    .then(async (r) => { if (!r.ok) throw new Error((await r.json()).detail || r.status); return r.json() })

export const createAdminLead = (token, body) =>
  fetch(`${BASE}/api/admin/leads`, {
    method: 'POST',
    headers: h(token),
    body: JSON.stringify(body),
  }).then(async (r) => { if (!r.ok) throw new Error((await r.json()).detail || r.status); return r.json() })

export const fetchAdminTeam = (token) =>
  fetch(`${BASE}/api/admin/team`, { headers: h(token) })
    .then(async (r) => { if (!r.ok) throw new Error((await r.json()).detail || r.status); return r.json() })

export const createInvite = (token, body) =>
  fetch(`${BASE}/api/admin/invites`, {
    method: 'POST',
    headers: h(token),
    body: JSON.stringify(body),
  }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

export const fetchInvites = (token) =>
  fetch(`${BASE}/api/admin/invites`, { headers: h(token) })
    .then(async (r) => {
      if (!r.ok) throw new Error((await r.json()).detail || r.status)
      return r.json()
    })

export const revokeInvite = (token, inviteId) =>
  fetch(`${BASE}/api/admin/invites/revoke`, {
    method: 'POST',
    headers: h(token),
    body: JSON.stringify({ invite_id: inviteId }),
  }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })
