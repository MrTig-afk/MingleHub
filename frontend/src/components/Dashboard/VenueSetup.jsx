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
  padding: '11px 13px',
  borderRadius: '10px',
  border: '1.5px solid var(--line)',
  background: 'var(--bg-container)',
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

function Dropdown({ items, onPick }) {
  return (
    <div style={{
      position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 10,
      background: 'var(--bg-container)', border: '1.5px solid var(--line)',
      borderRadius: '10px', marginTop: '4px', maxHeight: '220px', overflowY: 'auto',
    }}>
      {items.map((s, i) => (
        <div
          key={i}
          onMouseDown={() => onPick(s)}
          style={{
            padding: '10px 12px', cursor: 'pointer', fontSize: '14px',
            borderBottom: i < items.length - 1 ? '1px solid var(--line)' : 'none',
          }}
        >
          {s.label}
        </div>
      ))}
    </div>
  )
}

// First-run wizard for a newly-provisioned venue_owner with no venue yet. Both the
// name and address fields use a keyless (Photon/OSM, AU-biased) autocomplete: picking a
// named place from the NAME field also auto-fills the address; the ADDRESS field is the
// fallback for venues not in OSM. Then setup-venue creates the venue + tables.
export default function VenueSetup({ token, onDone, prefill }) {
  const [name, setName] = useState(prefill?.venue_name || '')
  const [venueType, setVenueType] = useState('bar')
  const [tableCount, setTableCount] = useState(4)
  const [allowAdult, setAllowAdult] = useState(false)
  const [address, setAddress] = useState(prefill?.address || '')
  const [created, setCreated] = useState(null) // setup-venue response -> success screen
  const [coords, setCoords] = useState({
    latitude: prefill?.lat ?? null,
    longitude: prefill?.lng ?? null,
    place_id: prefill?.place_id ?? null,
  })
  const [nameSug, setNameSug] = useState([])
  const [addrSug, setAddrSug] = useState([])
  const [showName, setShowName] = useState(false)
  const [showAddr, setShowAddr] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)
  const nameTimer = useRef(null)
  const addrTimer = useRef(null)

  const onNameType = (val) => {
    setName(val)
    setShowAddr(false)
    clearTimeout(nameTimer.current)
    if (val.trim().length < 3) { setNameSug([]); setShowName(false); return }
    nameTimer.current = setTimeout(async () => {
      const res = await geoAutocomplete(token, val.trim())
      // Only named places (POIs) are useful as venue-name matches.
      setNameSug((res.suggestions || []).filter((s) => s.name))
      setShowName(true)
    }, 300)
  }

  const pickName = (s) => {
    setName(s.name)
    if (s.address) {
      setAddress(s.address)
      setCoords({ latitude: s.latitude, longitude: s.longitude, place_id: s.place_id })
    }
    setNameSug([]); setShowName(false)
  }

  const onAddrType = (val) => {
    setAddress(val)
    setCoords({ latitude: null, longitude: null, place_id: null }) // typing invalidates a prior pick
    setShowName(false)
    clearTimeout(addrTimer.current)
    if (val.trim().length < 3) { setAddrSug([]); setShowAddr(false); return }
    addrTimer.current = setTimeout(async () => {
      const res = await geoAutocomplete(token, val.trim())
      setAddrSug(res.suggestions || [])
      setShowAddr(true)
    }, 300)
  }

  const pickAddr = (s) => {
    setAddress(s.address || s.label)
    setCoords({ latitude: s.latitude, longitude: s.longitude, place_id: s.place_id })
    setAddrSug([]); setShowAddr(false)
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
      const result = await setupVenue(token, {
        name: name.trim(),
        venue_type: venueType,
        table_count: count,
        allow_adult: allowAdult,
        address: address.trim() || null,
        latitude: coords.latitude,
        longitude: coords.longitude,
        place_id: coords.place_id,
      })
      setCreated(result) // -> success screen
    } catch (e) {
      setError(e.message || 'Could not set up venue.')
      setSubmitting(false)
    }
  }

  if (created) {
    return (
      <div style={fullScreen}>
        <div style={{ ...cardStyle, maxWidth: '440px', width: '100%', textAlign: 'center' }}>
          <div style={{ width: '52px', height: '52px', borderRadius: '50%', margin: '0 auto', display: 'grid', placeItems: 'center', background: 'rgba(57,224,139,0.12)', border: '1.5px solid var(--correct)', color: 'var(--correct)', fontSize: '24px', fontWeight: 700 }}>✓</div>
          <h1 style={{ fontFamily: 'var(--font-headline)', fontSize: '22px', margin: '10px 0 4px' }}>
            {name.trim()} is set up
          </h1>
          <p style={{ color: 'var(--on-surface-dim)', fontSize: '14px', margin: '0 0 22px' }}>
            {created.table_count} table{created.table_count > 1 ? 's' : ''} created. We&rsquo;ll send
            your NFC tags to place on each table so patrons can tap and play.
          </p>
          <button onClick={onDone} style={{ ...buttonStyle, width: '100%' }}>
            Go to dashboard
          </button>
        </div>
      </div>
    )
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
        <div style={{ position: 'relative' }}>
          <input
            style={inputStyle}
            value={name}
            onChange={(e) => onNameType(e.target.value)}
            onFocus={() => nameSug.length && setShowName(true)}
            placeholder="Start typing your venue name…"
            autoComplete="off"
            maxLength={120}
          />
          {showName && nameSug.length > 0 && <Dropdown items={nameSug} onPick={pickName} />}
        </div>

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
            value={address}
            onChange={(e) => onAddrType(e.target.value)}
            onFocus={() => addrSug.length && setShowAddr(true)}
            placeholder="e.g. 55 Elizabeth St, Melbourne"
            autoComplete="off"
          />
          {showAddr && addrSug.length > 0 && <Dropdown items={addrSug} onPick={pickAddr} />}
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
