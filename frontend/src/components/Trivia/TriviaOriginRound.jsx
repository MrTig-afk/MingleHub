import { useEffect, useRef, useState } from 'react'
import AnswerTiles from './AnswerTiles'
import Leaderboard from './Leaderboard'
import useSessionChannel from '../../hooks/useSessionChannel'
import {
  answerTrivia, beginTrivia, fetchTriviaCurrent, finishTrivia, nextTrivia, startTrivia,
} from '../../services/patronApi'

const GET_READY_MS = 3500
const POLL_MS = 1500

// gamespec Round Type 2 — Trivia, from the session-origin phone's side. Trivia is
// auto-entered by the round engine between Chooser rounds (no manual start): on
// mount it opens the round (auto-enrolling everyone), shows a brief "get ready"
// splash on all phones, then reveals the first question. The origin holder is
// also a player, so this screen lets them answer too (AnswerTiles), and they
// drive the advance between questions. onDone() returns to the round engine.
//
// Phases: starting -> getready -> question -> leaderboard. If fewer than 2
// players are active, the round can't run and onDone() fires immediately so the
// engine picks a different round type.
export default function TriviaOriginRound({ sessionId, phoneId, tableId, onDone }) {
  const [phase, setPhase] = useState('starting')
  const [joinedCount, setJoinedCount] = useState(0)
  const [answeredCount, setAnsweredCount] = useState(0)
  const [question, setQuestion] = useState(null)
  const [reveal, setReveal] = useState(null)
  const [leaderboard, setLeaderboard] = useState([])
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const roundIdRef = useRef(null)
  const pollRef = useRef(null)

  // Open the round on mount (auto-enrolls all active members). start is
  // idempotent, so a StrictMode double-mount / re-tap returns the same round.
  useEffect(() => {
    let cancelled = false
    startTrivia(sessionId, phoneId)
      .then(async (data) => {
        if (cancelled) return
        roundIdRef.current = data.trivia_round_id
        setJoinedCount(data.joined_count ?? 0)
        if (data.status === 'in_progress') {
          // Resuming an already-started round (re-tap mid-Trivia) -- jump to the
          // live question rather than replaying the get-ready splash.
          try {
            const cur = await fetchTriviaCurrent(sessionId, phoneId)
            if (cancelled) return
            if (cur.question) {
              setQuestion(cur.question)
              setPhase('question')
              return
            }
          } catch { /* fall through to get-ready */ }
        }
        setPhase('getready')
      })
      .catch(() => {
        // not_enough_players (or any start failure) -> let the engine pick
        // another round type instead of blocking on a Trivia round.
        if (!cancelled) onDone()
      })
    return () => { cancelled = true }
  }, [sessionId, phoneId, onDone])

  // After the get-ready splash, reveal the first question (this starts the 20s
  // timer server-side). Non-origin phones see the splash on trivia:gather and
  // the question on trivia:question, so everyone stays in step.
  useEffect(() => {
    if (phase !== 'getready') return
    const id = setTimeout(async () => {
      try {
        const data = await beginTrivia(roundIdRef.current, phoneId)
        setQuestion(data.question)
        setReveal(null)
        setAnsweredCount(0)
        setPhase('question')
      } catch (e) {
        setError(e.message)
      }
    }, GET_READY_MS)
    return () => clearTimeout(id)
  }, [phase, phoneId])

  // Poll answered-count while a question is live (realtime just nudges a re-poll).
  useEffect(() => {
    if (phase !== 'question') return
    let cancelled = false
    const tick = async () => {
      if (!roundIdRef.current) return
      try {
        const state = await fetchTriviaCurrent(sessionId, phoneId)
        if (cancelled) return
        if (typeof state.joined_count === 'number') setJoinedCount(state.joined_count)
        if (typeof state.answered_count === 'number') setAnsweredCount(state.answered_count)
      } catch {
        // Non-fatal — the origin's own action responses drive the flow.
      }
    }
    pollRef.current = tick
    tick()
    const id = setInterval(tick, POLL_MS)
    return () => { cancelled = true; clearInterval(id); pollRef.current = null }
  }, [phase, sessionId, phoneId])

  useSessionChannel(tableId, phoneId, () => {
    if (pollRef.current) pollRef.current()
  })

  const handleAnswer = async (letter) => {
    const data = await answerTrivia(roundIdRef.current, phoneId, question.index, letter)
    setReveal(data)
  }

  const handleNext = async () => {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      const data = await nextTrivia(roundIdRef.current, phoneId)
      setQuestion(data.question)
      setReveal(null)
      setAnsweredCount(0)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const handleFinish = async () => {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      const data = await finishTrivia(roundIdRef.current, phoneId)
      setLeaderboard(data.leaderboard || [])
      setPhase('leaderboard')
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  if (phase === 'starting') {
    return <Screen><p style={dimMono}>Loading Trivia…</p></Screen>
  }

  if (phase === 'getready') {
    return (
      <Screen>
        <p style={{ fontSize: '44px', margin: 0 }}>🧠</p>
        <h1 style={headlineStyle}>Trivia round!</h1>
        <p style={dimMono}>Get ready — everyone answers on their own phone</p>
        <p style={dimMono}>[{joinedCount} playing]</p>
      </Screen>
    )
  }

  if (phase === 'question' && question) {
    const isLast = question.index >= question.total - 1
    return (
      <Screen>
        <AnswerTiles question={question} reveal={reveal} onAnswer={handleAnswer} />
        <p style={dimMono}>{answeredCount} of {joinedCount} answered</p>
        {isLast ? (
          <button onClick={handleFinish} disabled={busy} style={primaryButton}>
            {busy ? 'Saving…' : 'Finish & show scores'}
          </button>
        ) : (
          <button onClick={handleNext} disabled={busy} style={primaryButton}>
            {busy ? 'Loading…' : 'Next question →'}
          </button>
        )}
        {error && <p style={errStyle}>{error}</p>}
      </Screen>
    )
  }

  if (phase === 'leaderboard') {
    return (
      <Screen>
        <p style={{ fontSize: '32px', margin: 0 }}>🏆</p>
        <Leaderboard rows={leaderboard} title="Trivia results" />
        <button onClick={onDone} style={primaryButton}>Back to the game</button>
      </Screen>
    )
  }

  return <Screen><p style={dimMono}>Loading…</p>{error && <p style={errStyle}>{error}</p>}</Screen>
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
