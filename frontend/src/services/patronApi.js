const BASE = import.meta.env.VITE_API_URL
const KEY = import.meta.env.VITE_API_KEY
const h = { 'Content-Type': 'application/json', 'X-API-Key': KEY }

// Public — no auth token. Resolves an NFC tap (venue/table/signature/counter)
// into venue branding info, or throws if the tap can't be verified.
export const fetchTap = ({ venueSlug, tableNumber, tagUid, counter, sig }) => {
  const params = new URLSearchParams({
    venue_slug: venueSlug,
    table_number: tableNumber,
    tag_uid: tagUid,
    counter,
    sig,
  })
  return fetch(`${BASE}/api/patron/tap?${params}`, { headers: h }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || r.status)
    return r.json()
  })
}

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
