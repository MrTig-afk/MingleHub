import { useEffect, useState } from 'react'
import {
  fetchAdminVenueDetail,
  patchAdminVenue,
  fetchAdminVenueConfigHistory,
} from '../../services/adminApi'
import {
  buttonStyle,
  buttonSecondaryStyle,
  cardStyle,
  labelStyle,
  chipStyle,
} from '../Dashboard/dashboardStyles'

const shimmerCard = (height = 80) => ({
  ...cardStyle,
  height,
  animation: 'dev-shimmer 1.5s infinite',
  background: 'var(--bg-container)',
  marginBottom: '12px',
})

const TEST_CHIP = { background: 'rgba(255,215,0,0.15)', color: '#FFD700' }
const smallChip = (extra) => ({
  fontSize: '11px',
  padding: '2px 8px',
  borderRadius: '10px',
  fontWeight: 700,
  ...extra,
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

const EMPTY_FORM = {
  name: '',
  billing_unit: '',
  retap_interval_minutes: '',
  nightly_cap_weekday: '',
  nightly_cap_weekend: '',
  restrict_adult_content: false,
  is_test: false,
  status: 'active',
  reason: '',
}

// High-impact fields that require a diff/confirm step before patching.
const HIGH_IMPACT = ['billing_unit', 'status', 'is_test']

function venueToForm(venue) {
  return {
    name: venue.name ?? '',
    billing_unit: venue.billing_unit ?? '',
    retap_interval_minutes: String(venue.retap_interval_minutes ?? ''),
    nightly_cap_weekday: venue.nightly_cap_weekday ?? '',
    nightly_cap_weekend: venue.nightly_cap_weekend ?? '',
    restrict_adult_content: Boolean(venue.restrict_adult_content),
    is_test: Boolean(venue.is_test),
    status: venue.status || 'active',
    reason: '',
  }
}

export default function AdminVenueDetail({ token, venueId, navigate }) {
  const [status, setStatus] = useState('loading')
  const [venue, setVenue] = useState(null)
  const [history, setHistory] = useState([])
  const [historyTotal, setHistoryTotal] = useState(0)
  const [error, setError] = useState(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [form, setForm] = useState(EMPTY_FORM)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)
  const [saveSuccess, setSaveSuccess] = useState(null)
  // When non-null, holds {changes, reason} awaiting admin confirmation.
  const [pendingConfirm, setPendingConfirm] = useState(null)

  // Fetch venue detail + config history in parallel.
  // All setState calls are after await (react-hooks/set-state-in-effect compliant).
  useEffect(() => {
    let cancelled = false
    const run = async () => {
      try {
        const [detail, hist] = await Promise.all([
          fetchAdminVenueDetail(token, venueId),
          fetchAdminVenueConfigHistory(token, venueId, { limit: 50, offset: 0 }),
        ])
        if (cancelled) return
        // table_count / sessions_tonight / lifetime_sessions are top-level on the
        // response, not inside `venue` — merge them so the stats row renders them.
        setVenue({
          ...detail.venue,
          table_count: detail.table_count,
          sessions_tonight: detail.sessions_tonight,
          lifetime_sessions: detail.lifetime_sessions,
        })
        setHistory(hist.history || [])
        setHistoryTotal(hist.total || 0)
        setForm(venueToForm(detail.venue))
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
  }, [token, venueId, reloadKey])

  // Build the list of changed fields for the current form vs saved venue values.
  function buildChanges() {
    const changes = []
    if (form.name.trim() !== (venue.name ?? ''))
      changes.push({ field: 'name', oldVal: venue.name, newVal: form.name.trim() })
    if (parseFloat(form.billing_unit) !== parseFloat(venue.billing_unit))
      changes.push({ field: 'billing_unit', oldVal: venue.billing_unit, newVal: form.billing_unit })
    if (parseInt(form.retap_interval_minutes, 10) !== venue.retap_interval_minutes)
      changes.push({ field: 'retap_interval_minutes', oldVal: venue.retap_interval_minutes, newVal: form.retap_interval_minutes })
    if (parseFloat(form.nightly_cap_weekday) !== parseFloat(venue.nightly_cap_weekday))
      changes.push({ field: 'nightly_cap_weekday', oldVal: venue.nightly_cap_weekday, newVal: form.nightly_cap_weekday })
    if (parseFloat(form.nightly_cap_weekend) !== parseFloat(venue.nightly_cap_weekend))
      changes.push({ field: 'nightly_cap_weekend', oldVal: venue.nightly_cap_weekend, newVal: form.nightly_cap_weekend })
    if (form.restrict_adult_content !== venue.restrict_adult_content)
      changes.push({ field: 'restrict_adult_content', oldVal: venue.restrict_adult_content, newVal: form.restrict_adult_content })
    if (form.is_test !== venue.is_test)
      changes.push({ field: 'is_test', oldVal: venue.is_test, newVal: form.is_test })
    if (form.status !== venue.status)
      changes.push({ field: 'status', oldVal: venue.status, newVal: form.status })
    return changes
  }

  // Fire the actual PATCH call. Called both from handleOverride (no high-impact)
  // and from confirmOverride (after the user confirms the diff panel).
  async function applyPatch() {
    setSaving(true)
    setSaveError(null)
    setSaveSuccess(null)

    const body = { reason: form.reason }
    if (form.name.trim() !== (venue.name ?? ''))
      body.name = form.name.trim()
    if (parseFloat(form.billing_unit) !== parseFloat(venue.billing_unit))
      body.billing_unit = parseFloat(form.billing_unit)
    if (parseInt(form.retap_interval_minutes, 10) !== venue.retap_interval_minutes)
      body.retap_interval_minutes = parseInt(form.retap_interval_minutes, 10)
    if (parseFloat(form.nightly_cap_weekday) !== parseFloat(venue.nightly_cap_weekday))
      body.nightly_cap_weekday = parseFloat(form.nightly_cap_weekday)
    if (parseFloat(form.nightly_cap_weekend) !== parseFloat(venue.nightly_cap_weekend))
      body.nightly_cap_weekend = parseFloat(form.nightly_cap_weekend)
    if (form.restrict_adult_content !== venue.restrict_adult_content)
      body.restrict_adult_content = form.restrict_adult_content
    if (form.is_test !== venue.is_test)
      body.is_test = form.is_test
    if (form.status !== venue.status)
      body.status = form.status

    const changedCount = Object.keys(body).length - 1 // minus reason
    if (changedCount === 0) {
      setSaveSuccess('No changes to apply')
      setSaving(false)
      return
    }

    try {
      const result = await patchAdminVenue(token, venueId, body)
      setSaveSuccess(`Override applied: ${result.overrides_recorded} field(s) updated`)
      setForm((f) => ({ ...f, reason: '' }))
      setReloadKey((k) => k + 1)
    } catch (e) {
      setSaveError(e.message)
    }
    setSaving(false)
  }

  const handleOverride = async () => {
    const changes = buildChanges()
    const hasHighImpact = changes.some((c) => HIGH_IMPACT.includes(c.field))

    // If there are high-impact fields and no confirmation pending yet, show diff panel.
    if (hasHighImpact && pendingConfirm === null) {
      setPendingConfirm({ changes, reason: form.reason.trim() })
      return
    }

    await applyPatch()
  }

  const confirmOverride = async () => {
    setPendingConfirm(null)
    await applyPatch()
  }

  if (status === 'loading') {
    return (
      <div>
        {[1, 2, 3].map((i) => <div key={i} style={shimmerCard()} />)}
      </div>
    )
  }

  if (status === 'error') {
    return (
      <div style={{ ...cardStyle, marginTop: '8px' }}>
        <p style={{ color: 'var(--tertiary)', fontFamily: 'var(--font-mono)', fontSize: '13px', margin: '0 0 12px' }}>
          Could not load venue. {error}
        </p>
        <button
          onClick={() => { setStatus('loading'); setError(null); setReloadKey((k) => k + 1) }}
          style={buttonStyle}
        >
          Retry
        </button>
      </div>
    )
  }

  const reasonBlank = !form.reason.trim()

  return (
    <div>
      {/* Back link */}
      <button
        onClick={() => navigate('/admin/venues')}
        style={{ ...buttonSecondaryStyle, marginBottom: '16px', fontSize: '13px' }}
      >
        Back to Venues
      </button>

      {/* Venue info card */}
      <div style={{ ...cardStyle, marginBottom: '16px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '8px' }}>
          <h2 style={{ fontFamily: 'var(--font-headline)', fontSize: '20px', margin: 0 }}>
            {venue.name}
          </h2>
          <div style={{ display: 'flex', gap: '6px', alignItems: 'center', flexShrink: 0 }}>
            <span style={chipStyle(venue.status === 'active' ? 'active' : 'paused')}>
              {venue.status}
            </span>
            {venue.is_test && (
              <span style={smallChip(TEST_CHIP)}>Test</span>
            )}
          </div>
        </div>
        <div style={{ ...labelStyle, marginBottom: '4px' }}>
          {venue.slug} -- {venue.venue_type}
        </div>
        <div style={{ fontSize: '13px', color: 'var(--on-surface-dim)', display: 'flex', gap: '16px', flexWrap: 'wrap', marginBottom: '8px' }}>
          <span>Tables: <strong style={{ color: 'var(--on-surface)' }}>{venue.table_count ?? 0}</strong></span>
          <span>Tonight: <strong style={{ color: 'var(--on-surface)' }}>{venue.sessions_tonight ?? 0}</strong></span>
          <span>Lifetime: <strong style={{ color: 'var(--on-surface)' }}>{venue.lifetime_sessions ?? 0}</strong></span>
        </div>
        <div style={{ ...labelStyle, fontSize: '12px' }}>
          Created: {venue.created_at ? new Date(venue.created_at).toLocaleDateString() : '--'}
          {' '}|{' '}
          Updated: {venue.updated_at ? new Date(venue.updated_at).toLocaleDateString() : '--'}
        </div>
      </div>

      {/* Override form */}
      <div style={{ ...cardStyle, marginBottom: '16px' }}>
        <h3 style={{ fontFamily: 'var(--font-headline)', fontSize: '16px', margin: '0 0 16px' }}>
          Override Configuration
        </h3>

        {/* Test Mode highlight */}
        <div style={{
          ...cardStyle,
          background: venue.is_test ? 'rgba(255,215,0,0.08)' : 'var(--bg-surface)',
          marginBottom: '16px',
          padding: '12px',
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <span style={{ fontWeight: 700 }}>Test Mode</span>
              <span style={{ ...labelStyle, marginLeft: '8px' }}>(Skips billing and excludes from analytics)</span>
            </div>
            <span style={{ ...labelStyle, fontWeight: 700, color: venue.is_test ? '#FFD700' : 'var(--on-surface-dim)' }}>
              Currently: {venue.is_test ? 'ON' : 'OFF'}
            </span>
          </div>
        </div>

        {/* Venue name — admin-only rename (owners see this read-only) */}
        <div style={{ marginBottom: '12px' }}>
          <label style={{ ...labelStyle, display: 'block', marginBottom: '4px' }}>
            Venue Name
            <span style={{ marginLeft: '8px', color: 'var(--on-surface-dim)' }}>
              (current: {venue.name})
            </span>
          </label>
          <input
            type="text"
            maxLength={120}
            value={form.name}
            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
            style={inputStyle}
          />
        </div>

        {/* Field rows — each shows current value beside the label */}
        {[
          { key: 'billing_unit', label: 'Billing Unit', type: 'number', step: '0.01', min: '0', current: venue.billing_unit },
          { key: 'retap_interval_minutes', label: 'Retap Interval (minutes)', type: 'number', step: '1', min: '1', current: venue.retap_interval_minutes },
          { key: 'nightly_cap_weekday', label: 'Nightly Cap Weekday', type: 'number', step: '0.01', min: '0', current: venue.nightly_cap_weekday },
          { key: 'nightly_cap_weekend', label: 'Nightly Cap Weekend', type: 'number', step: '0.01', min: '0', current: venue.nightly_cap_weekend },
        ].map(({ key, label, type, step, min, current }) => (
          <div key={key} style={{ marginBottom: '12px' }}>
            <label style={{ ...labelStyle, display: 'block', marginBottom: '4px' }}>
              {label}
              <span style={{ marginLeft: '8px', color: 'var(--on-surface-dim)' }}>
                (current: {current})
              </span>
            </label>
            <input
              type={type}
              step={step}
              min={min}
              value={form[key]}
              onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
              style={inputStyle}
            />
          </div>
        ))}

        {/* Checkboxes */}
        <div style={{ marginBottom: '12px' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={form.restrict_adult_content}
              onChange={(e) => setForm((f) => ({ ...f, restrict_adult_content: e.target.checked }))}
            />
            <span style={labelStyle}>
              Restrict Adult Content
              <span style={{ marginLeft: '8px' }}>(current: {venue.restrict_adult_content ? 'yes' : 'no'})</span>
            </span>
          </label>
        </div>

        <div style={{ marginBottom: '12px' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={form.is_test}
              onChange={(e) => setForm((f) => ({ ...f, is_test: e.target.checked }))}
            />
            <span style={labelStyle}>
              Test Mode
              <span style={{ marginLeft: '8px' }}>(current: {venue.is_test ? 'yes' : 'no'})</span>
            </span>
          </label>
        </div>

        {/* Status select */}
        <div style={{ marginBottom: '16px' }}>
          <label style={{ ...labelStyle, display: 'block', marginBottom: '4px' }}>
            Status
            <span style={{ marginLeft: '8px' }}>(current: {venue.status})</span>
          </label>
          <select
            value={form.status}
            onChange={(e) => setForm((f) => ({ ...f, status: e.target.value }))}
            style={inputStyle}
          >
            <option value="active">active</option>
            <option value="suspended">suspended</option>
            <option value="cancelled">cancelled</option>
          </select>
        </div>

        {/* Reason textarea (REQUIRED) */}
        <div style={{ marginBottom: '16px' }}>
          <label style={{ ...labelStyle, display: 'block', marginBottom: '4px', fontWeight: 700, color: 'var(--on-surface)' }}>
            Reason for change (required)
          </label>
          <textarea
            placeholder="Why is this change being made?"
            maxLength={500}
            value={form.reason}
            onChange={(e) => { setForm((f) => ({ ...f, reason: e.target.value })); setSaveSuccess(null) }}
            style={{
              ...inputStyle,
              minHeight: '80px',
              resize: 'vertical',
              fontFamily: 'var(--font-body)',
            }}
          />
          <div style={{ ...labelStyle, textAlign: 'right', marginTop: '2px' }}>
            {form.reason.length}/500
          </div>
        </div>

        <button
          onClick={handleOverride}
          disabled={saving || reasonBlank}
          style={{ ...buttonStyle, opacity: (saving || reasonBlank) ? 0.5 : 1 }}
        >
          {saving ? 'Applying...' : 'Apply Override'}
        </button>

        {saveSuccess && (
          <p style={{ color: 'var(--secondary)', fontSize: '13px', marginTop: '8px' }}>
            {saveSuccess}
          </p>
        )}
        {saveError && (
          <p style={{ color: 'var(--tertiary)', fontFamily: 'var(--font-mono)', fontSize: '13px', marginTop: '8px' }}>
            {saveError}
          </p>
        )}

        {/* Diff/confirm panel for high-impact changes */}
        {pendingConfirm && (
          <div style={{
            ...cardStyle,
            background: 'rgba(231, 0, 110, 0.08)',
            border: '2px solid var(--tertiary)',
            marginTop: '12px',
            padding: '16px',
          }}>
            <div style={{ fontWeight: 700, marginBottom: '8px', color: 'var(--tertiary)' }}>
              Confirm High-Impact Change
            </div>
            {pendingConfirm.changes.map(({ field, oldVal, newVal }) => (
              <div key={field} style={{ fontSize: '13px', marginBottom: '4px' }}>
                <strong>{field}:</strong> {String(oldVal)} &rarr; {String(newVal)}
              </div>
            ))}
            <div style={{ fontSize: '13px', fontStyle: 'italic', margin: '8px 0', color: 'var(--on-surface-dim)' }}>
              Reason: &ldquo;{pendingConfirm.reason}&rdquo;
            </div>
            <div style={{ display: 'flex', gap: '8px', marginTop: '12px' }}>
              <button
                onClick={confirmOverride}
                disabled={saving}
                style={{ ...buttonStyle, opacity: saving ? 0.5 : 1 }}
              >
                {saving ? 'Applying...' : 'Confirm Override'}
              </button>
              <button onClick={() => setPendingConfirm(null)} style={buttonSecondaryStyle}>
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Config history */}
      <div style={cardStyle}>
        <h3 style={{ fontFamily: 'var(--font-headline)', fontSize: '16px', margin: '0 0 16px' }}>
          Change History
        </h3>

        {history.length === 0 ? (
          <p style={{ ...labelStyle, margin: 0 }}>No configuration changes recorded</p>
        ) : (
          history.map((entry) => (
            <div key={entry.id} style={{ borderBottom: '1px solid var(--outline)', paddingBottom: '12px', marginBottom: '12px' }}>
              <div style={{ marginBottom: '2px' }}>
                <strong>{entry.field_name}</strong>
                <span style={labelStyle}>
                  {' '}{entry.old_value} &rarr; {entry.new_value}
                </span>
              </div>
              <div style={{ ...labelStyle, fontStyle: 'italic', marginBottom: '2px' }}>
                &ldquo;{entry.reason}&rdquo;
              </div>
              <div style={{ ...labelStyle, fontSize: '12px' }}>
                by {entry.changed_by_clerk_id || entry.changed_by || 'unknown'}
                {' -- '}
                {entry.created_at ? new Date(entry.created_at).toLocaleString() : '--'}
              </div>
            </div>
          ))
        )}

        {/* Paginated "Load More" button */}
        {history.length < historyTotal && (
          <button
            onClick={async () => {
              try {
                const more = await fetchAdminVenueConfigHistory(token, venueId, {
                  limit: 50,
                  offset: history.length,
                })
                setHistory((prev) => [...prev, ...(more.history || [])])
                setHistoryTotal(more.total)
              } catch {
                // Silently fail — existing history remains visible.
              }
            }}
            style={{ ...buttonSecondaryStyle, marginTop: '12px', width: '100%' }}
          >
            Load More ({historyTotal - history.length} remaining)
          </button>
        )}
      </div>
    </div>
  )
}
