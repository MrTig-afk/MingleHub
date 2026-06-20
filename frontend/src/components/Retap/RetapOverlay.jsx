import { useEffect, useState } from 'react'

// Rendered by the parent when retap.state is 'prompt' or 'paused'.
// Not rendered for 'active' or 'expired' (parent condition guards this).
// No button: physical NFC tap is the only way to dismiss the overlay.
// The next poll returning state='active' causes the parent to unmount us.
export default function RetapOverlay({ state, secondsLeft }) {
  const [display, setDisplay] = useState(secondsLeft)

  // Reset local countdown when the server sends a new value (new poll tick).
  // queueMicrotask defers the setState out of the effect's synchronous body
  // so it doesn't trigger a cascading render — same pattern as SessionParticipant.
  useEffect(() => {
    queueMicrotask(() => setDisplay(secondsLeft))
  }, [secondsLeft])

  // Client-side 1s tick for smooth countdown between polls.
  // Keyed on secondsLeft so a new poll value re-arms the timer.
  // StrictMode-safe: cleanup clears the previous interval before the
  // double-invoked effect sets up a new one.
  useEffect(() => {
    const id = setInterval(() => {
      setDisplay((d) => (d <= 0 ? 0 : d - 1))
    }, 1000)
    return () => clearInterval(id)
  }, [secondsLeft])

  const mm = Math.floor(display / 60)
  const ss = String(display % 60).padStart(2, '0')

  const isPrompt = state === 'prompt'

  return (
    <div style={overlayStyle(isPrompt)}>
      <h1 style={headlineStyle}>
        {isPrompt ? 'Still playing?' : 'Game paused'}
      </h1>
      <p style={subStyle}>
        {isPrompt
          ? 'Tap the tag to keep playing'
          : 'Tap the tag to resume'}
      </p>
      <p style={timerStyle}>{mm}:{ss}</p>
    </div>
  )
}

const overlayStyle = (isPrompt) => ({
  position: 'fixed',
  inset: 0,
  zIndex: 50,
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  gap: '16px',
  background: isPrompt
    ? 'rgba(0, 0, 0, 0.75)'
    : 'rgba(0, 0, 0, 0.9)',
  color: '#fff',
  textAlign: 'center',
  padding: '24px',
})

const headlineStyle = {
  fontFamily: 'var(--font-headline)',
  fontSize: '28px',
  margin: 0,
}

const subStyle = {
  fontFamily: 'var(--font-mono)',
  fontSize: '14px',
  color: 'rgba(255, 255, 255, 0.7)',
  margin: 0,
}

const timerStyle = {
  fontFamily: 'var(--font-mono)',
  fontSize: '48px',
  fontWeight: 700,
  margin: 0,
}
