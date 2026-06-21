import { useEffect, useState } from 'react'
import { ClerkProvider, SignedIn, SignedOut, SignIn, useAuth, useClerk } from '@clerk/clerk-react'
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

const CLERK_KEY = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY

function navigate(path) {
  window.history.pushState({}, '', path)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

const fullScreen = {
  minHeight: '100dvh',
  background: 'var(--bg-floor)',
  color: 'var(--on-surface)',
  fontFamily: 'var(--font-body)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  padding: '24px',
}

function LoadingShimmer() {
  return (
    <div style={{ ...fullScreen, flexDirection: 'column', gap: '12px' }}>
      {[1, 2, 3].map((i) => (
        <div key={i} style={{
          width: '100%', maxWidth: '360px', height: '20px', borderRadius: '6px',
          background: 'var(--bg-container)', animation: 'dev-shimmer 1.5s infinite',
        }} />
      ))}
    </div>
  )
}

function Centered({ children }) {
  return <div style={fullScreen}>{children}</div>
}

// Gate: Clerk if configured, else the dev-login flow.
export default function AdminRoot() {
  if (CLERK_KEY) {
    return (
      <ClerkProvider publishableKey={CLERK_KEY} afterSignOutUrl="/admin">
        <SignedOut>
          <Centered><SignIn routing="hash" /></Centered>
        </SignedOut>
        <SignedIn>
          <ClerkAuthed />
        </SignedIn>
      </ClerkProvider>
    )
  }
  return <DevAuthed />
}

function ClerkAuthed() {
  const { getToken } = useAuth()
  const { signOut } = useClerk()
  const [token, setToken] = useState(null)

  useEffect(() => {
    let active = true
    const refresh = async () => {
      try { const t = await getToken(); if (active) setToken(t) } catch { /* keep last */ }
    }
    refresh()
    const id = setInterval(refresh, 30000)
    return () => { active = false; clearInterval(id) }
  }, [getToken])

  if (!token) return <LoadingShimmer />
  return (
    <AdminInner
      token={token}
      onLogout={() => signOut({ redirectUrl: '/admin' })}
      renderUnauth={() => (
        <Centered>
          <div style={{ ...cardStyle, maxWidth: '480px', textAlign: 'center' }}>
            <p style={{ marginBottom: '16px' }}>
              You&rsquo;re signed in, but this account isn&rsquo;t set up as an admin.
            </p>
            <button onClick={() => signOut({ redirectUrl: '/admin' })} style={buttonStyle}>Sign out</button>
          </div>
        </Centered>
      )}
    />
  )
}

function DevAuthed() {
  const [token, setToken] = useState(() => localStorage.getItem('mh_admin_token'))
  const login = () => setToken(localStorage.getItem('mh_admin_token'))
  const onLogout = () => { localStorage.removeItem('mh_admin_token'); setToken(null); navigate('/admin/login') }

  if (!token) return <AdminLogin onLoginSuccess={login} />
  return (
    <AdminInner
      token={token}
      onLogout={onLogout}
      renderUnauth={() => { localStorage.removeItem('mh_admin_token'); return <AdminLogin onLoginSuccess={login} /> }}
    />
  )
}

function AdminInner({ token, onLogout, renderUnauth }) {
  const [authState, setAuthState] = useState('loading') // loading | ok | venue_wrong_surface | unauth | error
  const [user, setUser] = useState(null)
  const [error, setError] = useState(null)
  const [path, setPath] = useState(window.location.pathname)

  const checkAuth = async () => {
    setAuthState('loading')
    try {
      const me = await fetchAdminMe(token)
      if (me.role !== 'admin') {
        setUser(me)
        setAuthState('venue_wrong_surface')
        return
      }
      setUser(me)
      if (window.location.pathname === '/admin/login') navigate('/admin')
      setAuthState('ok')
    } catch (e) {
      const msg = e.message || ''
      const is401 = msg.includes('401') || msg.includes('token') || msg.includes('expired') || msg.includes('not found')
      if (is401) {
        setAuthState('unauth')
      } else {
        setError(msg)
        setAuthState('error')
      }
    }
  }

  useEffect(() => {
    const updatePath = () => setPath(window.location.pathname)
    window.addEventListener('popstate', updatePath)
    const id = setTimeout(checkAuth, 0)
    return () => { clearTimeout(id); window.removeEventListener('popstate', updatePath) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  if (authState === 'loading') return <LoadingShimmer />
  if (authState === 'unauth') return renderUnauth()

  if (authState === 'venue_wrong_surface') {
    return (
      <Centered>
        <div style={{ ...cardStyle, maxWidth: '480px', textAlign: 'center' }}>
          <p>This is the admin area. Venue accounts use /dashboard.</p>
          <button onClick={onLogout} style={{ ...buttonStyle, marginTop: '12px' }}>Log out / use a different account</button>
        </div>
      </Centered>
    )
  }

  if (authState === 'error') {
    return (
      <Centered>
        <div style={{ ...cardStyle, maxWidth: '480px', textAlign: 'center' }}>
          <p style={{ color: 'var(--tertiary)', fontFamily: 'var(--font-mono)', fontSize: '13px', marginBottom: '16px' }}>
            {error}
          </p>
          <button onClick={checkAuth} style={buttonStyle}>Retry</button>
          <button onClick={onLogout} style={{ ...buttonStyle, background: 'transparent', color: 'var(--on-surface-dim)', marginTop: '10px' }}>
            Log out / use a different account
          </button>
        </div>
      </Centered>
    )
  }

  // authState === 'ok'
  if (path === '/admin/login') {
    navigate('/admin')
    return null
  }

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
    <AdminShell onLogout={onLogout} navigate={navigate} currentPath={path} user={user}>
      {content}
    </AdminShell>
  )
}
