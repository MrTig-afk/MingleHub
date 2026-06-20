import { useCallback, useEffect, useRef, useState } from 'react'
import ChooserRound from '../ChooserRound/ChooserRound'
import FingerChooser from '../FingerChooser/FingerChooser'
import TriviaOriginRound from '../Trivia/TriviaOriginRound'
import Toast from '../Toast'
import useSessionChannel from '../../hooks/useSessionChannel'
import { fetchLeaderboard, pickHotSeat } from '../../services/patronApi'

// Round-type cadence stand-in until the weighted theme engine exists. Trivia is
// NOT something anyone taps to start -- the game surfaces it automatically.
// TRIVIA_EVERY fires Trivia on every Nth round: 1 = EVERY round is Trivia (so the
// very first round, immediately after Start, is Trivia -- a deliberate diagnostic
// setting). Use 3 for the real rhythm (rounds 1 & 2 Chooser, round 3 Trivia), or
// 0/null for a random ~50/50 mix -- NOTE random mode must store the per-round
// decision in state rather than computing it inline below.
const TRIVIA_EVERY = 1

function decideRoundType(roundNumber) {
  if (!TRIVIA_EVERY) return Math.random() < 0.5 ? 'trivia' : 'chooser'
  return roundNumber % TRIVIA_EVERY === 0 ? 'trivia' : 'chooser'
}

// gamespec.md Step 5 — Round Flow, on the session-origin (table) phone. The round
// engine picks each round's type automatically. Chooser runs the finger picker;
// Trivia runs TriviaOriginRound. The finger picker waits for the number of ACTIVE
// players (refreshed when someone leaves) so a Chooser round still starts after a
// leave -- otherwise it would wait for more fingers than are at the table. The
// host also gets a toast whenever a player leaves.
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
  const [activeCount, setActiveCount] = useState(playerCount)
  const [toast, setToast] = useState(null)
  const toastTimer = useRef(null)

  const roundType = decideRoundType(roundNumber)

  const showToast = useCallback((msg) => {
    setToast(msg)
    clearTimeout(toastTimer.current)
    toastTimer.current = setTimeout(() => setToast(null), 3500)
  }, [])

  // Keep requiredFingers in sync with how many players are actually still in.
  const refreshActiveCount = useCallback(async () => {
    try {
      const data = await fetchLeaderboard(sessionId)
      const active = (data.leaderboard || []).filter((r) => !r.left_early).length
      if (active >= 2) setActiveCount(active)
    } catch {
      // keep the current count on a transient failure
    }
  }, [sessionId])

  // Initial active-player count (await precedes setState — the state update is
  // not synchronous within the effect).
  useEffect(() => {
    let cancelled = false
    fetchLeaderboard(sessionId)
      .then((data) => {
        if (cancelled) return
        const active = (data.leaderboard || []).filter((r) => !r.left_early).length
        if (active >= 2) setActiveCount(active)
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [sessionId])

  useSessionChannel(tableId, phoneId, (event, payload) => {
    if (event !== 'player_left') return
    const name = payload?.name || 'A player'
    // Defer the state updates out of the broadcast handler's synchronous path.
    queueMicrotask(() => { showToast(`${name} left the game`); refreshActiveCount() })
  })

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

  const renderRound = () => {
    if (roundType === 'trivia') {
      return (
        <TriviaOriginRound sessionId={sessionId} phoneId={phoneId} tableId={tableId} onDone={advanceRound} />
      )
    }
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
          requiredFingers={activeCount}
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
        <p style={roundBadgeStyle}>
          Round {roundNumber} · Chooser{nextIsTrivia ? '  ·  🧠 Trivia next' : ''}
        </p>
        <p style={venueLabelStyle}>{venueName}</p>
      </div>
    )
  }

  return (
    <>
      {renderRound()}
      <Toast message={toast} />
    </>
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
