import { useEffect, useRef, useState } from 'react'
import AnswerTiles from './AnswerTiles'
import Leaderboard from './Leaderboard'
import {
  answerTrivia, beginTrivia, fetchLeaderboard, fetchTriviaCurrent, finishTrivia, startTrivia,
} from '../../services/patronApi'

const GET_READY_MS = 3500

function firstUnanswered(answers, total) {
  for (let i = 0; i < total; i++) if (!answers[i]) return i
  return Math.max(0, total - 1)
}

// gamespec Round Type 2 — Trivia, from the session-origin phone's side. Trivia is
// auto-entered between Chooser rounds (no manual start). SELF-PACED: every phone
// gets all 5 questions up front and walks them at its own speed — answering, then
// tapping "Next" to move to ITS next question (nobody is advanced by anyone else).
// The origin holder plays like everyone, and when they finish they tap "Finish" /
// "Back to the game", which ends the round and returns to the round loop.
//
// Phases: starting -> getready -> question -> leaderboard. <2 players -> onDone().
export default function TriviaOriginRound({ sessionId, phoneId, onDone }) {
  const [phase, setPhase] = useState('starting')
  const [questions, setQuestions] = useState([])
  const [myIndex, setMyIndex] = useState(0)
  const [answers, setAnswers] = useState({}) // index -> reveal { selected_option, correct_option, is_correct, score_awarded }
  const [leaderboard, setLeaderboard] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const roundIdRef = useRef(null)

  // Open the round on mount (idempotent). Resume straight into the questions if
  // it's already in progress (re-tap mid-Trivia).
  useEffect(() => {
    let cancelled = false
    startTrivia(sessionId, phoneId)
      .then(async (data) => {
        if (cancelled) return
        roundIdRef.current = data.trivia_round_id
        if (data.status === 'in_progress') {
          const cur = await fetchTriviaCurrent(sessionId, phoneId)
          if (cancelled) return
          const qs = cur.questions || []
          const ans = cur.my_answers || {}
          setQuestions(qs)
          setAnswers(ans)
          setMyIndex(firstUnanswered(ans, qs.length))
          setPhase('question')
        } else {
          setPhase('getready')
        }
      })
      .catch(() => { if (!cancelled) onDone() }) // not_enough_players etc -> skip Trivia
    return () => { cancelled = true }
  }, [sessionId, phoneId, onDone])

  // After the get-ready splash, fetch all questions and start.
  useEffect(() => {
    if (phase !== 'getready') return
    const id = setTimeout(async () => {
      try {
        const data = await beginTrivia(roundIdRef.current, phoneId)
        setQuestions(data.questions || [])
        setMyIndex(0)
        setAnswers({})
        setPhase('question')
      } catch (e) {
        setError(e.message)
      }
    }, GET_READY_MS)
    return () => clearTimeout(id)
  }, [phase, phoneId])

  const handleAnswer = async (letter, timeMs) => {
    const data = await answerTrivia(roundIdRef.current, phoneId, myIndex, letter, timeMs)
    setAnswers((prev) => ({ ...prev, [myIndex]: data }))
  }

  const handleNext = () => {
    if (myIndex < questions.length - 1) setMyIndex(myIndex + 1)
  }

  // Origin finished its own 5 -> show the (live) leaderboard WITHOUT ending the
  // round, so slower players keep going. The round auto-completes server-side
  // once everyone's done.
  const handleSeeScores = async () => {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      const data = await fetchLeaderboard(sessionId)
      setLeaderboard(data.leaderboard || [])
      setPhase('leaderboard')
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  // Leaving the Trivia screen advances the game loop. finishTrivia is a fallback
  // that force-ends the round if some player never finished; if it already
  // auto-completed, the 409 is harmless.
  const handleBackToGame = async () => {
    try { await finishTrivia(roundIdRef.current, phoneId) } catch { /* already complete */ }
    onDone()
  }

  if (phase === 'starting') {
    return <Screen><p style={dimMono}>Loading Trivia…</p></Screen>
  }

  if (phase === 'getready') {
    return (
      <Screen>
        <p style={{ fontSize: '44px', margin: 0 }}>🧠</p>
        <h1 style={headlineStyle}>Trivia round!</h1>
        <p style={dimMono}>Get ready — answer on your own phone, at your own pace</p>
      </Screen>
    )
  }

  if (phase === 'question' && questions[myIndex]) {
    const reveal = answers[myIndex] || null
    const answered = Boolean(reveal)
    const isLast = myIndex >= questions.length - 1
    return (
      <Screen>
        <AnswerTiles question={questions[myIndex]} reveal={reveal} onAnswer={handleAnswer} />
        {answered && (
          isLast ? (
            <button onClick={handleSeeScores} disabled={busy} style={primaryButton}>
              {busy ? 'Loading…' : 'See scores →'}
            </button>
          ) : (
            <button onClick={handleNext} style={primaryButton}>Next question →</button>
          )
        )}
        {!answered && <p style={dimMono}>Pick your answer to continue</p>}
        {error && <p style={errStyle}>{error}</p>}
      </Screen>
    )
  }

  if (phase === 'leaderboard') {
    return (
      <Screen>
        <p style={{ fontSize: '32px', margin: 0 }}>🏆</p>
        <Leaderboard rows={leaderboard} title="Trivia results" />
        <button onClick={handleBackToGame} style={primaryButton}>Back to the game</button>
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
