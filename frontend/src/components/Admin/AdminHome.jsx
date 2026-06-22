import { useEffect, useState } from 'react'
import { fetchAdminOverview } from '../../services/adminApi'
import { buttonStyle, buttonSecondaryStyle, cardStyle, chipStyle, formatRelativeTime } from '../Dashboard/dashboardStyles'
import usePolling from '../Dashboard/usePolling'

const shimmerCard = (height = 80) => ({
  ...cardStyle,
  height,
  animation: 'dev-shimmer 1.5s infinite',
  background: 'var(--bg-container)',
})

export default function AdminHome({ token, navigate }) {
  const { data, status, error, lastUpdatedAt, reload } = usePolling(
    () => fetchAdminOverview(token),
    { intervalMs: 7000, tokenKey: 'mh_admin_token', cacheKey: 'admin:home' }
  )

  // Tick every 10 seconds so "updated N ago" re-renders without a new fetch.
  const [, setTick] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 10000)
    return () => clearInterval(id)
  }, [])

  const [venueSort, setVenueSort] = useState('active')

  if (status === 'loading') {
    return (
      <div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
          {[1, 2, 3, 4, 5, 6].map((i) => <div key={i} style={shimmerCard(80)} />)}
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
          Could not load admin data. {error}
        </p>
        <button onClick={reload} style={buttonStyle}>Retry</button>
      </div>
    )
  }

  const platform = data?.platform || {}
  const perVenue = data?.per_venue || []

  const sortedVenues = [...perVenue].sort((a, b) => {
    if (venueSort === 'active') return b.active_sessions - a.active_sessions || a.name.localeCompare(b.name)
    if (venueSort === 'tonight') return b.sessions_tonight - a.sessions_tonight || a.name.localeCompare(b.name)
    return a.name.localeCompare(b.name)
  })

  const platformCards = [
    { value: platform.total_venues,        label: 'total venues' },
    { value: platform.active_venues_now,   label: 'venues active now' },
    { value: platform.active_sessions_now, label: 'sessions active now' },
    { value: platform.sessions_tonight,    label: 'sessions tonight' },
    { value: platform.players_tonight,     label: 'players tonight' },
    { value: platform.rounds_tonight,      label: 'rounds tonight' },
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

      {/* Platform stat cards — 2 columns, 3 rows */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
        {platformCards.map(({ value, label }) => (
          <div key={label} style={cardStyle}>
            <div style={{ fontFamily: 'var(--font-headline)', fontSize: '28px', color: 'var(--on-surface)' }}>
              {value ?? 0}
            </div>
            <div style={{ fontSize: '13px', color: 'var(--on-surface-dim)' }}>{label}</div>
          </div>
        ))}
      </div>

      {/* Per-venue breakdown */}
      <h2 style={{ fontFamily: 'var(--font-headline)', fontSize: '18px', margin: '24px 0 12px' }}>
        Per-Venue Breakdown
      </h2>

      {/* Sort buttons */}
      <div style={{ display: 'flex', gap: '8px', marginBottom: '12px' }}>
        {[
          { key: 'active', label: 'Active' },
          { key: 'tonight', label: 'Tonight' },
          { key: 'name', label: 'Name' },
        ].map(({ key, label }) => (
          <button
            key={key}
            onClick={() => setVenueSort(key)}
            style={{
              ...(venueSort === key ? buttonStyle : buttonSecondaryStyle),
              padding: '6px 12px',
              fontSize: '12px',
            }}
          >
            {label}
          </button>
        ))}
      </div>

      {perVenue.length === 0 && (
        <p style={{ color: 'var(--on-surface-dim)', textAlign: 'center', padding: '16px 0' }}>
          No venues configured
        </p>
      )}

      {/* Always listed (idle or not) and clickable -> venue detail. */}
      {sortedVenues.map((v) => (
        <div
          key={v.venue_id}
          onClick={() => navigate(`/admin/venues/${v.venue_id}`)}
          style={{ ...cardStyle, marginBottom: '12px', cursor: 'pointer' }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
            <span style={{ fontWeight: 700 }}>
              {v.name}
              <span style={{ color: 'var(--on-surface-dim)', fontWeight: 400, marginLeft: '6px' }}>&rsaquo;</span>
            </span>
            {v.active_sessions > 0 && (
              <span style={chipStyle('active')}>{v.active_sessions} active</span>
            )}
          </div>
          <div style={{ fontSize: '13px', color: 'var(--on-surface-dim)', marginBottom: '4px' }}>
            {v.slug}
          </div>
          <div style={{ fontSize: '13px', color: 'var(--on-surface-dim)', display: 'flex', gap: '12px' }}>
            <span>{v.sessions_tonight} sessions tonight</span>
            <span>{v.players_tonight} players tonight</span>
          </div>
        </div>
      ))}
    </div>
  )
}
