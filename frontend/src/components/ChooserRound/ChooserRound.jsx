import { useEffect, useRef, useState } from 'react'
import { completeRound, drawCard, redrawRound, skipRound } from '../../services/patronApi'

// gamespec.md: Chooser round -- card display, Complete/Skip/Redraw actions,
// responsible drinking disclaimer (once per session, server-controlled). Redraw
// gives a new card of the same category (free -- Chooser awards no points), EXCEPT
// on a Drink Card: a drink can't be re-rolled into a softer one, so Redraw is
// hidden there (Complete or Skip only).
//
// State machine:
//   card_loading -> card_shown -> resolving -> result -> (onRoundComplete)
//
// The disclaimer overlay is rendered before the card if show_drink_disclaimer
// is true; dismissing it reveals the card beneath.

// One luminous accent per card type — distinct hues in the After Dark family
// (categories must stay tellable apart, so per-type color is the right call here).
const CARD_TYPE_META = {
  icebreaker: { label: 'Icebreaker', color: '#2DE2E6' },
  truth:      { label: 'Truth',      color: '#B98CFF' },
  dare:       { label: 'Dare',       color: '#FF8A4C' },
  compliment: { label: 'Compliment', color: '#FF6FA5' },
  challenge:  { label: 'Challenge',  color: '#FFC857' },
  drink:      { label: 'Drink',      color: '#39E08B' },
  flirty:     { label: 'Flirty',     color: '#FF5C9E' },
}

