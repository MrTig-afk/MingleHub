import { useEffect, useState } from 'react'
import Leaderboard from '../Trivia/Leaderboard'
import { fetchRecap } from '../../services/patronApi'

// Terminal recap screen shown after the game ends (via End Game button,
// game_ended broadcast, idle timeout, or re-tap on a recently-ended session).
// Fetches aggregated stats and renders them. No round controls.
export default function Recap({ sessionId, venueName }) {
  const [recap, setRecap] = useState(null)
  const [error, setError] = useState(null)
  const [copied, setCopied] = useState(false)
  const meName = (() => { try { return localStorage.getItem('mh_player_name') } catch { return null } })()

  // Standard React 18 fetch pattern: a no-op-guard ref would deadlock under
  // StrictMode (the first fetch resolves after cleanup set cancelled=true, and
  // the second mount skips). Just cancel stale results; a double GET is harmless
  // for this read-only endpoint.
  useEffect(() => {
    let cancelled = false
    fetchRecap(sessionId)
      .then((data) => { if (!cancelled) setRecap(data) })
      .catch((e) => { if (!cancelled) setError(e.message) })
    return () => { cancelled = true }
  }, [sessionId])

  const handleShare = async () => {
    if (!recap) return
    if (navigator.share) {
      // navigator.share throws DOMException on user cancel — ignore it
      navigator.share({ text: recap.share_text }).catch(() => {})
    } else {
      navigator.clipboard.writeText(recap.share_text).then(() => {
        setCopied(true)
        setTimeout(() => setCopied(false), 2500)
      }).catch(() => {})
    }
  }

  if (error) {
    return (
      <Screen>
        <h1 style={headlineStyle}>Game Over &mdash; {venueName}</h1>
        <p style={dimMono}>{error}</p>
      </Screen>
    )
  }

  if (!recap) {
    return (
      <Screen>
        <h1 style={headlineStyle}>Game Over &mdash; {venueName}</h1>
        <p style={dimMono}>Loading recap…</p>
      </Screen>
    )
  }

  const mostPicked = recap.most_picked_player
  const accuracyDisplay = recap.trivia_accuracy !== null
    ? `${recap.trivia_correct} / ${recap.trivia_total} correct`
    : '---'

  return (
    <Screen>
      <p style={{ fontSize: '48px', margin: 0 }}>&#x1F3C6;</p>
      <h1 style={headlineStyle}>Game Over &mdash; {recap.venue_name}</h1>

      <Leaderboard rows={recap.leaderboard} title="Final Scores" meName={meName} />

      <div style={statsCardStyle}>
        <StatRow label="Most Picked Player" value={
          mostPicked
            ? `${mostPicked.name} (${mostPicked.times_selected} selections)`
            : '---'
        } />
        <StatRow label="Cards Played" value={String(recap.cards_played)} />
        <StatRow label="Trivia Accuracy" value={accuracyDisplay} />
        <StatRow label="Total Group Score" value={`${recap.total_score} pts`} />
        <StatRow label="Roulette Rounds" value={String(recap.roulette_rounds)} />
      </div>

      <button
        onClick={() => { window.location.href = `${window.location.pathname}?newgame=1` }}
        style={primaryButton}
      >
        New game
      </button>
      <button onClick={handleShare} style={secondaryButton}>
        {copied ? 'Copied!' : 'Share'}
      </button>
    </Screen>
  )
}

function StatRow({ label, value }) {
  return (
    <div style={statRowStyle}>
      <span style={dimMono}>{label}</span>
      <span style={statValueStyle}>{value}</span>
    </div>
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

const secondaryButton = {
  ...primaryButton,
  background: 'transparent',
  color: 'var(--on-surface)',
  border: '1px solid var(--outline)',
  fontWeight: 600,
}

const statsCardStyle = {
  background: 'var(--glass-bg)',
  border: '1px solid var(--glass-border)',
  borderRadius: '16px',
  padding: '20px',
  width: '100%',
  maxWidth: '360px',
  display: 'flex',
  flexDirection: 'column',
  gap: '10px',
}

const statRowStyle = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
}

const statValueStyle = {
  fontFamily: 'var(--font-mono)',
  fontSize: '13px',
  color: 'var(--on-surface)',
}
