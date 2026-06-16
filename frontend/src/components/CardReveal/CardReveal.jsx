import { useState, useEffect, useRef } from 'react'
import { FREE_TIER, FREE_TIER_CARD_LIMIT } from '../../config'

const CATEGORY_META = {
  icebreakers:     { label: 'Icebreakers',    icon: '🌊', var: '--accent-icebreakers' },
  truth:           { label: 'Truth',          icon: '🔍', var: '--accent-truth' },
  dares:           { label: 'Dares',          icon: '🔥', var: '--accent-dares' },
  compliments:     { label: 'Compliments',    icon: '💛', var: '--accent-compliments' },
  dirty:           { label: 'Dirty',          icon: '💋', var: '--accent-dirty' },
  deep:            { label: 'Deep',           icon: '🌌', var: '--accent-deep' },
  party:           { label: 'Party',          icon: '🎉', var: '--accent-party' },
  debate:          { label: 'Debate',         icon: '⚡', var: '--accent-truth' },
  freshers:        { label: 'Freshers',       icon: '🎒', var: '--accent-icebreakers' },
  hot_takes:       { label: 'Hot Takes',      icon: '🌶️', var: '--accent-dares' },
  would_you_rather:{ label: 'Would U Rather', icon: '🤔', var: '--accent-deep' },
  mix:             { label: 'Mix',            icon: '🔀', var: '--primary' },
}

const TIMER_TOTAL = 10

