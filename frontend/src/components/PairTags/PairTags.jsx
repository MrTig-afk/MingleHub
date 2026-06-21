import { useRef, useState } from 'react'
import { devLogin, devResetTable, fetchTables, fetchVenue, pairTag } from '../../services/dashboardApi'
import { simulateTap as simulateTagSignature } from '../../services/patronApi'

// Must match the clerk_user_id values seeded by scripts/seed_platform.py.
// Only venue_owner accounts — pairing is owner-only per gamespec.md.
const DEV_OWNERS = [
  { id: 'dev_owner_a', label: "Owner A — Fifty Five Bar" },
  { id: 'dev_owner_b', label: 'Owner B — The Last Chance' },
]

const supportsWebNfc = typeof window !== 'undefined' && 'NDEFReader' in window

export default function PairTags() {
  const [clerkUserId, setClerkUserId] = useState(DEV_OWNERS[0].id)
  const [token, setToken] = useState(null)
  const [venueSlug, setVenueSlug] = useState(null)
  const [tables, setTables] = useState([])
  const [tableNumber, setTableNumber] = useState(1)
  const [status, setStatus] = useState('idle') // idle | loading | error
  const [error, setError] = useState(null)
  const [lastPaired, setLastPaired] = useState(null)
  const [nextTapCounter, setNextTapCounter] = useState(1)

  const signInAndLoadTables = async () => {
    setStatus('loading')
    setError(null)
    try {
      const { token: newToken } = await devLogin(clerkUserId)
      setToken(newToken)
      const [loadedTables, venue] = await Promise.all([fetchTables(newToken), fetchVenue(newToken)])
      setTables(loadedTables)
      setTableNumber(loadedTables[0]?.table_number ?? 1)
      setVenueSlug(venue.slug)
      setStatus('idle')
    } catch (e) {
      setError(e.message)
      setStatus('error')
    }
  }

  const finishPairing = async (tagUid) => {
    setStatus('loading')
    setError(null)
    try {
      const result = await pairTag(token, tagUid, tableNumber)
      setLastPaired(result)
      setNextTapCounter(1) // a freshly-(re)paired tag's counter starts over
      setTables(await fetchTables(token))
      setStatus('idle')
    } catch (e) {
      setError(e.message)
      setStatus('error')
    }
  }

  // Guards against a double-tap firing this twice for the same counter
  // before the disabled-button re-render lands (the React state update is
  // async; a fast double-tap on mobile can beat it). Two requests racing
  // for the same counter would otherwise see one succeed and one correctly
  // — but confusingly — get rejected as a replay.
  const tapInFlightRef = useRef(false)

  // Stands in for a real tap: asks the backend (acting as the tag) to
  // compute a valid signature for this counter, then opens the exact
  // public landing route a real tap would — proving the full NFC
  // verification path end-to-end without any hardware.
  //
  // A fresh phone_id is generated per click (carried in the URL — see
  // PatronLanding.jsx's resolvePhoneId) so each "Open Game" click
  // simulates a *different* phone tapping in. Open it multiple times to
  // simulate multiple phones joining the same lobby; each opens in a new
  // tab so you can watch them all update live.
  const openGameTap = async (counter) => {
    if (tapInFlightRef.current) return
    tapInFlightRef.current = true
    setStatus('loading')
    setError(null)
    try {
      const { tag_uid, sig } = await simulateTagSignature(lastPaired.tag_uid, counter)
      const params = new URLSearchParams({ tag_uid, counter, sig, phone_id: crypto.randomUUID() })
      window.open(`/${venueSlug}/${lastPaired.table_number}?${params}`, '_blank')
      if (counter >= nextTapCounter) setNextTapCounter(counter + 1)
      setStatus('idle')
    } catch (e) {
      setError(e.message)
      setStatus('error')
    } finally {
      tapInFlightRef.current = false
    }
  }

  // Real path — reads the tag's UID over Web NFC (Chrome on Android only).
  const scanRealTag = async () => {
    setStatus('loading')
    setError(null)
    try {
      const reader = new window.NDEFReader()
      await reader.scan()
      reader.onreading = (event) => finishPairing(event.serialNumber)
    } catch (e) {
      setError(e.message)
      setStatus('error')
    }
  }

  // Dev path — no NFC hardware needed. Generates a fake UID so the full
  // pairing flow (DB write, venue scoping, re-pairing) can be tested from
  // any browser, on desktop, with nothing tapped.
  const simulateTap = () => finishPairing(crypto.randomUUID())

  // Ends every active session/lobby at the selected table so the next
  // "Open Game" starts from a clean lobby instead of resuming whatever
  // groups an earlier test round left active. Doesn't touch the tag's
  // counter — that's independent of which table/sessions exist, and
  // resetting it here would make the next tap a replay of an
  // already-used counter.
  const resetTable = async () => {
    setStatus('loading')
    setError(null)
    try {
      await devResetTable(token, tableNumber)
      setStatus('idle')
    } catch (e) {
      setError(e.message)
      setStatus('error')
    }
  }

  return (
    <div style={{
      minHeight: '100dvh',
      background: 'var(--bg-floor)',
      color: 'var(--on-surface)',
      fontFamily: 'var(--font-body)',
      padding: '24px',
      display: 'flex',
      flexDirection: 'column',
      gap: '16px',
      maxWidth: '480px',
      margin: '0 auto',
    }}>
      <h1 style={{ fontFamily: 'var(--font-headline)', fontSize: '22px' }}>
        Pair NFC Tags
      </h1>
      <p style={{ fontSize: '13px', color: 'var(--on-surface-dim)' }}>
        Tap each tag against your phone to tie it to a table. No real tag handy?
        Use "Simulate Tap" below — it exercises the same backend pairing logic.
      </p>

      {!token ? (
        <>
          <select
            value={clerkUserId}
            onChange={(e) => setClerkUserId(e.target.value)}
            style={selectStyle}
          >
            {DEV_OWNERS.map((u) => (
              <option key={u.id} value={u.id}>{u.label}</option>
            ))}
          </select>
          <button onClick={signInAndLoadTables} disabled={status === 'loading'} style={buttonStyle}>
            {status === 'loading' ? 'Signing in…' : 'Dev Sign In'}
          </button>
        </>
      ) : (
        <>
          <label style={{ fontSize: '13px', color: 'var(--on-surface-dim)' }}>
            Table to pair
            <select
              value={tableNumber}
              onChange={(e) => setTableNumber(Number(e.target.value))}
              style={selectStyle}
            >
              {tables.map((t) => (
                <option key={t.id} value={t.table_number}>
                  Table {t.table_number} {t.tag_paired ? '(already paired)' : '(unpaired)'}
                </option>
              ))}
            </select>
          </label>

          {supportsWebNfc && (
            <button onClick={scanRealTag} disabled={status === 'loading'} style={buttonStyle}>
              Tap Tag to Pair
            </button>
          )}
          <button
            onClick={simulateTap}
            disabled={status === 'loading'}
            style={{ ...buttonStyle, background: 'var(--secondary)' }}
          >
            {status === 'loading' ? 'Pairing…' : 'Simulate Tap (dev)'}
          </button>
          <button
            onClick={resetTable}
            disabled={status === 'loading'}
            style={{ ...buttonStyle, background: 'var(--bg-surface)', color: 'var(--on-surface)', border: '1px solid var(--outline)' }}
          >
            Reset Table {tableNumber} (dev) — end all active games
          </button>
        </>
      )}

      {error && (
        <p style={{ color: 'var(--tertiary)', fontFamily: 'var(--font-mono)', fontSize: '13px' }}>
          Error: {error}
        </p>
      )}

      {lastPaired && (
        <div style={{
          background: 'var(--glass-bg)',
          border: '1px solid var(--glass-border)',
          borderRadius: '8px',
          padding: '12px',
          fontFamily: 'var(--font-mono)',
          fontSize: '13px',
        }}>
          <div style={{ color: 'var(--secondary)', marginBottom: '6px' }}>Paired ✅</div>
          <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
            {JSON.stringify(lastPaired, null, 2)}
          </pre>
        </div>
      )}

      {lastPaired && (
        <>
          <p style={{ fontSize: '13px', color: 'var(--on-surface-dim)' }}>
            Now simulate a patron tapping this tag — opens the real public
            landing route and runs it through actual NFC signature
            verification (no hardware needed).
          </p>
          <button
            onClick={() => openGameTap(nextTapCounter)}
            disabled={status === 'loading'}
            style={buttonStyle}
          >
            Open Game (tap #{nextTapCounter})
          </button>
          {nextTapCounter > 1 && (
            <button
              onClick={() => openGameTap(nextTapCounter - 1)}
              disabled={status === 'loading'}
              style={{ ...buttonStyle, background: 'var(--tertiary)' }}
            >
              Replay tap #{nextTapCounter - 1} (expect rejected)
            </button>
          )}
        </>
      )}
    </div>
  )
}

const selectStyle = {
  padding: '12px',
  borderRadius: '8px',
  background: 'var(--bg-surface)',
  color: 'var(--on-surface)',
  border: '1px solid var(--outline)',
  width: '100%',
}

const buttonStyle = {
  padding: '12px',
  borderRadius: '8px',
  background: 'var(--primary)',
  color: 'var(--bg-floor)',
  fontWeight: 700,
  border: 'none',
}
