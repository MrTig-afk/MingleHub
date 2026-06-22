import { useEffect, useState } from 'react'
import { fetchSettings, patchSettings } from '../../services/dashboardApi'
import { buttonStyle, cardStyle, formatMoney, labelStyle, selectStyle } from './dashboardStyles'
import ThemePicker from './ThemePicker'

const shimmerCard = (height = 80) => ({
  ...cardStyle,
  height,
  animation: 'dev-shimmer 1.5s infinite',
  background: 'var(--bg-container)',
})

export default function DashboardSettings({ token, user }) {
  const [data, setData] = useState(null)
  const [fetchError, setFetchError] = useState(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [editName, setEditName] = useState('')
  const [editRestrict, setEditRestrict] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveMsg, setSaveMsg] = useState(null)

  useEffect(() => {
    let cancelled = false
    const run = async () => {
      try {
        const d = await fetchSettings(token)
        if (cancelled) return
        setData(d)
        setEditName(d.editable.name)
        setEditRestrict(d.editable.restrict_adult_content)
        setFetchError(null)
      } catch (e) {
        if (cancelled) return
        const msg = e.message || ''
        if (msg.includes('401') || msg.includes('token') || msg.includes('expired')) {
          localStorage.removeItem('mh_dashboard_token')
          window.location.reload()
          return
        }
        setFetchError(msg)
      }
    }
    run()
    return () => { cancelled = true }
  }, [token, reloadKey])

  // Guard against accidental navigation when there are unsaved changes.
  // hasChanges is derived from state so React sees it update correctly as a dep.
  const hasChanges = data && (
    editName.trim() !== data.editable.name ||
    editRestrict !== data.editable.restrict_adult_content
  )
  useEffect(() => {
    if (!hasChanges) return
    const handler = (e) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [hasChanges])

  // Owner-only guard — checked after all hooks.
  if (user.role !== 'venue_owner') {
    return (
      <div style={{ ...cardStyle, marginTop: '8px', textAlign: 'center' }}>
        <p style={{ color: 'var(--on-surface-dim)' }}>
          Settings are only available to venue owners.
        </p>
      </div>
    )
  }

  if (data === null && fetchError === null) {
    return (
      <div>
        {[120, 80, 80].map((h, i) => (
          <div key={i} style={{ ...shimmerCard(h), marginBottom: '12px' }} />
        ))}
      </div>
    )
  }

  if (fetchError !== null) {
    return (
      <div style={{ ...cardStyle, marginTop: '8px' }}>
        <p style={{ color: 'var(--tertiary)', fontFamily: 'var(--font-mono)', fontSize: '13px', margin: '0 0 12px' }}>
          Could not load settings. {fetchError}
        </p>
        <button
          onClick={() => { setData(null); setFetchError(null); setReloadKey((k) => k + 1) }}
          style={buttonStyle}
        >
          Retry
        </button>
      </div>
    )
  }

  const nameValid = editName.trim().length > 0
  const saveDisabled = !hasChanges || !nameValid || saving

  const handleSave = async () => {
    setSaving(true)
    setSaveMsg(null)
    try {
      const updated = await patchSettings(token, {
        name: editName.trim(),
        restrict_adult_content: editRestrict,
      })
      setData(updated)
      setEditName(updated.editable.name)
      setEditRestrict(updated.editable.restrict_adult_content)
      setSaveMsg('Saved')
      setTimeout(() => setSaveMsg(null), 3000)
    } catch (e) {
      setSaveMsg(e.message || 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      <ThemePicker token={token} />

      {/* Editable settings card */}
      <div style={cardStyle}>
        <div style={{ ...labelStyle, marginBottom: '4px' }}>Venue Name</div>
        <input
          type="text"
          value={editName}
          onChange={(e) => setEditName(e.target.value)}
          maxLength={120}
          style={selectStyle}
        />
        {editName.trim() === '' && (
          <div style={{ color: 'var(--tertiary)', fontSize: '12px', marginTop: '4px' }}>
            Name cannot be empty
          </div>
        )}

        <div style={{ marginTop: '16px' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={editRestrict}
              onChange={(e) => setEditRestrict(e.target.checked)}
            />
            <span> Restrict adult content</span>
          </label>
          <div style={{ ...labelStyle, marginTop: '4px', fontStyle: 'italic' }}>
            When on, the Adults Only toggle never appears for patrons.
            Changes apply to new games only -- active sessions are not affected.
          </div>
        </div>

        <button
          onClick={handleSave}
          disabled={saveDisabled}
          style={{ ...buttonStyle, opacity: saveDisabled ? 0.5 : 1, marginTop: '16px' }}
        >
          {saving ? 'Saving...' : 'Save Settings'}
        </button>

        {saveMsg !== null && (
          <div style={{
            marginTop: '8px',
            fontSize: '13px',
            color: saveMsg === 'Saved' ? 'var(--secondary)' : 'var(--tertiary)',
          }}>
            {saveMsg === 'Saved' ? 'Saved ✓' : saveMsg}
          </div>
        )}
      </div>

      {/* Read-only admin-managed settings card */}
      <div style={{ ...cardStyle, marginTop: '20px' }}>
        <div style={{ fontWeight: 700, fontSize: '14px', marginBottom: '8px' }}>
          Admin-Managed Settings
        </div>
        <div style={{ ...labelStyle, marginBottom: '12px' }}>
          Contact support to change these.
        </div>
        {[
          ['Re-tap interval', `${data.read_only.retap_interval_minutes} min`],
          ['Billing unit', `${formatMoney(data.read_only.billing_unit)} / table / night`],
          ['Nightly cap (weekday)', formatMoney(data.read_only.nightly_cap_weekday)],
          ['Nightly cap (weekend)', formatMoney(data.read_only.nightly_cap_weekend)],
        ].map(([label, value]) => (
          <div
            key={label}
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              padding: '4px 0',
              fontSize: '13px',
              color: 'var(--on-surface-dim)',
            }}
          >
            <span>{label}</span>
            <span>{value}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
