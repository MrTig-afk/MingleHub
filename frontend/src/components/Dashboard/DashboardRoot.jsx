import { useEffect, useState } from 'react'
import { fetchMe, fetchVenue } from '../../services/dashboardApi'
import PairTags from '../PairTags/PairTags.jsx'
// TODO (future slice): refactor PairTags to accept the shell token as a prop
// rather than managing its own login flow internally.
import DashboardLogin from './DashboardLogin.jsx'
import DashboardShell from './DashboardShell.jsx'
import DashboardHome from './DashboardHome.jsx'
import DashboardTables from './DashboardTables.jsx'
import DashboardTableDetail from './DashboardTableDetail.jsx'
import DashboardInsights from './DashboardInsights.jsx'
import { cardStyle, buttonStyle } from './dashboardStyles'

// Pushes a new path and triggers popstate so DashboardRoot re-reads location.
function navigate(path) {
  window.history.pushState({}, '', path)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

function Placeholder({ label }) {
  return (
    <div style={{ padding: '48px 0', textAlign: 'center', color: 'var(--on-surface-dim)' }}>
      <h2 style={{ fontFamily: 'var(--font-headline)', marginBottom: '8px' }}>{label}</h2>
      <p>Coming in the next slice</p>
    </div>
  )
}

export default function DashboardRoot() {
  const [authState, setAuthState] = useState('loading') // loading | logged_out | logged_in | error | admin_wrong_surface
  const [user, setUser] = useState(null)
  const [venue, setVenue] = useState(null)
  const [token, setToken] = useState(null)
  const [error, setError] = useState(null)
  const [path, setPath] = useState(window.location.pathname)

  const updatePath = () => setPath(window.location.pathname)

  // checkAuth: reads token from localStorage, validates /me, loads /venue.
  // Called on mount and after a successful login.
  const checkAuth = async () => {
    const stored = localStorage.getItem('mh_dashboard_token')
    if (!stored) {
      setAuthState('logged_out')
      return
    }
    setAuthState('loading')
    try {
      const me = await fetchMe(stored)
      if (me.role === 'admin') {
        setUser(me)
        setToken(stored)
        setAuthState('admin_wrong_surface')
        return
      }
      if (!me.venue_id) {
        setError('Account not linked to a venue.')
        setAuthState('error')
        return
      }
      const v = await fetchVenue(stored)
      setUser(me)
      setVenue(v)
      setToken(stored)
      // If we're on the login page after re-auth, redirect to home
      if (window.location.pathname === '/dashboard/login') {
        navigate('/dashboard')
      }
      setAuthState('logged_in')
    } catch (e) {
      const msg = e.message || ''
      const is401 = msg.includes('401') || msg.includes('token') || msg.includes('expired') || msg.includes('not found')
      if (is401) {
        localStorage.removeItem('mh_dashboard_token')
        setAuthState('logged_out')
      } else {
        setError(msg)
        setAuthState('error')
      }
    }
  }

  useEffect(() => {
    window.addEventListener('popstate', updatePath)
    // Deferred a tick so checkAuth()'s setState lands in a macrotask rather than
    // synchronously in the effect body (react-hooks/set-state-in-effect).
    const id = setTimeout(checkAuth, 0)
    return () => {
      clearTimeout(id)
      window.removeEventListener('popstate', updatePath)
    }
  }, [])

  const handleLogout = () => {
    localStorage.removeItem('mh_dashboard_token')
    setUser(null)
    setVenue(null)
    setToken(null)
    setAuthState('logged_out')
    navigate('/dashboard/login')
  }

  // --- Auth state renderers ---

  if (authState === 'loading') {
    return (
      <div style={{
        minHeight: '100dvh',
        background: 'var(--bg-floor)',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        alignItems: 'center',
        gap: '12px',
        padding: '24px',
      }}>
        {[1, 2, 3].map((i) => (
          <div key={i} style={{
            width: '100%',
            maxWidth: '360px',
            height: '20px',
            borderRadius: '6px',
            background: 'var(--bg-container)',
            animation: 'dev-shimmer 1.5s infinite',
          }} />
        ))}
      </div>
    )
  }

  if (authState === 'logged_out') {
    return <DashboardLogin onLoginSuccess={checkAuth} />
  }

  if (authState === 'admin_wrong_surface') {
    return (
      <div style={{
        minHeight: '100dvh',
        background: 'var(--bg-floor)',
        color: 'var(--on-surface)',
        fontFamily: 'var(--font-body)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '24px',
      }}>
        <div style={{ ...cardStyle, maxWidth: '480px', textAlign: 'center' }}>
          <p>Admin accounts use /admin. This dashboard is for venue owners and staff.</p>
        </div>
      </div>
    )
  }

  if (authState === 'error') {
    return (
      <div style={{
        minHeight: '100dvh',
        background: 'var(--bg-floor)',
        color: 'var(--on-surface)',
        fontFamily: 'var(--font-body)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '24px',
      }}>
        <div style={{ ...cardStyle, maxWidth: '480px', textAlign: 'center' }}>
          <p style={{ color: 'var(--tertiary)', fontFamily: 'var(--font-mono)', fontSize: '13px', marginBottom: '16px' }}>
            {error}
          </p>
          <button onClick={checkAuth} style={buttonStyle}>Retry</button>
        </div>
      </div>
    )
  }

  // authState === 'logged_in': render shell + sub-route switch

  // Redirect /dashboard/login -> /dashboard
  if (path === '/dashboard/login') {
    navigate('/dashboard')
    return null
  }

  let content
  if (path === '/dashboard') {
    content = <DashboardHome token={token} navigate={navigate} />
  } else if (path === '/dashboard/pair-tags') {
    content = <PairTags />
  } else if (path === '/dashboard/tables' || path === '/dashboard/tables/') {
    content = <DashboardTables token={token} navigate={navigate} />
  } else if (path.startsWith('/dashboard/tables/')) {
    const tableId = path.replace('/dashboard/tables/', '').replace(/\/$/, '')
    content = <DashboardTableDetail token={token} tableId={tableId} navigate={navigate} user={user} />
  } else if (path === '/dashboard/insights') {
    content = <DashboardInsights token={token} />
  } else if (path === '/dashboard/settings') {
    content = <Placeholder label="Settings" />
  } else if (path === '/dashboard/billing') {
    content = <Placeholder label="Billing" />
  } else {
    content = <Placeholder label="Not Found" />
  }

  return (
    <DashboardShell
      user={user}
      venue={venue}
      token={token}
      onLogout={handleLogout}
      navigate={navigate}
      currentPath={path}
    >
      {content}
    </DashboardShell>
  )
}
