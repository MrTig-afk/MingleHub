import { useState, useRef } from 'react'
import { setupVenue, geoAutocomplete } from '../../services/dashboardApi'
import { cardStyle, buttonStyle } from './dashboardStyles'

const VENUE_TYPES = ['cafe', 'pub', 'bar', 'brewery', 'other']

const fullScreen = {
  minHeight: '100dvh',
  background: 'var(--bg-floor)',
  color: 'var(--on-surface)',
  fontFamily: 'var(--font-body)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  padding: '24px',
}

const inputStyle = {
  width: '100%',
  padding: '10px 12px',
  borderRadius: '8px',
  border: '1px solid rgba(255,255,255,0.14)',
  background: 'var(--bg-floor)',
  color: 'var(--on-surface)',
  fontFamily: 'var(--font-body)',
  fontSize: '15px',
  boxSizing: 'border-box',
}

const labelStyle = {
  display: 'block',
  fontSize: '13px',
  color: 'var(--on-surface-dim)',
  marginBottom: '6px',
  marginTop: '16px',
}

// First-run wizard shown to a newly-provisioned venue_owner who has no venue yet.
// Captures the 5 fields + a keyless (Photon/OSM) address autocomplete, then creates
// the venue + tables via setup-venue and calls onDone() to re-enter the dashboard.
export default function VenueSetup({ token, onDone }) {
  const [name, setName] = useState('')
  const [venueType, setVenueType] = useState('bar')
  const [tableCount, setTableCount] = useState(4)
  const [allowAdult, setAllowAdult] = useState(false)
  const [query, setQuery] = useState('')
  const [picked, setPicked] = useState(null) // { label, latitude, longitude, place_id }
  const [suggestions, setSuggestions] = useState([])
  const [showSug, setShowSug] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)
  const debounceRef = useRef(null)

  const onAddressType = (val) => {
    setQuery(val)
    setPicked(null) // typing invalidates a prior selection's coords
    clearTimeout(debounceRef.current)
    if (val.trim().length < 3) { setSuggestions([]); setShowSug(false); return }
    debounceRef.current = setTimeout(async () => {
      const res = await geoAutocomplete(token, val.trim())
      setSuggestions(res.suggestions || [])
      setShowSug(true)
    }, 300)
  }

  const pick = (s) => {
    setPicked(s)
    setQuery(s.label)
    setSuggestions([])
    setShowSug(false)
  }

  const submit = async () => {
    setError(null)
    if (!name.trim()) { setError('Venue name is required.'); return }
    const count = Number(tableCount)
    if (!Number.isInteger(count) || count < 1 || count > 50) {
      setError('Number of tables must be between 1 and 50.'); return
    }
    setSubmitting(true)
    try {
      await setupVenue(token, {
        name: name.trim(),
        venue_type: venueType,
        table_count: count,
        allow_adult: allowAdult,
        address: picked?.label || query.trim() || null,
        latitude: picked?.latitude ?? null,
        longitude: picked?.longitude ?? null,
        place_id: picked?.place_id ?? null,
      })
      onDone()
    } catch (e) {
      setError(e.message || 'Could not set up venue.')
      setSubmitting(false)
    }
  }

  return (
    <div style={fullScreen}>
      <div style={{ ...cardStyle, maxWidth: '440px', width: '100%' }}>
        <h1 style={{ fontFamily: 'var(--font-headline)', fontSize: '24px', margin: '0 0 4px' }}>
          Set up your venue
        </h1>
        <p style={{ color: 'var(--on-surface-dim)', fontSize: '14px', margin: '0 0 4px' }}>
          A few details and you&rsquo;re ready to pair tags and go live.
        </p>

        <label style={labelStyle}>Venue name</label>
        <input
          style={inputStyle}
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="The Lion's Den"
          maxLength={120}
        />

        <label style={labelStyle}>Venue type</label>
        <select style={inputStyle} value={venueType} onChange={(e) => setVenueType(e.target.value)}>
          {VENUE_TYPES.map((t) => (
            <option key={t} value={t}>{t[0].toUpperCase() + t.slice(1)}</option>
          ))}
        </select>

        <label style={labelStyle}>Address</label>
        <div style={{ position: 'relative' }}>
          <input
            style={inputStyle}
            value={query}
            onChange={(e) => onAddressType(e.target.value)}
            onFocus={() => suggestions.length && setShowSug(true)}
            placeholder="Start typing your address…"
            autoComplete="off"
          />
          {showSug && suggestions.length > 0 && (
            <div style={{
              position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 10,
              background: 'var(--bg-container)', border: '1px solid rgba(255,255,255,0.14)',
              borderRadius: '8px', marginTop: '4px', maxHeight: '220px', overflowY: 'auto',
            }}>
              {suggestions.map((s, i) => (
                <div
                  key={i}
                  onMouseDown={() => pick(s)}
                  style={{
                    padding: '10px 12px', cursor: 'pointer', fontSize: '14px',
                    borderBottom: i < suggestions.length - 1 ? '1px solid var(--bg-floor)' : 'none',
                  }}
                >
                  {s.label}
                </div>
              ))}
            </div>
          )}
        </div>

        <label style={labelStyle}>Number of tables</label>
        <input
          style={inputStyle}
          type="number"
          min={1}
          max={50}
          value={tableCount}
          onChange={(e) => setTableCount(e.target.value)}
        />

        <label style={{ ...labelStyle, display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }}>
          <input type="checkbox" checked={allowAdult} onChange={(e) => setAllowAdult(e.target.checked)} />
          Allow 18+ content at this venue
        </label>

        {error && (
          <p style={{ color: 'var(--tertiary)', fontSize: '13px', marginTop: '14px', fontFamily: 'var(--font-mono)' }}>
            {error}
          </p>
        )}

        <button
          onClick={submit}
          disabled={submitting}
          style={{ ...buttonStyle, width: '100%', marginTop: '22px', opacity: submitting ? 0.6 : 1 }}
        >
          {submitting ? 'Creating…' : 'Create my venue'}
        </button>
      </div>
    </div>
  )
}
