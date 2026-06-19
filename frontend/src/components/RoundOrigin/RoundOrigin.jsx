import { useState } from 'react'
import ChooserRound from '../ChooserRound/ChooserRound'
import FingerChooser from '../FingerChooser/FingerChooser'
import TriviaOriginRound from '../Trivia/TriviaOriginRound'
import { pickHotSeat } from '../../services/patronApi'

// Round-type cadence stand-in until the weighted theme engine exists. Trivia is
// NOT something anyone taps to start -- the game surfaces it automatically
// between Chooser rounds. CADENCE === 'every3' fires Trivia on every 3rd round
// (easy to reach while testing); switch to 'random' for a ~50/50 mix once the
// flow is signed off. (gamespec: "draws from a weighted pool of round types".)
const CADENCE = 'every3' // 'every3' | 'random'

function decideRoundType(roundNumber) {
  if (CADENCE === 'random') return Math.random() < 0.5 ? 'trivia' : 'chooser'
  return roundNumber % 3 === 0 ? 'trivia' : 'chooser'
}

// gamespec.md Step 5 — Round Flow, on the session-origin phone. Everyone else's
// phone runs SessionParticipant. The origin's round engine decides each round's
// type automatically (no manual picker): a Chooser round runs the finger picker
// -> ChooserRound; a Trivia round runs TriviaOriginRound, which auto-enters with
// a brief "get ready" splash on all phones. Finishing a round advances to the
// next one.
export default function RoundOrigin({ venueName, sessionId, phoneId, tableId, adultsOnly, playerCount = 2 }) {
  const [roundNumber, setRoundNumber] = useState(1)
  const [roundType, setRoundType] = useState(() => decideRoundType(1))
  const [hotSeat, setHotSeat] = useState(null)
  const [error, setError] = useState(null)
  const [picking, setPicking] = useState(false)
  // Cross-round memory of recent winners' screen positions: FingerChooser
  // unmounts each round, so the picker can't remember on its own.
  const [recentWinners, setRecentWinners] = useState([])

  const advanceRound = () => {
    const next = roundNumber + 1
    setHotSeat(null)
    setRoundNumber(next)
    setRoundType(decideRoundType(next))
  }

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

  // --- Trivia round (auto-entered) ---
  if (roundType === 'trivia') {
    return (
      <TriviaOriginRound
        sessionId={sessionId}
        phoneId={phoneId}
        tableId={tableId}
        onDone={advanceRound}
      />
    )
  }

  // --- Chooser round ---
  if (hotSeat) {
    return (
      <ChooserRound
        sessionId={sessionId}
        phoneId={phoneId}
        hotSeat={hotSeat}
        adultsOnly={adultsOnly}
        onRoundComplete={advanceRound}
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
        // steers away from so selection spreads around the table.
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
