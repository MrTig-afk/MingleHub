import { useEffect, useState } from 'react'
import { ClerkProvider, SignedIn, SignedOut, SignIn, useAuth, useClerk } from '@clerk/clerk-react'
import { fetchMe, fetchVenue, redeemInvite } from '../../services/dashboardApi'
import PairTags from '../PairTags/PairTags.jsx'
import DashboardLogin from './DashboardLogin.jsx'
import DashboardShell from './DashboardShell.jsx'
import DashboardHome from './DashboardHome.jsx'
import DashboardTables from './DashboardTables.jsx'
import DashboardTableDetail from './DashboardTableDetail.jsx'
import DashboardInsights from './DashboardInsights.jsx'
import DashboardSettings from './DashboardSettings.jsx'
import DashboardBilling from './DashboardBilling.jsx'
import VenueSetup from './VenueSetup.jsx'
import { clearDashboardCache } from './usePolling'
import { cardStyle, buttonStyle } from './dashboardStyles'

// Clerk activates when its publishable key is present; otherwise the dev-login flow
// is used (so local tooling/tests keep working). The backend accepts both a Clerk
// JWT and a dev token in DEV_MODE.
const CLERK_KEY = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY

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

// ---------------------------------------------------------------------------
// Gate: Clerk if configured, else the dev-login flow.
// ---------------------------------------------------------------------------
export default function DashboardRoot() {
  if (CLERK_KEY) {
    return (
      <ClerkProvider publishableKey={CLERK_KEY} afterSignOutUrl="/dashboard">
        <SignedOut>
          <Centered>
            {/* After sign-in/up, land on /dashboard (not the patron root). An owner
                with no venue yet is then routed to the setup wizard by DashboardInner;
                an existing owner/staff goes straight to their dashboard. */}
            <SignIn
              routing="hash"
              forceRedirectUrl="/dashboard"
              signUpForceRedirectUrl="/dashboard"
            />
          </Centered>
        </SignedOut>
        <SignedIn>
          <ClerkAuthed />
        </SignedIn>
      </ClerkProvider>
    )
  }
  return <DevAuthed />
}

// Clerk-authed: hold a refreshed Clerk JWT, then run the dashboard with it.
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
    const id = setInterval(refresh, 30000) // Clerk session tokens last ~60s
    return () => { active = false; clearInterval(id) }
  }, [getToken])

  if (!token) return <LoadingShimmer />
  return (
    <DashboardInner
      token={token}
      onLogout={() => { clearDashboardCache(); signOut({ redirectUrl: '/dashboard' }) }}
      // A valid Clerk session whose user isn't provisioned in `users` yet.
      renderUnauth={() => (
        <Centered>
          <div style={{ ...cardStyle, maxWidth: '480px', textAlign: 'center' }}>
            <p style={{ marginBottom: '16px' }}>
              You&rsquo;re signed in, but this account isn&rsquo;t linked to a venue yet.
              Ask an admin to set it up.
            </p>
            <button onClick={() => signOut({ redirectUrl: '/dashboard' })} style={buttonStyle}>
              Sign out
            </button>
          </div>
        </Centered>
      )}
    />
  )
}

// Dev-login authed: token from localStorage; unauth -> the dev-login form.
function DevAuthed() {
  const [token, setToken] = useState(() => localStorage.getItem('mh_dashboard_token'))
  const login = () => setToken(localStorage.getItem('mh_dashboard_token'))
  const onLogout = () => { clearDashboardCache(); localStorage.removeItem('mh_dashboard_token'); setToken(null); navigate('/dashboard/login') }

  if (!token) return <DashboardLogin onLoginSuccess={login} />
  return (
    <DashboardInner
      token={token}
      onLogout={onLogout}
      renderUnauth={() => { localStorage.removeItem('mh_dashboard_token'); return <DashboardLogin onLoginSuccess={login} /> }}
    />
  )
}

