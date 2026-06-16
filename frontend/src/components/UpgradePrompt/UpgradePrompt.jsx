import { useEffect, useState } from 'react'
import { captureInterest } from '../../services/api'

const FEATURES = [
  { icon: '♾️', text: 'Unlimited cards per session' },
  { icon: '⏭️', text: 'Unlimited skips' },
  { icon: '🎴', text: 'All decks unlocked — Party + Uni' },
  { icon: '⏱️', text: 'Timer off toggle' },
  { icon: '🚫', text: 'No interruptions' },
  { icon: '🔀', text: 'Deck mixing' },
  { icon: '📊', text: 'Shareable game recap' },
  { icon: '👤', text: 'Custom player names' },
  { icon: '🎃', text: 'Seasonal packs' },
  { icon: '🎮', text: 'Host controls' },
]

const TRIGGERS = {
  card_limit:  { headline: "You're on a roll 🔥",  sub: "You've hit the free limit for this session." },
  locked_deck: { headline: 'Premium Deck 🔒',      sub: 'This deck is part of FirstMove Premium.' },
  mix_decks:   { headline: 'Mix Decks 🔀',         sub: 'Combine multiple decks — a Premium feature.' },
  share_recap: { headline: 'Share Recap 📊',        sub: 'Share your game summary — a Premium feature.' },
  redraw:      { headline: 'Redraw used up',        sub: "You've used your free redraw for this session." },
}

