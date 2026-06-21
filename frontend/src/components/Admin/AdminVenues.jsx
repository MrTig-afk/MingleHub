import { useEffect, useState } from 'react'
import { fetchAdminVenues } from '../../services/adminApi'
import { buttonStyle, cardStyle, labelStyle } from '../Dashboard/dashboardStyles'

const shimmerCard = (height = 64) => ({
  ...cardStyle,
  height,
  animation: 'dev-shimmer 1.5s infinite',
  background: 'var(--bg-container)',
  marginBottom: '12px',
})

const ACTIVE_CHIP = { background: 'rgba(0,238,252,0.15)', color: 'var(--secondary)' }
const DIM_CHIP = { color: 'var(--on-surface-dim)' }
const TEST_CHIP = { background: 'rgba(255,215,0,0.15)', color: '#FFD700' }

const smallChip = (extra) => ({
  fontSize: '11px',
  padding: '2px 8px',
  borderRadius: '10px',
  fontWeight: 700,
  ...extra,
})

export default function AdminVenues({ token }) {
  const [data, setData] = useState(null)
  const [status, setStatus] = useState('loading') // loading | ready | error
  const [error, setError] = useState(null)
  // Bumped by Retry to re-trigger the fetch effect (deps otherwise unchanged).
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    const run = async () => {
      try {
        const result = await fetchAdminVenues(token)
        if (cancelled) return
        setData(result)
        setStatus('ready')
      } catch (e) {
        if (cancelled) return
        const msg = e.message || ''
        if (msg.includes('401') || msg.includes('token') || msg.includes('expired')) {
          localStorage.removeItem('mh_admin_token')
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
          Could not load venues. {error}
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

  const venues = data?.venues || []

  if (venues.length === 0) {
    return (
      <div style={{ ...cardStyle, marginTop: '8px', textAlign: 'center' }}>
        <p style={{ color: 'var(--on-surface-dim)', margin: 0 }}>No venues found</p>
      </div>
    )
  }

  return (
    <div>
      <h2 style={{ fontFamily: 'var(--font-headline)', fontSize: '18px', marginBottom: '12px', marginTop: 0 }}>
        Venues
      </h2>

      {venues.map((venue) => (
        // TODO (Slice 5): make rows clickable -> /admin/venues/{id}
        <div key={venue.id} style={{ ...cardStyle, marginBottom: '12px' }}>
          {/* Top row: name + status/test chips */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
            <span style={{ fontWeight: 700 }}>{venue.name}</span>
            <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
              {venue.status === 'active'
                ? <span style={smallChip(ACTIVE_CHIP)}>{venue.status}</span>
                : <span style={smallChip(DIM_CHIP)}>{venue.status}</span>
              }
              {venue.is_test && (
                <span style={smallChip(TEST_CHIP)}>Test</span>
              )}
            </div>
          </div>
          {/* Slug + type */}
          <div style={{ ...labelStyle, marginBottom: '4px' }}>
            {venue.slug} -- {venue.venue_type}
          </div>
          {/* Stats row */}
          <div style={{ fontSize: '13px', color: 'var(--on-surface-dim)', display: 'flex', gap: '12px' }}>
            <span>{venue.table_count} tables</span>
            <span>{venue.active_sessions} active</span>
            <span>{venue.sessions_tonight} tonight</span>
          </div>
        </div>
      ))}
    </div>
  )
}
