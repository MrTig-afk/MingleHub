import { useEffect, useState } from 'react'
import { fetchAdminLeads, createAdminLead } from '../../services/adminApi'
import { buttonStyle, cardStyle, labelStyle, selectStyle } from '../Dashboard/dashboardStyles'

const shimmerCard = (height = 80) => ({
  ...cardStyle,
  height,
  animation: 'dev-shimmer 1.5s infinite',
  background: 'var(--bg-container)',
  marginBottom: '12px',
})

const inputStyle = {
  padding: '10px 12px',
  borderRadius: '8px',
  background: 'var(--bg-surface)',
  color: 'var(--on-surface)',
  border: '1px solid var(--outline)',
  width: '100%',
  boxSizing: 'border-box',
}

const EMPTY_FORM = { name: '', email: '', phone: '', venue_name: '', source: '', notes: '' }

export default function AdminLeads({ token }) {
  const [status, setStatus] = useState('loading')
  const [leads, setLeads] = useState([])
  const [error, setError] = useState(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [form, setForm] = useState(EMPTY_FORM)
  const [formStatus, setFormStatus] = useState('idle') // idle | saving | success | error
  const [formError, setFormError] = useState(null)

  // All setState calls are after await (react-hooks/set-state-in-effect compliant).
  useEffect(() => {
    let cancelled = false
    const run = async () => {
      setStatus('loading')
      try {
        const result = await fetchAdminLeads(token)
        if (cancelled) return
        setLeads(result.leads || [])
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
  }, [token, reloadKey])

  const handleSubmit = async () => {
    setFormStatus('saving')
    setFormError(null)

    // Build body with only non-empty fields
    const body = {}
    if (form.name.trim()) body.name = form.name.trim()
    if (form.email.trim()) body.email = form.email.trim()
    if (form.phone.trim()) body.phone = form.phone.trim()
    if (form.venue_name.trim()) body.venue_name = form.venue_name.trim()
    if (form.source) body.source = form.source
    if (form.notes.trim()) body.notes = form.notes.trim()

    try {
      await createAdminLead(token, body)
      setFormStatus('success')
      setForm(EMPTY_FORM)
      setReloadKey((k) => k + 1)
      setTimeout(() => setFormStatus('idle'), 3000)
    } catch (e) {
      setFormStatus('error')
      setFormError(e.message)
    }
  }

  const emailValid = !form.email.trim() || /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email.trim())
  const submitDisabled = formStatus === 'saving' || (!form.name.trim() && !form.email.trim()) || !emailValid

  return (
    <div>
      <h2 style={{ fontFamily: 'var(--font-headline)', fontSize: '18px', marginTop: 0, marginBottom: '16px' }}>
        Leads
      </h2>

      {/* New Lead Form */}
      <div style={{ ...cardStyle, marginBottom: '16px' }}>
        <h3 style={{ fontFamily: 'var(--font-headline)', fontSize: '15px', margin: '0 0 12px' }}>
          Add New Lead
        </h3>

        {[
          { key: 'name', label: 'Name', type: 'text' },
          { key: 'email', label: 'Email', type: 'text' },
          { key: 'phone', label: 'Phone', type: 'text' },
          { key: 'venue_name', label: 'Venue Name', type: 'text' },
        ].map(({ key, label, type }) => (
          <div key={key} style={{ marginBottom: '10px' }}>
            <label style={{ ...labelStyle, display: 'block', marginBottom: '4px' }}>{label}</label>
            <input
              type={type}
              value={form[key]}
              onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
              style={inputStyle}
            />
            {key === 'email' && form.email.trim() && !emailValid && (
              <div style={{ color: 'var(--tertiary)', fontSize: '12px', marginTop: '2px' }}>
                Invalid email format
              </div>
            )}
          </div>
        ))}

        <div style={{ marginBottom: '10px' }}>
          <label style={{ ...labelStyle, display: 'block', marginBottom: '4px' }}>Source</label>
          <select
            value={form.source}
            onChange={(e) => setForm((f) => ({ ...f, source: e.target.value }))}
            style={selectStyle}
          >
            <option value="">-- select source --</option>
            <option value="contact_form">Contact Form</option>
            <option value="in_person">In Person</option>
            <option value="demo">Demo</option>
          </select>
        </div>

        <div style={{ marginBottom: '12px' }}>
          <label style={{ ...labelStyle, display: 'block', marginBottom: '4px' }}>Notes</label>
          <textarea
            value={form.notes}
            onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
            style={{ ...inputStyle, minHeight: '70px', resize: 'vertical', fontFamily: 'var(--font-body)' }}
          />
        </div>

        {!form.name.trim() && !form.email.trim() && (
          <div style={{ fontSize: '12px', color: 'var(--on-surface-dim)', marginBottom: '8px' }}>
            Name or email is required
          </div>
        )}

        <button
          onClick={handleSubmit}
          disabled={submitDisabled}
          style={{ ...buttonStyle, opacity: submitDisabled ? 0.5 : 1 }}
        >
          {formStatus === 'saving' ? 'Adding...' : 'Add Lead'}
        </button>

        {formStatus === 'success' && (
          <p style={{ color: 'var(--secondary)', fontSize: '13px', marginTop: '8px' }}>
            Lead added successfully
          </p>
        )}
        {formStatus === 'error' && (
          <p style={{ color: 'var(--tertiary)', fontFamily: 'var(--font-mono)', fontSize: '13px', marginTop: '8px' }}>
            {formError}
          </p>
        )}
      </div>

      {/* Leads list */}
      {status === 'loading' && (
        <div>
          {[1, 2, 3].map((i) => <div key={i} style={shimmerCard()} />)}
        </div>
      )}

      {status === 'error' && (
        <div style={{ ...cardStyle, marginTop: '8px' }}>
          <p style={{ color: 'var(--tertiary)', fontFamily: 'var(--font-mono)', fontSize: '13px', margin: '0 0 12px' }}>
            Could not load leads. {error}
          </p>
          <button
            onClick={() => { setError(null); setReloadKey((k) => k + 1) }}
            style={buttonStyle}
          >
            Retry
          </button>
        </div>
      )}

      {status === 'ready' && leads.length === 0 && (
        <div style={{ ...cardStyle, textAlign: 'center' }}>
          <p style={{ ...labelStyle, margin: 0 }}>No leads yet</p>
        </div>
      )}

      {status === 'ready' && leads.map((lead) => (
        <div key={lead.id} style={{ ...cardStyle, marginBottom: '12px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '4px' }}>
            <span style={{ fontWeight: 700 }}>{lead.name || '--'}</span>
            <span style={{ ...labelStyle, fontSize: '12px' }}>
              {lead.created_at ? new Date(lead.created_at).toLocaleDateString() : '--'}
            </span>
          </div>
          {lead.email && (
            <div style={{ ...labelStyle, marginBottom: '4px' }}>{lead.email}</div>
          )}
          <div style={{ fontSize: '13px', color: 'var(--on-surface-dim)', display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
            {lead.phone && <span>Phone: {lead.phone}</span>}
            {lead.venue_name && <span>Venue: {lead.venue_name}</span>}
            {lead.source && <span>Source: {lead.source}</span>}
          </div>
          {lead.notes && (
            <p style={{ ...labelStyle, margin: '8px 0 0', fontSize: '13px' }}>{lead.notes}</p>
          )}
        </div>
      ))}
    </div>
  )
}
