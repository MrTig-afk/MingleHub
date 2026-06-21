import { useEffect, useState } from 'react'
import { fetchAdminMe } from '../../services/adminApi'
import AdminLogin from './AdminLogin.jsx'
import AdminShell from './AdminShell.jsx'
import AdminHome from './AdminHome.jsx'
import AdminVenues from './AdminVenues.jsx'
import AdminVenueDetail from './AdminVenueDetail.jsx'
import AdminSupport from './AdminSupport.jsx'
import AdminTeam from './AdminTeam.jsx'
import AdminLeads from './AdminLeads.jsx'
import { cardStyle, buttonStyle } from '../Dashboard/dashboardStyles'

// Pushes a new path and triggers popstate so AdminRoot re-reads location.
function navigate(path) {
  window.history.pushState({}, '', path)
  window.dispatchEvent(new PopStateEvent('popstate'))
}


export default function AdminRoot() {
  // loading | logged_out | logged_in | error | venue_wrong_surface
  const [authState, setAuthState] = useState('loading')
  const [user, setUser] = useState(null)
  const [token, setToken] = useState(null)
  const [error, setError] = useState(null)
  const [path, setPath] = useState(window.location.pathname)

  const updatePath = () => setPath(window.location.pathname)

  // checkAuth: reads mh_admin_token from localStorage, validates /admin/me,
  // checks that the role is 'admin', and routes to the appropriate state.
  const checkAuth = async () => {
    const stored = localStorage.getItem('mh_admin_token')
    if (!stored) {
      setAuthState('logged_out')
      return
    }
    setAuthState('loading')
    try {
      const me = await fetchAdminMe(stored)
      if (me.role !== 'admin') {
        // Venue owner or staff landed on the admin surface — show a friendly message.
        setUser(me)
        setToken(stored)
        setAuthState('venue_wrong_surface')
        return
      }
      setUser(me)
      setToken(stored)
      setAuthState('logged_in')
      if (window.location.pathname === '/admin/login') {
        navigate('/admin')
      }
    } catch (e) {
      const msg = e.message || ''
      const is401 = msg.includes('401') || msg.includes('token') || msg.includes('expired') || msg.includes('not found')
      if (is401) {
        localStorage.removeItem('mh_admin_token')
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
    localStorage.removeItem('mh_admin_token')
    setUser(null)
    setToken(null)
    setAuthState('logged_out')
    navigate('/admin/login')
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
    return <AdminLogin onLoginSuccess={checkAuth} />
  }

  if (authState === 'venue_wrong_surface') {
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
          <p>This is the admin area. Venue accounts use /dashboard.</p>
          <p style={{ fontSize: '13px', color: 'var(--on-surface-dim)', margin: '8px 0 0' }}>
            Signed in as: {user?.clerk_user_id}
          </p>
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

  // Redirect /admin/login -> /admin
  if (path === '/admin/login') {
    navigate('/admin')
    return null
  }

  // Venue detail match MUST come before the exact /admin/venues check.
  const venueDetailMatch = path.match(/^\/admin\/venues\/([a-f0-9-]+)$/i)

  let content
  if (path === '/admin') {
    content = <AdminHome token={token} />
  } else if (venueDetailMatch) {
    content = <AdminVenueDetail token={token} venueId={venueDetailMatch[1]} navigate={navigate} />
  } else if (path === '/admin/venues') {
    content = <AdminVenues token={token} navigate={navigate} />
  } else if (path === '/admin/support') {
    content = <AdminSupport token={token} />
  } else if (path === '/admin/team') {
    content = <AdminTeam token={token} />
  } else if (path === '/admin/leads') {
    content = <AdminLeads token={token} />
  } else {
    content = (
      <div style={{ padding: '48px 0', textAlign: 'center', color: 'var(--on-surface-dim)' }}>
        <h2 style={{ fontFamily: 'var(--font-headline)', marginBottom: '8px' }}>Not Found</h2>
      </div>
    )
  }

  return (
    <AdminShell
      onLogout={handleLogout}
      navigate={navigate}
      currentPath={path}
    >
      {content}
    </AdminShell>
  )
}
