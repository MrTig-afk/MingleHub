import React from 'react'
import ReactDOM from 'react-dom/client'
import { Analytics } from '@vercel/analytics/react'
import Splash from './components/Splash/Splash.jsx'
import PatronLanding from './components/PatronLanding/PatronLanding.jsx'
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

const page = isPatronRoute
  ? <PatronLanding />
  : <Splash />

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {page}
    <Analytics />
  </React.StrictMode>
)
