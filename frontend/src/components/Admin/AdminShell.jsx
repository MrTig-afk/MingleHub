// Nav bar + layout wrapper for all authenticated admin pages.
// Receives navigate from AdminRoot so links use pushState (no full reload).
// No venue prop — admin has no venue_id.

const adminLinks = [
  { path: '/admin', label: 'Home' },
  { path: '/admin/venues', label: 'Venues' },
  { path: '/admin/support', label: 'Support' },
  { path: '/admin/team', label: 'Team' },
  { path: '/admin/leads', label: 'Leads' },
]

export default function AdminShell({
  onLogout,
  navigate,
  currentPath,
  children,
}) {
  const adminBadgeStyle = {
    fontSize: '11px',
    padding: '2px 8px',
    borderRadius: '10px',
    fontWeight: 700,
    background: 'var(--tertiary)',
    color: 'var(--bg-floor)',
  }

  return (
    <div style={{ minHeight: '100dvh', background: 'var(--bg-floor)', color: 'var(--on-surface)', fontFamily: 'var(--font-body)' }}>
      {/* Top nav bar */}
      <div style={{
        position: 'sticky',
        top: 0,
        zIndex: 50,
        background: 'var(--bg-surface)',
        borderBottom: '1px solid var(--outline)',
        padding: '12px 16px',
        display: 'flex',
        flexDirection: 'row',
        justifyContent: 'space-between',
        alignItems: 'center',
      }}>
        <span style={{
          fontFamily: 'var(--font-headline)',
          fontSize: '16px',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
          maxWidth: '60%',
        }}>
          MingleHub Admin
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
          <span style={adminBadgeStyle}>Admin</span>
          <button
            onClick={onLogout}
            style={{
              background: 'none',
              border: 'none',
              color: 'var(--on-surface-dim)',
              fontSize: '13px',
              cursor: 'pointer',
              marginLeft: '12px',
              padding: 0,
            }}
          >
            Logout
          </button>
        </div>
      </div>

      {/* Horizontal nav links */}
      <div style={{
        background: 'var(--bg-floor)',
        display: 'flex',
        flexDirection: 'row',
        overflowX: 'auto',
        whiteSpace: 'nowrap',
        gap: '16px',
        padding: '8px 16px',
      }}>
        {adminLinks.map((link) => {
          const isActive = currentPath === link.path
          return (
            <a
              key={link.path}
              href={link.path}
              onClick={(e) => {
                e.preventDefault()
                navigate(link.path)
              }}
              style={{
                fontSize: '14px',
                color: isActive ? 'var(--primary)' : 'var(--on-surface-dim)',
                textDecoration: 'none',
                borderBottom: isActive ? '2px solid var(--primary)' : '2px solid transparent',
                paddingBottom: '4px',
              }}
            >
              {link.label}
            </a>
          )
        })}
      </div>

      {/* Content area */}
      <div style={{ maxWidth: '960px', margin: '0 auto', padding: '16px' }}>
        {children}
      </div>
    </div>
  )
}
