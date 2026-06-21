import { useEffect, useState } from 'react'
import { fetchOverview, fetchTables } from '../../services/dashboardApi'
import { buttonStyle, cardStyle, chipStyle, formatDuration, formatRelativeTime } from './dashboardStyles'
import usePolling from './usePolling'

const shimmerCard = (height = 80) => ({
  ...cardStyle,
  height,
  animation: 'dev-shimmer 1.5s infinite',
  background: 'var(--bg-container)',
})

export default function DashboardHome({ token, navigate }) {
  const { data, status, error, lastUpdatedAt, reload } = usePolling(
    () => Promise.all([fetchOverview(token), fetchTables(token)])
      .then(([overview, tables]) => ({ ...overview, tables })),
    { intervalMs: 7000, tokenKey: 'mh_dashboard_token' }
  )

  // Tick every 10 seconds so "updated N ago" re-renders without a new fetch.
  const [, setTick] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 10000)
    return () => clearInterval(id)
  }, [])

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
        <button onClick={reload} style={buttonStyle}>Retry</button>
      </div>
    )
  }

  const tonight = data?.tonight || {}
  const activeSessions = data?.active_sessions || []
  const pairedTables = (data?.tables || []).filter((t) => t.tag_paired)
  // Idle = a paired table with no live session right now. Active tables render
  // first (highlighted), idle ones below (dimmed) — both open the same detail.
  const idleTables = pairedTables.filter((t) => t.active_session_count === 0)
  const hasTables = pairedTables.length > 0 || activeSessions.length > 0

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

      {status === 'ready' && lastUpdatedAt && (
        <p style={{ fontSize: '11px', color: 'var(--on-surface-dim)', margin: '0 0 8px', textAlign: 'right' }}>
          Updated {formatRelativeTime(lastUpdatedAt)}
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

      {/* No tables configured at all */}
      {!hasTables && (
        <div style={{ ...cardStyle, marginTop: '16px', textAlign: 'center' }}>
          <p style={{ color: 'var(--on-surface-dim)', margin: '0 0 12px' }}>No tables set up yet.</p>
          <button onClick={() => navigate('/dashboard/pair-tags')} style={buttonStyle}>
            Pair NFC Tags
          </button>
        </div>
      )}

      {/* Tables: active (highlighted) first, idle (dimmed) below — both clickable */}
      {hasTables && (
        <>
          <h2 style={{ fontFamily: 'var(--font-headline)', fontSize: '18px', margin: '24px 0 12px' }}>
            Tables
          </h2>

          {activeSessions.length === 0 && (
            <p style={{ color: 'var(--on-surface-dim)', fontSize: '13px', margin: '0 0 12px' }}>
              No active games right now.
            </p>
          )}

          {activeSessions.map((s) => {
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
              <div
                key={s.session_id}
                style={{ ...cardStyle, marginBottom: '12px', cursor: s.table_id ? 'pointer' : 'default' }}
                onClick={s.table_id ? () => navigate(`/dashboard/tables/${s.table_id}`) : undefined}
              >
                {/* Top row: table number + status chip */}
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
                  <span style={{ fontWeight: 700 }}>
                    Table {s.table_number}
                    <span style={{ color: 'var(--on-surface-dim)', fontWeight: 400, marginLeft: '6px' }}>&rsaquo;</span>
                  </span>
                  <span style={chipStyle(s.status)}>
                    {s.status === 'active' && (
                      <span style={{
                        display: 'inline-block',
                        width: '6px',
                        height: '6px',
                        borderRadius: '50%',
                        background: 'var(--secondary)',
                        marginRight: '4px',
                        verticalAlign: 'middle',
                        animation: 'pulse-dot 2s ease-in-out infinite',
                      }} />
                    )}
                    {s.status}
                  </span>
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

          {idleTables.map((t) => (
            <div
              key={t.id}
              style={{ ...cardStyle, marginBottom: '8px', cursor: 'pointer', opacity: 0.5 }}
              onClick={() => navigate(`/dashboard/tables/${t.id}`)}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontWeight: 700 }}>
                  Table {t.table_number}
                  <span style={{ color: 'var(--on-surface-dim)', fontWeight: 400, marginLeft: '6px' }}>&rsaquo;</span>
                </span>
                <span style={{ fontSize: '12px', color: 'var(--on-surface-dim)' }}>idle</span>
              </div>
            </div>
          ))}
        </>
      )}
    </div>
  )
}
