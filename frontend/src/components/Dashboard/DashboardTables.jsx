import { fetchTables } from '../../services/dashboardApi'
import { buttonStyle, cardStyle, labelStyle } from './dashboardStyles'
import usePolling from './usePolling'

const shimmerCard = (height = 64) => ({
  ...cardStyle,
  height,
  animation: 'dev-shimmer 1.5s infinite',
  background: 'var(--bg-container)',
  marginBottom: '12px',
})

const LIVE_CHIP = { background: 'rgba(57,224,139,0.12)', color: 'var(--correct)', border: '1px solid rgba(57,224,139,0.35)' }
const IDLE_CHIP = { color: 'var(--on-surface-dim)', border: '1px solid var(--line)' }

const smallChip = (extra) => ({
  fontFamily: 'var(--font-mono)',
  fontSize: '10px',
  letterSpacing: '0.06em',
  textTransform: 'uppercase',
  padding: '3px 9px',
  borderRadius: '6px',
  fontWeight: 500,
  ...extra,
})

export default function DashboardTables({ token, navigate }) {
  // SWR: seeded instantly from cache on revisit, then revalidated + polled.
  const { data: tables, status, error, reload } = usePolling(
    () => fetchTables(token),
    { intervalMs: 7000, tokenKey: 'mh_dashboard_token', cacheKey: 'dash:tables' },
  )

  if (status === 'loading') {
    return (
      <div>
        {[1, 2, 3].map((i) => <div key={i} style={shimmerCard()} />)}
      </div>
    )
  }

  if (status === 'error') {
    return (
      <div style={{ ...cardStyle, marginTop: '8px' }}>
        <p style={{ color: 'var(--tertiary)', fontFamily: 'var(--font-mono)', fontSize: '13px', margin: '0 0 12px' }}>
          Could not load tables. {error}
        </p>
        <button
          onClick={reload}
          style={buttonStyle}
        >
          Retry
        </button>
      </div>
    )
  }

  // Every table the venue has is shown — tags ship pre-written per table, so
  // there is no "unconfigured" table to hide.
  const visibleTables = tables || []

  if (visibleTables.length === 0) {
    return (
      <div style={{ ...cardStyle, marginTop: '8px', textAlign: 'center' }}>
        <p style={{ color: 'var(--on-surface-dim)', margin: 0 }}>
          No tables yet.
        </p>
      </div>
    )
  }

  return (
    <div>
      <h2 style={{ fontFamily: 'var(--font-headline)', fontSize: '18px', marginBottom: '12px', marginTop: 0 }}>
        Tables
      </h2>

      {visibleTables.map((table) => (
        <div key={table.id}>
          <div
            style={{ ...cardStyle, cursor: 'pointer', marginBottom: '12px' }}
            onClick={() => navigate(`/dashboard/tables/${table.id}`)}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <div style={{ fontWeight: 700 }}>Table {table.table_number}</div>
                <div style={labelStyle}>
                  {table.content_ceiling === 'adults_allowed' ? 'Adults allowed' : 'Standard'}
                </div>
              </div>
              <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
                {table.active_session_count > 0
                  ? <span style={smallChip(LIVE_CHIP)}>{table.active_session_count} live</span>
                  : <span style={smallChip(IDLE_CHIP)}>Idle</span>
                }
                <span style={{ color: 'var(--on-surface-dim)', fontSize: '18px' }}>&rsaquo;</span>
              </div>
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}
