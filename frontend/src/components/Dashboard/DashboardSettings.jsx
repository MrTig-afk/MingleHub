import { useEffect, useState } from 'react'
import { fetchSettings, patchSettings, cancelVenue, reactivateVenue } from '../../services/dashboardApi'
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
  const [editRestrict, setEditRestrict] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveMsg, setSaveMsg] = useState(null)
  const [cancelStarted, setCancelStarted] = useState(false)
  const [cancelReason, setCancelReason] = useState('')
  const [cancelConfirm, setCancelConfirm] = useState(false)
  const [cancelBusy, setCancelBusy] = useState(false)
  const [cancelError, setCancelError] = useState(null)
  const [reactivateBusy, setReactivateBusy] = useState(false)
  const [reactivateError, setReactivateError] = useState(null)
  // Days remaining in the reactivation window — set inside useEffect so Date.now()
  // is called outside render, satisfying react-hooks/purity.
  const [daysRemaining, setDaysRemaining] = useState(0)

  useEffect(() => {
    let cancelled = false
    const run = async () => {
      try {
        const d = await fetchSettings(token)
        if (cancelled) return
        setData(d)
        setEditRestrict(d.editable.restrict_adult_content)
        setFetchError(null)
        // Compute days remaining outside render to satisfy react-hooks/purity.
        if (d.venue_status?.cancelled_at) {
          const diff = 7 - Math.floor(
            (Date.now() - new Date(d.venue_status.cancelled_at).getTime()) / 86400000
          )
          setDaysRemaining(Math.max(0, diff))
        } else {
          setDaysRemaining(0)
        }
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

  // Auto-save the adult-content toggle on change. Optimistic: flip the UI first,
  // then persist; revert if the PATCH fails. Mirrors the ThemePicker pattern.
  const handleToggleRestrict = async (next) => {
    const prev = editRestrict
    setEditRestrict(next)
    setSaving(true)
    setSaveMsg(null)
    try {
      const updated = await patchSettings(token, { restrict_adult_content: next })
      setData(updated)
      setEditRestrict(updated.editable.restrict_adult_content)
      setSaveMsg('Saved')
      setTimeout(() => setSaveMsg(null), 3000)
    } catch (e) {
      setEditRestrict(prev)
      setSaveMsg(e.message || 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const handleCancel = async () => {
    setCancelBusy(true)
    setCancelError(null)
    try {
      await cancelVenue(token, cancelReason.trim())
      setCancelConfirm(false)
      setCancelStarted(false)
      setCancelReason('')
      setReloadKey((k) => k + 1)
    } catch (e) {
      setCancelError(e.message || 'Cancel failed')
    } finally {
      setCancelBusy(false)
    }
  }

  const handleReactivate = async () => {
    setReactivateBusy(true)
    setReactivateError(null)
    try {
      await reactivateVenue(token)
      setReloadKey((k) => k + 1)
    } catch (e) {
      setReactivateError(e.message || 'Reactivate failed')
    } finally {
      setReactivateBusy(false)
    }
  }

  const venueStatus = data?.venue_status?.status
  const canReactivate = data?.venue_status?.can_reactivate

  return (
    <div>
      {/* Status banner — shown when venue is not active */}
      {venueStatus === 'suspended' && (
        <div style={{
          ...cardStyle,
          background: 'rgba(255, 215, 0, 0.15)',
          border: '1px solid rgba(255, 215, 0, 0.5)',
          marginBottom: '12px',
        }}>
          <span style={{ color: '#FFD700', fontWeight: 700 }}>Account suspended</span>
          {' -- '}unpaid invoice. Go to Billing to settle.
        </div>
      )}
      {venueStatus === 'cancelled' && canReactivate && (
        <div style={{
          ...cardStyle,
          background: 'rgba(231, 0, 110, 0.12)',
          border: '1px solid rgba(231, 0, 110, 0.4)',
          marginBottom: '12px',
        }}>
          <span style={{ color: 'var(--tertiary)', fontWeight: 700 }}>Account cancelled.</span>
          {' '}{daysRemaining} day{daysRemaining === 1 ? '' : 's'} left to reactivate.
        </div>
      )}
      {venueStatus === 'cancelled' && !canReactivate && (
        <div style={{
          ...cardStyle,
          background: 'rgba(100,100,100,0.15)',
          border: '1px solid rgba(100,100,100,0.4)',
          marginBottom: '12px',
        }}>
          <span style={{ color: 'var(--on-surface-dim)', fontWeight: 700 }}>Account cancelled.</span>
          {' '}Reactivation expired. Contact support.
        </div>
      )}

      <ThemePicker token={token} />

      {/* Editable settings card — auto-saves on change (no Save button) */}
      <div style={cardStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
          <div style={{ fontWeight: 700, fontSize: '14px' }}>Content</div>
          {saveMsg !== null && (
            <span style={{
              fontSize: '12px',
              color: saveMsg === 'Saved' ? 'var(--secondary)' : 'var(--tertiary)',
            }}>
              {saveMsg === 'Saved' ? 'Saved ✓' : saveMsg}
            </span>
          )}
        </div>

        <div style={{ marginTop: '12px' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: saving ? 'wait' : 'pointer' }}>
            <input
              type="checkbox"
              checked={editRestrict}
              disabled={saving}
              onChange={(e) => handleToggleRestrict(e.target.checked)}
            />
            <span> Restrict adult content</span>
          </label>
          <div style={{ ...labelStyle, marginTop: '4px', fontStyle: 'italic' }}>
            When on, the Adults Only toggle never appears for patrons.
            Changes apply to new games only -- active sessions are not affected.
          </div>
        </div>
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
          ['Venue name', data.read_only.name],
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

      {/* Cancel account section — only shown when active */}
      {venueStatus === 'active' && (
        <div style={{
          ...cardStyle,
          marginTop: '20px',
          border: '1px solid rgba(231, 0, 110, 0.4)',
        }}>
          <div style={{ fontWeight: 700, fontSize: '14px', marginBottom: '8px', color: 'var(--tertiary)' }}>
            Cancel Account
          </div>
          {!cancelStarted ? (
            // Step 1 — intent only. The reason box appears after the owner
            // commits to cancelling, not before.
            <>
              <div style={labelStyle}>
                Cancelling stops new games immediately. You have 7 days to reactivate.
              </div>
              <button
                onClick={() => setCancelStarted(true)}
                style={{
                  ...buttonStyle,
                  background: 'var(--tertiary)',
                  marginTop: '12px',
                }}
              >
                Cancel Account
              </button>
            </>
          ) : !cancelConfirm ? (
            // Step 2 — collect the reason.
            <>
              <div style={labelStyle}>
                Tell us why you&rsquo;re cancelling (required), then continue.
              </div>
              <textarea
                value={cancelReason}
                onChange={(e) => setCancelReason(e.target.value)}
                placeholder="Reason for cancelling (required)"
                maxLength={500}
                rows={3}
                autoFocus
                style={{
                  ...selectStyle,
                  marginTop: '12px',
                  resize: 'vertical',
                }}
              />
              <div style={{ display: 'flex', gap: '8px', marginTop: '12px' }}>
                <button
                  onClick={() => { if (cancelReason.trim().length > 0) setCancelConfirm(true) }}
                  disabled={cancelReason.trim().length === 0}
                  style={{
                    ...buttonStyle,
                    background: 'var(--tertiary)',
                    opacity: cancelReason.trim().length === 0 ? 0.5 : 1,
                  }}
                >
                  Continue
                </button>
                <button
                  onClick={() => { setCancelStarted(false); setCancelReason(''); setCancelError(null) }}
                  style={{ ...buttonStyle, background: 'var(--bg-surface)', color: 'var(--on-surface)' }}
                >
                  Go Back
                </button>
              </div>
            </>
          ) : (
            // Step 3 — final confirm.
            <>
              <div style={{ ...labelStyle, marginBottom: '12px' }}>
                Are you sure? Your tags will stop working immediately. You have 7 days to reactivate.
              </div>
              <div style={{ display: 'flex', gap: '8px' }}>
                <button
                  onClick={handleCancel}
                  disabled={cancelBusy}
                  style={{ ...buttonStyle, background: 'var(--tertiary)', opacity: cancelBusy ? 0.5 : 1 }}
                >
                  {cancelBusy ? 'Cancelling...' : 'Yes, Cancel'}
                </button>
                <button
                  onClick={() => { setCancelConfirm(false); setCancelError(null) }}
                  style={{ ...buttonStyle, background: 'var(--bg-surface)', color: 'var(--on-surface)' }}
                >
                  Go Back
                </button>
              </div>
            </>
          )}
          {cancelError && (
            <div style={{ color: 'var(--tertiary)', fontSize: '13px', marginTop: '8px' }}>
              {cancelError}
            </div>
          )}
        </div>
      )}

      {/* Reactivate section — only shown when cancelled within window */}
      {venueStatus === 'cancelled' && canReactivate && (
        <div style={{
          ...cardStyle,
          marginTop: '20px',
          border: '1px solid rgba(0, 238, 100, 0.4)',
        }}>
          <div style={{ fontWeight: 700, fontSize: '14px', marginBottom: '8px', color: 'var(--secondary)' }}>
            Reactivate Account
          </div>
          <div style={labelStyle}>
            {daysRemaining} day{daysRemaining === 1 ? '' : 's'} remaining to reactivate at no extra charge.
          </div>
          <button
            onClick={handleReactivate}
            disabled={reactivateBusy}
            style={{ ...buttonStyle, marginTop: '12px', opacity: reactivateBusy ? 0.5 : 1 }}
          >
            {reactivateBusy ? 'Reactivating...' : 'Reactivate My Account'}
          </button>
          {reactivateError && (
            <div style={{ color: 'var(--tertiary)', fontSize: '13px', marginTop: '8px' }}>
              {reactivateError}
            </div>
          )}
        </div>
      )}

      {/* Expired window — cancelled but can no longer self-reactivate */}
      {venueStatus === 'cancelled' && !canReactivate && (
        <div style={{ ...cardStyle, marginTop: '20px', background: 'rgba(100,100,100,0.1)' }}>
          <div style={{ ...labelStyle }}>
            Your reactivation window has expired. Contact support to reactivate.
          </div>
        </div>
      )}

      {/* Suspended — payment required */}
      {venueStatus === 'suspended' && (
        <div style={{
          ...cardStyle,
          marginTop: '20px',
          background: 'rgba(255, 215, 0, 0.1)',
          border: '1px solid rgba(255, 215, 0, 0.4)',
        }}>
          <div style={{ color: '#FFD700', fontWeight: 700, marginBottom: '4px' }}>
            Account Suspended
          </div>
          <div style={labelStyle}>
            Your account is suspended due to an unpaid invoice. Please settle your balance in Billing.
          </div>
        </div>
      )}
    </div>
  )
}
