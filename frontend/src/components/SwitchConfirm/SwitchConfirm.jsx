import { useState } from 'react'
import { leaveSession } from '../../services/patronApi'

// Single active seat: shown when a phone that's already in a live game taps a
// DIFFERENT table. Rather than silently joining a second game, the patron chooses
// to switch (leave the old game first) or keep playing where they are.
export default function SwitchConfirm({ thisTableNumber, other, phoneId }) {
  const [busy, setBusy] = useState(false)

  const handleSwitch = async () => {
    if (busy) return
    setBusy(true)
    try {
      await leaveSession(other.session_id, phoneId)
    } catch {
      // already gone / ended — proceed either way
    }
    // Plain re-tap of THIS table (phone_id resolves from localStorage). The phone
    // is no longer in the old session, so it now lands in this table's lobby/join.
    window.location.href = window.location.pathname
  }

  const handleStay = () => {
    // Back to the game we're still in.
    window.location.href = `/${other.venue_slug}/${other.table_number}`
  }

  return (
    <div style={screenStyle}>
      <p style={{ fontSize: '40px', margin: 0 }}>&#x1F504;</p>
      <h1 style={headlineStyle}>Switch tables?</h1>
      <p style={dimMono}>
        You&rsquo;re still in a game at {other.venue_name} &mdash; Table {other.table_number}.
      </p>
      <p style={dimMono}>
        Joining Table {thisTableNumber} will leave that game.
      </p>
      <button onClick={handleSwitch} disabled={busy} style={primaryButton}>
        {busy ? 'Switching…' : `Switch to Table ${thisTableNumber}`}
      </button>
      <button onClick={handleStay} disabled={busy} style={secondaryButton}>
        Keep playing at Table {other.table_number}
      </button>
    </div>
  )
}

const screenStyle = {
  minHeight: '100dvh',
  background: 'var(--bg-floor)',
  color: 'var(--on-surface)',
  fontFamily: 'var(--font-body)',
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  gap: '16px',
  padding: '24px',
  textAlign: 'center',
}

const headlineStyle = { fontFamily: 'var(--font-headline)', fontSize: '26px', margin: 0 }
const dimMono = { fontFamily: 'var(--font-mono)', fontSize: '13px', color: 'var(--on-surface-dim)', margin: 0 }

const primaryButton = {
  padding: '16px',
  borderRadius: '10px',
  background: 'var(--primary)',
  color: 'var(--bg-floor)',
  fontWeight: 700,
  fontSize: '16px',
  border: 'none',
  width: '100%',
  maxWidth: '320px',
  cursor: 'pointer',
}

const secondaryButton = {
  ...primaryButton,
  background: 'transparent',
  color: 'var(--on-surface)',
  border: '1px solid var(--outline)',
  fontWeight: 600,
}
