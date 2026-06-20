import { useCallback, useEffect, useRef, useState } from 'react'
import AnswerTiles from './AnswerTiles'
import Leaderboard from './Leaderboard'
import Recap from '../Recap/Recap'
import Toast from '../Toast'
import useSessionChannel from '../../hooks/useSessionChannel'
import { answerTrivia, fetchTriviaCurrent, leaveSession, rejoinSession, voteLoser } from '../../services/patronApi'

const POLL_MS = 2000
const ROULETTE_READ_MS = 10000 // read the Roulette challenge before voting opens

function firstUnanswered(answers, total) {
  for (let i = 0; i < total; i++) if (!answers[String(i)] && !answers[i]) return i
  return Math.max(0, total - 1)
}

// The non-origin (joined) phone's view of a session. Default is the between-rounds
// leaderboard. Trivia is auto-entered by the origin's round engine — every active
// phone gets all 5 questions, then SELF-PACES (answer, tap Next, at its own speed).
// A "Leave game" button is always available; leaving notifies the table (and, in a
// Trivia round, everyone). Realtime nudges a re-poll; the 2s poll is source of truth.
export default function SessionParticipant({ venueName, sessionId, phoneId, tableId }) {
  const [state, setState] = useState(null)
  const [myIndex, setMyIndex] = useState(0)
  const [localAnswers, setLocalAnswers] = useState({})
  const [done, setDone] = useState(false)
  const [left, setLeft] = useState(false)
  const [error, setError] = useState(null)
  const [toast, setToast] = useState(null)
  const [gameEnded, setGameEnded] = useState(false)
  const [rouletteResult, setRouletteResult] = useState(null) // brief result flash
  const [rouletteVoteOpen, setRouletteVoteOpen] = useState(false) // false during the read window
  const pollRef = useRef(null)
  const roundRef = useRef(null)
  const toastTimer = useRef(null)
  const rouletteTimer = useRef(null)
  const rouletteRoundRef = useRef(null) // which roulette round the read timer is armed for

  const showToast = useCallback((msg) => {
    setToast(msg)
    clearTimeout(toastTimer.current)
    toastTimer.current = setTimeout(() => setToast(null), 3500)
  }, [])

  useEffect(() => {
    if (left) return
    let cancelled = false
    const tick = async () => {
      try {
        const data = await fetchTriviaCurrent(sessionId, phoneId)
        if (cancelled) return
        setState(data)
        if (data.phase === 'question' && data.trivia_round_id !== roundRef.current) {
          roundRef.current = data.trivia_round_id
          setMyIndex(firstUnanswered(data.my_answers || {}, (data.questions || []).length))
          setLocalAnswers({})
          setDone(false)
        }
        if (data.phase !== 'question') roundRef.current = data.trivia_round_id || null
      } catch (e) {
        if (!cancelled) setError(e.message)
      }
    }
    pollRef.current = tick
    tick()
    const id = setInterval(tick, POLL_MS)
    return () => { cancelled = true; clearInterval(id); pollRef.current = null }
  }, [sessionId, phoneId, left])

  // Roulette read window: when a new roulette round appears, hold the vote UI for
  // ~10s so everyone can read the challenge, then open voting automatically.
  useEffect(() => {
    if (state?.phase !== 'roulette' || !state.round_id) return
    if (rouletteRoundRef.current === state.round_id) return
    rouletteRoundRef.current = state.round_id
    queueMicrotask(() => setRouletteVoteOpen(false)) // deferred so it's not a sync set-in-effect
    const open = setTimeout(() => setRouletteVoteOpen(true), ROULETTE_READ_MS)
    return () => clearTimeout(open)
  }, [state?.phase, state?.round_id])

  useSessionChannel(tableId, phoneId, (event, payload) => {
    // Multi-group safety: only react to game_ended for our own session.
    if (event === 'game_ended' && payload?.session_id === sessionId) {
      queueMicrotask(() => setGameEnded(true))
    }
    if (event === 'player_left') showToast(`${payload?.name || 'A player'} left the game`)
    if (event === 'player_rejoined') showToast(`${payload?.name || 'A player'} rejoined`)
    // Once the roulette round resolves, get_current_state returns between_rounds,
    // so show the result (who lost) from the broadcast for a few seconds before
    // the poll replaces it with the scoreboard.
    if (event === 'roulette:result') {
      setRouletteResult(payload)
      clearTimeout(rouletteTimer.current)
      rouletteTimer.current = setTimeout(() => setRouletteResult(null), 3500)
    }
    // roulette:vote re-polls to update the vote count; other events too.
    if (pollRef.current) pollRef.current()
  })

  const handleAnswer = async (letter, timeMs) => {
    const data = await answerTrivia(state.trivia_round_id, phoneId, myIndex, letter, timeMs)
    setLocalAnswers((prev) => ({ ...prev, [myIndex]: data }))
  }

  const handleVoteLoser = async (playerId) => {
    try {
      await voteLoser(state.round_id, phoneId, playerId)
      // Re-poll to get updated vote count / result from server
      pollRef.current?.()
    } catch (e) {
      setError(e.message)
    }
  }

  const handleNext = () => {
    const total = (state?.questions || []).length
    if (myIndex < total - 1) setMyIndex(myIndex + 1)
    else setDone(true)
  }

  const handleLeave = async () => {
    try {
      await leaveSession(sessionId, phoneId)
      setLeft(true)
    } catch (e) {
      setError(e.message)
    }
  }

  const handleRejoin = async () => {
    try {
      await rejoinSession(sessionId, phoneId)
      setLeft(false)
      pollRef.current?.()
    } catch (e) {
      setError(e.message)
    }
  }

  const renderContent = () => {
    if (gameEnded) {
      return <Recap sessionId={sessionId} venueName={venueName} />
    }
    // Left this game (just tapped Leave, or re-tapped back in later — the server
    // remembers via left_early). Offer Rejoin with the live scoreboard; never the
    // Leave button. A new phone never reaches here (it gets Join-or-New instead).
    const amLeft = left || Boolean(state?.left_early)
    if (amLeft) {
      return (
        <Screen>
          <h1 style={headlineStyle}>You left the game</h1>
          <p style={dimMono}>Tap rejoin to come back — your score is saved.</p>
          <Leaderboard rows={state?.leaderboard || []} title="Scoreboard" />
          <button onClick={handleRejoin} style={primaryButton}>Rejoin game</button>
          {error && <p style={errStyle}>{error}</p>}
        </Screen>
      )
    }
    // Brief Roulette result flash (overrides the poll's between_rounds for ~3.5s).
    if (rouletteResult) {
      if (rouletteResult.skipped) {
        return <Screen><p style={{ fontSize: '48px', margin: 0 }}>⏭️</p><h2 style={headlineStyle}>Skipped</h2></Screen>
      }
      const losers = rouletteResult.losers || []
      const names = losers.map((l) => l.name).join(', ')
      return (
        <Screen>
          <p style={{ fontSize: '48px', margin: 0 }}>🎰</p>
          <h2 style={headlineStyle}>{losers.length > 0 ? `${names} lost!` : 'All tied!'}</h2>
          {losers.length > 0 && rouletteResult.drink_consequence && (
            <p style={dimMono}>{rouletteResult.drink_consequence}</p>
          )}
          {rouletteResult.points_awarded > 0 && <p style={dimMono}>+3 for everyone else</p>}
        </Screen>
      )
    }
    if (!state) {
      return <Screen><p style={dimMono}>Connecting…</p>{error && <p style={errStyle}>{error}</p>}</Screen>
    }

    if (state.phase === 'roulette') {
      // Active Roulette round -- show vote UI if not yet voted, progress if already voted.
      return (
        <Screen>
          <p style={{ fontSize: '44px', margin: 0 }}>🎰</p>
          <h1 style={headlineStyle}>Roulette!</h1>
          <p style={{ ...dimMono, fontSize: '16px', margin: '0 0 8px' }}>{state.prompt}</p>
          <p style={dimMono}>{state.drink_consequence}</p>

          {/* Read window first, then the vote UI (player buttons, hidden once voted). */}
          {!rouletteVoteOpen && !state.my_vote && (
            <p style={dimMono}>Read the challenge — get ready to vote…</p>
          )}
          {rouletteVoteOpen && !state.my_vote && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', width: '100%', maxWidth: '320px' }}>
              <p style={dimMono}>Who lost?</p>
              {(state.players || []).map((p) => (
                <button key={p.id} onClick={() => handleVoteLoser(p.id)} style={primaryButton}>
                  {p.name}
                </button>
              ))}
            </div>
          )}
          {state.my_vote && <p style={dimMono}>Voted! {state.voted_count}/{state.active_total}</p>}

          <button onClick={handleLeave} style={leaveButton}>Leave game</button>
          {error && <p style={errStyle}>{error}</p>}
        </Screen>
      )
    }

    if (state.phase === 'gather') {
      return (
        <Screen>
          <p style={{ fontSize: '44px', margin: 0 }}>🧠</p>
          <h1 style={headlineStyle}>Trivia round!</h1>
          <p style={dimMono}>Get ready — answer on your own phone, at your own pace</p>
          <p style={dimMono}>[{state.joined_count} playing]</p>
          <button onClick={handleLeave} style={leaveButton}>Leave game</button>
          {error && <p style={errStyle}>{error}</p>}
        </Screen>
      )
    }

    if (state.phase === 'question' && (state.questions || []).length > 0) {
      if (!state.is_participant) {
        // Rejoined (or arrived) after this round was already underway — sit it out
        // and join the next one, without disrupting the players mid-round.
        return (
          <Screen>
            <p style={dimMono}>A round's in progress — you'll join the next one</p>
            <Leaderboard rows={state.leaderboard} title="Scoreboard" />
            <button onClick={handleLeave} style={leaveButton}>Leave game</button>
          </Screen>
        )
      }
      if (done) {
        return (
          <Screen>
            <p style={{ fontSize: '28px', margin: 0 }}>✅</p>
            <h1 style={headlineStyle}>All done!</h1>
            <Leaderboard rows={state.leaderboard} title="Scoreboard" />
            <p style={dimMono}>Others are still playing…</p>
            <button onClick={handleLeave} style={leaveButton}>Leave game</button>
          </Screen>
        )
      }
      const question = state.questions[myIndex]
      const reveal = localAnswers[myIndex] || state.my_answers?.[String(myIndex)] || null
      const answered = Boolean(reveal)
      const isLast = myIndex >= state.questions.length - 1
      return (
        <Screen>
          <AnswerTiles question={question} reveal={reveal} onAnswer={handleAnswer} />
          {answered && (
            <button onClick={handleNext} style={primaryButton}>
              {isLast ? 'See scores →' : 'Next question →'}
            </button>
          )}
          {!answered && <p style={dimMono}>Pick your answer to continue</p>}
          <button onClick={handleLeave} style={leaveButton}>Leave game</button>
          {error && <p style={errStyle}>{error}</p>}
        </Screen>
      )
    }

    // between_rounds (default) — live leaderboard + leave.
    return (
      <Screen>
        <p style={dimMono}>Playing at {venueName} 🍺</p>
        <Leaderboard rows={state.leaderboard} title="Scoreboard" />
        <p style={dimMono}>Round in progress on the table phone…</p>
        <button onClick={handleLeave} style={leaveButton}>Leave game</button>
        {error && <p style={errStyle}>{error}</p>}
      </Screen>
    )
  }

  return (
    <>
      {renderContent()}
      <Toast message={toast} />
    </>
  )
}

function Screen({ children }) {
  return <div style={screenStyle}>{children}</div>
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

const leaveButton = {
  background: 'transparent',
  color: 'var(--on-surface-dim)',
  border: '1px solid var(--outline)',
  borderRadius: '8px',
  padding: '10px 18px',
  fontSize: '13px',
  cursor: 'pointer',
}
