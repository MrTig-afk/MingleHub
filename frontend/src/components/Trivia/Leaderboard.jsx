// Per-player score leaderboard (gamespec: Trivia between-rounds screen + recap).
// Active players first (best score first), then anyone who left early.
const MEDALS = ['\u{1F947}', '\u{1F948}', '\u{1F949}']

export default function Leaderboard({ rows = [], title = 'Leaderboard' }) {
  const active = rows.filter((r) => !r.left_early)
  const left = rows.filter((r) => r.left_early)

  return (
    <div style={wrapStyle}>
      <h2 style={titleStyle}>{title}</h2>
      <ul style={listStyle}>
        {active.map((r, i) => (
          <li key={`a-${i}`} style={rowStyle}>
            <span style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
              <span style={{ width: '20px', display: 'inline-block' }}>
                {MEDALS[i] || `${i + 1}.`}
              </span>
              {r.name}
            </span>
            <strong>{r.score} pts</strong>
          </li>
        ))}
        {left.map((r, i) => (
          <li key={`l-${i}`} style={{ ...rowStyle, opacity: 0.6 }}>
            <span>Left early — {r.name}</span>
            <span>{r.score} pts</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

const wrapStyle = {
  background: 'var(--glass-bg)',
  border: '1px solid var(--glass-border)',
  borderRadius: '16px',
  padding: '20px',
  width: '100%',
  maxWidth: '360px',
}

const titleStyle = {
  fontFamily: 'var(--font-headline)',
  fontSize: '18px',
  margin: '0 0 14px',
  textAlign: 'center',
}

const listStyle = { listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: '8px' }

const rowStyle = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  fontFamily: 'var(--font-mono)',
  fontSize: '14px',
}
