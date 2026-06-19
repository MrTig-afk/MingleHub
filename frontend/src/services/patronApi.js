const BASE = import.meta.env.VITE_API_URL
const KEY = import.meta.env.VITE_API_KEY
const h = { 'Content-Type': 'application/json', 'X-API-Key': KEY }

// Public — no auth token. Resolves an NFC tap (venue/table, optionally
// signature/counter) into venue branding info + lobby state, or throws
// if the venue/table can't be resolved. Signature params are omitted when
// the tag is a plain NTAG 213 (no crypto), letting the backend skip
// verification and still resolve the table.
export const fetchTap = ({ venueSlug, tableNumber, tagUid, counter, sig, phoneId }) => {
  const params = new URLSearchParams({
    venue_slug: venueSlug,
    table_number: tableNumber,
    phone_id: phoneId,
  })
  if (tagUid) params.set('tag_uid', tagUid)
  if (sig) params.set('sig', sig)
  if (counter != null && !isNaN(counter)) params.set('counter', counter)
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

export const setLobbyName = (lobbyId, phoneId, name) =>
  fetch(`${BASE}/api/patron/lobby/${lobbyId}/set-name`, {
    method: 'POST',
    headers: h,
    body: JSON.stringify({ phone_id: phoneId, name }),
  }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

export const startGame = (lobbyId, { phoneId, adultsOnly, groupLabel }) =>
  fetch(`${BASE}/api/patron/lobby/${lobbyId}/start`, {
    method: 'POST',
    headers: h,
    body: JSON.stringify({
      phone_id: phoneId,
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

// Called by the session-origin phone once the local finger picker has
// chosen a finger — resolves that to a real game_players row server-side
// and increments times_selected. Any other phone calling this gets a 403.
export const pickHotSeat = (sessionId, phoneId) =>
  fetch(`${BASE}/api/patron/sessions/${sessionId}/select-hot-seat`, {
    method: 'POST',
    headers: h,
    body: JSON.stringify({ phone_id: phoneId }),
  }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

// Chooser round API — draw a card, then complete/skip/redraw it.
export const drawCard = (sessionId, phoneId, playerId) =>
  fetch(`${BASE}/api/patron/sessions/${sessionId}/draw-card`, {
    method: 'POST',
    headers: h,
    body: JSON.stringify({ phone_id: phoneId, player_id: playerId }),
  }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

export const completeRound = (roundId, phoneId) =>
  fetch(`${BASE}/api/patron/rounds/${roundId}/complete`, {
    method: 'POST',
    headers: h,
    body: JSON.stringify({ phone_id: phoneId }),
  }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

export const skipRound = (roundId, phoneId) =>
  fetch(`${BASE}/api/patron/rounds/${roundId}/skip`, {
    method: 'POST',
    headers: h,
    body: JSON.stringify({ phone_id: phoneId }),
  }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

export const redrawRound = (roundId, phoneId) =>
  fetch(`${BASE}/api/patron/rounds/${roundId}/redraw`, {
    method: 'POST',
    headers: h,
    body: JSON.stringify({ phone_id: phoneId }),
  }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })

export const fetchChannelAuth = (tableId, phoneId) =>
  fetch(`${BASE}/api/patron/channel-auth`, {
    method: 'POST',
    headers: h,
    body: JSON.stringify({ phone_id: phoneId, table_id: tableId }),
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
