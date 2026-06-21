import React from 'react'
import ReactDOM from 'react-dom/client'
import { Analytics } from '@vercel/analytics/react'
import Splash from './components/Splash/Splash.jsx'
import PatronLanding from './components/PatronLanding/PatronLanding.jsx'
import './styles/main.css'

// Dashboard and Admin are lazy-loaded so patron pages never download their JS.
// eslint-disable-next-line react-refresh/only-export-components
const DashboardRoot = React.lazy(() => import('./components/Dashboard/DashboardRoot.jsx'))
// eslint-disable-next-line react-refresh/only-export-components
const AdminRoot = React.lazy(() => import('./components/Admin/AdminRoot.jsx'))

// Always keep the phone on fresh code (dev AND prod). The PWA is self-destroying
// now (see vite.config.js), but a phone that cached an earlier build still has the
// old worker; unregistering any service worker on load and dropping its caches
// flushes it so the latest bundle always wins -- no private tab or manual
// cache-clear needed. Harmless no-op once no worker remains.
if (typeof navigator !== 'undefined' && 'serviceWorker' in navigator) {
  navigator.serviceWorker.getRegistrations()
    .then((regs) => regs.forEach((r) => r.unregister()))
    .catch(() => {})
  if (typeof caches !== 'undefined') {
    caches.keys().then((keys) => keys.forEach((k) => caches.delete(k))).catch(() => {})
  }
}

// The public game route per gamespec.md: minglehub.com/{venue-slug}/{table-number}.
const path = window.location.pathname
const isPatronRoute = /^\/[a-z0-9-]+\/[0-9]+$/.test(path)

// Dashboard and admin prefix checks come BEFORE the patron regex to be explicit
// about intent. (Neither /dashboard/* nor /admin/* would match the patron regex
// anyway -- it requires exactly /{slug}/{number} -- but ordering is defensive.)
let page
if (path.startsWith('/dashboard')) {
  page = <DashboardRoot />
} else if (path.startsWith('/admin')) {
  page = <AdminRoot />
} else if (isPatronRoute) {
  page = <PatronLanding />
} else {
  page = <Splash />
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <React.Suspense fallback={
      <div style={{
        minHeight: '100dvh',
        background: '#0A0A0C',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}>
        <div style={{
          width: '120px',
          height: '4px',
          borderRadius: '2px',
          background: '#1E1E24',
          overflow: 'hidden',
        }}>
          <div style={{
            width: '40%',
            height: '100%',
            background: '#ECB2FF',
            borderRadius: '2px',
            animation: 'dev-shimmer 1.5s infinite',
          }} />
        </div>
      </div>
    }>
      {page}
    </React.Suspense>
    <Analytics />
  </React.StrictMode>
)
