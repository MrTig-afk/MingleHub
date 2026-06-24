import { useCallback, useEffect, useRef, useState } from 'react'
import AnswerTiles from './AnswerTiles'
import Leaderboard from './Leaderboard'
import Recap from '../Recap/Recap'
import RetapOverlay from '../Retap/RetapOverlay'
import RoundOrigin from '../RoundOrigin/RoundOrigin'
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
  // Host migration: pendingPromotion is set on host_changed broadcast or poll fallback.
  // promoted is only set true at a between-rounds boundary so we never interrupt a round.
  const [pendingPromotion, setPendingPromotion] = useState(false)
  const [promoted, setPromoted] = useState(false)
  const pollRef = useRef(null)
  const roundRef = useRef(null)
  // The patron's own name (saved when they set it in the lobby) — highlights
  // their row on the leaderboard as "You". Best-effort; null if unknown.
  const meName = (() => { try { return localStorage.getItem('mh_player_name') } catch { return null } })()
  const toastTimer = useRef(null)
  const rouletteTimer = useRef(null)
  const rouletteRoundRef = useRef(null) // which roulette round the read timer is armed for

  const showToast = useCallback((msg) => {
    setToast(msg)
    clearTimeout(toastTimer.current)
    toastTimer.current = setTimeout(() => setToast(null), 3500)
  }, [])

  useEffect(() => {
    // Stop polling once the phone has left or been promoted (RoundOrigin manages its own state).
    if (left || promoted) return
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
        // Poll fallback: detect promotion even if host_changed broadcast was missed.
        if (data.is_origin && !promoted) {
          setPendingPromotion(true)
        }
      } catch (e) {
        if (!cancelled) setError(e.message)
      }
    }
    pollRef.current = tick
    tick()
    const id = setInterval(tick, POLL_MS)
    return () => { cancelled = true; clearInterval(id); pollRef.current = null }
  }, [sessionId, phoneId, left, promoted])

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

  // Deferred promotion: only switch to host at a between-rounds boundary so we
  // never interrupt a mid-round new host. StrictMode: setPromoted(true) is idempotent.
  // queueMicrotask defers the setState out of the effect's synchronous body.
  useEffect(() => {
    if (!pendingPromotion) return
    if (!state) return
    // between_rounds or ended -> promote immediately; otherwise wait for the next poll tick.
    if (state.phase === 'between_rounds' || state.phase === 'ended') {
      queueMicrotask(() => setPromoted(true))
    }
  }, [pendingPromotion, state])

  useSessionChannel(tableId, phoneId, (event, payload) => {
    // Multi-group safety: only react to game_ended for our own session.
    if (event === 'game_ended' && payload?.session_id === sessionId) {
      queueMicrotask(() => setGameEnded(true))
    }
    // Host migration broadcast: promote this phone or show a toast to everyone else.
    if (event === 'host_changed' && payload?.session_id === sessionId) {
      if (payload.new_host_phone_id === phoneId) {
        queueMicrotask(() => setPendingPromotion(true))
      } else {
        const who = payload.old_host_name ? `${payload.old_host_name} left — ` : ''
        showToast(`${who}${payload.new_host_name || 'A player'} is now the host`)
      }
      return
    }
    // Multi-group safety: scope the player toasts to our own session.
    if (event === 'player_left' && payload?.session_id === sessionId) showToast(`${payload?.name || 'A player'} left the game`)
    if (event === 'player_rejoined' && payload?.session_id === sessionId) showToast(`${payload?.name || 'A player'} rejoined`)
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
    // Host promotion: this phone is now the session origin. Render RoundOrigin
    // inline -- it fetches the server-authoritative round number on mount.
    if (promoted) {
      return (
        <RoundOrigin
          venueName={venueName}
          sessionId={sessionId}
          phoneId={phoneId}
          tableId={tableId}
        />
      )
    }
    // Game over: the realtime game_ended flag is the fast path; the poll's
    // phase==='ended' is the reliable fallback if that broadcast was missed.
    if (gameEnded || state?.phase === 'ended') {
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
          <Leaderboard rows={state?.leaderboard || []} title="Scoreboard" meName={meName} />
          <button onClick={handleRejoin} style={primaryButton}>Rejoin game</button>
          {error && <p style={errStyle}>{error}</p>}
        </Screen>
      )
    }
    // Brief Roulette result flash (overrides the poll's between_rounds for ~3.5s).
    if (rouletteResult) {
      if (rouletteResult.skipped) {
        return <Screen><div style={{ fontFamily: 'var(--font-display)', fontSize: '42px', color: 'var(--on-surface-dim)', letterSpacing: '0.04em' }}>SKIPPED</div></Screen>
      }
      const losers = rouletteResult.losers || []
      const names = losers.map((l) => l.name).join(', ')
      return (
        <Screen>
          <div style={{ fontFamily: 'var(--font-display)', fontSize: '13px', letterSpacing: '0.22em', color: 'var(--primary)', textShadow: '0 0 12px rgba(255,45,120,0.5)' }}>ROULETTE</div>
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
          <div style={{ fontFamily: 'var(--font-display)', fontSize: '54px', color: 'var(--primary)', letterSpacing: '0.02em', lineHeight: 1, textShadow: '0 0 26px rgba(255,45,120,0.5)' }}>ROULETTE</div>
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
          <div style={{ fontFamily: 'var(--font-display)', fontSize: '54px', color: 'var(--primary)', letterSpacing: '0.02em', lineHeight: 1, textShadow: '0 0 26px rgba(255,45,120,0.5)' }}>TRIVIA</div>
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
            <Leaderboard rows={state.leaderboard} title="Scoreboard" meName={meName} />
            <button onClick={handleLeave} style={leaveButton}>Leave game</button>
          </Screen>
        )
      }
      if (done) {
        return (
          <Screen>
            <div style={{ fontFamily: 'var(--font-display)', fontSize: '40px', color: 'var(--correct)', letterSpacing: '0.03em', textShadow: '0 0 22px rgba(57,224,139,0.4)' }}>ALL DONE</div>
            <Leaderboard rows={state.leaderboard} title="Scoreboard" meName={meName} />
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
          <AnswerTiles question={question} reveal={reveal} onAnswer={handleAnswer} phoneId={phoneId} />
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
        <p style={dimMono}>Playing at {venueName}</p>
        <Leaderboard rows={state.leaderboard} title="Scoreboard" meName={meName} />
        <p style={dimMono}>Round in progress on the table phone…</p>
        <button onClick={handleLeave} style={leaveButton}>Leave game</button>
        {error && <p style={errStyle}>{error}</p>}
      </Screen>
    )
  }

  return (
    <>
      {renderContent()}
      {state?.retap && (state.retap.state === 'prompt' || state.retap.state === 'paused') && (
        <RetapOverlay state={state.retap.state} secondsLeft={state.retap.seconds_left} />
      )}
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
