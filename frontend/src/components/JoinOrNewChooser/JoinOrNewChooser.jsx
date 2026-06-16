import { useState } from 'react'
import { joinSession, startNewGroup } from '../../services/patronApi'

const MAX_GROUPS = 3

// gamespec.md Player Flow Step 3 — shown when a phone taps a table that
// already has 1-3 active groups. table_full (3 groups) hides "Start a new
// group" entirely, matching "This table is full — join one of these groups".
export default function JoinOrNewChooser({ tableNumber, tableId, phoneId, groups, onJoined, onNewGroup }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const isFull = groups.length >= MAX_GROUPS

  const handleJoin = async (sessionId) => {
    setBusy(true)
    setError(null)
    try {
      const result = await joinSession(sessionId, phoneId)
      onJoined({ ...result, sessionId })
    } catch (e) {
      setError(e.message)
      setBusy(false)
    }
  }

  const handleNewGroup = async () => {
    setBusy(true)
    setError(null)
    try {
      const lobby = await startNewGroup(tableId, phoneId)
      onNewGroup(lobby)
    } catch (e) {
      setError(e.message)
      setBusy(false)
    }
  }

  return (
    <div style={containerStyle}>
      <h1 style={{ fontFamily: 'var(--font-headline)', fontSize: '22px', textAlign: 'center' }}>
        Table {tableNumber} already has a game running
      </h1>

      {isFull && (
        <p style={{ textAlign: 'center', color: 'var(--on-surface-dim)' }}>
          This table is full — join one of these groups
        </p>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
        {groups.map((g) => (
          <div key={g.session_id} style={groupCardStyle}>
            <div>
              <div style={{ fontWeight: 700 }}>{g.group_label}</div>
              <div style={{ fontSize: '12px', color: 'var(--on-surface-dim)' }}>{g.player_count} players</div>
            </div>
            <button onClick={() => handleJoin(g.session_id)} disabled={busy} style={joinButtonStyle}>
              Join their game
            </button>
          </div>
        ))}
      </div>

      {!isFull && (
        <button onClick={handleNewGroup} disabled={busy} style={newGroupButtonStyle}>
          Start a new group at this table
        </button>
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
  gap: '20px',
  padding: '24px',
  maxWidth: '420px',
  margin: '0 auto',
}

const groupCardStyle = {
  background: 'var(--glass-bg)',
  border: '1px solid var(--glass-border)',
  borderRadius: '12px',
  padding: '14px 16px',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  gap: '12px',
}

const joinButtonStyle = {
  padding: '10px 16px',
  borderRadius: '8px',
  background: 'var(--primary)',
  color: 'var(--bg-floor)',
  fontWeight: 700,
  border: 'none',
  whiteSpace: 'nowrap',
}

const newGroupButtonStyle = {
  padding: '14px',
  borderRadius: '8px',
  background: 'var(--bg-surface)',
  color: 'var(--on-surface)',
  border: '1px solid var(--outline)',
  fontWeight: 700,
}
