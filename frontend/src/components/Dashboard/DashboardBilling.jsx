import { useEffect, useState } from 'react'
import { fetchBilling, syncBillingToStripe } from '../../services/dashboardApi'
import { buttonStyle, buttonSecondaryStyle, cardStyle, formatMoney, labelStyle } from './dashboardStyles'

/**
 * Returns true when the error is a network/offline issue (fetch itself threw),
 * false when the server responded with an HTTP error (we got a status code).
 *
 * Detection heuristic:
 * - navigator.onLine === false  --> definitely offline
 * - err is a TypeError --> fetch rejects with TypeError on network failure
 *   (DNS failure, connection refused, CORS preflight blocked by offline, etc.)
 * - err.message is a raw HTTP status code string or server detail --> server responded
 */
function isNetworkError(err) {
  if (!navigator.onLine) return true
  if (err instanceof TypeError) return true
  return false
}

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
  const [settleState, setSettleState] = useState('idle')
  // 'idle' | 'syncing' | 'synced' | 'network_error' | 'server_error' | 'confirming'
  const [settleError, setSettleError] = useState(null)

  // Detect return from Stripe Checkout (future: once backend URLs point here)
  const [paymentReturn, setPaymentReturn] = useState(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.get('payment') === 'success') return 'success'
    if (params.get('payment') === 'cancelled') return 'cancelled'
    return null
  })

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

  useEffect(() => {
    if (paymentReturn) {
      const url = new URL(window.location)
      url.searchParams.delete('payment')
      window.history.replaceState({}, '', url)
    }
  }, [paymentReturn])

  const handleSettle = async () => {
    setSettleState('syncing')
    setSettleError(null)
    try {
      await syncBillingToStripe(token)
      setSettleState('synced')
      // Refresh billing data to reflect the new invoice status
      setReloadKey((k) => k + 1)
    } catch (err) {
      // 401 / token expiry -> logout (matches existing pattern from lines 28-31)
      const msg = err.message || ''
      if (msg.includes('401') || msg.includes('token') || msg.includes('expired')) {
        localStorage.removeItem('mh_dashboard_token')
        window.location.reload()
        return
      }
      // Distinguish network error from server error
      if (isNetworkError(err)) {
        setSettleState('network_error')
        setSettleError(null)
      } else {
        setSettleState('server_error')
        setSettleError(msg || 'Something went wrong')
      }
    }
  }

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
      <style>{`@keyframes spin { to { transform: rotate(360deg) } }`}</style>

      {paymentReturn === 'success' && (
        <div style={{
          ...cardStyle,
          background: 'rgba(57, 224, 139, 0.12)',
          border: '1px solid rgba(57, 224, 139, 0.35)',
          marginBottom: '16px',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}>
          <span style={{ fontSize: '13px', color: 'var(--correct)', fontWeight: 700 }}>
            Payment received. Your invoice will update shortly.
          </span>
          <button
            onClick={() => setPaymentReturn(null)}
            style={{ background: 'none', border: 'none', color: 'var(--on-surface-dim)', cursor: 'pointer', fontSize: '16px' }}
          >
            x
          </button>
        </div>
      )}

      {paymentReturn === 'cancelled' && (
        <div style={{
          ...cardStyle,
          background: 'rgba(255, 200, 87, 0.12)',
          border: '1px solid rgba(255, 200, 87, 0.35)',
          marginBottom: '16px',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}>
          <span style={{ fontSize: '13px', color: 'var(--gold)' }}>
            Payment was not completed. You can try again any time -- no charge was made.
          </span>
          <button
            onClick={() => setPaymentReturn(null)}
            style={{ background: 'none', border: 'none', color: 'var(--on-surface-dim)', cursor: 'pointer', fontSize: '16px' }}
          >
            x
          </button>
        </div>
      )}

      {/* Venue status note — shown when not active */}
      {data.venue_status && data.venue_status !== 'active' && (
        <div style={{
          ...cardStyle,
          background: 'rgba(100,100,100,0.15)',
          marginBottom: '16px',
          fontSize: '13px',
        }}>
          Your venue is currently <strong>{data.venue_status}</strong>. Existing invoices are shown below.
        </div>
      )}

      {/* Estimate disclaimer badge — stronger styling */}
      <div style={{
        ...cardStyle,
        background: 'rgba(255, 200, 87, 0.2)',
        border: '2px solid rgba(255, 200, 87, 0.5)',
        textAlign: 'center',
        marginBottom: '16px',
        padding: '12px 16px',
      }}>
        <span style={{ color: 'var(--gold)', fontWeight: 700, fontSize: '14px', letterSpacing: '0.5px' }}>
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
            background: 'rgba(255, 92, 108, 0.15)',
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

      {/* Payment status + settle action card */}
      <div style={{ ...cardStyle, marginBottom: '12px' }}>
        <div style={{ fontWeight: 700, fontSize: '14px', marginBottom: '8px' }}>Payment</div>
        <div style={{ fontSize: '16px', fontWeight: 700 }}>
          {data.payment_status === 'connected' ? 'Connected' : 'Not connected'}
        </div>

        {/* Settle button -- shown when there are pending or failed invoices */}
        {data.invoice_history.some((iv) => iv.status === 'pending' || iv.status === 'failed') && (
          <div style={{ marginTop: '12px' }}>
            {settleState === 'idle' && (
              <button onClick={() => setSettleState('confirming')} style={buttonStyle}>
                Settle invoice
              </button>
            )}

            {settleState === 'confirming' && (
              <div>
                <p style={{
                  fontSize: '13px',
                  color: 'var(--on-surface)',
                  margin: '0 0 10px',
                  lineHeight: '1.5',
                }}>
                  This will send your latest invoice to Stripe for payment.
                  Stripe handles the charge safely -- retrying is always safe and
                  will never result in a double charge.
                </p>
                <div style={{ display: 'flex', gap: '10px' }}>
                  <button onClick={handleSettle} style={buttonStyle}>
                    Confirm
                  </button>
                  <button
                    onClick={() => setSettleState('idle')}
                    style={buttonSecondaryStyle}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {settleState === 'syncing' && (
              <div style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                fontSize: '13px',
                color: 'var(--on-surface-dim)',
              }}>
                <span style={{
                  display: 'inline-block',
                  width: '14px',
                  height: '14px',
                  border: '2px solid var(--on-surface-dim)',
                  borderTopColor: 'var(--primary)',
                  borderRadius: '50%',
                  animation: 'spin 0.8s linear infinite',
                }} />
                Sending to Stripe...
              </div>
            )}

            {settleState === 'synced' && (
              <div style={{
                fontSize: '13px',
                color: 'var(--correct)',
                marginTop: '4px',
                lineHeight: '1.5',
              }}>
                Invoice sent to Stripe. Payment will be processed automatically.
                This page will update once confirmed.
              </div>
            )}

            {settleState === 'network_error' && (
              <div style={{
                marginTop: '12px',
                padding: '12px 14px',
                background: 'rgba(255, 200, 87, 0.12)',
                border: '1px solid rgba(255, 200, 87, 0.35)',
                borderRadius: '10px',
              }}>
                <p style={{
                  fontSize: '13px',
                  color: 'var(--gold)',
                  fontWeight: 700,
                  margin: '0 0 6px',
                }}>
                  Couldn't reach MingleHub
                </p>
                <p style={{
                  fontSize: '13px',
                  color: 'var(--on-surface)',
                  margin: '0 0 10px',
                  lineHeight: '1.5',
                }}>
                  Check your internet connection and try again.
                  Nothing was charged -- retrying is always safe.
                </p>
                <button onClick={handleSettle} style={buttonStyle}>
                  Try again
                </button>
              </div>
            )}

            {settleState === 'server_error' && (
              <div style={{
                marginTop: '12px',
                padding: '12px 14px',
                background: 'rgba(255, 92, 108, 0.12)',
                border: '1px solid rgba(255, 92, 108, 0.35)',
                borderRadius: '10px',
              }}>
                <p style={{
                  fontSize: '13px',
                  color: 'var(--tertiary)',
                  fontWeight: 700,
                  margin: '0 0 6px',
                }}>
                  Something went wrong
                </p>
                <p style={{
                  fontSize: '13px',
                  color: 'var(--on-surface)',
                  margin: '0 0 4px',
                  lineHeight: '1.5',
                }}>
                  {settleError || 'Could not process this request.'}{' '}
                  Nothing was charged.
                </p>
                <button
                  onClick={() => { setSettleState('idle'); setSettleError(null) }}
                  style={buttonSecondaryStyle}
                >
                  Dismiss
                </button>
              </div>
            )}
          </div>
        )}
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
