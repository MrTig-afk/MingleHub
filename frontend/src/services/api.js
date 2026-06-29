import { API_BASE as BASE } from './apiBase'
const KEY = import.meta.env.VITE_API_KEY
const h = { 'Content-Type': 'application/json', 'X-API-Key': KEY }

export const fetchPacks = (mode = 'party') =>
  fetch(`${BASE}/api/packs?mode=${mode}`, { headers: h }).then(r => {
    if (!r.ok) throw new Error(r.status)
    return r.json()
  })

export const fetchPack = (id) =>
  fetch(`${BASE}/api/packs/${id}`, { headers: h }).then(r => {
    if (!r.ok) throw new Error(r.status)
    return r.json()
  })

export const createCheckoutSession = () =>
  fetch(`${BASE}/api/create-checkout-session`, { method: 'POST', headers: h }).then(r => {
    if (!r.ok) throw new Error(r.status)
    return r.json()
  })

export const captureInterest = (email, mode, trigger) =>
  fetch(`${BASE}/api/interest`, {
    method: 'POST',
    headers: { ...h, 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, mode, trigger }),
  }).then(r => {
    if (!r.ok) throw new Error(r.status)
    return r.json()
  })
