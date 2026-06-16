import { useEffect, useRef, useState } from 'react'
import { claimHost, pollLobby, startGame } from '../../services/patronApi'

const POLL_MS = 2000

// gamespec.md Player Flow Step 2 + Step 4 — shown when the first phone taps
// and no session exists yet (Lobby), and once a host is chosen, doubles as
// the Setup screen (player count/names, optional group label, Adults Only
// toggle). Polls instead of using realtime infra (none wired up yet) so
// every phone in the lobby sees phone count / host / start updates within
// ~2s of each other.
export default function Lobby({ venueName, lobbyId, phoneId, adultsOnlyAllowed, onGameStarted }) {
  const [state, setState] = useState(null)
  const [error, setError] = useState(null)
  const [playerCount, setPlayerCount] = useState(2)
  const [namesText, setNamesText] = useState('')
  const [groupLabel, setGroupLabel] = useState('')
  const [adultsOnly, setAdultsOnly] = useState(false)
  const [starting, setStarting] = useState(false)
  const startedRef = useRef(false)

  useEffect(() => {
    let cancelled = false
    const tick = async () => {
      try {
        const result = await pollLobby(lobbyId)
        if (cancelled) return
        setState(result)
        if (result.status === 'converted' && !startedRef.current) {
          startedRef.current = true
          onGameStarted(result)
        }
      } catch (e) {
        if (!cancelled) setError(e.message)
      }
    }
    tick()
    const id = setInterval(tick, POLL_MS)
    return () => { cancelled = true; clearInterval(id) }
  }, [lobbyId, onGameStarted])

  const isHost = state?.host_phone_id === phoneId
  const noHostYet = state && !state.host_phone_id

  const handleClaimHost = async () => {
    try {
      await claimHost(lobbyId, phoneId)
    } catch (e) {
      setError(e.message)
    }
  }

  const handleStart = async () => {
    setStarting(true)
    setError(null)
    try {
      const names = namesText.trim()
        ? namesText.split(',').map((n) => n.trim()).filter(Boolean)
        : null
      await startGame(lobbyId, {
        phoneId,
        playerCount,
        playerNames: names,
        adultsOnly: adultsOnlyAllowed && adultsOnly,
        groupLabel: groupLabel.trim() || null,
      })
      // onGameStarted fires from the next poll tick once status flips to
      // 'converted' — keeps host and joiners on the exact same trigger.
    } catch (e) {
      setError(e.message)
      setStarting(false)
    }
  }

  return (
    <div style={containerStyle}>
      <h1 style={{ fontFamily: 'var(--font-headline)', fontSize: '24px', textAlign: 'center' }}>
        Welcome to {venueName} 🍺
      </h1>

      {!state ? (
        <p style={{ textAlign: 'center', color: 'var(--on-surface-dim)' }}>Connecting…</p>
      ) : isHost ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <p style={{ textAlign: 'center', color: 'var(--on-surface-dim)' }}>
            {state.phone_count} phone{state.phone_count === 1 ? '' : 's'} connected
          </p>
          <label style={labelStyle}>
            Players ({playerCount})
            <input
              type="range" min="2" max="8" value={playerCount}
              onChange={(e) => setPlayerCount(Number(e.target.value))}
              style={{ width: '100%' }}
            />
          </label>
          <label style={labelStyle}>
            Names (optional, comma-separated)
            <input
              type="text" value={namesText} onChange={(e) => setNamesText(e.target.value)}
              placeholder="Kaushik, Sarah, James" style={inputStyle}
            />
          </label>
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
              Adults Only 🔥
            </label>
          )}
          <button onClick={handleStart} disabled={starting} style={buttonStyle}>
            {starting ? 'Starting…' : 'Start Game'}
          </button>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', alignItems: 'center' }}>
          <p style={{ color: 'var(--on-surface-dim)' }}>
            Everyone tap the tag, then one of you set up the game
          </p>
          <p style={{ fontFamily: 'var(--font-mono)', fontSize: '14px' }}>
            [{state.phone_count} phone{state.phone_count === 1 ? '' : 's'} connected]
          </p>
          {noHostYet ? (
            <button onClick={handleClaimHost} style={buttonStyle}>Set up the game</button>
          ) : (
            <p style={{ color: 'var(--on-surface-dim)' }}>hang tight 🍺</p>
          )}
        </div>
      )}

      {error && (
        <p style={{ color: 'var(--tertiary)', fontFamily: 'var(--font-mono)', fontSize: '13px', textAlign: 'center' }}>
          {error}
        </p>
      )}
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