export default function UpgradePrompt({ reason, mode, onDismiss }) {
  const { headline, sub } = TRIGGERS[reason] ?? TRIGGERS.locked_deck
  const [screen, setScreen] = useState('features') // 'features' | 'notify'
  const [email, setEmail] = useState('')
  const [interestStatus, setInterestStatus] = useState(null) // null | 'loading' | 'success' | 'duplicate' | 'error'

  const handleInterest = async () => {
    if (!email.trim()) return
    setInterestStatus('loading')
    try {
      const { already_registered } = await captureInterest(email.trim(), mode ?? 'party', reason ?? 'card_limit')
      setInterestStatus(already_registered ? 'duplicate' : 'success')
    } catch {
      setInterestStatus('error')
    }
  }

  useEffect(() => {
    const topic = import.meta.env.VITE_NTFY_INTEREST_TOPIC
    if (!topic) return
    fetch(`https://ntfy.sh/${topic}`, {
      method: 'POST',
      body: `Someone tapped Upgrade to Premium\nReason: ${reason}\nMode: ${mode ?? 'unknown'}`,
      headers: { Title: 'Upgrade tap 💰', Priority: 'default' },
    }).catch(() => {})
    // Intentionally fire-once on mount — reason/mode are fixed for this
    // prompt's lifetime, and adding them as deps would resend the
    // notification on every re-render if either ever changed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const interestDone = interestStatus === 'success' || interestStatus === 'duplicate'

  return (
    <div
      onClick={onDismiss}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1000,
        background: 'rgba(10,10,12,0.92)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '24px',
        overflowY: 'auto',
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: 'var(--glass-bg)',
          backdropFilter: 'blur(24px)',
          WebkitBackdropFilter: 'blur(24px)',
          border: '1px solid rgba(255,255,255,0.12)',
          borderRadius: '24px',
          padding: '32px 28px',
          width: '100%',
          maxWidth: '360px',
          display: 'flex',
          flexDirection: 'column',
          gap: '20px',
          boxShadow: '0 0 60px rgba(189,0,255,0.15)',
        }}
      >
        {/* Header */}
        <div style={{ textAlign: 'center' }}>
          <h2 style={{
            fontFamily: 'var(--font-headline)',
            fontWeight: 900,
            fontSize: 'clamp(22px, 6vw, 26px)',
            color: 'var(--on-surface)',
            margin: '0 0 6px',
            lineHeight: 1.2,
          }}>
            {headline}
          </h2>
          <p style={{
            fontFamily: 'var(--font-body)',
            fontSize: '14px',
            color: 'var(--on-surface-dim)',
            margin: 0,
          }}>
            {sub}
          </p>
        </div>

        {screen === 'features' ? (
          <>
            {/* Feature list */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              <p style={{
                fontFamily: 'var(--font-mono)',
                fontSize: '11px',
                color: 'var(--primary)',
                margin: '0 0 2px',
                letterSpacing: '0.1em',
                textTransform: 'uppercase',
              }}>
                What you get
              </p>
              {FEATURES.map(({ icon, text }) => (
                <div key={text} style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <span style={{ fontSize: '16px', flexShrink: 0 }}>{icon}</span>
                  <span style={{ fontFamily: 'var(--font-body)', fontSize: '14px', color: 'var(--on-surface)' }}>
                    {text}
                  </span>
                </div>
              ))}
            </div>

            {/* CTA */}
            <button
              onClick={() => setScreen('notify')}
              style={{
                background: 'var(--primary)',
                border: 'none',
                borderRadius: '14px',
                padding: '18px',
                fontFamily: 'var(--font-headline)',
                fontWeight: 800,
                fontSize: '16px',
                color: '#0A0A0C',
                cursor: 'pointer',
                minHeight: '56px',
                boxShadow: '0 0 32px var(--primary-glow)55',
                transition: 'opacity 0.15s',
              }}
              onTouchStart={e => e.currentTarget.style.opacity = '0.85'}
              onTouchEnd={e => e.currentTarget.style.opacity = '1'}
            >
              Upgrade to Premium →
            </button>

            <button
              onClick={onDismiss}
              style={{
                background: 'none',
                border: 'none',
                fontFamily: 'var(--font-body)',
                fontSize: '14px',
                color: 'var(--on-surface-dim)',
                cursor: 'pointer',
                padding: '4px',
                opacity: 0.6,
              }}
            >
              Maybe later
            </button>
          </>
        ) : (
          <>
            {/* Notify screen */}
            <p style={{
              fontFamily: 'var(--font-body)',
              fontSize: '14px',
              color: 'var(--on-surface-dim)',
              margin: 0,
              textAlign: 'center',
              lineHeight: 1.55,
            }}>
              Premium is launching soon. Drop your email and we'll let you know the moment it's live.
            </p>

            {interestDone ? (
              <p style={{
                fontFamily: 'var(--font-headline)',
                fontWeight: 700,
                fontSize: '16px',
                color: 'var(--primary)',
                textAlign: 'center',
                margin: 0,
              }}>
                {interestStatus === 'success'
                  ? "You're on the list 🎉 We'll reach out when premium is live"
                  : "You're already on the list 👀"}
              </p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                <input
                  type="email"
                  placeholder="your@email.com"
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && handleInterest()}
                  autoFocus
                  style={{
                    background: 'rgba(255,255,255,0.06)',
                    border: '1px solid rgba(255,255,255,0.12)',
                    borderRadius: '12px',
                    padding: '14px 16px',
                    fontFamily: 'var(--font-body)',
                    fontSize: '15px',
                    color: 'var(--on-surface)',
                    outline: 'none',
                    width: '100%',
                    boxSizing: 'border-box',
                  }}
                />
                <button
                  onClick={handleInterest}
                  disabled={interestStatus === 'loading' || !email.trim()}
                  style={{
                    background: 'var(--primary)',
                    border: 'none',
                    borderRadius: '14px',
                    padding: '16px',
                    fontFamily: 'var(--font-headline)',
                    fontWeight: 800,
                    fontSize: '15px',
                    color: '#0A0A0C',
                    cursor: interestStatus === 'loading' || !email.trim() ? 'default' : 'pointer',
                    minHeight: '52px',
                    transition: 'opacity 0.15s',
                    opacity: interestStatus === 'loading' || !email.trim() ? 0.5 : 1,
                  }}
                >
                  {interestStatus === 'loading' ? 'Saving…' : 'Notify Me When Live'}
                </button>
                {interestStatus === 'error' && (
                  <p style={{
                    fontFamily: 'var(--font-body)',
                    fontSize: '13px',
                    color: 'var(--tertiary)',
                    textAlign: 'center',
                    margin: 0,
                  }}>
                    Something went wrong, try again
                  </p>
                )}
              </div>
            )}

            <button
              onClick={interestDone ? onDismiss : () => setScreen('features')}
              style={{
                background: 'none',
                border: 'none',
                fontFamily: 'var(--font-body)',
                fontSize: '14px',
                color: 'var(--on-surface-dim)',
                cursor: 'pointer',
                padding: '4px',
                opacity: 0.6,
              }}
            >
              {interestDone ? 'Close' : '← Back'}
            </button>
          </>
        )}
      </div>
    </div>
  )
}
