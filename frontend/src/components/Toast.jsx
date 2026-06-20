// Tiny transient banner pinned to the top of the screen. The parent owns the
// message + auto-dismiss; this is just the presentation.
export default function Toast({ message }) {
  if (!message) return null
  return <div style={toastStyle}>{message}</div>
}

const toastStyle = {
  position: 'fixed',
  top: 'calc(env(safe-area-inset-top, 0px) + 14px)',
  left: '50%',
  transform: 'translateX(-50%)',
  background: 'var(--glass-bg)',
  border: '1px solid var(--glass-border)',
  borderRadius: '10px',
  padding: '10px 16px',
  fontFamily: 'var(--font-mono)',
  fontSize: '13px',
  color: 'var(--on-surface)',
  zIndex: 100,
  maxWidth: '90vw',
  textAlign: 'center',
  boxShadow: '0 6px 24px rgba(0,0,0,0.35)',
}
