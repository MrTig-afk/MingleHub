import { useEffect, useState } from 'react'
import { fetchAdminSupport, patchAdminSupportMessage } from '../../services/adminApi'
import { buttonStyle, buttonSecondaryStyle, cardStyle, labelStyle } from '../Dashboard/dashboardStyles'

const shimmerCard = (height = 80) => ({
  ...cardStyle,
  height,
  animation: 'dev-shimmer 1.5s infinite',
  background: 'var(--bg-container)',
  marginBottom: '12px',
})

const OPEN_CHIP = { background: 'rgba(0,238,252,0.15)', color: 'var(--secondary)' }
const RESOLVED_CHIP = { background: 'rgba(0,200,100,0.15)', color: '#00C864' }
const statusChip = (s) => ({
  fontSize: '11px',
  padding: '2px 8px',
  borderRadius: '10px',
  fontWeight: 700,
  ...(s === 'open' ? OPEN_CHIP : RESOLVED_CHIP),
})

export default function AdminSupport({ token }) {
  const [status, setStatus] = useState('loading')
  const [messages, setMessages] = useState([])
  const [error, setError] = useState(null)
  const [filter, setFilter] = useState('open')
  const [reloadKey, setReloadKey] = useState(0)
  const [patchingId, setPatchingId] = useState(null)

  // All setState calls are after await (react-hooks/set-state-in-effect compliant).
  useEffect(() => {
    let cancelled = false
    const run = async () => {
      setStatus('loading')
      try {
        const result = await fetchAdminSupport(token, filter)
        if (cancelled) return
        setMessages(result.messages || [])
        setStatus('ready')
      } catch (e) {
        if (cancelled) return
        const msg = e.message || ''
        if (msg.includes('401') || msg.includes('token') || msg.includes('expired')) {
          localStorage.removeItem('mh_admin_token')
          window.location.reload()
          return
        }
        setStatus('error')
        setError(msg)
      }
    }
    run()
    return () => { cancelled = true }
  }, [token, filter, reloadKey])

  const handlePatchStatus = async (messageId, newStatus) => {
    setPatchingId(messageId)
    try {
      await patchAdminSupportMessage(token, messageId, { status: newStatus })
      setReloadKey((k) => k + 1)
    } catch (e) {
      setError(e.message)
    }
    setPatchingId(null)
  }

  const emptyLabel = filter === 'all' ? 'No support messages' : `No ${filter} support messages`

  return (
    <div>
      <h2 style={{ fontFamily: 'var(--font-headline)', fontSize: '18px', marginTop: 0, marginBottom: '12px' }}>
        Support Inbox
      </h2>

      {/* Filter row */}
      <div style={{ display: 'flex', gap: '8px', marginBottom: '16px' }}>
        {['open', 'resolved', 'all'].map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            style={filter === f ? buttonStyle : buttonSecondaryStyle}
          >
            {f.charAt(0).toUpperCase() + f.slice(1)}
          </button>
        ))}
      </div>

      {status === 'loading' && (
        <div>
          {[1, 2, 3].map((i) => <div key={i} style={shimmerCard()} />)}
        </div>
      )}

      {status === 'error' && (
        <div style={{ ...cardStyle, marginTop: '8px' }}>
          <p style={{ color: 'var(--tertiary)', fontFamily: 'var(--font-mono)', fontSize: '13px', margin: '0 0 12px' }}>
            Could not load messages. {error}
          </p>
          <button
            onClick={() => { setError(null); setReloadKey((k) => k + 1) }}
            style={buttonStyle}
          >
            Retry
          </button>
        </div>
      )}

      {status === 'ready' && messages.length === 0 && (
        <div style={{ ...cardStyle, textAlign: 'center' }}>
          <p style={{ ...labelStyle, margin: 0 }}>{emptyLabel}</p>
        </div>
      )}

      {status === 'ready' && messages.map((msg) => (
        <div key={msg.id} style={{ ...cardStyle, marginBottom: '12px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
            <span style={{ fontWeight: 700 }}>{msg.name || 'Anonymous'}</span>
            <span style={statusChip(msg.status)}>{msg.status}</span>
          </div>
          {msg.email && (
            <div style={{ ...labelStyle, marginBottom: '4px' }}>{msg.email}</div>
          )}
          <p style={{ margin: '8px 0', fontSize: '14px', lineHeight: '1.5' }}>{msg.message}</p>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ ...labelStyle, fontSize: '12px' }}>
              {msg.created_at ? new Date(msg.created_at).toLocaleString() : '--'}
            </span>
            <button
              onClick={() => handlePatchStatus(msg.id, msg.status === 'open' ? 'resolved' : 'open')}
              disabled={patchingId === msg.id}
              style={{
                ...buttonSecondaryStyle,
                fontSize: '12px',
                padding: '6px 12px',
                opacity: patchingId === msg.id ? 0.5 : 1,
              }}
            >
              {msg.status === 'open' ? 'Mark Resolved' : 'Reopen'}
            </button>
          </div>
        </div>
      ))}
    </div>
  )
}
