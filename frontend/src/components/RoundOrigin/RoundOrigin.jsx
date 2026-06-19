import { useState } from 'react'
import ChooserRound from '../ChooserRound/ChooserRound'
import FingerChooser from '../FingerChooser/FingerChooser'
import { pickHotSeat } from '../../services/patronApi'

// gamespec.md Step 5 — Round Flow, Finger Picker section. Renders only on
// the session-origin phone (the one that started the game) — everyone
// else's screen shows a "watch the table phone" placeholder instead.
//
// After the finger picker resolves a hot-seat player, renders ChooserRound
// which draws a card and handles Complete/Skip/Redraw. When ChooserRound
// calls onRoundComplete(), hotSeat is cleared back to null and the finger
// picker resets for the next round.
export default function RoundOrigin({ venueName, sessionId, phoneId, adultsOnly, playerCount = 2 }) {
  const [hotSeat, setHotSeat] = useState(null)
  const [error, setError] = useState(null)
  const [picking, setPicking] = useState(false)
  // Cross-round memory of recent winners' screen positions. Lives here (not in
  // the picker) because FingerChooser unmounts/remounts every round when
  // ChooserRound takes over — so the picker can't remember across rounds on its
  // own. The picker weights each pick away from these spots so the selection
  // spreads around the table instead of clustering on one person/area.
  const [recentWinners, setRecentWinners] = useState([])

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
      <ChooserRound
        sessionId={sessionId}
        phoneId={phoneId}
        hotSeat={hotSeat}
        adultsOnly={adultsOnly}
        onRoundComplete={() => setHotSeat(null)}
      />
    )
  }

  return (
    <div style={{ position: 'relative', minHeight: '100dvh' }}>
      <FingerChooser
        onCardDraw={handleChosen}
        requiredFingers={playerCount}
        recentWinnerPositions={recentWinners}
        // Keep only the last 3 winning spots — a sliding window the picker
        // steers away from, so it avoids the recent areas without permanently
        // ruling anyone out over a long session.
        onWinnerChosen={(pos) => setRecentWinners((prev) => [...prev, pos].slice(-3))}
        hideBack
      />
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


