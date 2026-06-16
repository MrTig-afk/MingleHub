import { useState } from 'react'
import { devLogin, fetchTables, pairTag } from '../../services/dashboardApi'

// Must match the clerk_user_id values seeded by scripts/seed_platform.py.
// Only venue_owner accounts — pairing is owner-only per gamespec.md.
const DEV_OWNERS = [
  { id: 'dev_owner_a', label: "Owner A — The Lion's Den" },
  { id: 'dev_owner_b', label: 'Owner B — The Brew House' },
]

const supportsWebNfc = typeof window !== 'undefined' && 'NDEFReader' in window

export default function PairTags() {
  const [clerkUserId, setClerkUserId] = useState(DEV_OWNERS[0].id)
  const [token, setToken] = useState(null)
  const [tables, setTables] = useState([])
  const [tableNumber, setTableNumber] = useState(1)
  const [status, setStatus] = useState('idle') // idle | loading | error
  const [error, setError] = useState(null)
  const [lastPaired, setLastPaired] = useState(null)

  const signInAndLoadTables = async () => {
    setStatus('loading')
    setError(null)
    try {
      const { token: newToken } = await devLogin(clerkUserId)
      setToken(newToken)
      const loadedTables = await fetchTables(newToken)
      setTables(loadedTables)
      setTableNumber(loadedTables[0]?.table_number ?? 1)
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
      setTables(await fetchTables(token))
      setStatus('idle')
    } catch (e) {
      setError(e.message)
      setStatus('error')
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
