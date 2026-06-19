// gamespec: Trivia "Answer Tiles" — large touch-friendly A/B/C/D buttons.
// Shows the question, the four options, a 20s countdown, and (once the phone has
// answered) a per-phone reveal: your pick, whether it was right, and the correct
// option. The correct option is only ever known AFTER answering (the server
// withholds it until then), so `reveal` is null until the answer comes back.
import { useEffect, useState } from 'react'

const LETTERS = ['A', 'B', 'C', 'D']

export default function AnswerTiles({ question, reveal, onAnswer, disabled }) {
  const [secondsLeft, setSecondsLeft] = useState(question?.seconds_remaining ?? question?.duration_seconds ?? 20)
  const [submitting, setSubmitting] = useState(null)

  // Count down from the server's seconds_remaining using THIS device's own clock
  // (no absolute server timestamp to parse), so timezone/clock differences can't
  // make the timer read "time's up" instantly. Each poll resyncs to the latest
  // server value via the seconds_remaining dependency.
  useEffect(() => {
    const total = question?.seconds_remaining ?? question?.duration_seconds ?? 20
    const startMs = Date.now()
    const tick = () => {
      const elapsed = (Date.now() - startMs) / 1000
      setSecondsLeft(Math.max(0, Math.ceil(total - elapsed)))
    }
    tick()
    const id = setInterval(tick, 250)
    return () => clearInterval(id)
  }, [question?.index, question?.seconds_remaining, question?.duration_seconds])

  const handlePick = async (letter) => {
    if (disabled || reveal || submitting) return
    setSubmitting(letter)
    try {
      await onAnswer(letter)
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
        {LETTERS.map((letter) => {
          const isMine = reveal?.selected_option === letter
          const isCorrect = reveal?.correct_option === letter
          let background = 'var(--glass-bg)'
          let borderColor = 'var(--outline)'
          if (answered) {
            if (isCorrect) { background = 'rgba(102, 187, 106, 0.25)'; borderColor = '#66bb6a' }
            else if (isMine) { background = 'rgba(239, 83, 80, 0.22)'; borderColor = '#ef5350' }
          }
          return (
            <button
              key={letter}
              onClick={() => handlePick(letter)}
              disabled={disabled || answered || Boolean(submitting)}
              style={{ ...tileStyle, background, borderColor }}
            >
              <span style={letterBadgeStyle}>{letter}</span>
              <span style={{ flex: 1, textAlign: 'left' }}>{question.options[letter]}</span>
              {answered && isCorrect && <span>✅</span>}
              {answered && isMine && !isCorrect && <span>❌</span>}
            </button>
          )
        })}
      </div>

      {answered && (
        <p style={revealStyle}>
          {reveal.is_correct
            ? `Correct! +${reveal.score_awarded} pts`
            : `Not quite — +${reveal.score_awarded} pts. Take a sip 🍺`}
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
  fontSize: '20px',
  lineHeight: 1.4,
  margin: 0,
  textAlign: 'center',
}

const tilesStyle = { display: 'flex', flexDirection: 'column', gap: '10px' }

const tileStyle = {
  display: 'flex',
  alignItems: 'center',
  gap: '12px',
  padding: '16px',
  borderRadius: '12px',
  border: '1px solid var(--outline)',
  color: 'var(--on-surface)',
  fontSize: '15px',
  fontFamily: 'var(--font-body)',
  cursor: 'pointer',
  width: '100%',
}

const letterBadgeStyle = {
  fontFamily: 'var(--font-mono)',
  fontWeight: 700,
  fontSize: '14px',
  width: '24px',
  height: '24px',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  borderRadius: '6px',
  background: 'var(--bg-surface)',
}

const revealStyle = {
  textAlign: 'center',
  fontFamily: 'var(--font-mono)',
  fontSize: '14px',
  margin: 0,
}
