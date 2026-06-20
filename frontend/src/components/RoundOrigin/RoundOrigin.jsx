import { useCallback, useEffect, useRef, useState } from 'react'
import ChooserRound from '../ChooserRound/ChooserRound'
import FingerChooser from '../FingerChooser/FingerChooser'
import Recap from '../Recap/Recap'
import RetapOverlay from '../Retap/RetapOverlay'
import RouletteRound from '../Roulette/RouletteRound'
import TriviaOriginRound from '../Trivia/TriviaOriginRound'
import Toast from '../Toast'
import useSessionChannel from '../../hooks/useSessionChannel'
import {
  endGame,
  fetchCurrentRound,
  fetchLeaderboard,
  leaveSession,
  pickHotSeat,
  rejoinSession,
} from '../../services/patronApi'

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
//
// initialRoundNumber: provided on resume (from server resume payload) so a
// reloaded or newly-promoted host continues at the correct round without
// relying on localStorage. When absent, fetched from GET /sessions/{id}/current-round.
export default function RoundOrigin({
  venueName, sessionId, phoneId, tableId, adultsOnly,
  playerCount = 2, initialRoundNumber,
}) {
  // roundNumber === null means "loading" — waiting on the server-authoritative value.
  const [roundNumber, setRoundNumber] = useState(() => {
    if (initialRoundNumber != null && initialRoundNumber >= 1) return initialRoundNumber
    return null // will fetch on mount
  })
  const [hotSeat, setHotSeat] = useState(null)
  const [error, setError] = useState(null)
  const [picking, setPicking] = useState(false)
  const [recentWinners, setRecentWinners] = useState([])
  const [activeCount, setActiveCount] = useState(playerCount)
  const [toast, setToast] = useState(null)
  const [gameEnded, setGameEnded] = useState(false)
  const [hostLeft, setHostLeft] = useState(false)
  const [retap, setRetap] = useState(null)
  const toastTimer = useRef(null)

  // roundNumber is null only when we need to fetch it from the server (no prop provided).
  // The effect deps include roundNumber so it re-evaluates after any set, but the early
  // return on `roundNumber !== null` means it only actually fetches once.
  useEffect(() => {
    if (roundNumber !== null) return
    let cancelled = false
    fetchCurrentRound(sessionId)
      .then((data) => {
        if (cancelled) return
        // current_round_number is the last CREATED round; the next round to play is +1.
        setRoundNumber(data.current_round_number + 1)
        // Set the real count even when it's 1 — a lone (e.g. just-migrated) host
        // must drop below 2 so the "Waiting for players" screen shows instead of a
        // finger picker that waits forever for a 2nd finger.
        if (data.active_count) setActiveCount(data.active_count)
      })
      .catch(() => {
        if (!cancelled) setRoundNumber(1) // safe fallback
      })
    return () => { cancelled = true }
  }, [sessionId, roundNumber])

  // roundType is only meaningful once roundNumber is loaded.
  const roundType = roundNumber !== null ? decideRoundType(roundNumber, activeCount) : null

  const showToast = useCallback((msg) => {
    setToast(msg)
    clearTimeout(toastTimer.current)
    toastTimer.current = setTimeout(() => setToast(null), 3500)
  }, [])

  // Keep requiredFingers in sync with how many players are actually still in.
  // Set the count even when it's 1 (see fetch-current-round note) so a lone host
  // falls through to the Waiting screen rather than a stuck finger picker.
  const refreshActiveCount = useCallback(async () => {
    try {
      const data = await fetchLeaderboard(sessionId)
      const active = (data.leaderboard || []).filter((r) => !r.left_early).length
      if (active) setActiveCount(active)
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
        if (active) setActiveCount(active)
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [sessionId])

  // While short-handed (a lone migrated host, or everyone but one left), poll the
  // active count so a re-join resumes the game even if the player_rejoined broadcast
  // was missed — realtime accelerates, the poll is the source of truth.
  useEffect(() => {
    if (activeCount >= 2) return
    const id = setInterval(refreshActiveCount, 2000)
    return () => clearInterval(id)
  }, [activeCount, refreshActiveCount])

  // Periodic poll (~10s) for retap state + lazy-end detection.
  // The mount-only fetch above seeds roundNumber; this poll is separate and
  // complementary -- it updates the retap overlay and catches lazy session ends.
  // StrictMode-safe: cancelled flag + clearInterval cleanup prevent leaks.
  useEffect(() => {
    if (gameEnded || hostLeft) return
    let cancelled = false
    const tick = async () => {
      try {
        const data = await fetchCurrentRound(sessionId)
        if (cancelled) return
        setRetap(data.retap || null)
        if (data.ended) {
          setGameEnded(true)
        }
        if (data.active_count) setActiveCount(data.active_count)
      } catch {
        // Transient failure -- keep current state
      }
    }
    tick() // immediate first tick
    const id = setInterval(tick, 10000)
    return () => { cancelled = true; clearInterval(id) }
  }, [sessionId, gameEnded, hostLeft])

  useSessionChannel(tableId, phoneId, (event, payload) => {
    // Multi-group safety: only react to game_ended for our own session.
    if (event === 'game_ended' && payload?.session_id === sessionId) {
      queueMicrotask(() => setGameEnded(true))
      return
    }
    if (event !== 'player_left' && event !== 'player_rejoined') return
    if (payload?.session_id !== sessionId) return // multi-group safety
    const name = payload?.name || 'A player'
    const msg = event === 'player_left' ? `${name} left the game` : `${name} rejoined`
    // Defer the state updates out of the broadcast handler's synchronous path.
    queueMicrotask(() => { showToast(msg); refreshActiveCount() })
  })

  // Stable within a round (roundNumber only changes when we advance).
  const advanceRound = useCallback(() => {
    setHotSeat(null)
    setRoundNumber((n) => n + 1)
  }, [])

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

  const handleEndGame = async () => {
    if (!window.confirm('End the game for everyone?')) return
    try {
      await endGame(sessionId, phoneId)
      setGameEnded(true)
    } catch (e) {
      setError(e.message)
    }
  }

  const handleHostLeave = async () => {
    if (!window.confirm('Leave the game? Another player will become the host.')) return
    try {
      const result = await leaveSession(sessionId, phoneId)
      if (result.ended) {
        setGameEnded(true)
      } else {
        setHostLeft(true)
      }
    } catch (e) {
      setError(e.message)
    }
  }

  const handleRejoin = async () => {
    try {
      await rejoinSession(sessionId, phoneId)
      // We're no longer the host (it migrated away). Re-resolve via a reload so
      // PatronLanding routes us back in as a participant (is_origin is now false)
      // instead of resuming a host UI whose actions would 403.
      window.location.reload()
    } catch (e) {
      setError(e.message)
    }
  }

  const renderRound = () => {
    if (gameEnded) {
      return <Recap sessionId={sessionId} venueName={venueName} />
    }
    if (hostLeft) {
      return (
        <div style={screenStyle}>
          <h1 style={headlineStyle}>You left the game</h1>
          <p style={dimMono}>Another player is now the host. Your score is saved.</p>
          <button onClick={handleRejoin} style={primaryButton}>Rejoin game</button>
          {error && <p style={errStyle}>{error}</p>}
        </div>
      )
    }
    // Loading: waiting on server-authoritative round number
    if (roundNumber === null) {
      return (
        <div style={screenStyle}>
          <p style={dimMono}>Loading…</p>
        </div>
      )
    }

    let roundContent
    if (roundType === 'roulette') {
      // key by roundNumber so advancing Roulette -> Roulette remounts it
      // and starts a fresh round (same guard as TriviaOriginRound).
      roundContent = (
        <RouletteRound
          key={roundNumber}
          sessionId={sessionId}
          phoneId={phoneId}
          tableId={tableId}
          onDone={advanceRound}
        />
      )
    } else if (roundType === 'trivia') {
      // key by roundNumber so advancing Trivia -> Trivia remounts it and actually
      // starts a fresh round (otherwise the same element just re-renders).
      roundContent = (
        <TriviaOriginRound
          key={roundNumber}
          sessionId={sessionId}
          phoneId={phoneId}
          tableId={tableId}
          onDone={advanceRound}
        />
      )
    } else if (hotSeat) {
      roundContent = (
        <ChooserRound
          sessionId={sessionId}
          phoneId={phoneId}
          hotSeat={hotSeat}
          adultsOnly={adultsOnly}
          onRoundComplete={advanceRound}
        />
      )
    } else if (activeCount < 2) {
      // Short-handed between rounds (e.g. a lone migrated host): the finger picker
      // needs 2 fingers, so wait for a (re)join instead of stranding the host. The
      // poll above resumes automatically once someone's back; End Game is in the overlay.
      roundContent = (
        <div style={screenStyle}>
          <h1 style={headlineStyle}>Waiting for players…</h1>
          <p style={dimMono}>The game needs at least 2 players. It’ll continue as soon as someone joins or re-taps back in.</p>
          <p style={venueLabelStyle}>{venueName}</p>
        </div>
      )
    } else {
      roundContent = (
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

    // Host controls (Leave + End Game) persist as a fixed overlay over all round types.
    // zIndex 60 keeps End Game tappable even when the retap overlay (zIndex 50) is showing.
    return (
      <>
        {roundContent}
        {retap && (retap.state === 'prompt' || retap.state === 'paused') && (
          <RetapOverlay state={retap.state} secondsLeft={retap.seconds_left} />
        )}
        <div style={hostControlsStyle}>
          <button onClick={handleHostLeave} style={hostLeaveButtonStyle}>Leave</button>
          <button onClick={handleEndGame} style={endGameButtonStyle}>End Game</button>
        </div>
        <Toast message={toast} />
      </>
    )
  }

  return (
    <>
      {renderRound()}
      {/* Toast rendered here too so it shows on the non-round screens (hostLeft, loading) */}
      {(gameEnded || hostLeft || roundNumber === null) && <Toast message={toast} />}
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

// Parent container positions both buttons; individual buttons don't need position:fixed.
// zIndex 60 keeps End Game tappable even when the retap overlay (zIndex 50) is showing.
const hostControlsStyle = {
  position: 'fixed',
  bottom: 'calc(env(safe-area-inset-bottom, 0px) + 16px)',
  right: 'var(--safe-margin)',
  display: 'flex',
  gap: '8px',
  zIndex: 60,
}

const hostLeaveButtonStyle = {
  background: 'transparent',
  color: 'var(--on-surface-dim)',
  border: '1px solid var(--outline)',
  borderRadius: '8px',
  padding: '10px 14px',
  fontSize: '13px',
  cursor: 'pointer',
}

const endGameButtonStyle = {
  background: 'transparent',
  color: 'var(--on-surface-dim)',
  border: '1px solid var(--outline)',
  borderRadius: '8px',
  padding: '10px 18px',
  fontSize: '13px',
  cursor: 'pointer',
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
const errStyle = { color: 'var(--tertiary)', fontFamily: 'var(--font-mono)', fontSize: '12px', margin: 0 }

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
