import { useEffect, useState } from 'react'
import { fetchInsights } from '../../services/dashboardApi'
import { buttonStyle, buttonSecondaryStyle, cardStyle } from './dashboardStyles'
import { readCache, writeCache } from './usePolling'

const shimmerCard = (height = 80) => ({
  ...cardStyle,
  height,
  animation: 'dev-shimmer 1.5s infinite',
  background: 'var(--bg-container)',
})

const RANGE_LABELS = {
  tonight: 'Tonight',
  '7d': '7 Days',
  '30d': '30 Days',
}

function StatCard({ value, label }) {
  return (
    <div style={cardStyle}>
      <div style={{ fontFamily: 'var(--font-headline)', fontSize: '28px', color: 'var(--on-surface)' }}>
        {value ?? 0}
      </div>
      <div style={{ fontSize: '13px', color: 'var(--on-surface-dim)' }}>{label}</div>
    </div>
  )
}

const ROUND_TYPE_COLORS = {
  chooser: 'rgba(236, 178, 255, 0.35)',
  roulette: 'rgba(231, 0, 110, 0.25)',
  trivia: 'rgba(0, 238, 252, 0.25)',
}

function ProportionalBar({ count, maxCount, color = 'rgba(151, 71, 255, 0.2)' }) {
  const pct = maxCount > 0 ? Math.round((count / maxCount) * 100) : 0
  return (
    <div style={{ position: 'relative', height: '20px', borderRadius: '4px', background: 'var(--bg-container)', overflow: 'hidden' }}>
      <div style={{
        position: 'absolute',
        left: 0, top: 0, bottom: 0,
        width: `${pct}%`,
        background: color,
        borderRadius: '4px',
        transition: 'width 0.3s',
      }} />
      <div style={{
        position: 'absolute',
        right: '8px',
        top: 0,
        bottom: 0,
        display: 'flex',
        alignItems: 'center',
        fontSize: '12px',
        fontWeight: 700,
        color: 'var(--on-surface)',
      }}>
        {count}
      </div>
    </div>
  )
}

