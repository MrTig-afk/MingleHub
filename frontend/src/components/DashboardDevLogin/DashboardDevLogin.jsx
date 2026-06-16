import { useState } from 'react'
import { devLogin, fetchMe, fetchVenue, fetchAdminPing } from '../../services/dashboardApi'

// Must match the clerk_user_id values seeded by scripts/seed_platform.py
const DEV_USERS = [
  { id: 'dev_owner_a', label: 'Owner A — The Lion\'s Den' },
  { id: 'dev_staff_a', label: 'Staff A — The Lion\'s Den' },
  { id: 'dev_owner_b', label: 'Owner B — The Brew House' },
  { id: 'dev_admin', label: 'Admin' },
]

export default function DashboardDevLogin() {
  const [clerkUserId, setClerkUserId] = useState(DEV_USERS[0].id)
  const [status, setStatus] = useState('idle') // idle | loading | error
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)

  const signIn = async () => {
    setStatus('loading')
    setError(null)
    setResult(null)
    try {
      const { token } = await devLogin(clerkUserId)
      const me = await fetchMe(token)

      let venue = null
      let venueError = null
      let admin = null
      let adminError = null

      try {
        venue = await fetchVenue(token)
      } catch (e) {
        venueError = e.message
      }
      try {
        admin = await fetchAdminPing(token)
      } catch (e) {
        adminError = e.message
      }

      setResult({ me, venue, venueError, admin, adminError })
      setStatus('idle')
    } catch (e) {
      setError(e.message)
      setStatus('error')
    }
  }

  return (
    <div style={{
      minHeight: '100dvh',
      background: 'var(--bg-floor)',
      color: 'var(--on-surface)',
      fontFamily: 'var(--font-body)',
      padding: '24px',
      display: 'flex',
      flexDirection: 'column',
      gap: '16px',
      maxWidth: '480px',
      margin: '0 auto',
    }}>
      <h1 style={{ fontFamily: 'var(--font-headline)', fontSize: '22px' }}>
        Platform Foundation — Dev Login
      </h1>
      <p style={{ fontSize: '13px', color: 'var(--on-surface-dim)' }}>
        Dev-only stub auth proof. Sign in as a seeded user and confirm identity,
        venue scoping, and role gating all work end-to-end.
      </p>

      <select
        value={clerkUserId}
        onChange={(e) => setClerkUserId(e.target.value)}
        style={{
          padding: '12px',
          borderRadius: '8px',
          background: 'var(--bg-surface)',
          color: 'var(--on-surface)',
          border: '1px solid var(--outline)',
        }}
      >
        {DEV_USERS.map((u) => (
          <option key={u.id} value={u.id}>{u.label}</option>
        ))}
      </select>

      <button
        onClick={signIn}
        disabled={status === 'loading'}
        style={{
          padding: '12px',
          borderRadius: '8px',
          background: 'var(--primary)',
          color: 'var(--bg-floor)',
          fontWeight: 700,
          border: 'none',
        }}
      >
        {status === 'loading' ? 'Signing in…' : 'Dev Sign In'}
      </button>

      {error && (
        <p style={{ color: 'var(--tertiary)', fontFamily: 'var(--font-mono)', fontSize: '13px' }}>
          Error: {error}
        </p>
      )}

      {result && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', fontFamily: 'var(--font-mono)', fontSize: '13px' }}>
          <ResultBlock title="GET /dashboard/me" data={result.me} />
          <ResultBlock title="GET /dashboard/venue" data={result.venue} error={result.venueError} />
          <ResultBlock title="GET /admin/ping" data={result.admin} error={result.adminError} />
        </div>
      )}
    </div>
  )
}

function ResultBlock({ title, data, error }) {
  return (
    <div style={{
      background: 'var(--glass-bg)',
      border: '1px solid var(--glass-border)',
      borderRadius: '8px',
      padding: '12px',
    }}>
      <div style={{ color: 'var(--secondary)', marginBottom: '6px' }}>{title}</div>
      {error
        ? <div style={{ color: 'var(--tertiary)' }}>{error}</div>
        : <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{JSON.stringify(data, null, 2)}</pre>
      }
    </div>
  )
}
