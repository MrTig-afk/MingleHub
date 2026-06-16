import React from 'react'
import ReactDOM from 'react-dom/client'
import { Analytics } from '@vercel/analytics/react'
import App from './App.jsx'
import DashboardDevLogin from './components/DashboardDevLogin/DashboardDevLogin.jsx'
import PairTags from './components/PairTags/PairTags.jsx'
import './styles/main.css'

// No router dependency yet — /dashboard is just a dev proof page for the
// platform foundation (auth/venue/role). Swapped for real routing once the
// dashboard becomes a real feature.
const path = window.location.pathname
const page = path.startsWith('/dashboard/pair-tags')
  ? <PairTags />
  : path.startsWith('/dashboard')
    ? <DashboardDevLogin />
    : <App />

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {page}
    <Analytics />
  </React.StrictMode>
)