export default function DashboardInsights({ token }) {
  // data: null means loading has not completed yet for the current range.
  const ckey = (r) => `dash:insights:${r}`
  // data: null means loading not done for the current range; seeded from the SWR cache.
  const [data, setData] = useState(() => readCache(ckey('tonight')) ?? null)
  const [fetchError, setFetchError] = useState(null)
  const [range, setRange] = useState('tonight')
  // Bumped by Retry to re-trigger the fetch effect when the range is unchanged.
  const [reloadKey, setReloadKey] = useState(0)

  // On range change, seed instantly from cache for that range (shimmer only if uncached).
  const changeRange = (newRange) => {
    setData(readCache(ckey(newRange)) ?? null)
    setFetchError(null)
    setRange(newRange)
  }

  const retry = () => {
    setData(null)
    setFetchError(null)
    setReloadKey((k) => k + 1)
  }

  useEffect(() => {
    let cancelled = false
    const run = async () => {
      try {
        const d = await fetchInsights(token, range)
        if (cancelled) return
        setData(d)
        writeCache(ckey(range), d)
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
  }, [token, range, reloadKey])

  // data === null and no error = still loading
  if (data === null && fetchError === null) {
    return (
      <div>
        <div style={{ display: 'flex', gap: '8px', marginBottom: '16px' }}>
          {['tonight', '7d', '30d'].map((r) => (
            <button
              key={r}
              onClick={() => changeRange(r)}
              style={{
                ...(range === r ? buttonStyle : buttonSecondaryStyle),
                padding: '8px 16px',
                fontSize: '13px',
              }}
            >
              {RANGE_LABELS[r]}
            </button>
          ))}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
          {[1, 2, 3, 4].map((i) => <div key={i} style={shimmerCard(80)} />)}
        </div>
      </div>
    )
  }

  if (fetchError !== null) {
    return (
      <div style={{ ...cardStyle, marginTop: '8px' }}>
        <p style={{ color: 'var(--tertiary)', fontFamily: 'var(--font-mono)', fontSize: '13px', margin: '0 0 12px' }}>
          Could not load insights. {fetchError}
        </p>
        <button
          onClick={retry}
          style={buttonStyle}
        >
          Retry
        </button>
      </div>
    )
  }

  const totals = data?.totals || {}
  const roundMix = data?.round_mix || { chooser: 0, roulette: 0, trivia: 0 }
  const trivia = data?.trivia || { correct: 0, wrong: 0, accuracy: null }
  const rouletteDrinks = data?.roulette_and_drinks || { roulette_completed: 0, drink_rounds: 0 }
  const trend = data?.trend || []

  const maxRoundMix = Math.max(roundMix.chooser, roundMix.roulette, roundMix.trivia, 1)
  const roundTotal = roundMix.chooser + roundMix.roulette + roundMix.trivia
  const maxTrend = Math.max(...trend.map((t) => t.count), 1)

  const avgMinDisplay = totals.avg_session_minutes != null ? `${totals.avg_session_minutes} min` : '--'
  const avgPlayersDisplay = totals.avg_players != null ? String(totals.avg_players) : '--'

  return (
    <div>
      {/* Range selector */}
      <div style={{ display: 'flex', gap: '8px', marginBottom: '16px' }}>
        {['tonight', '7d', '30d'].map((r) => (
          <button
            key={r}
            onClick={() => changeRange(r)}
            style={{
              ...(range === r ? buttonStyle : buttonSecondaryStyle),
              padding: '8px 16px',
              fontSize: '13px',
            }}
          >
            {RANGE_LABELS[r]}
          </button>
        ))}
      </div>

      {/* Totals grid */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', marginBottom: '12px' }}>
        <StatCard value={totals.sessions ?? 0} label="sessions" />
        <StatCard value={totals.players ?? 0} label="players" />
        <StatCard value={totals.rounds ?? 0} label="rounds" />
        <StatCard value={avgMinDisplay} label="avg session" />
      </div>

      {/* Avg group size — single-width card */}
      <div style={{ marginBottom: '12px' }}>
        <StatCard value={avgPlayersDisplay} label="avg group size" />
      </div>

      {totals.sessions === 0 && (
        <div style={{ ...cardStyle, textAlign: 'center', marginBottom: '12px', color: 'var(--on-surface-dim)' }}>
          No games in this period.
        </div>
      )}

      {/* Round mix widget */}
      <div style={{ ...cardStyle, marginBottom: '12px' }}>
        <div style={{ fontWeight: 700, fontSize: '14px', marginBottom: '8px' }}>Round Mix</div>
        {roundMix.chooser === 0 && roundMix.roulette === 0 && roundMix.trivia === 0
          ? <div style={{ fontSize: '13px', color: 'var(--on-surface-dim)' }}>No rounds yet</div>
          : (
            <>
              {[
                { label: 'Chooser', key: 'chooser' },
                { label: 'Roulette', key: 'roulette' },
                { label: 'Trivia', key: 'trivia' },
              ].map(({ label, key }) => (
                <div key={key} style={{ marginBottom: '8px' }}>
                  <div style={{ fontSize: '13px', marginBottom: '4px' }}>
                    {label}{' '}
                    <span style={{ color: 'var(--on-surface-dim)' }}>
                      ({roundTotal > 0 ? Math.round((roundMix[key] / roundTotal) * 100) : 0}%)
                    </span>
                  </div>
                  <ProportionalBar count={roundMix[key]} maxCount={maxRoundMix} color={ROUND_TYPE_COLORS[key]} />
                </div>
              ))}
            </>
          )
        }
      </div>

      {/* Trivia accuracy widget */}
      <div style={{ ...cardStyle, marginBottom: '12px' }}>
        <div style={{ fontWeight: 700, fontSize: '14px', marginBottom: '8px' }}>Trivia</div>
        {trivia.accuracy == null
          ? (
            <>
              <div style={{ fontFamily: 'var(--font-headline)', fontSize: '28px' }}>--</div>
              <div style={{ fontSize: '13px', color: 'var(--on-surface-dim)' }}>No trivia played</div>
            </>
          )
          : (
            <>
              <div style={{ fontFamily: 'var(--font-headline)', fontSize: '28px' }}>
                {Math.round(trivia.accuracy * 100)}%
              </div>
              <div style={{ fontSize: '13px', color: 'var(--on-surface-dim)' }}>
                {trivia.correct} / {trivia.correct + trivia.wrong} correct
              </div>
            </>
          )
        }
      </div>

      {/* Roulette & Drinks widget */}
      <div style={{ ...cardStyle, marginBottom: '12px' }}>
        <div style={{ fontWeight: 700, fontSize: '14px', marginBottom: '8px' }}>Roulette &amp; Drinks</div>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px', padding: '4px 0' }}>
          <span>Roulette completed</span>
          <span style={{ fontWeight: 700 }}>{rouletteDrinks.roulette_completed}</span>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px', padding: '4px 0' }}>
          <span>Drink rounds</span>
          <span style={{ fontWeight: 700 }}>{rouletteDrinks.drink_rounds}</span>
        </div>
      </div>

      {/* Sessions trend widget */}
      <div style={{ ...cardStyle, marginBottom: '12px' }}>
        <div style={{ fontWeight: 700, fontSize: '14px', marginBottom: '8px' }}>Sessions per Night</div>
        {trend.length > 0 && (
          <div style={{ fontSize: '12px', color: 'var(--on-surface-dim)', textAlign: 'right', marginBottom: '4px' }}>
            Peak: {maxTrend} sessions
          </div>
        )}
        {trend.length === 0
          ? <div style={{ fontSize: '13px', color: 'var(--on-surface-dim)' }}>No data for this period.</div>
          : trend.map((item) => {
            const dateLabel = new Date(item.date + 'T00:00:00').toLocaleDateString([], { month: 'short', day: 'numeric' })
            return (
              <div key={item.date} style={{ marginBottom: '8px' }}>
                <div style={{ fontSize: '13px', marginBottom: '4px' }}>{dateLabel}</div>
                <ProportionalBar count={item.count} maxCount={maxTrend} />
              </div>
            )
          })
        }
      </div>
    </div>
  )
}