// adultsOnly is intentionally not destructured -- card filtering is enforced
// server-side; the client does not need this value to render the round.
export default function ChooserRound({ sessionId, phoneId, hotSeat, onRoundComplete }) {
  const [phase, setPhase] = useState('card_loading') // card_loading | disclaimer | card_shown | resolving | result
  const [card, setCard] = useState(null)
  const [roundId, setRoundId] = useState(null)
  const [resultData, setResultData] = useState(null) // { result }
  const [error, setError] = useState(null)
  const resultTimerRef = useRef(null)

  // Draw card on mount
  useEffect(() => {
    let cancelled = false
    drawCard(sessionId, phoneId, hotSeat.player_id)
      .then((data) => {
        if (cancelled) return
        setCard(data.card)
        setRoundId(data.round_id)
        if (data.show_drink_disclaimer) {
          setPhase('disclaimer')
        } else {
          setPhase('card_shown')
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e.message)
          setPhase('card_shown')
        }
      })
    return () => { cancelled = true }
  }, [sessionId, phoneId, hotSeat.player_id])

  // Auto-advance from result phase after 1.5s
  useEffect(() => {
    if (phase === 'result') {
      resultTimerRef.current = setTimeout(() => {
        onRoundComplete()
      }, 1500)
    }
    return () => clearTimeout(resultTimerRef.current)
  }, [phase, onRoundComplete])

  const handleComplete = async () => {
    setPhase('resolving')
    try {
      const data = await completeRound(roundId, phoneId)
      setResultData(data)
      setPhase('result')
    } catch (e) {
      setError(e.message)
      setPhase('card_shown')
    }
  }

  const handleSkip = async () => {
    setPhase('resolving')
    try {
      const data = await skipRound(roundId, phoneId)
      setResultData(data)
      setPhase('result')
    } catch (e) {
      setError(e.message)
      setPhase('card_shown')
    }
  }

  // Redraw: new card, same category. Hidden on drink cards (see header comment).
  const handleRedraw = async () => {
    setError(null)
    try {
      const data = await redrawRound(roundId, phoneId)
      setCard(data.card)
      if (data.show_drink_disclaimer) setPhase('disclaimer')
    } catch (e) {
      setError(e.message)
    }
  }

  const meta = card ? (CARD_TYPE_META[card.type] || CARD_TYPE_META.icebreaker) : null

  // --- Disclaimer overlay ---
  if (phase === 'disclaimer') {
    return (
      <div style={overlayStyle}>
        <div style={glassCardStyle}>
          <h2 style={{ fontFamily: 'var(--font-headline)', fontSize: '20px', margin: '0 0 12px' }}>
            Responsible Drinking
          </h2>
          <p style={{ color: 'var(--on-surface-dim)', fontSize: '14px', lineHeight: 1.5, margin: '0 0 24px' }}>
            MingleHub encourages responsible drinking.
            <br />
            Know your limits.
          </p>
          <button
            onClick={() => setPhase('card_shown')}
            style={primaryButtonStyle}
          >
            Got it
          </button>
        </div>
      </div>
    )
  }

  // --- Loading ---
  if (phase === 'card_loading') {
    return (
      <div style={overlayStyle}>
        <p style={{ color: 'var(--on-surface-dim)', fontFamily: 'var(--font-mono)' }}>Drawing card…</p>
      </div>
    )
  }

  // --- Resolving ---
  if (phase === 'resolving') {
    return (
      <div style={overlayStyle}>
        <p style={{ color: 'var(--on-surface-dim)', fontFamily: 'var(--font-mono)' }}>Saving…</p>
      </div>
    )
  }

  // --- Result flash ---
  if (phase === 'result' && resultData) {
    const won = resultData.result === 'completed'
    return (
      <div style={overlayStyle}>
        <div style={{ fontFamily: 'var(--font-display)', fontSize: '46px', color: won ? 'var(--correct)' : 'var(--on-surface-dim)', letterSpacing: '0.03em', textShadow: won ? '0 0 24px rgba(57,224,139,0.4)' : 'none' }}>{won ? 'DONE' : 'SKIPPED'}</div>
      </div>
    )
  }

  // --- Card shown (main state) ---
  return (
    <div style={overlayStyle}>
      {/* Card — no hot-seat name shown: the finger picker can't reliably
          identify which person was chosen, so naming them would be misleading. */}
      {card ? (
        <div style={{ ...glassCardStyle, borderColor: meta.color }}>
          {/* Category chip */}
          <div style={{ marginBottom: '16px' }}>
            <span style={{
              display: 'inline-block',
              fontSize: '11px', fontFamily: 'var(--font-mono)', textTransform: 'uppercase',
              letterSpacing: '0.1em', color: meta.color,
              background: `${meta.color}1a`, border: `1px solid ${meta.color}55`,
              padding: '4px 10px', borderRadius: '6px',
            }}>
              {meta.label}
            </span>
          </div>
          {/* Card content */}
          <p style={{ fontSize: '20px', fontFamily: 'var(--font-headline)', lineHeight: 1.4, margin: 0 }}>
            {card.content}
          </p>
        </div>
      ) : (
        <p style={{ color: 'var(--tertiary)', fontFamily: 'var(--font-mono)' }}>{error || 'No card loaded'}</p>
      )}

      {/* Error */}
      {error && (
        <p style={{ color: 'var(--tertiary)', fontSize: '12px', fontFamily: 'var(--font-mono)', margin: 0 }}>
          {error}
        </p>
      )}

      {/* Action buttons */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', width: '100%', maxWidth: '320px' }}>
        <button onClick={handleComplete} style={primaryButtonStyle} disabled={!card}>
          Complete
        </button>
        {card?.type !== 'drink' && (
          <button onClick={handleRedraw} style={secondaryButtonStyle} disabled={!card}>
            Redraw
          </button>
        )}
        <button onClick={handleSkip} style={secondaryButtonStyle} disabled={!card}>
          Skip
        </button>
      </div>
    </div>
  )
}

const overlayStyle = {
  minHeight: '100dvh',
  background: 'var(--bg-floor)',
  color: 'var(--on-surface)',
  fontFamily: 'var(--font-body)',
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  gap: '20px',
  padding: '24px',
  textAlign: 'center',
}

const glassCardStyle = {
  background: 'var(--bg-surface)',
  border: '1.5px solid var(--line)',
  borderRadius: '16px',
  padding: '28px 24px',
  maxWidth: '360px',
  width: '100%',
  textAlign: 'left',
}

const primaryButtonStyle = {
  padding: '16px',
  borderRadius: '10px',
  background: 'var(--primary)',
  color: 'var(--bg-floor)',
  fontWeight: 700,
  fontSize: '16px',
  border: 'none',
  width: '100%',
  cursor: 'pointer',
}

const secondaryButtonStyle = {
  padding: '14px',
  borderRadius: '10px',
  background: 'transparent',
  color: 'var(--on-surface)',
  fontWeight: 600,
  fontSize: '14px',
  border: '1px solid var(--outline)',
  width: '100%',
  cursor: 'pointer',
}
