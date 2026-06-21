import { useEffect, useState } from 'react'
import { fetchTables } from '../../services/dashboardApi'
import { buttonStyle, cardStyle, labelStyle } from './dashboardStyles'

const shimmerCard = (height = 64) => ({
  ...cardStyle,
  height,
  animation: 'dev-shimmer 1.5s infinite',
  background: 'var(--bg-container)',
  marginBottom: '12px',
})

const PAIRED_CHIP = { background: 'rgba(0,238,252,0.15)', color: 'var(--secondary)' }
const UNPAIRED_CHIP = { background: 'rgba(255,215,0,0.15)', color: '#FFD700' }
const LIVE_CHIP = { background: 'rgba(0,238,252,0.15)', color: 'var(--secondary)' }
const IDLE_CHIP = { color: 'var(--on-surface-dim)' }

const smallChip = (extra) => ({
  fontSize: '11px',
  padding: '2px 8px',
  borderRadius: '10px',
  fontWeight: 700,
  ...extra,
})

export default function DashboardTables({ token, navigate }) {
  const [tables, setTables] = useState(null)
  const [status, setStatus] = useState('loading')
  const [error, setError] = useState(null)
  // Bumped by Retry to re-trigger the fetch effect (deps otherwise unchanged).
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    const run = async () => {
      try {
        const data = await fetchTables(token)
        if (cancelled) return
        setTables(data)
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
  }, [token, reloadKey])

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
          onClick={() => { setStatus('loading'); setError(null); setReloadKey((k) => k + 1) }}
          style={buttonStyle}
        >
          Retry
        </button>
      </div>
    )
  }

  if (!tables || tables.length === 0) {
    return (
      <div style={{ ...cardStyle, marginTop: '8px', textAlign: 'center' }}>
        <p style={{ color: 'var(--on-surface-dim)', margin: '0 0 12px' }}>No tables configured yet.</p>
        <button onClick={() => navigate('/dashboard/pair-tags')} style={buttonStyle}>
          Pair NFC Tags
        </button>
      </div>
    )
  }

  return (
    <div>
      <h2 style={{ fontFamily: 'var(--font-headline)', fontSize: '18px', marginBottom: '12px', marginTop: 0 }}>
        Tables
      </h2>

      {tables.map((table) => (
        <div key={table.id}>
          <div
            style={{ ...cardStyle, cursor: 'pointer', marginBottom: table.tag_paired ? '12px' : '4px' }}
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
                {table.tag_paired
                  ? <span style={smallChip(PAIRED_CHIP)}>Paired</span>
                  : <span style={smallChip(UNPAIRED_CHIP)}>Unpaired</span>
                }
                {table.active_session_count > 0
                  ? <span style={smallChip(LIVE_CHIP)}>{table.active_session_count} live</span>
                  : <span style={smallChip(IDLE_CHIP)}>Idle</span>
                }
                <span style={{ color: 'var(--on-surface-dim)', fontSize: '18px' }}>&rsaquo;</span>
              </div>
            </div>
          </div>
          {!table.tag_paired && (
            <div style={{ marginBottom: '12px', paddingLeft: '4px' }}>
              <span
                style={{ fontSize: '12px', color: 'var(--primary)', cursor: 'pointer' }}
                onClick={() => navigate('/dashboard/pair-tags')}
              >
                Pair this table
              </span>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
