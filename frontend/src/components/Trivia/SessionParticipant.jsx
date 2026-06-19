import { useEffect, useRef, useState } from 'react'
import AnswerTiles from './AnswerTiles'
import Leaderboard from './Leaderboard'
import useSessionChannel from '../../hooks/useSessionChannel'
import { answerTrivia, fetchTriviaCurrent, leaveSession } from '../../services/patronApi'

const POLL_MS = 2000

// The non-origin (joined) phone's view of a session. Default state is the
// between-rounds leaderboard (gamespec: Between-Rounds Screen). Trivia is
// auto-entered by the origin's round engine — every active phone is enrolled
// server-side, so this phone shows a brief "get ready" splash, then the live
// question with A/B/C/D tiles, and reveals the result per phone. Realtime
// accelerates delivery; the 2s poll of trivia/current is the source of truth
// (and the only path when Supabase realtime is not configured).
export default function SessionParticipant({ venueName, sessionId, phoneId, tableId }) {
  const [state, setState] = useState(null)
  const [localReveal, setLocalReveal] = useState(null) // { index, data } — instant feedback pre-poll
  const [left, setLeft] = useState(false)
  const [error, setError] = useState(null)
  const pollRef = useRef(null)

  // Poll trivia/current as the source of truth for this phone's view.
  useEffect(() => {
    if (left) return
    let cancelled = false
    const tick = async () => {
      try {
        const data = await fetchTriviaCurrent(sessionId, phoneId)
        if (cancelled) return
        setState(data)
      } catch (e) {
        if (!cancelled) setError(e.message)
      }
    }
    pollRef.current = tick
    tick()
    const id = setInterval(tick, POLL_MS)
    return () => { cancelled = true; clearInterval(id); pollRef.current = null }
  }, [sessionId, phoneId, left])

  useSessionChannel(tableId, phoneId, () => {
    if (pollRef.current) pollRef.current()
  })

  const handleAnswer = async (letter) => {
    const data = await answerTrivia(state.trivia_round_id, phoneId, state.question.index, letter)
    setLocalReveal({ index: state.question.index, data })
    if (pollRef.current) pollRef.current()
  }

  const handleLeave = async () => {
    try {
      await leaveSession(sessionId, phoneId)
      setLeft(true)
    } catch (e) {
      setError(e.message)
    }
  }

  if (left) {
    return (
      <Screen>
        <h1 style={headlineStyle}>Thanks for playing 🍺</h1>
        <p style={dimMono}>You've left the game. Your score is saved on the recap.</p>
      </Screen>
    )
  }

  if (!state) {
    return <Screen><p style={dimMono}>Connecting…</p>{error && <p style={errStyle}>{error}</p>}</Screen>
  }

  if (state.phase === 'gather') {
    return (
      <Screen>
        <p style={{ fontSize: '44px', margin: 0 }}>🧠</p>
        <h1 style={headlineStyle}>Trivia round!</h1>
        <p style={dimMono}>Get ready — answer on your own phone</p>
        <p style={dimMono}>[{state.joined_count} playing]</p>
        {error && <p style={errStyle}>{error}</p>}
      </Screen>
    )
  }

  if (state.phase === 'question' && state.question) {
    const reveal = localReveal?.index === state.question.index ? localReveal.data : state.my_answer
    if (!state.is_participant) {
      // A session member who didn't join this round just spectates the scores.
      return (
        <Screen>
          <p style={dimMono}>Trivia in progress — you didn't join this round</p>
          <Leaderboard rows={state.leaderboard} title="Scoreboard" />
        </Screen>
      )
    }
    return (
      <Screen>
        <AnswerTiles question={state.question} reveal={reveal} onAnswer={handleAnswer} />
        <p style={dimMono}>{state.answered_count} answered</p>
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

const leaveButton = {
  background: 'transparent',
  color: 'var(--on-surface-dim)',
  border: '1px solid var(--outline)',
  borderRadius: '8px',
  padding: '10px 18px',
  fontSize: '13px',
  cursor: 'pointer',
}
