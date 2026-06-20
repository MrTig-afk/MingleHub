import { useCallback, useEffect, useRef, useState } from 'react'
import ChooserRound from '../ChooserRound/ChooserRound'
import FingerChooser from '../FingerChooser/FingerChooser'
import RouletteRound from '../Roulette/RouletteRound'
import TriviaOriginRound from '../Trivia/TriviaOriginRound'
import Toast from '../Toast'
import useSessionChannel from '../../hooks/useSessionChannel'
import { fetchLeaderboard, pickHotSeat } from '../../services/patronApi'

// Round cadence: Chooser -> Roulette -> Trivia -> Chooser -> Roulette -> Trivia …
// Falls back to Chooser when a round type needs >= 2 active players and there
// aren't enough at the table (Roulette and Trivia both require at least 2).
const ROUND_CADENCE = ['chooser', 'roulette', 'trivia']

function decideRoundType(roundNumber, activeCount) {
  const type = ROUND_CADENCE[(roundNumber - 1) % 3]
  if (type === 'roulette' && activeCount < 2) return 'chooser'
  if (type === 'trivia' && activeCount < 2) return 'chooser'
  return type
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

  const roundType = decideRoundType(roundNumber, activeCount)

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
    if (event !== 'player_left' && event !== 'player_rejoined') return
    const name = payload?.name || 'A player'
    const msg = event === 'player_left' ? `${name} left the game` : `${name} rejoined`
    // Defer the state updates out of the broadcast handler's synchronous path.
    queueMicrotask(() => { showToast(msg); refreshActiveCount() })
  })

  // Stable within a round (roundNumber only changes when we advance), so passing
  // it as onDone doesn't change identity on unrelated re-renders (e.g. a toast).
  const advanceRound = useCallback(() => {
    setHotSeat(null)
    setRoundNumber((n) => {
      const next = n + 1
      localStorage.setItem(roundKey, String(next))
      return next
    })
  }, [roundKey])

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
    if (roundType === 'roulette') {
      // key by roundNumber so advancing Roulette -> Roulette remounts it
      // and starts a fresh round (same guard as TriviaOriginRound).
      return (
        <RouletteRound
          key={roundNumber}
          sessionId={sessionId}
          phoneId={phoneId}
          tableId={tableId}
          onDone={advanceRound}
        />
      )
    }
    if (roundType === 'trivia') {
      // key by roundNumber so advancing Trivia -> Trivia remounts it and actually
      // starts a fresh round (otherwise the same element just re-renders).
      return (
        <TriviaOriginRound
          key={roundNumber}
          sessionId={sessionId}
          phoneId={phoneId}
          tableId={tableId}
          onDone={advanceRound}
        />
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
          Round {roundNumber} · {roundType === 'roulette' ? 'Roulette' : roundType === 'trivia' ? 'Trivia' : 'Chooser'}
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
