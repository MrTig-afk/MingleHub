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
