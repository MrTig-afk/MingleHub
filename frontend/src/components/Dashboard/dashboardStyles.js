// Shared inline style constants for all Dashboard components.
// Follows the same CSS-var idiom as PairTags.jsx — no separate CSS modules.

export const buttonStyle = {
  padding: '12px',
  borderRadius: '8px',
  background: 'var(--primary)',
  color: 'var(--bg-floor)',
  fontWeight: 700,
  border: 'none',
  cursor: 'pointer',
}

export const buttonSecondaryStyle = {
  ...buttonStyle,
  background: 'var(--bg-surface)',
  color: 'var(--on-surface)',
  border: '1px solid var(--outline)',
}

export const selectStyle = {
  padding: '12px',
  borderRadius: '8px',
  background: 'var(--bg-surface)',
  color: 'var(--on-surface)',
  border: '1px solid var(--outline)',
  width: '100%',
}

export const cardStyle = {
  background: 'var(--glass-bg)',
  border: '1px solid var(--glass-border)',
  borderRadius: '12px',
  padding: '16px',
}

export const labelStyle = {
  fontSize: '13px',
  color: 'var(--on-surface-dim)',
}

// Session status chip colours — shared by DashboardHome and DashboardTableDetail.
export const STATUS_CHIP = {
  active: { background: 'rgba(0, 238, 252, 0.15)', color: 'var(--secondary)' },
  idle:   { background: 'rgba(255, 215, 0, 0.15)',  color: '#FFD700' },
  paused: { background: 'rgba(231, 0, 110, 0.15)',  color: 'var(--tertiary)' },
  lobby:  { background: 'rgba(236, 178, 255, 0.15)', color: 'var(--primary)' },
}

export const chipStyle = (status) => ({
  ...(STATUS_CHIP[status] || STATUS_CHIP.active),
  fontSize: '11px',
  padding: '2px 8px',
  borderRadius: '10px',
  fontWeight: 700,
})

export function formatDuration(seconds) {
  if (!seconds || seconds < 60) return '<1m'
  const m = Math.floor(seconds / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  const rm = m % 60
  return rm > 0 ? `${h}h ${rm}m` : `${h}h`
}

export function formatMoney(value) {
  const num = typeof value === 'string' ? parseFloat(value) : value
  if (num == null || isNaN(num)) return 'A$0.00'
  return 'A$' + num.toLocaleString('en-AU', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

export function formatRelativeTime(date) {
  if (!date) return ''
  const seconds = Math.floor((Date.now() - date.getTime()) / 1000)
  if (seconds < 5) return 'just now'
  if (seconds < 60) return seconds + 's ago'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return minutes + 'm ago'
  const hours = Math.floor(minutes / 60)
  return hours + 'h ago'
}
