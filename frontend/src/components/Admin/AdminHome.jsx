import { useEffect, useState } from 'react'
import { fetchAdminOverview } from '../../services/adminApi'
import { buttonStyle, cardStyle, chipStyle } from '../Dashboard/dashboardStyles'

const shimmerCard = (height = 80) => ({
  ...cardStyle,
  height,
  animation: 'dev-shimmer 1.5s infinite',
  background: 'var(--bg-container)',
})

export default function AdminHome({ token }) {
  const [data, setData] = useState(null)
  const [status, setStatus] = useState('loading') // loading | ready | error | reconnecting
  const [error, setError] = useState(null)

  const load = () => {
    setStatus('loading')
    fetchAdminOverview(token)
      .then((d) => {
        setData(d)
        setStatus('ready')
      })
      .catch((e) => {
        const msg = e.message || ''
        if (msg.includes('401') || msg.includes('token') || msg.includes('expired')) {
          localStorage.removeItem('mh_admin_token')
          window.location.reload()
          return
        }
        setStatus('error')
        setError(msg)
      })
  }

  // Initial fetch. All setState happens inside the async resolution (after the
  // await), never synchronously in the effect body (react-hooks/set-state-in-effect).
  useEffect(() => {
    let cancelled = false
    const run = async () => {
      try {
        const d = await fetchAdminOverview(token)
        if (cancelled) return
        setData(d)
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
  }, [token])

  // Poll every 7 seconds once the initial fetch has settled.
  useEffect(() => {
    if (status === 'loading' || !token) return
    const id = setInterval(() => {
      fetchAdminOverview(token)
        .then((d) => {
          setData(d)
          setStatus('ready')
        })
        .catch((e) => {
          const msg = e.message || ''
          if (msg.includes('401') || msg.includes('token') || msg.includes('expired')) {
            localStorage.removeItem('mh_admin_token')
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
        <button onClick={load} style={buttonStyle}>Retry</button>
      </div>
    )
  }

  const platform = data?.platform || {}
  const perVenue = data?.per_venue || []

  const platformCards = [
    { value: platform.total_venues,        label: 'total venues' },
    { value: platform.active_venues_now,   label: 'venues active now' },
    { value: platform.active_sessions_now, label: 'sessions active now' },
    { value: platform.sessions_tonight,    label: 'sessions tonight' },
    { value: platform.players_tonight,     label: 'players tonight' },
    { value: platform.rounds_tonight,      label: 'rounds tonight' },
  ]

  const noActivity = perVenue.every((v) => v.active_sessions === 0 && v.sessions_tonight === 0)

  return (
    <div>
      {status === 'reconnecting' && (
        <p style={{ fontSize: '12px', color: 'var(--on-surface-dim)', margin: '0 0 8px' }}>
          Reconnecting...
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

      {perVenue.length === 0 && (
        <p style={{ color: 'var(--on-surface-dim)', textAlign: 'center', padding: '16px 0' }}>
          No venues configured
        </p>
      )}

      {perVenue.length > 0 && noActivity && (
        <p style={{ color: 'var(--on-surface-dim)', textAlign: 'center', padding: '16px 0' }}>
          No activity tonight
        </p>
      )}

      {perVenue.length > 0 && !noActivity && perVenue.map((v) => (
        <div key={v.venue_id} style={{ ...cardStyle, marginBottom: '12px' }}>
          {/* Top row: name + active chip */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
            <span style={{ fontWeight: 700 }}>{v.name}</span>
            {v.active_sessions > 0 && (
              <span style={chipStyle('active')}>{v.active_sessions} active</span>
            )}
          </div>
          {/* Slug */}
          <div style={{ fontSize: '13px', color: 'var(--on-surface-dim)', marginBottom: '4px' }}>
            {v.slug}
          </div>
          {/* Stats row */}
          <div style={{ fontSize: '13px', color: 'var(--on-surface-dim)', display: 'flex', gap: '12px' }}>
            <span>{v.sessions_tonight} sessions tonight</span>
            <span>{v.players_tonight} players tonight</span>
          </div>
        </div>
      ))}
    </div>
  )
}
