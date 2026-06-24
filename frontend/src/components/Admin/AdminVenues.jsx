import { useEffect, useState } from 'react'
import { fetchAdminVenues } from '../../services/adminApi'
import { buttonStyle, buttonSecondaryStyle, cardStyle, labelStyle } from '../Dashboard/dashboardStyles'

const shimmerCard = (height = 64) => ({
  ...cardStyle,
  height,
  animation: 'dev-shimmer 1.5s infinite',
  background: 'var(--bg-container)',
  marginBottom: '12px',
})

const ACTIVE_CHIP = { background: 'rgba(45,226,230,0.15)', color: 'var(--secondary)' }
const DIM_CHIP = { color: 'var(--on-surface-dim)' }
const TEST_CHIP = { background: 'rgba(255,200,87,0.15)', color: 'var(--gold)' }

const smallChip = (extra) => ({
  fontSize: '11px',
  padding: '2px 8px',
  borderRadius: '10px',
  fontWeight: 700,
  ...extra,
})

const inputStyle = {
  padding: '10px 12px',
  borderRadius: '8px',
  background: 'var(--bg-surface)',
  color: 'var(--on-surface)',
  border: '1px solid var(--outline)',
  width: '100%',
  boxSizing: 'border-box',
}

export default function AdminVenues({ token, navigate }) {
  const [data, setData] = useState(null)
  const [status, setStatus] = useState('loading') // loading | ready | error
  const [error, setError] = useState(null)
  // Bumped by Retry to re-trigger the fetch effect (deps otherwise unchanged).
  const [reloadKey, setReloadKey] = useState(0)

  // Client-side search / filter / sort state.
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [testFilter, setTestFilter] = useState('all')
  const [sort, setSort] = useState('name')

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

  // Client-side filter + sort.
  const lowerSearch = search.toLowerCase()
  const filteredVenues = venues
    .filter((v) => {
      if (search && !v.name.toLowerCase().includes(lowerSearch) && !v.slug.toLowerCase().includes(lowerSearch)) return false
      if (statusFilter !== 'all' && v.status !== statusFilter) return false
      if (testFilter === 'test' && !v.is_test) return false
      return true
    })
    .sort((a, b) => {
      if (sort === 'active') return b.active_sessions - a.active_sessions || a.name.localeCompare(b.name)
      if (sort === 'tonight') return b.sessions_tonight - a.sessions_tonight || a.name.localeCompare(b.name)
      return a.name.localeCompare(b.name)
    })

  return (
    <div>
      <h2 style={{ fontFamily: 'var(--font-headline)', fontSize: '18px', marginBottom: '12px', marginTop: 0 }}>
        Venues
      </h2>

      {/* Search box */}
      <input
        type="text"
        placeholder="Search name or slug..."
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        style={{ ...inputStyle, marginBottom: '12px' }}
      />

      {/* Status + test filter row */}
      <div style={{ display: 'flex', gap: '8px', marginBottom: '12px', flexWrap: 'wrap' }}>
        {['all', 'active', 'suspended'].map((f) => (
          <button
            key={f}
            onClick={() => setStatusFilter(f)}
            style={{
              ...(statusFilter === f ? buttonStyle : buttonSecondaryStyle),
              padding: '6px 12px',
              fontSize: '12px',
            }}
          >
            {f.charAt(0).toUpperCase() + f.slice(1)}
          </button>
        ))}
        <button
          onClick={() => setTestFilter(testFilter === 'test' ? 'all' : 'test')}
          style={{
            ...(testFilter === 'test'
              ? { ...buttonStyle, background: 'var(--gold)', color: '#0A0A0C' }
              : buttonSecondaryStyle),
            padding: '6px 12px',
            fontSize: '12px',
          }}
        >
          Test Only
        </button>
      </div>

      {/* Sort row */}
      <div style={{ display: 'flex', gap: '8px', marginBottom: '12px' }}>
        {[{ key: 'name', label: 'Name' }, { key: 'active', label: 'Active' }, { key: 'tonight', label: 'Tonight' }].map(({ key, label }) => (
          <button
            key={key}
            onClick={() => setSort(key)}
            style={{
              ...(sort === key ? buttonStyle : buttonSecondaryStyle),
              padding: '6px 12px',
              fontSize: '12px',
            }}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Empty filter result */}
      {filteredVenues.length === 0 && venues.length > 0 && (
        <div style={{ ...cardStyle, textAlign: 'center' }}>
          <p style={{ ...labelStyle, margin: 0 }}>No venues match filters</p>
        </div>
      )}

      {filteredVenues.map((venue) => (
        <div
          key={venue.id}
          onClick={() => navigate(`/admin/venues/${venue.id}`)}
          style={{ ...cardStyle, marginBottom: '12px', cursor: 'pointer', opacity: venue.is_test ? 0.6 : 1 }}
        >
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
