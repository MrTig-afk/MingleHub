import { useEffect, useState } from 'react'
import { fetchBilling } from '../../services/dashboardApi'
import { buttonStyle, cardStyle, formatMoney, labelStyle } from './dashboardStyles'

const shimmerCard = (height = 80) => ({
  ...cardStyle,
  height,
  animation: 'dev-shimmer 1.5s infinite',
  background: 'var(--bg-container)',
})

export default function DashboardBilling({ token, user }) {
  const [data, setData] = useState(null)
  const [fetchError, setFetchError] = useState(null)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    const run = async () => {
      try {
        const d = await fetchBilling(token)
        if (cancelled) return
        setData(d)
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

  // Owner-only guard — checked after all hooks.
  if (user.role !== 'venue_owner') {
    return (
      <div style={{ ...cardStyle, marginTop: '8px', textAlign: 'center' }}>
        <p style={{ color: 'var(--on-surface-dim)' }}>
          Billing is only available to venue owners.
        </p>
      </div>
    )
  }

  if (data === null && fetchError === null) {
    return (
      <div>
        {[60, 100, 120, 80, 80].map((h, i) => (
          <div key={i} style={{ ...shimmerCard(h), marginBottom: '12px' }} />
        ))}
      </div>
    )
  }

  if (fetchError !== null) {
    return (
      <div style={{ ...cardStyle, marginTop: '8px' }}>
        <p style={{ color: 'var(--tertiary)', fontFamily: 'var(--font-mono)', fontSize: '13px', margin: '0 0 12px' }}>
          Could not load billing. {fetchError}
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

  return (
    <div>
      {/* Estimate disclaimer badge — stronger styling */}
      <div style={{
        ...cardStyle,
        background: 'rgba(255, 215, 0, 0.2)',
        border: '2px solid rgba(255, 215, 0, 0.5)',
        textAlign: 'center',
        marginBottom: '16px',
        padding: '12px 16px',
      }}>
        <span style={{ color: '#FFD700', fontWeight: 700, fontSize: '14px', letterSpacing: '0.5px' }}>
          ESTIMATE -- NOT A REAL CHARGE
        </span>
      </div>

      {data.is_test_venue && (
        <div style={{ ...labelStyle, textAlign: 'center', marginBottom: '12px' }}>
          Test venue -- excluded from real invoices.
        </div>
      )}

      {/* Billing model card */}
      <div style={{ ...cardStyle, marginBottom: '12px' }}>
        <div style={{ fontWeight: 700, fontSize: '14px', marginBottom: '8px' }}>Billing Model</div>
        <div style={{ fontSize: '13px' }}>
          {formatMoney(data.model.billing_unit)} per {data.model.block_minutes}-min block of active play
        </div>
        <div style={{ fontSize: '13px' }}>
          Weekday cap: {formatMoney(data.model.nightly_cap_weekday)}/table/night
          ({data.model.blocks_per_night_cap_weekday} blocks)
        </div>
        <div style={{ fontSize: '13px' }}>
          Weekend cap: {formatMoney(data.model.nightly_cap_weekend)}/table/night
          ({data.model.blocks_per_night_cap_weekend} blocks)
        </div>
      </div>

      {/* Tonight card */}
      <div style={{ ...cardStyle, marginBottom: '12px' }}>
        <div style={{ fontWeight: 700, fontSize: '14px', marginBottom: '8px' }}>Tonight</div>
        <div style={{ fontFamily: 'var(--font-headline)', fontSize: '28px' }}>
          {formatMoney(data.tonight.total)}
        </div>
        <div style={{ fontSize: '13px', color: 'var(--on-surface-dim)', marginTop: '4px' }}>
          {data.tonight.blocks_billed} block{data.tonight.blocks_billed === 1 ? '' : 's'} billed
        </div>
        {data.tonight.cap_applied && (
          <div style={{
            display: 'inline-block',
            marginTop: '6px',
            background: 'rgba(231, 0, 110, 0.15)',
            color: 'var(--tertiary)',
            fontSize: '11px',
            padding: '2px 8px',
            borderRadius: '10px',
            fontWeight: 700,
          }}>
            Cap reached on a table
          </div>
        )}
      </div>

      {/* Play-time analytics card */}
      <div style={{ ...cardStyle, marginBottom: '12px' }}>
        <div style={{ fontWeight: 700, fontSize: '14px', marginBottom: '8px' }}>
          Play Time (month to date)
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px', padding: '3px 0' }}>
          <span style={{ color: 'var(--on-surface-dim)' }}>Actual play</span>
          <span>{data.play_analytics.actual_play_minutes} min</span>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px', padding: '3px 0' }}>
          <span style={{ color: 'var(--on-surface-dim)' }}>Billed span</span>
          <span>{data.play_analytics.billed_span_minutes} min</span>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px', padding: '3px 0' }}>
          <span style={{ color: 'var(--on-surface-dim)' }}>Billed</span>
          <span>{data.play_analytics.billed_blocks} blocks</span>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px', padding: '3px 0' }}>
          <span style={{ color: 'var(--on-surface-dim)' }}>Unbilled remainder</span>
          <span>{data.play_analytics.unbilled_remainder_minutes} min</span>
        </div>
      </div>

      {/* Month estimate card */}
      <div style={{ ...cardStyle, marginBottom: '12px' }}>
        <div style={{ fontWeight: 700, fontSize: '14px', marginBottom: '8px' }}>Month to Date</div>
        <div style={{ fontFamily: 'var(--font-headline)', fontSize: '28px' }}>
          {formatMoney(data.month_estimate.total)}
        </div>
        <div style={{ fontSize: '13px', color: 'var(--on-surface-dim)', marginTop: '2px' }}>
          {data.month_estimate.blocks_billed} blocks billed
        </div>
        <div style={{ marginTop: '8px' }}>
          {data.month_estimate.nights.length > 0
            ? data.month_estimate.nights.map((night) => {
                const dateLabel = new Date(night.date + 'T00:00:00').toLocaleDateString([], {
                  month: 'short',
                  day: 'numeric',
                })
                return (
                  <div
                    key={night.date}
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      fontSize: '12px',
                      padding: '4px 0',
                    }}
                  >
                    <span>{dateLabel}</span>
                    <span>
                      {night.blocks_billed} block{night.blocks_billed === 1 ? '' : 's'} -- {formatMoney(night.amount)}
                      {night.cap_applied && (
                        <span style={{ color: 'var(--on-surface-dim)' }}> (capped)</span>
                      )}
                    </span>
                  </div>
                )
              })
            : (
              <div style={{ fontSize: '13px', color: 'var(--on-surface-dim)' }}>
                No billable nights this month.
              </div>
            )
          }
        </div>
      </div>

      {/* Payment status card */}
      <div style={{ ...cardStyle, marginBottom: '12px' }}>
        <div style={{ fontWeight: 700, fontSize: '14px', marginBottom: '8px' }}>Payment</div>
        <div style={{ fontSize: '16px', fontWeight: 700 }}>
          {data.payment_status === 'connected' ? 'Connected' : 'Not connected'}
        </div>
        <div style={{ ...labelStyle, marginTop: '4px' }}>Stripe integration coming soon.</div>
      </div>

      {/* Invoice history card */}
      <div style={cardStyle}>
        <div style={{ fontWeight: 700, fontSize: '14px', marginBottom: '8px' }}>Invoice History</div>
        {data.invoice_history.length > 0
          ? data.invoice_history.map((iv) => (
              <div
                key={iv.period_start}
                style={{
                  display: 'flex', justifyContent: 'space-between',
                  fontSize: '12px', padding: '4px 0',
                }}
              >
                <span>{iv.period_start}</span>
                <span>
                  {formatMoney(iv.total_amount)}
                  <span style={{ color: 'var(--on-surface-dim)' }}> ({iv.status})</span>
                </span>
              </div>
            ))
          : (
            <div style={{ fontSize: '13px', color: 'var(--on-surface-dim)' }}>No invoices yet.</div>
          )
        }
      </div>
    </div>
  )
}
