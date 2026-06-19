export default function Splash() {
  return (
    <div style={{
      minHeight: '100dvh',
      background: 'var(--bg-floor)',
      color: 'var(--on-surface)',
      fontFamily: 'var(--font-body)',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      gap: '16px',
      padding: '24px',
      textAlign: 'center',
    }}>
      <h1 style={{ fontFamily: 'var(--font-headline)', fontWeight: 800, fontSize: 'clamp(24px, 7vw, 32px)' }}>
        MingleHub
      </h1>
      <p style={{ fontSize: '18px', color: 'var(--on-surface-dim)' }}>
        Tap the NFC tag at your table to play
      </p>
    </div>
  );
}
