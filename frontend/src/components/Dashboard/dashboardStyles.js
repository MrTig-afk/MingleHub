// Shared inline style constants for all Dashboard components.
// Follows the same CSS-var idiom as PairTags.jsx — no separate CSS modules.

export const buttonStyle = {
  padding: '12px 16px',
  borderRadius: '10px',
  background: 'var(--primary)',
  color: 'var(--bg-floor)',
  fontFamily: 'var(--font-headline)',
  fontWeight: 700,
  border: 'none',
  cursor: 'pointer',
  boxShadow: '0 0 22px rgba(255, 45, 120, 0.32)',
}

export const buttonSecondaryStyle = {
  ...buttonStyle,
  background: 'var(--bg-surface)',
  color: 'var(--on-surface)',
  border: '1.5px solid var(--line)',
  boxShadow: 'none',
}

export const selectStyle = {
  padding: '12px 14px',
  borderRadius: '10px',
  background: 'var(--bg-container)',
  color: 'var(--on-surface)',
  border: '1.5px solid var(--line)',
  width: '100%',
}

export const cardStyle = {
  background: 'var(--bg-surface)',
  border: '1.5px solid var(--line)',
  borderRadius: '16px',
  padding: '18px',
}

export const labelStyle = {
  fontSize: '13px',
  color: 'var(--on-surface-dim)',
}

// Session status chip colours — shared by DashboardHome and DashboardTableDetail.
export const STATUS_CHIP = {
  active: { background: 'rgba(57, 224, 139, 0.12)', color: 'var(--correct)',  border: '1px solid rgba(57, 224, 139, 0.35)' },
  idle:   { background: 'rgba(255, 200, 87, 0.12)', color: 'var(--gold)',     border: '1px solid rgba(255, 200, 87, 0.35)' },
  paused: { background: 'rgba(255, 92, 108, 0.12)', color: 'var(--tertiary)', border: '1px solid rgba(255, 92, 108, 0.35)' },
  lobby:  { background: 'rgba(255, 45, 120, 0.12)', color: 'var(--primary)',  border: '1px solid rgba(255, 45, 120, 0.35)' },
}

export const chipStyle = (status) => ({
  ...(STATUS_CHIP[status] || STATUS_CHIP.active),
  fontFamily: 'var(--font-mono)',
  fontSize: '10px',
  letterSpacing: '0.06em',
  textTransform: 'uppercase',
  padding: '3px 9px',
  borderRadius: '6px',
  fontWeight: 500,
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
