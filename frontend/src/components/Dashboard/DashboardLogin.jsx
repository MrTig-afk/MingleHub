import { useState } from 'react'
import { devLogin } from '../../services/dashboardApi'
import { buttonStyle, selectStyle } from './dashboardStyles'

// Must match clerk_user_id values seeded by scripts/seed_platform.py.
// Expanded beyond PairTags' owner-only list to cover all roles for testing.
const DEV_USERS = [
  { id: 'dev_owner_a', label: "Owner A -- Fifty Five Bar" },
  { id: 'dev_staff_a', label: "Staff A -- Fifty Five Bar" },
  { id: 'dev_owner_b', label: 'Owner B -- The Last Chance' },
  { id: 'dev_admin',   label: 'Admin (no venue)' },
]

export default function DashboardLogin({ onLoginSuccess }) {
  const [clerkUserId, setClerkUserId] = useState(DEV_USERS[0].id)
  const [status, setStatus] = useState('idle') // idle | loading | error
  const [error, setError] = useState(null)

  const handleSignIn = async () => {
    setStatus('loading')
    setError(null)
    try {
      const { token } = await devLogin(clerkUserId)
      localStorage.setItem('mh_dashboard_token', token)
      onLoginSuccess()
    } catch (e) {
      setStatus('error')
      setError(e.message)
    }
  }

  return (
    <div style={{
      minHeight: '100dvh',
      background: 'var(--bg-floor)',
      color: 'var(--on-surface)',
      fontFamily: 'var(--font-body)',
      display: 'flex',
      flexDirection: 'column',
      gap: '16px',
      padding: '24px',
      maxWidth: '480px',
      margin: '0 auto',
      justifyContent: 'center',
    }}>
      <h1 style={{ fontFamily: 'var(--font-headline)', fontSize: '22px', margin: 0 }}>
        Dashboard Login
      </h1>
      <p style={{ fontSize: '13px', color: 'var(--on-surface-dim)', margin: 0 }}>
        Dev mode -- select a seeded account
      </p>

      <select
        value={clerkUserId}
        onChange={(e) => setClerkUserId(e.target.value)}
        style={selectStyle}
      >
        {DEV_USERS.map((u) => (
          <option key={u.id} value={u.id}>{u.label}</option>
        ))}
      </select>

      <button
        onClick={handleSignIn}
        disabled={status === 'loading'}
        style={buttonStyle}
      >
        {status === 'loading' ? 'Signing in...' : 'Sign In'}
      </button>

      {error && (
        <p style={{ color: 'var(--tertiary)', fontFamily: 'var(--font-mono)', fontSize: '13px', margin: 0 }}>
          Error: {error}
        </p>
      )}
    </div>
  )
}
