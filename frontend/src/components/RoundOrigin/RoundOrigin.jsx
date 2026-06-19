import { useState } from 'react'
import ChooserRound from '../ChooserRound/ChooserRound'
import FingerChooser from '../FingerChooser/FingerChooser'
import TriviaOriginRound from '../Trivia/TriviaOriginRound'
import { pickHotSeat } from '../../services/patronApi'

// Round-type cadence stand-in until the weighted theme engine exists. Trivia is
// NOT something anyone taps to start -- the game surfaces it automatically
// between Chooser rounds. TRIVIA_EVERY fires Trivia on every Nth round: 3 means
// rounds 1 & 2 are Chooser and round 3 is Trivia (then 4 & 5 Chooser, 6 Trivia,
// ...). Set to 0/null for a random ~50/50 mix once the flow is signed off --
// NOTE that random mode must store the per-round decision in state rather than
// computing it inline below, or it would re-roll on every render.
// (gamespec: "draws from a weighted pool of round types".)
const TRIVIA_EVERY = 3

function decideRoundType(roundNumber) {
  if (!TRIVIA_EVERY) return Math.random() < 0.5 ? 'trivia' : 'chooser'
  return roundNumber % TRIVIA_EVERY === 0 ? 'trivia' : 'chooser'
}

// gamespec.md Step 5 — Round Flow, on the session-origin phone. Everyone else's
// phone runs SessionParticipant. The origin's round engine decides each round's
// type automatically (no manual picker): a Chooser round runs the finger picker
// -> ChooserRound; a Trivia round runs TriviaOriginRound, which auto-enters with
// a brief "get ready" splash on all phones. Finishing a round advances to the
// next one. The round counter is persisted per session so a re-tap or reload of
// the table phone resumes the right round (NFC re-taps are normal mid-session).
export default function RoundOrigin({ venueName, sessionId, phoneId, tableId, adultsOnly, playerCount = 2 }) {
  const roundKey = `mh_round_${sessionId}`
  const [roundNumber, setRoundNumber] = useState(() => {
    const saved = Number(localStorage.getItem(roundKey))
    return saved >= 1 ? saved : 1
  })
  const [hotSeat, setHotSeat] = useState(null)
  const [error, setError] = useState(null)
  const [picking, setPicking] = useState(false)
  const [recentWinners, setRecentWinners] = useState([])

  // Derive the type directly from the (persisted) round number so it can't
  // drift out of sync with the counter across re-renders/remounts.
  const roundType = decideRoundType(roundNumber)

  const advanceRound = () => {
    const next = roundNumber + 1
    localStorage.setItem(roundKey, String(next))
    setHotSeat(null)
    setRoundNumber(next)
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

  const nextIsTrivia = decideRoundType(roundNumber + 1) === 'trivia'
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
      {/* Round indicator — also confirms the table phone is on the latest build. */}
      <p style={roundBadgeStyle}>
        Round {roundNumber} · Chooser{nextIsTrivia ? '  ·  🧠 Trivia next' : ''}
      </p>
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

const roundBadgeStyle = {
  position: 'fixed',
  top: 'calc(env(safe-area-inset-top, 0px) + 22px)',
  left: 'var(--safe-margin)',
  margin: 0,
  fontSize: '11px',
  color: 'var(--on-surface-dim)',
  fontFamily: 'var(--font-mono)',
  zIndex: 30,
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
