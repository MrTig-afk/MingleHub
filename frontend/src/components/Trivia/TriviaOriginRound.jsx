import { useCallback, useEffect, useRef, useState } from 'react'
import AnswerTiles from './AnswerTiles'
import Leaderboard from './Leaderboard'
import useSessionChannel from '../../hooks/useSessionChannel'
import {
  abandonTrivia, answerTrivia, beginTrivia, fetchTriviaCurrent,
  finishTrivia, nextTrivia, startTrivia,
} from '../../services/patronApi'

const GATHER_SECONDS = 60
const POLL_MS = 1500

// gamespec Round Type 2 — Trivia, from the session-origin phone's side. The
// origin drives the flow: open the gather, begin once >=2 phones are in, advance
// between the 5 questions, then finish to a leaderboard. The origin holder is
// also a player, so this screen lets them answer too (AnswerTiles).
//
// Phases: starting -> gather -> question -> leaderboard. If fewer than 2 phones
// have joined when the 60s gather elapses, the round is abandoned (onDone()).
export default function TriviaOriginRound({ sessionId, phoneId, tableId, onDone }) {
  const [phase, setPhase] = useState('starting')
  const [joinedCount, setJoinedCount] = useState(1)
  const [answeredCount, setAnsweredCount] = useState(0)
  const [question, setQuestion] = useState(null)
  const [reveal, setReveal] = useState(null)
  const [leaderboard, setLeaderboard] = useState([])
  const [gatherLeft, setGatherLeft] = useState(GATHER_SECONDS)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const roundIdRef = useRef(null)
  const pollRef = useRef(null)

  const handleBegin = useCallback(async () => {
    if (busy || phase !== 'gather') return
    setBusy(true)
    setError(null)
    try {
      const data = await beginTrivia(roundIdRef.current, phoneId)
      setQuestion(data.question)
      setReveal(null)
      setAnsweredCount(0)
      setPhase('question')
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }, [busy, phase, phoneId])

  const handleAbandon = useCallback(async () => {
    try {
      if (roundIdRef.current) await abandonTrivia(roundIdRef.current, phoneId)
    } catch {
      // Even if the abandon call fails, return to the picker — the round is dead.
    }
    onDone()
  }, [phoneId, onDone])

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

  // Open the gather on mount.
  useEffect(() => {
    let cancelled = false
    startTrivia(sessionId, phoneId)
      .then((data) => {
        if (cancelled) return
        roundIdRef.current = data.trivia_round_id
        setJoinedCount(data.joined_count ?? 1)
        setPhase('gather')
      })
      .catch((e) => { if (!cancelled) setError(e.message) })
    return () => { cancelled = true }
  }, [sessionId, phoneId])

  // Poll current state for live counts (joined during gather, answered during a
  // question). Realtime, when configured, just triggers an immediate re-poll.
  useEffect(() => {
    if (phase !== 'gather' && phase !== 'question') return
    let cancelled = false
    const tick = async () => {
      if (!roundIdRef.current) return
      try {
        const state = await fetchTriviaCurrent(sessionId, phoneId)
        if (cancelled) return
        if (typeof state.joined_count === 'number') setJoinedCount(state.joined_count)
        if (typeof state.answered_count === 'number') setAnsweredCount(state.answered_count)
      } catch {
        // Transient poll failure is non-fatal — the origin's own action
        // responses remain the source of truth for question/flow state.
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

  // 60s gather countdown (interval only sets state, from an async callback).
  useEffect(() => {
    if (phase !== 'gather') return
    const startedAt = Date.now()
    const id = setInterval(() => {
      setGatherLeft(Math.max(0, GATHER_SECONDS - Math.floor((Date.now() - startedAt) / 1000)))
    }, 500)
    return () => clearInterval(id)
  }, [phase])

  // gamespec: if fewer than 2 phones have joined when the gather elapses, the
  // round is abandoned. (With >=2, the origin taps "Start Trivia" — the timer is
  // only the floor that forces a decision.)
  useEffect(() => {
    if (phase === 'gather' && gatherLeft <= 0 && joinedCount < 2) {
      handleAbandon()
    }
  }, [phase, gatherLeft, joinedCount, handleAbandon])

  if (phase === 'starting') {
    return <Screen><p style={dimMono}>Starting Trivia…</p>{error && <p style={errStyle}>{error}</p>}</Screen>
  }

  if (phase === 'gather') {
    const ready = joinedCount >= 2
    return (
      <Screen>
        <h1 style={headlineStyle}>Trivia round! 🧠</h1>
        <p style={dimMono}>Everyone tap the tag to join</p>
        <p style={{ fontSize: '40px', fontFamily: 'var(--font-mono)', margin: '4px 0' }}>{gatherLeft}s</p>
        <p style={dimMono}>[{joinedCount} phone{joinedCount === 1 ? '' : 's'} joined]</p>
        {ready ? (
          <button onClick={handleBegin} disabled={busy} style={primaryButton}>
            {busy ? 'Starting…' : 'Start Trivia'}
          </button>
        ) : (
          <p style={dimMono}>Waiting for at least 2 phones…</p>
        )}
        <button onClick={handleAbandon} style={textButton}>Cancel</button>
        {error && <p style={errStyle}>{error}</p>}
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

  return <Screen><p style={dimMono}>Loading…</p></Screen>
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

const textButton = {
  background: 'transparent',
  color: 'var(--on-surface-dim)',
  border: 'none',
  fontSize: '13px',
  cursor: 'pointer',
  textDecoration: 'underline',
}
