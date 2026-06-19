import { useState } from 'react'
import ChooserRound from '../ChooserRound/ChooserRound'
import FingerChooser from '../FingerChooser/FingerChooser'
import TriviaOriginRound from '../Trivia/TriviaOriginRound'
import { pickHotSeat } from '../../services/patronApi'

// gamespec.md Step 5 — Round Flow, on the session-origin phone (the one that
// started the game). Everyone else's phone runs SessionParticipant instead.
//
// Entry is a between-rounds round-type picker (gamespec: the game draws from a
// weighted theme pool; until the theme engine exists this manual Chooser/Trivia
// picker is the stand-in). Picking Chooser runs the finger picker -> ChooserRound;
// picking Trivia runs TriviaOriginRound. Either way, finishing a round returns
// here to the picker for the next round.
export default function RoundOrigin({ venueName, sessionId, phoneId, tableId, adultsOnly, playerCount = 2 }) {
  const [mode, setMode] = useState(null) // null = picker | 'chooser' | 'trivia'
  const [hotSeat, setHotSeat] = useState(null)
  const [error, setError] = useState(null)
  const [picking, setPicking] = useState(false)
  // Cross-round memory of recent winners' screen positions (see original note):
  // FingerChooser unmounts each round, so the picker can't remember on its own.
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

  const backToPicker = () => {
    setHotSeat(null)
    setMode(null)
  }

  // --- Trivia ---
  if (mode === 'trivia') {
    return (
      <TriviaOriginRound
        sessionId={sessionId}
        phoneId={phoneId}
        tableId={tableId}
        onDone={backToPicker}
      />
    )
  }

  // --- Chooser ---
  if (mode === 'chooser') {
    if (hotSeat) {
      return (
        <ChooserRound
          sessionId={sessionId}
          phoneId={phoneId}
          hotSeat={hotSeat}
          adultsOnly={adultsOnly}
          onRoundComplete={backToPicker}
        />
      )
    }
    return (
      <div style={{ position: 'relative', minHeight: '100dvh' }}>
        <FingerChooser
          onCardDraw={handleChosen}
          requiredFingers={playerCount}
          recentWinnerPositions={recentWinners}
          onWinnerChosen={(pos) => setRecentWinners((prev) => [...prev, pos].slice(-3))}
          hideBack
        />
        {(picking || error) && (
          <div style={bannerStyle}>
            {picking && <p style={{ margin: 0 }}>Picking…</p>}
            {error && <p style={{ margin: 0, color: 'var(--tertiary)' }}>{error}</p>}
          </div>
        )}
        <button onClick={() => setMode(null)} style={cancelStyle}>← Back</button>
        <p style={venueLabelStyle}>{venueName}</p>
      </div>
    )
  }

  // --- Round-type picker (between rounds) ---
  return (
    <div style={pickerStyle}>
      <p style={venueLabelStyle}>{venueName}</p>
      <h1 style={{ fontFamily: 'var(--font-headline)', fontSize: '26px', margin: 0 }}>
        Pick the next round
      </h1>
      <p style={{ fontFamily: 'var(--font-mono)', fontSize: '13px', color: 'var(--on-surface-dim)', margin: 0 }}>
        Your call on the table phone
      </p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', width: '100%', maxWidth: '320px' }}>
        <button onClick={() => setMode('chooser')} style={choiceButton}>
          <span style={{ fontSize: '22px' }}>🎴</span> Chooser
          <span style={choiceHint}>Cards · one player in the hot seat</span>
        </button>
        <button onClick={() => setMode('trivia')} style={choiceButton}>
          <span style={{ fontSize: '22px' }}>🧠</span> Trivia
          <span style={choiceHint}>Everyone answers on their own phone</span>
        </button>
      </div>
    </div>
  )
}

const pickerStyle = {
  minHeight: '100dvh',
  background: 'var(--bg-floor)',
  color: 'var(--on-surface)',
  fontFamily: 'var(--font-body)',
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  gap: '18px',
  padding: '24px',
  textAlign: 'center',
}

const choiceButton = {
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  gap: '4px',
  padding: '20px',
  borderRadius: '14px',
  background: 'var(--glass-bg)',
  border: '1px solid var(--glass-border)',
  color: 'var(--on-surface)',
  fontFamily: 'var(--font-headline)',
  fontSize: '18px',
  fontWeight: 700,
  cursor: 'pointer',
}

const choiceHint = {
  fontFamily: 'var(--font-mono)',
  fontSize: '11px',
  fontWeight: 400,
  color: 'var(--on-surface-dim)',
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

const cancelStyle = {
  position: 'fixed',
  top: 'calc(env(safe-area-inset-top, 0px) + 18px)',
  left: 'var(--safe-margin)',
  background: 'transparent',
  color: 'var(--on-surface-dim)',
  border: 'none',
  fontSize: '13px',
  fontFamily: 'var(--font-mono)',
  cursor: 'pointer',
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
