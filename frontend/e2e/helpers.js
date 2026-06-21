// Game-state setup via the backend API, so the UI tests can jump straight to the
// state they care about (a Roulette round, a drop-to-1, a recap) instead of
// driving the whole game through the browser. Plain taps (no tag_uid/sig) — the
// same path the static-URL frontend uses.
process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0' // backend uses a self-signed dev cert

const BASE = 'https://192.168.1.108:8000'
const KEY = 'dev-key'
const H = { 'Content-Type': 'application/json', 'X-API-Key': KEY }
const VENUE = 'lions-den'

async function api(method, path, body) {
  const opt = { method, headers: H }
  if (body) opt.body = JSON.stringify(body)
  const r = await fetch(`${BASE}/api/patron${path}`, opt)
  if (!r.ok) throw new Error(`${method} ${path} -> ${r.status}: ${await r.text()}`)
  return r.json()
}

export function freshPhone(label) {
  return `pw-${label}-${Date.now()}-${Math.floor(Math.random() * 1e6)}`
}

export async function ownerToken() {
  const r = await fetch(`${BASE}/api/auth/dev-login`, {
    method: 'POST', headers: H, body: JSON.stringify({ clerk_user_id: 'dev_owner_a' }),
  })
  return (await r.json()).token
}

export async function resetTable(tableNumber) {
  const token = await ownerToken()
  await fetch(`${BASE}/api/dashboard/dev-reset-table`, {
    method: 'POST',
    headers: { ...H, Authorization: `Bearer ${token}` },
    body: JSON.stringify({ table_number: tableNumber }),
  })
}

// Reset the table, tap N phones, name them, host=first, start. Returns session_id.
export async function startSession(tableNumber, phoneIds) {
  await resetTable(tableNumber)
  let first
  for (const pid of phoneIds) {
    const body = await api('GET',
      `/tap?venue_slug=${VENUE}&table_number=${tableNumber}&phone_id=${encodeURIComponent(pid)}`)
    if (!first) first = body
  }
  const lobby = first.table_state.lobby_id
  await api('POST', `/lobby/${lobby}/claim-host`, { phone_id: phoneIds[0] })
  for (let i = 0; i < phoneIds.length; i++) {
    await api('POST', `/lobby/${lobby}/set-name`, { phone_id: phoneIds[i], name: `P${i + 1}` })
  }
  const start = await api('POST', `/lobby/${lobby}/start`, { phone_id: phoneIds[0], adults_only: false })
  return start.session_id
}

// Play one Chooser round so a resume lands on round 2 (Roulette).
export async function playChooser(sess, host) {
  const hs = await api('POST', `/sessions/${sess}/select-hot-seat`, { phone_id: host })
  const d = await api('POST', `/sessions/${sess}/draw-card`, { phone_id: host, player_id: hs.player_id })
  await api('POST', `/rounds/${d.round_id}/complete`, { phone_id: host })
}

export const leave = (sess, phone) => api('POST', `/sessions/${sess}/leave`, { phone_id: phone })
export const endGame = (sess, phone) => api('POST', `/sessions/${sess}/end-game`, { phone_id: phone })

export function tablePath(tableNumber, phoneId) {
  return `/${VENUE}/${tableNumber}?phone_id=${encodeURIComponent(phoneId)}`
}
