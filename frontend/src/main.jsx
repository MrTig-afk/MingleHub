import React from 'react'
import ReactDOM from 'react-dom/client'
import { Analytics } from '@vercel/analytics/react'
import App from './App.jsx'
import DashboardDevLogin from './components/DashboardDevLogin/DashboardDevLogin.jsx'
import './styles/main.css'

// No router dependency yet — /dashboard is just a dev proof page for the
// platform foundation (auth/venue/role). Swapped for real routing once the
// dashboard becomes a real feature.
const isDashboard = window.location.pathname.startsWith('/dashboard')

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {isDashboard ? <DashboardDevLogin /> : <App />}
    <Analytics />
  </React.StrictMode>
)
