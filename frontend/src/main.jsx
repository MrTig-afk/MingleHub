import React from 'react'
import ReactDOM from 'react-dom/client'
import { Analytics } from '@vercel/analytics/react'
import Splash from './components/Splash/Splash.jsx'
import PatronLanding from './components/PatronLanding/PatronLanding.jsx'
import './styles/main.css'

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
