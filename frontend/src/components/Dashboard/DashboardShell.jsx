// Nav bar + layout wrapper for all authenticated dashboard pages.
// Receives navigate from DashboardRoot so links use pushState (no full reload).

const allLinks = [
  { path: '/dashboard', label: 'Home' },
  { path: '/dashboard/tables', label: 'Tables' },
  { path: '/dashboard/insights', label: 'Insights' },
  { path: '/dashboard/pair-tags', label: 'Pair Tags', ownerOnly: true },
  { path: '/dashboard/settings', label: 'Settings', ownerOnly: true },
  { path: '/dashboard/billing', label: 'Billing', ownerOnly: true },
]

export default function DashboardShell({
  user,
  venue,
  onLogout,
  navigate,
  currentPath,
  children,
}) {
  const isStaff = user.role === 'venue_staff'
  const visibleLinks = isStaff ? allLinks.filter((l) => !l.ownerOnly) : allLinks

  const roleBadgeStyle = {
    fontSize: '11px',
    padding: '2px 8px',
    borderRadius: '10px',
    fontWeight: 700,
    background: user.role === 'venue_owner' ? 'var(--primary)' : 'var(--secondary)',
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
          {venue.name}
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
          <span style={roleBadgeStyle}>
            {user.role === 'venue_owner' ? 'Owner' : 'Staff'}
          </span>
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
        {visibleLinks.map((link) => {
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
