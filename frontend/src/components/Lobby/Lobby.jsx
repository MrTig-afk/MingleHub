import { useEffect, useRef, useState } from 'react'
import { claimHost, pollLobby, setLobbyName, startGame } from '../../services/patronApi'
import useSessionChannel from '../../hooks/useSessionChannel'

const POLL_MS = 2000

// gamespec.md Player Flow Step 2 + Step 4 — shown when the first phone taps
// and no session exists yet (Lobby). After the host claims, doubles as the
// Setup screen: shows the roster of names from lobby phones, optional group
// label, Adults Only toggle, and Start. Every phone first enters their name.
export default function Lobby({ venueName, lobbyId, phoneId, tableId, adultsOnlyAllowed, onGameStarted }) {
  const [state, setState] = useState(null)
  const [error, setError] = useState(null)
  const [myName, setMyName] = useState('')
  const [hasSetName, setHasSetName] = useState(false)
  const [submittingName, setSubmittingName] = useState(false)
  const [groupLabel, setGroupLabel] = useState('')
  const [adultsOnly, setAdultsOnly] = useState(false)
  const [starting, setStarting] = useState(false)
  const startedRef = useRef(false)
  const [hasJoined, setHasJoined] = useState(false)
  // tickRef holds the current poll function so realtime events can trigger
  // an immediate poll without re-subscribing.
  const tickRef = useRef(null)

  useEffect(() => {
    let cancelled = false
    const tick = async () => {
      try {
        const result = await pollLobby(lobbyId, phoneId)
        if (cancelled) return
        setState(result)
        // Re-tap / page reload: detect own name already stored in lobby.
        if (!hasSetName && result.phones) {
          const mine = result.phones.find((p) => p.is_self)
          if (mine?.name) setHasSetName(true)
        }
        if (result.status === 'converted' && !startedRef.current) {
          startedRef.current = true
          onGameStarted(result)
        }
      } catch (e) {
        if (!cancelled) setError(e.message)
      }
    }
    tickRef.current = tick
    tick()
    const id = setInterval(tick, POLL_MS)
    return () => {
      cancelled = true
      clearInterval(id)
      tickRef.current = null
    }
  }, [lobbyId, onGameStarted, phoneId, hasSetName])

  // Realtime: when a lobby_update event arrives, immediately trigger a poll
  // to get the latest canonical state. The poll remains the primary source
  // of truth — realtime only accelerates delivery.
  useSessionChannel(tableId, phoneId, (event) => {
    if (event === 'lobby_update' && tickRef.current) {
      tickRef.current()
    }
  })

  const isHost = state?.is_host
  const noHostYet = state && state.host_name === null
  const phones = state?.phones ?? []
  const hostName = state?.host_name || null

  const handleSetName = async () => {
    const trimmed = myName.trim()
    if (!trimmed) return
    setSubmittingName(true)
    setError(null)
    try {
      await setLobbyName(lobbyId, phoneId, trimmed)
      setHasSetName(true)
    } catch (e) {
      setError(e.message)
    } finally {
      setSubmittingName(false)
    }
  }

  const handleClaimHost = async () => {
    try {
      await claimHost(lobbyId, phoneId)
    } catch (e) {
      setError(e.message)
    }
  }

  const handleStart = async () => {
    setError(null)
    setStarting(true)
    try {
      const startResult = await startGame(lobbyId, {
        phoneId,
        adultsOnly: adultsOnlyAllowed && adultsOnly,
        groupLabel: groupLabel.trim() || null,
      })
      // Fire onGameStarted immediately from the host's start response so
      // adultsOnly is available without an extra poll round-trip. The poll
      // path (status === 'converted') is kept as a fallback for non-host
      // phones; startedRef prevents double-firing if the poll also catches up.
      if (!startedRef.current) {
        startedRef.current = true
        onGameStarted({
          is_host: true,
          converted_session_id: startResult.session_id,
          adults_only: startResult.adults_only,
          player_count: startResult.player_count,
        })
      }
    } catch (e) {
      setError(e.message)
      setStarting(false)
    }
  }

  // Name entry screen — shown until this phone has submitted a name.
  if (!state || !hasSetName) {
    return (
      <div style={containerStyle}>
        <h1 style={{ fontFamily: 'var(--font-headline)', fontSize: '24px', textAlign: 'center' }}>
          Welcome to {venueName}
        </h1>
        <p style={{ textAlign: 'center', color: 'var(--on-surface-dim)' }}>
          {!state ? 'Connecting…' : "What's your name?"}
        </p>
        {state && (
          <>
            <input
              type="text"
              value={myName}
              onChange={(e) => setMyName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSetName()}
              placeholder="Your name"
              maxLength={60}
              style={inputStyle}
              autoFocus
            />
            <button
              onClick={handleSetName}
              disabled={submittingName || !myName.trim()}
              style={buttonStyle}
            >
              {submittingName ? 'Joining…' : 'Join'}
            </button>
          </>
        )}
        {error && <p style={errorStyle}>{error}</p>}
      </div>
    )
  }

  return (
    <div style={containerStyle}>
      <h1 style={{ fontFamily: 'var(--font-headline)', fontSize: '24px', textAlign: 'center' }}>
        Welcome to {venueName}
      </h1>

      {isHost ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div>
            <p style={{ fontSize: '13px', color: 'var(--on-surface-dim)', marginBottom: '8px' }}>Roster:</p>
            <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: '6px' }}>
              {phones.map((p, i) => (
                <li key={p.slot_id} style={{ fontFamily: 'var(--font-mono)', fontSize: '14px' }}>
                  {p.name || `Unnamed (Player ${i + 1})`}
                </li>
              ))}
            </ul>
            <p style={{ fontSize: '13px', color: 'var(--on-surface-dim)', marginTop: '8px' }}>
              [{phones.length} phone{phones.length === 1 ? '' : 's'} connected]
            </p>
          </div>

          <label style={labelStyle}>
            Group name (optional)
            <input
              type="text" value={groupLabel} onChange={(e) => setGroupLabel(e.target.value)}
              placeholder={`Table ${state.table_number ?? ''} Group 1`} style={inputStyle}
            />
          </label>

          {/* gamespec: Adults Only Toggle — default OFF, hidden entirely
              (not just disabled) unless the venue/table allow it. */}
          {adultsOnlyAllowed && (
            <label style={{ ...labelStyle, flexDirection: 'row', alignItems: 'center', gap: '10px' }}>
              <input
                type="checkbox" checked={adultsOnly}
                onChange={(e) => setAdultsOnly(e.target.checked)}
              />
              Adults Only
            </label>
          )}

          <button
            onClick={handleStart}
            disabled={starting || phones.length < 2}
            style={buttonStyle}
          >
            {starting ? 'Starting…' : 'Start Game'}
          </button>
          {phones.length < 2 && (
            <p style={{ textAlign: 'center', fontSize: '13px', color: 'var(--on-surface-dim)' }}>
              Waiting for at least 2 phones to join…
            </p>
          )}
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', alignItems: 'center' }}>
          {noHostYet ? (
            <>
              <div>
                <p style={{ fontSize: '13px', color: 'var(--on-surface-dim)', marginBottom: '8px', textAlign: 'center' }}>
                  Who's here:
                </p>
                <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: '4px', alignItems: 'center' }}>
                  {phones.map((p, i) => (
                    <li key={p.slot_id} style={{ fontFamily: 'var(--font-mono)', fontSize: '14px' }}>
                      {p.name || `Unnamed (Player ${i + 1})`}
                      {p.is_self ? ' (you)' : ''}
                    </li>
                  ))}
                </ul>
              </div>
              <p style={{ fontFamily: 'var(--font-mono)', fontSize: '14px' }}>
                [{phones.length} phone{phones.length === 1 ? '' : 's'} connected]
              </p>
              <button onClick={handleClaimHost} style={buttonStyle}>Start the game</button>
            </>
          ) : hasJoined ? (
            <p style={{ color: 'var(--on-surface-dim)', textAlign: 'center' }}>
              You&apos;re in &mdash; waiting for {hostName} to start
            </p>
          ) : (
            <>
              <p style={{ color: 'var(--on-surface-dim)', textAlign: 'center' }}>
                {hostName} is setting up the game
              </p>
              <button onClick={() => setHasJoined(true)} style={buttonStyle}>Join the game</button>
            </>
          )}
        </div>
      )}

      {error && <p style={errorStyle}>{error}</p>}
    </div>
  )
}

const containerStyle = {
  minHeight: '100dvh',
  background: 'var(--bg-floor)',
  color: 'var(--on-surface)',
  fontFamily: 'var(--font-body)',
  display: 'flex',
  flexDirection: 'column',
  justifyContent: 'center',
  gap: '24px',
  padding: '24px',
  maxWidth: '420px',
  margin: '0 auto',
}

const labelStyle = { fontSize: '13px', color: 'var(--on-surface-dim)', display: 'flex', flexDirection: 'column', gap: '6px' }

const inputStyle = {
  padding: '12px',
  borderRadius: '8px',
  background: 'var(--bg-surface)',
  color: 'var(--on-surface)',
  border: '1px solid var(--outline)',
}

const buttonStyle = {
  padding: '14px',
  borderRadius: '8px',
  background: 'var(--primary)',
  color: 'var(--bg-floor)',
  fontWeight: 700,
  border: 'none',
}

const errorStyle = {
  color: 'var(--tertiary)',
  fontFamily: 'var(--font-mono)',
  fontSize: '13px',
  textAlign: 'center',
}
