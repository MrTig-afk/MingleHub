import { useState } from 'react'
import { fetchTableDetail } from '../../services/dashboardApi'
import {
  buttonStyle,
  buttonSecondaryStyle,
  cardStyle,
  chipStyle,
  formatDuration,
  labelStyle,
} from './dashboardStyles'
import usePolling from './usePolling'

const shimmerCard = (height = 100) => ({
  ...cardStyle,
  height,
  animation: 'dev-shimmer 1.5s infinite',
  background: 'var(--bg-container)',
  marginBottom: '12px',
})

const PAIRED_CHIP = { background: 'rgba(0,238,252,0.15)', color: 'var(--secondary)' }
const UNPAIRED_CHIP = { background: 'rgba(255,215,0,0.15)', color: '#FFD700' }

const smallChip = (extra) => ({
  fontSize: '11px',
  padding: '2px 8px',
  borderRadius: '10px',
  fontWeight: 700,
  ...extra,
})

const END_REASON_LABELS = {
  manual: 'Ended',
  idle_timeout: 'Timed out',
  retap_expired: 'Re-tap expired',
  dev_reset: 'Reset',
}

function SessionCard({ session }) {
  const [showAllRounds, setShowAllRounds] = useState(false)

  const roundTypeLabel = session.current_round_type
    ? session.current_round_type.charAt(0).toUpperCase() + session.current_round_type.slice(1)
    : null

  let roundInfo
  if (!session.current_round_number) {
    roundInfo = 'Not started'
  } else {
    roundInfo = roundTypeLabel
      ? `Round ${session.current_round_number} -- ${roundTypeLabel}`
      : `Round ${session.current_round_number}`
  }

  const rounds = session.round_history || []
  const displayRounds = showAllRounds ? rounds : rounds.slice(-10)

  return (
    <div style={{ ...cardStyle, marginBottom: '12px' }}>
      {/* Top row: group label + status chip */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
        <span style={{ fontWeight: 700 }}>{session.group_label || 'Active session'}</span>
        <span style={chipStyle(session.status)}>{session.status}</span>
      </div>

      {/* Stats row */}
      <div style={{ fontSize: '13px', color: 'var(--on-surface-dim)', display: 'flex', gap: '12px', flexWrap: 'wrap', marginBottom: '4px' }}>
        <span>{roundInfo}</span>
        {session.status !== 'lobby' && <span>{formatDuration(session.seconds_active)}</span>}
        {session.host_name && <span>Host: {session.host_name}</span>}
      </div>

      {/* Leaderboard */}
      <div style={{ marginTop: '12px' }}>
        <div style={{ fontWeight: 700, fontSize: '13px', marginBottom: '4px' }}>Leaderboard</div>
        {(session.leaderboard || []).map((player) => (
          <div
            key={player.name}
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              padding: '4px 0',
              opacity: player.left_early ? 0.5 : 1,
            }}
          >
            <span style={{ fontSize: '13px' }}>
              {player.name}{player.left_early ? ' (left)' : ''}
            </span>
            <span style={{ fontSize: '13px', fontWeight: 700 }}>{player.score}</span>
          </div>
        ))}
        {(session.leaderboard || []).length === 0 && (
          <div style={{ fontSize: '13px', color: 'var(--on-surface-dim)' }}>No players yet.</div>
        )}
      </div>

      {/* Round history */}
      <div style={{ marginTop: '12px' }}>
        <div style={{ fontWeight: 700, fontSize: '13px', marginBottom: '4px' }}>Rounds</div>
        {rounds.length === 0 && (
          <div style={{ fontSize: '12px', color: 'var(--on-surface-dim)' }}>No rounds yet.</div>
        )}
        {rounds.length > 10 && !showAllRounds && (
          <div style={{ fontSize: '12px', color: 'var(--on-surface-dim)', marginBottom: '4px' }}>
            Showing last 10 of {rounds.length}.{' '}
            <span
              style={{ color: 'var(--primary)', cursor: 'pointer' }}
              onClick={() => setShowAllRounds(true)}
            >
              Show all ({rounds.length})
            </span>
          </div>
        )}
        {displayRounds.map((round) => (
          <div
            key={round.round_number}
            style={{
              display: 'flex',
              gap: '8px',
              fontSize: '12px',
              color: 'var(--on-surface-dim)',
              padding: '2px 0',
              alignItems: 'center',
            }}
          >
            <span style={{ fontWeight: 700, minWidth: '28px' }}>R{round.round_number}</span>
            <span>{round.round_type.charAt(0).toUpperCase() + round.round_type.slice(1)}</span>
            <span>{round.result}</span>
            {round.score_awarded > 0 && (
              <span style={{
                background: 'rgba(0,238,252,0.15)',
                color: 'var(--secondary)',
                fontSize: '11px',
                padding: '1px 6px',
                borderRadius: '8px',
                fontWeight: 700,
              }}>
                +{round.score_awarded}
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

export default function DashboardTableDetail({ token, tableId, navigate, user }) {
  const { data, status, error, reload } = usePolling(
    () => fetchTableDetail(token, tableId),
    { intervalMs: 7000, tokenKey: 'mh_dashboard_token' }
  )

  if (status === 'loading') {
    return (
      <div>
        {[1, 2].map((i) => <div key={i} style={shimmerCard()} />)}
      </div>
    )
  }

  if (status === 'error') {
    const is404 = error && (error.includes('404') || error.includes('not found') || error.includes('Not found'))
    return (
      <div style={{ ...cardStyle, marginTop: '8px' }}>
        {is404
          ? (
            <>
              <p style={{ color: 'var(--on-surface-dim)', margin: '0 0 12px' }}>Table not found.</p>
              <span
                style={{ fontSize: '14px', color: 'var(--primary)', cursor: 'pointer' }}
                onClick={() => navigate('/dashboard/tables')}
              >
                &lt; Tables
              </span>
            </>
          )
          : (
            <>
              <p style={{ color: 'var(--tertiary)', fontFamily: 'var(--font-mono)', fontSize: '13px', margin: '0 0 12px' }}>
                Could not load table. {error}
              </p>
              <button
                onClick={reload}
                style={buttonStyle}
              >
                Retry
              </button>
            </>
          )
        }
      </div>
    )
  }

  const table = data?.table || {}
  const tag = data?.tag || null
  const activeSessions = data?.active_sessions || []
  const recentSessions = data?.recent_sessions || []

  return (
    <div>
      {/* Back link */}
      <div style={{ marginBottom: '12px' }}>
        <span
          style={{ fontSize: '14px', color: 'var(--primary)', cursor: 'pointer' }}
          onClick={() => navigate('/dashboard/tables')}
        >
          &lt; Tables
        </span>
      </div>

      {/* Reconnecting banner */}
      {status === 'reconnecting' && (
        <p style={{ fontSize: '12px', color: 'var(--on-surface-dim)', margin: '0 0 8px' }}>
          Reconnecting...
        </p>
      )}

      {/* Header card */}
      <div style={cardStyle}>
        <div style={{ fontFamily: 'var(--font-headline)', fontSize: '20px', marginBottom: '4px' }}>
          Table {table.table_number}
        </div>
        <div style={{ ...labelStyle, marginBottom: '8px' }}>
          {table.content_ceiling === 'adults_allowed' ? 'Adults allowed' : 'Standard'}
        </div>
        {tag
          ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{ fontSize: '13px', color: 'var(--on-surface-dim)' }}>
                Tag: {tag.tag_uid.length > 8 ? tag.tag_uid.slice(0, 8) + '...' : tag.tag_uid}
              </span>
              <span style={smallChip(tag.status === 'active' ? PAIRED_CHIP : UNPAIRED_CHIP)}>
                {tag.status}
              </span>
            </div>
          )
          : (
            <span style={{ fontSize: '13px', ...smallChip(UNPAIRED_CHIP) }}>No tag paired</span>
          )
        }
      </div>

      {/* Active sessions */}
      <h2 style={{ fontFamily: 'var(--font-headline)', fontSize: '16px', marginTop: '20px', marginBottom: '12px' }}>
        Active Games
      </h2>

      {activeSessions.length === 0
        ? <p style={{ color: 'var(--on-surface-dim)' }}>No active games at this table.</p>
        : activeSessions.map((session) => (
          <SessionCard key={session.session_id} session={session} />
        ))
      }

      {/* Recent ended sessions tonight */}
      <h2 style={{ fontFamily: 'var(--font-headline)', fontSize: '16px', marginTop: '20px', marginBottom: '12px' }}>
        Earlier Tonight
      </h2>

      {recentSessions.length === 0
        ? <p style={{ color: 'var(--on-surface-dim)' }}>No completed games tonight.</p>
        : recentSessions.map((session) => {
          const endLabel = END_REASON_LABELS[session.end_reason] || session.end_reason || 'Ended'
          const endTime = session.ended_at
            ? new Date(session.ended_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
            : '--'
          return (
            <div key={session.session_id} style={{ ...cardStyle, marginBottom: '8px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
                <span style={{ fontWeight: 700 }}>{session.group_label || 'Session'}</span>
                <span style={smallChip({ background: 'var(--bg-container)', color: 'var(--on-surface-dim)' })}>
                  {endLabel}
                </span>
              </div>
              <div style={{ fontSize: '13px', color: 'var(--on-surface-dim)', display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
                <span>{session.player_count} players</span>
                <span>{session.total_rounds} rounds</span>
                <span>{session.total_score} pts</span>
                <span>{endTime}</span>
              </div>
            </div>
          )
        })
      }

      {/* Owner-only action buttons */}
      {user && user.role === 'venue_owner' && (
        <div style={{ display: 'flex', gap: '8px', marginTop: '24px' }}>
          <button
            onClick={() => navigate('/dashboard/pair-tags')}
            style={buttonSecondaryStyle}
          >
            Re-pair Tag
          </button>
          <button
            onClick={() => navigate('/dashboard/pair-tags')}
            style={{ ...buttonSecondaryStyle, color: 'var(--tertiary)' }}
          >
            Reset Table (dev)
          </button>
        </div>
      )}
    </div>
  )
}
