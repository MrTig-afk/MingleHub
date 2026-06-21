import React from 'react'
import ReactDOM from 'react-dom/client'
import { Analytics } from '@vercel/analytics/react'
import Splash from './components/Splash/Splash.jsx'
import PatronLanding from './components/PatronLanding/PatronLanding.jsx'
import DashboardRoot from './components/Dashboard/DashboardRoot.jsx'
import AdminRoot from './components/Admin/AdminRoot.jsx'
import './styles/main.css'

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
    {page}
    <Analytics />
  </React.StrictMode>
)
