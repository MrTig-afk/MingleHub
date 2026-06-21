import { useEffect, useState } from 'react'
import { fetchAdminTeam } from '../../services/adminApi'
import { buttonStyle, cardStyle, labelStyle } from '../Dashboard/dashboardStyles'

const shimmerCard = (height = 64) => ({
  ...cardStyle,
  height,
  animation: 'dev-shimmer 1.5s infinite',
  background: 'var(--bg-container)',
  marginBottom: '12px',
})

// Role badge styles — match AdminShell admin badge for admin role.
const roleBadge = (role) => {
  const base = {
    fontSize: '11px',
    padding: '2px 8px',
    borderRadius: '10px',
    fontWeight: 700,
  }
  if (role === 'admin') {
    return { ...base, background: 'var(--tertiary)', color: 'var(--bg-floor)' }
  }
  if (role === 'venue_owner') {
    return { ...base, background: 'var(--primary)', color: 'var(--bg-floor)' }
  }
  // venue_staff
  return { ...base, background: 'var(--bg-container)', color: 'var(--on-surface-dim)', border: '1px solid var(--outline)' }
}

export default function AdminTeam({ token }) {
  const [status, setStatus] = useState('loading')
  const [users, setUsers] = useState([])
  const [error, setError] = useState(null)
  const [reloadKey, setReloadKey] = useState(0)

  // All setState calls are after await (react-hooks/set-state-in-effect compliant).
  useEffect(() => {
    let cancelled = false
    const run = async () => {
      setStatus('loading')
      try {
        const result = await fetchAdminTeam(token)
        if (cancelled) return
        setUsers(result.users || [])
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

  return (
    <div>
      <h2 style={{ fontFamily: 'var(--font-headline)', fontSize: '18px', marginTop: 0, marginBottom: '4px' }}>
        Team
      </h2>
      <p style={{ ...labelStyle, marginTop: 0, marginBottom: '16px' }}>
        Read-only. Invite flow coming soon.
      </p>

      {status === 'loading' && (
        <div>
          {[1, 2, 3].map((i) => <div key={i} style={shimmerCard()} />)}
        </div>
      )}

      {status === 'error' && (
        <div style={{ ...cardStyle, marginTop: '8px' }}>
          <p style={{ color: 'var(--tertiary)', fontFamily: 'var(--font-mono)', fontSize: '13px', margin: '0 0 12px' }}>
            Could not load team. {error}
          </p>
          <button
            onClick={() => { setError(null); setReloadKey((k) => k + 1) }}
            style={buttonStyle}
          >
            Retry
          </button>
        </div>
      )}

      {status === 'ready' && users.length === 0 && (
        <div style={{ ...cardStyle, textAlign: 'center' }}>
          <p style={{ ...labelStyle, margin: 0 }}>No team members</p>
        </div>
      )}

      {status === 'ready' && users.map((u) => (
        <div key={u.id} style={{ ...cardStyle, marginBottom: '12px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
            <span style={{ fontWeight: 700 }}>{u.clerk_user_id}</span>
            <span style={roleBadge(u.role)}>{u.role}</span>
          </div>
          <div style={{ ...labelStyle, marginBottom: '2px' }}>
            {u.venue_name || 'Platform'}
          </div>
          <div style={{ ...labelStyle, fontSize: '12px' }}>
            Joined: {u.created_at ? new Date(u.created_at).toLocaleDateString() : '--'}
          </div>
        </div>
      ))}
    </div>
  )
}
