// gamespec: Trivia "Answer Tiles" — large touch-friendly A/B/C/D buttons.
// Shows the question, the four options, a 20s countdown, and (once the phone has
// answered) a per-phone reveal: your pick, whether it was right, and the correct
// option. The correct option is only ever known AFTER answering (the server
// withholds it until then), so `reveal` is null until the answer comes back.
import { useEffect, useMemo, useRef, useState } from 'react'

const LETTERS = ['A', 'B', 'C', 'D']

// Deterministic per (phoneId, question): renders the four options in a shuffled
// order that differs per phone but is stable for a given question (so tiles never
// move under a finger). Badges stay A-D by POSITION; each tile still submits its
// TRUE letter, so the server-side answer check is unaffected.
function shuffledOrder(seed) {
  let h = 2166136261
  for (let i = 0; i < seed.length; i++) {
    h ^= seed.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  const next = () => {
    h += 0x6d2b79f5
    let t = Math.imul(h ^ (h >>> 15), 1 | h)
    t ^= t + Math.imul(t ^ (t >>> 7), 61 | t)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
  const arr = ['A', 'B', 'C', 'D']
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(next() * (i + 1))
    const tmp = arr[i]; arr[i] = arr[j]; arr[j] = tmp
  }
  return arr
}

export default function AnswerTiles({ question, reveal, onAnswer, disabled, phoneId }) {
  const [secondsLeft, setSecondsLeft] = useState(question?.duration_seconds ?? 20)
  const [submitting, setSubmitting] = useState(null)
  // When THIS question was displayed — self-paced, so the 20s timer is per phone
  // per question, measured on this device's own clock and reported on answer.
  // Set in the countdown effect (runs on mount before any tap can happen).
  const shownAtRef = useRef(null)

  // Fresh 20s countdown each question (reset when question.index changes).
  useEffect(() => {
    const total = question?.duration_seconds ?? 20
    const startMs = Date.now()
    shownAtRef.current = startMs
    const tick = () => {
      const elapsed = (Date.now() - startMs) / 1000
      setSecondsLeft(Math.max(0, Math.ceil(total - elapsed)))
    }
    tick()
    const id = setInterval(tick, 250)
    return () => clearInterval(id)
  }, [question?.index, question?.duration_seconds])

  // Per-phone display order, stable per question and memoized so it isn't rehashed
  // on every render. Badges stay A-D by position; each tile submits its true letter.
  const order = useMemo(
    () => shuffledOrder(`${phoneId || ''}:${question?.index}`),
    [phoneId, question?.index],
  )

  const handlePick = async (letter) => {
    if (disabled || reveal || submitting) return
    setSubmitting(letter)
    try {
      await onAnswer(letter, Date.now() - (shownAtRef.current || Date.now()))
    } finally {
      setSubmitting(null)
    }
  }

  if (!question) return null
  const answered = Boolean(reveal)
  const expired = secondsLeft <= 0

  return (
    <div style={wrapStyle}>
      <div style={countdownRowStyle}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', color: 'var(--on-surface-dim)' }}>
          Question {question.index + 1} / {question.total}
        </span>
        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: '14px',
          color: expired ? 'var(--tertiary)' : 'var(--on-surface)',
        }}>
          {expired ? "Time's up" : `${secondsLeft}s`}
        </span>
      </div>

      <p style={questionStyle}>{question.question}</p>

      <div style={tilesStyle}>
        {order.map((trueLetter, pos) => {
          const isMine = reveal?.selected_option === trueLetter
          const isCorrect = reveal?.correct_option === trueLetter
          let background = 'var(--bg-surface)'
          let borderColor = 'var(--line)'
          if (answered) {
            if (isCorrect) { background = 'rgba(57, 224, 139, 0.10)'; borderColor = 'var(--correct)' }
            else if (isMine) { background = 'rgba(255, 92, 108, 0.12)'; borderColor = 'var(--tertiary)' }
          }
          return (
            <button
              key={trueLetter}
              onClick={() => handlePick(trueLetter)}
              disabled={disabled || answered || Boolean(submitting)}
              style={{ ...tileStyle, background, borderColor }}
            >
              <span style={letterBadgeStyle}>{LETTERS[pos]}</span>
              <span style={{ flex: 1, textAlign: 'left' }}>{question.options[trueLetter]}</span>
              {answered && isCorrect && <span style={{ color: 'var(--correct)', fontWeight: 700, fontSize: '17px' }}>✓</span>}
              {answered && isMine && !isCorrect && <span style={{ color: 'var(--tertiary)', fontWeight: 700, fontSize: '16px' }}>✕</span>}
            </button>
          )
        })}
      </div>

      {answered && (
        <p style={revealStyle}>
          {reveal.is_correct
            ? `Nice — that's right. +${reveal.score_awarded} pts`
            : `Not quite — +${reveal.score_awarded} pts. Take a sip.`}
        </p>
      )}
      {!answered && expired && (
        <p style={{ ...revealStyle, color: 'var(--on-surface-dim)' }}>
          Answer now for fewer points, or wait for the next question.
        </p>
      )}
    </div>
  )
}

const wrapStyle = {
  background: 'transparent',
  width: '100%',
  maxWidth: '360px',
  display: 'flex',
  flexDirection: 'column',
  gap: '16px',
}

const countdownRowStyle = { display: 'flex', justifyContent: 'space-between', alignItems: 'center' }

const questionStyle = {
  fontFamily: 'var(--font-headline)',
  fontWeight: 700,
  fontSize: '25px',
  letterSpacing: '-0.015em',
  lineHeight: 1.18,
  margin: 0,
  textAlign: 'left',
  color: 'var(--on-surface)',
}

const tilesStyle = { display: 'flex', flexDirection: 'column', gap: '10px' }

const tileStyle = {
  display: 'flex',
  alignItems: 'center',
  gap: '13px',
  padding: '15px',
  borderRadius: '13px',
  border: '1.5px solid var(--line)',
  color: 'var(--on-surface)',
  fontSize: '16.5px',
  fontWeight: 600,
  fontFamily: 'var(--font-headline)',
  cursor: 'pointer',
  width: '100%',
}

const letterBadgeStyle = {
  fontFamily: 'var(--font-mono)',
  fontWeight: 500,
  fontSize: '13px',
  width: '30px',
  height: '30px',
  flex: 'none',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  borderRadius: '8px',
  border: '1px solid var(--line)',
  color: 'var(--on-surface-dim)',
  background: 'var(--bg-container)',
}

const revealStyle = {
  textAlign: 'center',
  fontFamily: 'var(--font-mono)',
  fontSize: '14px',
  margin: 0,
}
