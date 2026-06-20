import { useEffect, useState } from 'react'
import { fetchOverview } from '../../services/dashboardApi'
import { buttonStyle, cardStyle, chipStyle, formatDuration } from './dashboardStyles'

const shimmerCard = (height = 80) => ({
  ...cardStyle,
  height,
  animation: 'dev-shimmer 1.5s infinite',
  background: 'var(--bg-container)',
})

export default function DashboardHome({ token, navigate }) {
  const [data, setData] = useState(null)
  const [status, setStatus] = useState('loading') // loading | ready | error | reconnecting
  const [error, setError] = useState(null)

  const load = () => {
    setStatus('loading')
    fetchOverview(token)
      .then((d) => {
        setData(d)
        setStatus('ready')
      })
      .catch((e) => {
        const msg = e.message || ''
        if (msg.includes('401') || msg.includes('token') || msg.includes('expired')) {
          localStorage.removeItem('mh_dashboard_token')
          window.location.reload()
          return
        }
        setStatus('error')
        setError(msg)
      })
  }

  // Initial fetch. All setState happens inside the async resolution (after the
  // await), never synchronously in the effect body — the same mount pattern as
  // PatronLanding (avoids react-hooks/set-state-in-effect cascading renders).
  useEffect(() => {
    let cancelled = false
    const run = async () => {
      try {
        const d = await fetchOverview(token)
        if (cancelled) return
        setData(d)
        setStatus('ready')
      } catch (e) {
        if (cancelled) return
        const msg = e.message || ''
        if (msg.includes('401') || msg.includes('token') || msg.includes('expired')) {
          localStorage.removeItem('mh_dashboard_token')
          window.location.reload()
          return
        }
        setStatus('error')
        setError(msg)
      }
    }
    run()
    return () => { cancelled = true }
  }, [token])

  // Poll every 7 seconds once the initial fetch has settled
  useEffect(() => {
    if (status === 'loading' || !token) return
    const id = setInterval(() => {
      fetchOverview(token)
        .then((d) => {
          setData(d)
          setStatus('ready')
        })
        .catch((e) => {
          const msg = e.message || ''
          if (msg.includes('401') || msg.includes('token') || msg.includes('expired')) {
            localStorage.removeItem('mh_dashboard_token')
            window.location.reload()
            return
          }
          // Keep last data visible, signal reconnecting
          setStatus('reconnecting')
        })
    }, 7000)
    return () => clearInterval(id)
  }, [status, token])

  if (status === 'loading') {
    return (
      <div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
          {[1, 2, 3, 4].map((i) => <div key={i} style={shimmerCard(80)} />)}
        </div>
        <div style={{ marginTop: '16px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
          {[1, 2].map((i) => <div key={i} style={shimmerCard(60)} />)}
        </div>
      </div>
    )
  }

  if (status === 'error') {
    return (
      <div style={{ ...cardStyle, marginTop: '8px' }}>
        <p style={{ color: 'var(--tertiary)', fontFamily: 'var(--font-mono)', fontSize: '13px', margin: '0 0 12px' }}>
          Could not load dashboard data. {error}
        </p>
        <button onClick={load} style={buttonStyle}>Retry</button>
      </div>
    )
  }

  const tonight = data?.tonight || {}
  const sessions = data?.active_sessions || []
  const noTablesNight = tonight.active_tables === 0 && tonight.sessions_tonight === 0

  const tonightCards = [
    { value: tonight.active_tables,   label: 'active now' },
    { value: tonight.players_tonight, label: 'players tonight' },
    { value: tonight.rounds_tonight,  label: 'rounds tonight' },
    { value: tonight.sessions_tonight, label: 'sessions tonight' },
  ]

  return (
    <div>
      {status === 'reconnecting' && (
        <p style={{ fontSize: '12px', color: 'var(--on-surface-dim)', margin: '0 0 8px' }}>
          Reconnecting...
        </p>
      )}

      {/* Tonight stat cards */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
        {tonightCards.map(({ value, label }) => (
          <div key={label} style={cardStyle}>
            <div style={{ fontFamily: 'var(--font-headline)', fontSize: '28px', color: 'var(--on-surface)' }}>
              {value ?? 0}
            </div>
            <div style={{ fontSize: '13px', color: 'var(--on-surface-dim)' }}>{label}</div>
          </div>
        ))}
      </div>

      {/* No tables CTA */}
      {noTablesNight && (
        <div style={{ ...cardStyle, marginTop: '16px', textAlign: 'center' }}>
          <p style={{ color: 'var(--on-surface-dim)', margin: '0 0 12px' }}>No tables set up yet.</p>
          <button onClick={() => navigate('/dashboard/pair-tags')} style={buttonStyle}>
            Pair NFC Tags
          </button>
        </div>
      )}

      {/* Live sessions section */}
      {!noTablesNight && (
        <>
          <h2 style={{ fontFamily: 'var(--font-headline)', fontSize: '18px', margin: '24px 0 12px' }}>
            Live Tables
          </h2>

          {sessions.length === 0 && tonight.sessions_tonight > 0 && (
            <p style={{ color: 'var(--on-surface-dim)', textAlign: 'center', padding: '16px 0' }}>
              No active games right now.
            </p>
          )}

          {sessions.length === 0 && tonight.sessions_tonight === 0 && !noTablesNight && (
            <p style={{ color: 'var(--on-surface-dim)', textAlign: 'center', padding: '32px 0' }}>
              No games tonight yet. Sessions will appear here when patrons start playing.
            </p>
          )}

          {sessions.map((s) => {
            const isLobby = s.status === 'lobby'
            const roundTypeLabel = s.current_round_type
              ? s.current_round_type.charAt(0).toUpperCase() + s.current_round_type.slice(1)
              : null

            let roundInfo
            if (isLobby) {
              roundInfo = `Lobby forming (${s.player_count})`
            } else if (!s.current_round_number) {
              roundInfo = 'Not started'
            } else {
              roundInfo = roundTypeLabel
                ? `Round ${s.current_round_number} -- ${roundTypeLabel}`
                : `Round ${s.current_round_number}`
            }

            return (
              <div key={s.session_id} style={{ ...cardStyle, marginBottom: '12px' }}>
                {/* Top row: table number + status chip */}
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
                  <span style={{ fontWeight: 700 }}>Table {s.table_number}</span>
                  <span style={chipStyle(s.status)}>{s.status}</span>
                </div>

                {/* Group label */}
                {s.group_label && (
                  <div style={{ fontSize: '13px', color: 'var(--on-surface-dim)', marginBottom: '4px' }}>
                    {s.group_label}
                  </div>
                )}

                {/* Stats row */}
                <div style={{ fontSize: '13px', color: 'var(--on-surface-dim)', display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
                  <span>{s.player_count} players</span>
                  <span>{roundInfo}</span>
                  {!isLobby && <span>{formatDuration(s.seconds_active)}</span>}
                </div>
              </div>
            )
          })}
        </>
      )}
    </div>
  )
}
