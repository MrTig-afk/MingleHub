import { useState } from 'react'
import FingerChooser from '../FingerChooser/FingerChooser'
import { pickHotSeat } from '../../services/patronApi'

// gamespec.md Step 5 — Round Flow, Finger Picker section. Renders only on
// the session-origin phone (the one that started the game) — everyone
// else's screen shows a "watch the table phone" placeholder instead. The
// finger-placement UI/animation (FingerChooser/useMultiTouch) already
// existed from the FirstMove carryover; this component is the "session
// integration" layer — once a finger is chosen locally, it asks the
// server which real player that maps to and persists times_selected.
//
// SCOPE NOTE: this only covers selecting the Hot Seat player. What happens
// next (a Chooser card, Trivia question, or Roulette challenge) is later
// backlog work — for now "Pick again" just loops the picker so the
// mechanism is exercisable end-to-end.
export default function RoundOrigin({ venueName, sessionId, phoneId }) {
  const [hotSeat, setHotSeat] = useState(null)
  const [error, setError] = useState(null)
  const [picking, setPicking] = useState(false)

  const handleChosen = async () => {
    setPicking(true)
    setError(null)
    try {
      const result = await pickHotSeat(sessionId, phoneId)
      setHotSeat(result)
    } catch (e) {
      setError(e.message)
    } finally {
      setPicking(false)
    }
  }

  if (hotSeat) {
    return (
      <div style={overlayStyle}>
        <h1 className="headline" style={{ fontFamily: 'var(--font-headline)', fontSize: '28px' }}>
          🎯 {hotSeat.name} is in the Hot Seat!
        </h1>
        <p style={{ fontSize: '13px', color: 'var(--on-surface-dim)', fontFamily: 'var(--font-mono)' }}>
          Picked {hotSeat.times_selected} time{hotSeat.times_selected === 1 ? '' : 's'} tonight
        </p>
        <button onClick={() => setHotSeat(null)} style={buttonStyle}>
          Pick again
        </button>
      </div>
    )
  }

  return (
    <div style={{ position: 'relative', minHeight: '100dvh' }}>
      <FingerChooser onCardDraw={handleChosen} hideBack />
      {(picking || error) && (
        <div style={bannerStyle}>
          {picking && <p style={{ margin: 0 }}>Picking…</p>}
          {error && <p style={{ margin: 0, color: 'var(--tertiary)' }}>{error}</p>}
        </div>
      )}
      <p style={venueLabelStyle}>{venueName}</p>
    </div>
  )
}

const overlayStyle = {
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

const bannerStyle = {
  position: 'fixed',
  bottom: '24px',
  left: '50%',
  transform: 'translateX(-50%)',
  background: 'var(--glass-bg)',
  border: '1px solid var(--glass-border)',
  borderRadius: '8px',
  padding: '10px 16px',
  fontFamily: 'var(--font-mono)',
  fontSize: '13px',
  zIndex: 40,
}

const venueLabelStyle = {
  position: 'fixed',
  top: 'calc(env(safe-area-inset-top, 0px) + 22px)',
  right: 'var(--safe-margin)',
  margin: 0,
  fontSize: '11px',
  color: 'var(--on-surface-dim)',
  fontFamily: 'var(--font-mono)',
  zIndex: 30,
}

const buttonStyle = {
  padding: '14px 24px',
  borderRadius: '8px',
  background: 'var(--primary)',
  color: 'var(--bg-floor)',
  fontWeight: 700,
  border: 'none',
}
