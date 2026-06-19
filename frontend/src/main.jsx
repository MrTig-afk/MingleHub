import React from 'react'
import ReactDOM from 'react-dom/client'
import { Analytics } from '@vercel/analytics/react'
import Splash from './components/Splash/Splash.jsx'
import PatronLanding from './components/PatronLanding/PatronLanding.jsx'
import './styles/main.css'

// Dev safety net: keep the table phone on fresh code. If any service worker is
// still registered on this origin (from an earlier PWA/build), unregister it and
// drop its caches so the dev server's latest bundle always wins -- no private
// tab or manual cache-clear needed. Production keeps its real service worker.
if (import.meta.env.DEV && typeof navigator !== 'undefined' && 'serviceWorker' in navigator) {
  navigator.serviceWorker.getRegistrations()
    .then((regs) => regs.forEach((r) => r.unregister()))
    .catch(() => {})
  if ('caches' in window) {
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
