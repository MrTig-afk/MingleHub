import { useEffect, useState } from 'react'
import { fetchThemes, fetchActiveTheme, setTheme } from '../../services/dashboardApi'
import { cardStyle, selectStyle } from './dashboardStyles'

// Owner control for tonight's theme. Named themes set the round/card mix; the
// "test" themes force a single game type (all_trivia/all_roulette/all_chooser)
// so you can isolate one game on repeat and watch the billing block counters.
export default function ThemePicker({ token }) {
  const [themes, setThemes] = useState([])
  const [active, setActive] = useState(null)
  const [saving, setSaving] = useState(false)
  const [saveMsg, setSaveMsg] = useState(null) // 'Saved' | error string | null
  const [error, setError] = useState(null)     // initial-load error only

  useEffect(() => {
    let cancelled = false
    Promise.all([fetchThemes(token), fetchActiveTheme(token)])
      .then(([t, a]) => {
        if (cancelled) return
        setThemes(t.themes || [])
        setActive(a.theme_key)
      })
      .catch((e) => { if (!cancelled) setError(e.message || 'Could not load themes') })
    return () => { cancelled = true }
  }, [token])

  const onChange = async (key) => {
    const prev = active
    setSaving(true)
    setSaveMsg(null)
    setActive(key)
    try {
      await setTheme(token, key)
      setSaveMsg('Saved')
      setTimeout(() => setSaveMsg(null), 3000)
    } catch (e) {
      setActive(prev)
      setSaveMsg(e.message || 'Could not set theme')
    } finally {
      setSaving(false)
    }
  }

  const named = themes.filter((t) => !t.is_test)
  const test = themes.filter((t) => t.is_test)

  return (
    <div style={{ ...cardStyle, marginBottom: '12px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '8px' }}>
        <div style={{ fontWeight: 700, fontSize: '14px' }}>Tonight&rsquo;s Theme</div>
        {saveMsg !== null && (
          <span style={{
            fontSize: '12px',
            color: saveMsg === 'Saved' ? 'var(--secondary)' : 'var(--tertiary)',
          }}>
            {saveMsg === 'Saved' ? 'Saved ✓' : saveMsg}
          </span>
        )}
      </div>
      <p style={{ fontSize: '13px', color: 'var(--on-surface-dim)', margin: '0 0 10px' }}>
        Sets the mix of round types &amp; cards for tonight. <strong>Test</strong> themes force a single
        game type &mdash; handy for isolating a game and watching the billing counters.
      </p>
      <select
        value={active || 'random'}
        onChange={(e) => onChange(e.target.value)}
        disabled={saving || themes.length === 0}
        style={selectStyle}
      >
        {named.length > 0 && (
          <optgroup label="Themes">
            {named.map((t) => (
              <option key={t.theme_key} value={t.theme_key}>{t.display_name}</option>
            ))}
          </optgroup>
        )}
        {test.length > 0 && (
          <optgroup label="Test — single game type">
            {test.map((t) => (
              <option key={t.theme_key} value={t.theme_key}>{t.display_name}</option>
            ))}
          </optgroup>
        )}
      </select>
      {error && (
        <p style={{ color: 'var(--tertiary)', fontSize: '12px', marginTop: '6px', fontFamily: 'var(--font-mono)' }}>
          {error}
        </p>
      )}
    </div>
  )
}
