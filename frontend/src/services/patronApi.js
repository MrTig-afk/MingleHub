const BASE = import.meta.env.VITE_API_URL
const KEY = import.meta.env.VITE_API_KEY
const h = { 'Content-Type': 'application/json', 'X-API-Key': KEY }

// Public — no auth token. Resolves an NFC tap (venue/table/signature/counter)
// into venue branding info + lobby/join-or-new/table-full state, or throws
// if the tap can't be verified.
export const fetchTap = ({ venueSlug, tableNumber, tagUid, counter, sig, phoneId }) => {
  const params = new URLSearchParams({
    venue_slug: venueSlug,
    table_number: tableNumber,
    tag_uid: tagUid,
    counter,
    sig,
    phone_id: phoneId,
  })
  return fetch(`${BASE}/api/patron/tap?${params}`, { headers: h }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })
}

export const pollLobby = (lobbyId) =>
  fetch(`${BASE}/api/patron/lobby/${lobbyId}`, { headers: h }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

export const claimHost = (lobbyId, phoneId) =>
  fetch(`${BASE}/api/patron/lobby/${lobbyId}/claim-host`, {
    method: 'POST',
    headers: h,
    body: JSON.stringify({ phone_id: phoneId }),
  }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

export const startGame = (lobbyId, { phoneId, playerCount, playerNames, adultsOnly, groupLabel }) =>
  fetch(`${BASE}/api/patron/lobby/${lobbyId}/start`, {
    method: 'POST',
    headers: h,
    body: JSON.stringify({
      phone_id: phoneId,
      player_count: playerCount,
      player_names: playerNames,
      adults_only: adultsOnly,
      group_label: groupLabel,
    }),
  }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

export const startNewGroup = (tableId, phoneId) =>
  fetch(`${BASE}/api/patron/table/${tableId}/new-group`, {
    method: 'POST',
    headers: h,
    body: JSON.stringify({ phone_id: phoneId }),
  }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

export const joinSession = (sessionId, phoneId, name) =>
  fetch(`${BASE}/api/patron/sessions/${sessionId}/join`, {
    method: 'POST',
    headers: h,
    body: JSON.stringify({ phone_id: phoneId, name }),
  }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

// Dev-only — stands in for a physical tag by computing the signature it
// would produce for a given tag_uid + counter. 404s outside DEV_MODE.
export const simulateTap = (tagUid, counter) =>
  fetch(`${BASE}/api/dev/simulate-tap`, {
    method: 'POST',
    headers: h,
    body: JSON.stringify({ tag_uid: tagUid, counter }),
  }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })
