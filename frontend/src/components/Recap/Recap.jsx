import { useCallback, useEffect, useState } from 'react'
import Leaderboard from '../Trivia/Leaderboard'
import { fetchRecap, fetchNewGame } from '../../services/patronApi'
import useSessionChannel from '../../hooks/useSessionChannel'

// Terminal recap screen shown after the game ends (via End Game button,
// game_ended broadcast, idle timeout, or re-tap on a recently-ended session).
// Fetches aggregated stats and renders them. No round controls.
// tableId + phoneId enable "new game starting" detection via poll fallback
// (realtime is best-effort; fetchChannelAuth rejects ended sessions).
export default function Recap({ sessionId, venueName, tableId, phoneId }) {
  const [recap, setRecap] = useState(null)
  const [error, setError] = useState(null)
  const [copied, setCopied] = useState(false)
  const [newGameAvailable, setNewGameAvailable] = useState(false)
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

  // Realtime best-effort: fetchChannelAuth checks active membership, which
  // fails for ended sessions (403). The hook handles the error silently and
  // stays disconnected. Wired here so it works if channel-auth is relaxed later.
  useSessionChannel(tableId, phoneId, useCallback((event) => {
    if (event === 'lobby_update') {
      setNewGameAvailable(true)
    }
  }, []))

  // Poll fallback — primary detection path since realtime won't work on Recap.
  // Stops polling once a new game is detected (interval cleaned up on true flip).
  useEffect(() => {
    if (!tableId || newGameAvailable) return
    let cancelled = false
    const tick = async () => {
      try {
        const data = await fetchNewGame(tableId, sessionId)
        if (!cancelled && (data.lobby_id || data.session_id)) {
          setNewGameAvailable(true)
        }
      } catch {
        // Transient failure -- keep polling
      }
    }
    const id = setInterval(tick, 2500)
    tick() // immediate first check
    return () => { cancelled = true; clearInterval(id) }
  }, [tableId, sessionId, newGameAvailable])

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
        <GameOverTitle venue={venueName} />
        <p style={dimMono}>{error}</p>
      </Screen>
    )
  }

  if (!recap) {
    return (
      <Screen>
        <GameOverTitle venue={venueName} />
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
      <GameOverTitle venue={recap.venue_name} />

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

      {newGameAvailable && (
        <button
          onClick={() => { window.location.href = `${window.location.pathname}?newgame=1` }}
          style={joinButton}
        >
          New game starting — Join
        </button>
      )}
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

// Gold "GAME OVER" wordmark with the venue as a mono eyebrow — gold is the
// winner/results color in After Dark. Shared by the loading, error, and recap views.
function GameOverTitle({ venue }) {
  return (
    <div style={{ textAlign: 'center' }}>
      <div style={{
        fontFamily: 'var(--font-mono)', fontSize: '11px', letterSpacing: '0.16em',
        textTransform: 'uppercase', color: 'var(--on-surface-dim)', marginBottom: '8px',
      }}>
        {venue}
      </div>
      <h1 style={{
        fontFamily: 'var(--font-display)', fontSize: '46px', color: 'var(--gold)',
        letterSpacing: '0.02em', lineHeight: 1, margin: 0,
        textShadow: '0 0 28px rgba(255, 200, 87, 0.45)',
      }}>
        GAME OVER
      </h1>
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

const joinButton = {
  ...primaryButton,
  background: 'var(--secondary)',
  color: 'var(--bg-floor)',
  animation: 'pulse-dot 1.4s infinite',
}

const secondaryButton = {
  ...primaryButton,
  background: 'transparent',
  color: 'var(--on-surface)',
  border: '1px solid var(--outline)',
  fontWeight: 600,
}

const statsCardStyle = {
  background: 'var(--bg-surface)',
  border: '1.5px solid var(--line)',
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
