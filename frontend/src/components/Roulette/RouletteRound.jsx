import { useEffect, useRef, useState } from 'react'
import { revealRoulette, skipRoulette, startRoulette, voteLoser } from '../../services/patronApi'
import useSessionChannel from '../../hooks/useSessionChannel'

const SPIN_MS = 2000
const READ_MS = 10000 // time to read the challenge before voting opens
const RESULT_AUTO_ADVANCE_MS = 3000
const VOTE_TIMEOUT_MS = 30000 // force a tally if the table forgets to Reveal

// gamespec Round Type 3 -- Roulette, from the session-origin phone's side.
// The whole table plays the same challenge; everyone votes on who lost.
//
// Phase state machine:
//   starting -> spin -> card -> voting -> result -> (onDone)
//
// StrictMode safety: startedRef prevents the mount effect from running twice
// (React StrictMode double-invokes effects in dev). Keyed by roundNumber in
// RoundOrigin so advancing to the next Roulette round remounts cleanly.
export default function RouletteRound({ sessionId, phoneId, tableId, onDone }) {
  const [phase, setPhase] = useState('starting')
  const [roundId, setRoundId] = useState(null)
  const [prompt, setPrompt] = useState('')
  const [drinkConsequence, setDrinkConsequence] = useState('')
  const [players, setPlayers] = useState([])
  const [votedCount, setVotedCount] = useState(0)
  const [activeTotal, setActiveTotal] = useState(0)
  const [myVote, setMyVote] = useState(null) // playerId this phone voted for
  const [result, setResult] = useState(null) // { skipped, losers, tallies, pointsAwarded }
  const [readSecondsLeft, setReadSecondsLeft] = useState(Math.ceil(READ_MS / 1000))
  const [error, setError] = useState(null)

  const startedRef = useRef(false)
  const resultTimerRef = useRef(null)

  // Start exactly once on mount -- StrictMode guard mirrors TriviaOriginRound
  useEffect(() => {
    if (startedRef.current) return
    startedRef.current = true

    queueMicrotask(async () => {
      try {
        const data = await startRoulette(sessionId, phoneId)
        setRoundId(data.round_id)
        setPrompt(data.prompt)
        setDrinkConsequence(data.drink_consequence)
        setPlayers(data.players || [])
        setVotedCount(data.voted_count || 0)
        setActiveTotal(data.active_total || 0)
        setPhase('spin')
      } catch (e) {
        if (e.message === 'not_enough_players') {
          // Not enough players -- let RoundOrigin fall back to Chooser
          onDone()
        } else {
          setError(e.message)
        }
      }
    })
  }, [sessionId, phoneId, onDone])

  // Spin phase: short animation, then reveal the card
  useEffect(() => {
    if (phase !== 'spin') return
    const id = setTimeout(() => setPhase('card'), SPIN_MS)
    return () => clearTimeout(id)
  }, [phase])

  // Card phase: ~10s to read the challenge out loud, then voting opens itself
  // (no manual tap). Ticks the visible countdown; the timeout flips to voting.
  useEffect(() => {
    if (phase !== 'card') return
    const tick = setInterval(() => setReadSecondsLeft((s) => Math.max(0, s - 1)), 1000)
    const done = setTimeout(() => setPhase('voting'), READ_MS)
    return () => { clearInterval(tick); clearTimeout(done) }
  }, [phase])

  // Auto-advance from result after 3s
  useEffect(() => {
    if (phase !== 'result') return
    resultTimerRef.current = setTimeout(() => onDone(), RESULT_AUTO_ADVANCE_MS)
    return () => clearTimeout(resultTimerRef.current)
  }, [phase, onDone])

  // Realtime: update vote progress and receive the final result
  useSessionChannel(tableId, phoneId, (event, payload) => {
    if (event === 'roulette:vote') {
      setVotedCount(payload?.voted_count ?? votedCount)
      setActiveTotal(payload?.active_total ?? activeTotal)
    }
    if (event === 'roulette:result') {
      setResult({
        skipped: payload?.skipped ?? false,
        losers: payload?.losers || [],
        tallies: payload?.tallies || {},
        pointsAwarded: payload?.points_awarded || 0,
      })
      setPhase('result')
    }
  })

  const handleVote = async (playerId) => {
    setError(null)
    setMyVote(playerId)
    try {
      const data = await voteLoser(roundId, phoneId, playerId)
      if (data.auto_tallied) {
        // Result will arrive via roulette:result broadcast; the auto-tally
        // response also carries the tally so we can display immediately.
        setResult({
          skipped: false,
          losers: data.losers || [],
          tallies: data.tallies || {},
          pointsAwarded: data.points_awarded || 0,
        })
        setPhase('result')
      } else {
        setVotedCount(data.voted_count || 0)
        setActiveTotal(data.active_total || 0)
      }
    } catch (e) {
      setMyVote(null)
      setError(e.message)
    }
  }

  const handleReveal = async () => {
    setError(null)
    try {
      const data = await revealRoulette(roundId, phoneId)
      setResult({
        skipped: false,
        losers: data.losers || [],
        tallies: data.tallies || {},
        pointsAwarded: data.points_awarded || 0,
      })
      setPhase('result')
    } catch (e) {
      setError(e.message)
    }
  }

  const handleSkip = async () => {
    setError(null)
    try {
      await skipRoulette(roundId, phoneId)
      setResult({ skipped: true, losers: [], tallies: {}, pointsAwarded: 0 })
      setPhase('result')
    } catch (e) {
      setError(e.message)
    }
  }

  // Safety net: if not everyone votes and the origin forgets to Reveal, force a
  // tally after 30s so the round can't hang. The ref (synced in an effect, not
  // during render) keeps the latest handleReveal without restarting the timer.
  const revealRef = useRef(handleReveal)
  useEffect(() => { revealRef.current = handleReveal })
  useEffect(() => {
    if (phase !== 'voting') return
    const id = setTimeout(() => revealRef.current?.(), VOTE_TIMEOUT_MS)
    return () => clearTimeout(id)
  }, [phase])

  // --- Starting ---
  if (phase === 'starting') {
    return (
      <div style={overlayStyle}>
        <p style={dimMonoStyle}>Starting Roulette…</p>
        {error && <p style={errStyle}>{error}</p>}
      </div>
    )
  }

  // --- Spin animation ---
  if (phase === 'spin') {
    return (
      <div style={overlayStyle}>
        <div style={{ width: '46px', height: '46px', borderRadius: '50%', border: '3px solid var(--line)', borderTopColor: 'var(--primary)', boxShadow: '0 0 18px rgba(255,45,120,0.35)', animation: 'spin 0.6s linear infinite' }} />
        <h1 style={headlineStyle}>Roulette!</h1>
        <p style={dimMonoStyle}>Picking a challenge…</p>
        <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
      </div>
    )
  }

  // --- Card shown (read the challenge, then move to voting) ---
  if (phase === 'card') {
    return (
      <div style={overlayStyle}>
        <div style={{ fontFamily: 'var(--font-display)', fontSize: '13px', letterSpacing: '0.22em', color: 'var(--primary)', textShadow: '0 0 12px rgba(255,45,120,0.5)' }}>ROULETTE</div>
        <h1 style={headlineStyle}>Challenge!</h1>
        <div style={cardStyle}>
          <p style={{ fontSize: '18px', fontFamily: 'var(--font-headline)', lineHeight: 1.4, margin: 0 }}>
            {prompt}
          </p>
          <p style={{ ...dimMonoStyle, marginTop: '12px' }}>
            Loser: {drinkConsequence}
          </p>
        </div>
        <p style={dimMonoStyle}>Read it out — voting opens in {readSecondsLeft}s</p>
        {error && <p style={errStyle}>{error}</p>}
      </div>
    )
  }

  // --- Voting: origin also votes ---
  if (phase === 'voting') {
    return (
      <div style={overlayStyle}>
        <div style={{ fontFamily: 'var(--font-display)', fontSize: '13px', letterSpacing: '0.22em', color: 'var(--primary)', textShadow: '0 0 12px rgba(255,45,120,0.5)' }}>ROULETTE</div>
        <h1 style={headlineStyle}>Vote for the loser</h1>
        <p style={dimMonoStyle}>{votedCount}/{activeTotal} voted</p>
        {myVote ? (
          <p style={dimMonoStyle}>
            You voted for {players.find((p) => p.id === myVote)?.name || '—'} — waiting for the others…
          </p>
        ) : (
          <div style={buttonGroupStyle}>
            {players.map((p) => (
              <button key={p.id} onClick={() => handleVote(p.id)} style={primaryButtonStyle}>
                {p.name}
              </button>
            ))}
          </div>
        )}
        {error && <p style={errStyle}>{error}</p>}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', width: '100%', maxWidth: '320px' }}>
          <button onClick={handleReveal} style={revealButtonStyle}>
            Reveal now
          </button>
          <button onClick={handleSkip} style={secondaryButtonStyle}>
            No clear loser? Skip
          </button>
        </div>
      </div>
    )
  }

  // --- Result ---
  if (phase === 'result' && result) {
    if (result.skipped) {
      return (
        <div style={overlayStyle}>
          <p style={{ fontSize: '48px', margin: 0 }}>⏭️</p>
          <h2 style={headlineStyle}>Skipped</h2>
        </div>
      )
    }
    const loserNames = result.losers.map((l) => l.name).join(', ') || 'Nobody'
    return (
      <div style={overlayStyle}>
        <div style={{ fontFamily: 'var(--font-display)', fontSize: '13px', letterSpacing: '0.22em', color: 'var(--primary)', textShadow: '0 0 12px rgba(255,45,120,0.5)' }}>ROULETTE</div>
        <h2 style={headlineStyle}>
          {result.losers.length > 0 ? `${loserNames} lost!` : 'All tied!'}
        </h2>
        {result.losers.length > 0 && (
          <p style={dimMonoStyle}>{drinkConsequence}</p>
        )}
        {result.pointsAwarded > 0 && (
          <p style={{ ...dimMonoStyle, marginTop: '4px' }}>+3 for everyone else</p>
        )}
      </div>
    )
  }

  return (
    <div style={overlayStyle}>
      <p style={dimMonoStyle}>Loading…</p>
      {error && <p style={errStyle}>{error}</p>}
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
  gap: '16px',
  padding: '24px',
  textAlign: 'center',
}

const headlineStyle = { fontFamily: 'var(--font-headline)', fontSize: '26px', margin: 0 }

const dimMonoStyle = {
  fontFamily: 'var(--font-mono)',
  fontSize: '13px',
  color: 'var(--on-surface-dim)',
  margin: 0,
}

const errStyle = { color: 'var(--tertiary)', fontFamily: 'var(--font-mono)', fontSize: '12px', margin: 0 }

const cardStyle = {
  background: 'var(--glass-bg)',
  border: '1px solid var(--glass-border)',
  borderRadius: '16px',
  padding: '24px',
  maxWidth: '360px',
  width: '100%',
  textAlign: 'left',
}

const buttonGroupStyle = {
  display: 'flex',
  flexDirection: 'column',
  gap: '10px',
  width: '100%',
  maxWidth: '320px',
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

const revealButtonStyle = {
  padding: '14px',
  borderRadius: '10px',
  background: 'var(--secondary, #6c63ff)',
  color: '#fff',
  fontWeight: 600,
  fontSize: '14px',
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
