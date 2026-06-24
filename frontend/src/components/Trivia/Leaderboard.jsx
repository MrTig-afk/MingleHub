// Per-player score leaderboard (gamespec: Trivia between-rounds screen + recap).
// Active players first (best score first), then anyone who left early.
// After Dark: card rows, rank numbers, and podium glow discs (gold/silver/bronze)
// on the top three. Pass `meName` to magenta-highlight the current player's row.

// gold / silver / bronze treatments for the top three discs.
const PODIUM = [
  { c: 'var(--gold)',   bg: 'rgba(255,200,87,0.10)', bd: 'rgba(255,200,87,0.55)', gl: 'rgba(255,200,87,0.60)' },
  { c: 'var(--silver)', bg: 'rgba(233,238,248,0.16)', bd: 'rgba(233,238,248,0.65)', gl: 'rgba(233,238,248,0.55)' },
  { c: 'var(--bronze)', bg: 'rgba(199,123,74,0.12)', bd: 'rgba(199,123,74,0.55)', gl: 'rgba(199,123,74,0.60)' },
]

export default function Leaderboard({ rows = [], title = 'Leaderboard', meName = null }) {
  const active = rows.filter((r) => !r.left_early)
  const left = rows.filter((r) => r.left_early)
  const initial = (n) => (n || '?').trim().charAt(0).toUpperCase()

  return (
    <div style={wrapStyle}>
      {title && <div style={titleStyle}>{title}</div>}
      <div style={listStyle}>
        {active.map((r, i) => {
          const isMe = Boolean(meName) && r.name === meName
          return (
            <div key={`a-${i}`} style={rowStyle(isMe)}>
              <span style={rankStyle(i, isMe)}>{i + 1}</span>
              <span style={avStyle(i, isMe)}>{initial(r.name)}</span>
              <span style={nameStyle}>{isMe ? 'You' : r.name}</span>
              <span style={ptsStyle}>{r.score}</span>
            </div>
          )
        })}
        {left.map((r, i) => (
          <div key={`l-${i}`} style={{ ...rowStyle(false), opacity: 0.45 }}>
            <span style={rankStyle(99, false)}>—</span>
            <span style={avStyle(99, false)}>{initial(r.name)}</span>
            <span style={nameStyle}>
              {r.name} <span style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--on-surface-dim)' }}>left</span>
            </span>
            <span style={ptsStyle}>{r.score}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

const wrapStyle = { width: '100%', maxWidth: '360px' }

const titleStyle = {
  fontFamily: 'var(--font-mono)',
  fontSize: '11px',
  letterSpacing: '0.1em',
  textTransform: 'uppercase',
  color: 'var(--on-surface-dim)',
  margin: '0 0 12px',
}

const listStyle = { display: 'flex', flexDirection: 'column', gap: '8px' }

function rowStyle(isMe) {
  const base = {
    display: 'flex', alignItems: 'center', gap: '12px',
    padding: '11px 13px', borderRadius: '11px',
    background: 'var(--bg-surface)', border: '1.5px solid var(--line)',
  }
  if (isMe) return { ...base, border: '1.5px solid rgba(255,45,120,0.5)', background: 'rgba(255,45,120,0.06)' }
  return base
}

function rankStyle(i, isMe) {
  const base = { fontFamily: 'var(--font-display)', fontSize: '15px', width: '18px', textAlign: 'center', flex: 'none' }
  if (isMe) return { ...base, color: 'var(--primary)' }
  return { ...base, color: PODIUM[i] ? PODIUM[i].c : 'var(--on-surface-dim)' }
}

function avStyle(i, isMe) {
  const base = {
    width: '32px', height: '32px', borderRadius: '50%', flex: 'none',
    display: 'grid', placeItems: 'center',
    fontFamily: 'var(--font-headline)', fontWeight: 700, fontSize: '13px',
  }
  if (isMe) return { ...base, color: 'var(--primary)', background: 'rgba(255,45,120,0.12)', border: '1.5px solid rgba(255,45,120,0.55)' }
  const p = PODIUM[i]
  if (p) return { ...base, color: p.c, background: p.bg, border: `1.5px solid ${p.bd}`, boxShadow: `0 0 16px ${p.gl}` }
  return { ...base, color: 'var(--on-surface)', background: 'var(--bg-container)', border: '1.5px solid var(--line)' }
}

const nameStyle = { fontFamily: 'var(--font-headline)', fontWeight: 600, fontSize: '15px', color: 'var(--on-surface)' }
const ptsStyle = { marginLeft: 'auto', fontFamily: 'var(--font-mono)', fontWeight: 500, fontSize: '14px', color: 'var(--on-surface)' }