// ---------------------------------------------------------------------------
// The dashboard itself, given an already-resolved bearer token.
// ---------------------------------------------------------------------------
function DashboardInner({ token, onLogout, renderUnauth }) {
  const [authState, setAuthState] = useState('loading') // loading | ok | setup | no_invite | invite_error | admin_wrong_surface | unauth | error
  const [user, setUser] = useState(null)
  const [venue, setVenue] = useState(null)
  const [error, setError] = useState(null)
  const [path, setPath] = useState(window.location.pathname)
  const [prefill, setPrefill] = useState(null)

  const checkAuth = async () => {
    setAuthState('loading')
    try {
      const me = await fetchMe(token)
      if (me.role === 'admin') {
        setUser(me)
        setAuthState('admin_wrong_surface')
        return
      }
      if (!me.venue_id) {
        setUser(me)
        // Check for ?invite=CODE in the URL
        const params = new URLSearchParams(window.location.search)
        const inviteCode = params.get('invite')
        if (inviteCode) {
          try {
            const { invite } = await redeemInvite(token, inviteCode)
            // Clean the URL (remove ?invite=...)
            window.history.replaceState({}, '', '/dashboard')
            setPrefill(invite)
            setAuthState('setup')
          } catch (e) {
            setError(e.message || 'Invalid or expired invite')
            setAuthState('invite_error')
          }
        } else if (me.has_redeemed_invite) {
          // Owner redeemed an invite previously but hasn't finished setup. Re-hydrate
          // the prefill from /me so a page refresh still pre-fills the wizard.
          if (me.invite_prefill) setPrefill(me.invite_prefill)
          setAuthState('setup')
        } else {
          // No venue, no invite -> locked "Contact us" screen
          setAuthState('no_invite')
        }
        return
      }
      const v = await fetchVenue(token)
      setUser(me)
      setVenue(v)
      if (window.location.pathname === '/dashboard/login') navigate('/dashboard')
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
  if (authState === 'setup') return <VenueSetup token={token} onDone={checkAuth} navigate={navigate} prefill={prefill} />

  if (authState === 'admin_wrong_surface') {
    return (
      <Centered>
        <div style={{ ...cardStyle, maxWidth: '480px', textAlign: 'center' }}>
          <p style={{ marginBottom: '16px' }}>Admin accounts use /admin. This dashboard is for venue owners and staff.</p>
          <button onClick={onLogout} style={buttonStyle}>Log out / use a different account</button>
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

  if (authState === 'no_invite') {
    return (
      <Centered>
        <div style={{ ...cardStyle, maxWidth: '480px', textAlign: 'center' }}>
          <h2 style={{ fontFamily: 'var(--font-headline)', fontSize: '22px', margin: '0 0 12px' }}>
            Welcome to MingleHub
          </h2>
          <p style={{ color: 'var(--on-surface-dim)', fontSize: '14px', margin: '0 0 20px' }}>
            To get started, you need an invite from MingleHub.
            Contact us to get your venue set up.
          </p>
          <button onClick={onLogout} style={buttonStyle}>Log out</button>
        </div>
      </Centered>
    )
  }

  if (authState === 'invite_error') {
    return (
      <Centered>
        <div style={{ ...cardStyle, maxWidth: '480px', textAlign: 'center' }}>
          <p style={{ color: 'var(--tertiary)', fontFamily: 'var(--font-mono)', fontSize: '13px', marginBottom: '16px' }}>
            {error}
          </p>
          <button onClick={onLogout} style={buttonStyle}>Log out</button>
        </div>
      </Centered>
    )
  }

  // authState === 'ok'
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
    content = <DashboardSettings token={token} user={user} />
  } else if (path === '/dashboard/billing') {
    content = <DashboardBilling token={token} user={user} />
  } else {
    content = <Placeholder label="Not Found" />
  }

  return (
    <DashboardShell user={user} venue={venue} token={token} onLogout={onLogout} navigate={navigate} currentPath={path}>
      {content}
    </DashboardShell>
  )
}