export default function CardReveal({ card, pack, onComplete, onNewCard, onRedraw, onEndGame, onBack, canSkip, skipsRemaining, showRedraw, devMode, packCardIndex }) {
  const [timeLeft, setTimeLeft] = useState(TIMER_TOTAL)
  const [toast, setToast] = useState(null) // null | 'skipped' | 'completed'
  const [showEndConfirm, setShowEndConfirm] = useState(false)
  const expiryFiredRef = useRef(false)

  // Countdown tick — never runs in dev mode
  useEffect(() => {
    if (devMode || timeLeft <= 0) return
    const id = setInterval(() => setTimeLeft(t => t - 1), 1000)
    return () => clearInterval(id)
  }, [timeLeft, devMode])

  // Timer expiry — completely skipped in dev mode; setToast never called in dev mode
  useEffect(() => {
    if (timeLeft !== 0 || expiryFiredRef.current) return
    expiryFiredRef.current = true
    if (devMode) return
    const willSkip = card?.type === 'hard_pass' || canSkip
    // Reacting to the countdown timer (an external system) hitting zero —
    // shows the toast once, guarded by expiryFiredRef above.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setToast(willSkip ? 'skipped' : 'completed')
    const id = setTimeout(() => {
      willSkip ? onNewCard() : onComplete()
    }, 1500)
    return () => clearTimeout(id)
  }, [timeLeft, devMode, card, canSkip, onNewCard, onComplete])

  if (!card) return null

  const isMix = pack?.id === 'mix'
  const cardPackId = isMix ? card.packId : pack?.id
  const meta = CATEGORY_META[cardPackId] ?? CATEGORY_META[pack?.id] ?? { label: pack?.name ?? '', icon: '🃏', var: '--primary' }
  const accent = `var(${meta.var})`
  const isHardPass = card.type === 'hard_pass'

  const timerExpired = timeLeft <= 0
  // Buttons are never disabled due to toast in dev mode — toast is never set in dev mode anyway
  const buttonsDisabled = (!devMode && toast !== null) || showEndConfirm
  const skipAllowed = !buttonsDisabled && (devMode || (!timerExpired && canSkip))

  const timerColor = timeLeft <= 3 ? 'var(--tertiary)' : accent
  const timerGlow = timeLeft <= 3 ? '0 0 16px var(--tertiary)' : `0 0 12px ${accent}`

  return (
    <div style={{
      minHeight: '100dvh',
      background: 'var(--bg-floor)',
      display: 'flex',
      flexDirection: 'column',
      padding: 'var(--safe-margin)',
      paddingTop: 'calc(env(safe-area-inset-top, 0px) + 16px)',
      paddingBottom: 'calc(var(--thumb-zone) + 16px)',
    }}>

      {/* Back + timer row */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
        <button
          onClick={onBack}
          style={{
            background: 'none',
            border: 'none',
            color: 'var(--on-surface-dim)',
            fontFamily: 'var(--font-mono)',
            fontSize: '14px',
            cursor: 'pointer',
            minHeight: '56px',
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: 0,
          }}
        >
          ← Back
        </button>

        {/* Countdown badge — hidden in dev mode */}
        {!devMode && (
          <div style={{
            fontFamily: 'var(--font-headline)',
            fontWeight: 900,
            fontSize: '22px',
            color: timerColor,
            boxShadow: timerGlow,
            background: `${timerColor.replace('var(', 'rgba(').replace(')', ',0.08)')}`,
            border: `1.5px solid ${timerColor}`,
            borderRadius: '12px',
            padding: '4px 14px',
            minWidth: '52px',
            textAlign: 'center',
            transition: 'color 0.3s, border-color 0.3s, box-shadow 0.3s',
            lineHeight: '32px',
          }}>
            {timerExpired ? '0s' : `${timeLeft}s`}
          </div>
        )}
      </div>

      {/* Card wrapper — badge + card centred as one unit */}
      <div style={{
        flex: 1,
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        paddingBottom: '16px',
        minHeight: 0,
      }}>
        {/* Category badge sits inside the wrapper so it travels with the card */}
        <div style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: '6px',
          background: `color-mix(in srgb, ${accent} 14%, transparent)`,
          border: `1px solid color-mix(in srgb, ${accent} 40%, transparent)`,
          borderRadius: '100px',
          padding: '4px 12px',
          alignSelf: 'flex-start',
          marginBottom: '8px',
          minHeight: '26px',
        }}>
          <span style={{ fontSize: '14px' }}>{meta.icon}</span>
          <span style={{
            fontFamily: 'var(--font-mono)',
            fontSize: '11px',
            fontWeight: 700,
            color: accent,
            textTransform: 'uppercase',
            letterSpacing: '0.08em',
          }}>
            {meta.label}
          </span>
        </div>

        <div
          className="glass-card"
          style={{
            border: `1.5px solid ${isHardPass ? 'var(--outline)' : accent}`,
            boxShadow: isHardPass ? 'none' : `0 0 40px color-mix(in srgb, ${accent} 30%, transparent)`,
            padding: '28px 24px',
            display: 'flex',
            flexDirection: 'column',
            gap: '16px',
            position: 'relative',
          }}
        >
          {FREE_TIER && !devMode && (
            <div style={{
              position: 'absolute',
              top: '12px',
              right: '14px',
              fontFamily: 'var(--font-mono)',
              fontSize: '11px',
              fontWeight: 700,
              color: 'var(--on-surface-dim)',
              opacity: 0.5,
              letterSpacing: '0.04em',
              pointerEvents: 'none',
            }}>
              {packCardIndex + 1}/{FREE_TIER_CARD_LIMIT}
            </div>
          )}
          <p style={{
            fontFamily: 'var(--font-headline)',
            fontWeight: 800,
            fontSize: 'clamp(20px, 5.5vw, 26px)',
            color: isHardPass ? 'var(--on-surface-dim)' : 'var(--on-surface)',
            margin: 0,
            lineHeight: 1.35,
          }}>
            {card.text}
          </p>

          {card.flavour && (
            <p style={{
              fontFamily: 'var(--font-body)',
              fontSize: '14px',
              color: 'var(--on-surface-dim)',
              fontStyle: 'italic',
              margin: 0,
              paddingTop: '8px',
              borderTop: '1px solid var(--outline)',
            }}>
              {card.flavour}
            </p>
          )}
        </div>
      </div>

      {/* Action buttons */}
      <div style={{ position: 'relative', display: 'flex', flexDirection: 'column', gap: '12px' }}>
        {toast && (
          <div style={{
            position: 'absolute',
            bottom: 'calc(100% + 16px)',
            left: 0,
            right: 0,
            display: 'flex',
            justifyContent: 'center',
            pointerEvents: 'none',
          }}>
            <div style={{
              animation: 'toast-fade 1.5s ease-in-out forwards',
              background: 'var(--glass-bg)',
              backdropFilter: 'blur(16px)',
              WebkitBackdropFilter: 'blur(16px)',
              border: '1px solid rgba(255,255,255,0.12)',
              borderRadius: '100px',
              padding: '10px 20px',
              fontFamily: 'var(--font-body)',
              fontWeight: 600,
              fontSize: '13px',
              color: 'var(--on-surface)',
              whiteSpace: 'nowrap',
            }}>
              {toast === 'skipped' ? "Time's up — skipped ⏱️" : "Time's up — completed ✅"}
            </div>
          </div>
        )}

        {!isHardPass && (
          <button
            onClick={!buttonsDisabled ? onComplete : undefined}
            disabled={buttonsDisabled}
            style={{
              background: accent,
              border: 'none',
              borderRadius: '14px',
              padding: '18px',
              fontFamily: 'var(--font-headline)',
              fontWeight: 800,
              fontSize: '16px',
              color: '#0A0A0C',
              cursor: buttonsDisabled ? 'default' : 'pointer',
              minHeight: '56px',
              boxShadow: `0 0 24px color-mix(in srgb, ${accent} 40%, transparent)`,
              transition: 'opacity 0.15s',
              opacity: buttonsDisabled ? 0.4 : 1,
            }}
            onTouchStart={e => { if (!buttonsDisabled) e.currentTarget.style.opacity = '0.85' }}
            onTouchEnd={e => { if (!buttonsDisabled) e.currentTarget.style.opacity = '1' }}
          >
            Complete ✓
          </button>
        )}

        {/* Redraw — only visible in dev mode (showRedraw = devMode && remaining > 1) */}
        {showRedraw && !isHardPass && (
          <button
            onClick={!buttonsDisabled ? onRedraw : undefined}
            disabled={buttonsDisabled}
            style={{
              background: 'var(--bg-container)',
              border: '1px solid var(--outline)',
              borderRadius: '14px',
              padding: '16px',
              fontFamily: 'var(--font-body)',
              fontSize: '15px',
              color: 'var(--on-surface-dim)',
              cursor: buttonsDisabled ? 'default' : 'pointer',
              minHeight: '56px',
              transition: 'opacity 0.3s',
              opacity: buttonsDisabled ? 0.3 : 1,
            }}
            onTouchStart={e => { if (!buttonsDisabled) e.currentTarget.style.opacity = '0.7' }}
            onTouchEnd={e => { if (!buttonsDisabled) e.currentTarget.style.opacity = '1' }}
          >
            Redraw
          </button>
        )}

        <button
          onClick={skipAllowed ? onNewCard : undefined}
          disabled={!skipAllowed}
          style={{
            background: 'var(--bg-container)',
            border: '1px solid var(--outline)',
            borderRadius: '14px',
            padding: '16px',
            fontFamily: 'var(--font-body)',
            fontSize: '15px',
            color: 'var(--on-surface-dim)',
            cursor: skipAllowed ? 'pointer' : 'default',
            minHeight: '56px',
            transition: 'opacity 0.3s',
            opacity: skipAllowed ? 1 : 0.3,
            pointerEvents: skipAllowed ? 'auto' : 'none',
          }}
          onTouchStart={e => { if (skipAllowed) e.currentTarget.style.opacity = '0.7' }}
          onTouchEnd={e => { if (skipAllowed) e.currentTarget.style.opacity = '1' }}
        >
          {isHardPass ? 'Next card →' : 'Skip'}
        </button>

        {FREE_TIER && skipsRemaining !== null && canSkip && !timerExpired && (
          <p style={{
            fontFamily: 'var(--font-mono)',
            fontSize: '11px',
            color: 'var(--on-surface-dim)',
            textAlign: 'center',
            margin: 0,
            opacity: 0.7,
          }}>
            {skipsRemaining} skip remaining
          </p>
        )}

        {!showEndConfirm ? (
          <button
            onClick={() => setShowEndConfirm(true)}
            style={{
              background: 'none',
              border: '1px solid rgba(255,255,255,0.08)',
              borderRadius: '12px',
              padding: '10px',
              fontFamily: 'var(--font-mono)',
              fontSize: '11px',
              color: 'var(--on-surface-dim)',
              cursor: 'pointer',
              minHeight: '40px',
              opacity: 0.45,
              transition: 'opacity 0.2s',
              letterSpacing: '0.04em',
            }}
            onTouchStart={e => e.currentTarget.style.opacity = '0.8'}
            onTouchEnd={e => e.currentTarget.style.opacity = '0.45'}
          >
            End Game
          </button>
        ) : (
          <div style={{ display: 'flex', gap: '8px' }}>
            <button
              onClick={onEndGame}
              style={{
                flex: 1,
                background: 'rgba(231,0,110,0.15)',
                border: '1px solid var(--tertiary)',
                borderRadius: '12px',
                padding: '12px',
                fontFamily: 'var(--font-headline)',
                fontWeight: 700,
                fontSize: '14px',
                color: 'var(--tertiary)',
                cursor: 'pointer',
                minHeight: '48px',
                transition: 'opacity 0.15s',
              }}
              onTouchStart={e => e.currentTarget.style.opacity = '0.7'}
              onTouchEnd={e => e.currentTarget.style.opacity = '1'}
            >
              End →
            </button>
            <button
              onClick={() => setShowEndConfirm(false)}
              style={{
                flex: 1,
                background: 'var(--bg-container)',
                border: '1px solid var(--outline)',
                borderRadius: '12px',
                padding: '12px',
                fontFamily: 'var(--font-body)',
                fontSize: '14px',
                color: 'var(--on-surface-dim)',
                cursor: 'pointer',
                minHeight: '48px',
                transition: 'opacity 0.15s',
              }}
              onTouchStart={e => e.currentTarget.style.opacity = '0.7'}
              onTouchEnd={e => e.currentTarget.style.opacity = '1'}
            >
              Cancel
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
