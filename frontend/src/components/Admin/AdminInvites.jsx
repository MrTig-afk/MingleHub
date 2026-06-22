import { useState, useEffect, useRef } from 'react'
import { QRCodeSVG } from 'qrcode.react'
import { createInvite, fetchInvites, revokeInvite, geoAutocomplete } from '../../services/adminApi'
import { cardStyle, buttonStyle } from '../Dashboard/dashboardStyles'

const inputStyle = {
  padding: '10px 12px',
  borderRadius: '8px',
  background: 'var(--bg-surface)',
  color: 'var(--on-surface)',
  border: '1px solid var(--outline)',
  width: '100%',
  boxSizing: 'border-box',
}

const labelStyle = {
  display: 'block',
  fontSize: '13px',
  color: 'var(--on-surface-dim)',
  marginBottom: '6px',
  marginTop: '14px',
}

function Dropdown({ items, onPick }) {
  return (
    <div style={{
      position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 10,
      background: 'var(--bg-container)', border: '1px solid rgba(255,255,255,0.14)',
      borderRadius: '8px', marginTop: '4px', maxHeight: '220px', overflowY: 'auto',
    }}>
      {items.map((s, i) => (
        <div
          key={i}
          onMouseDown={() => onPick(s)}
          style={{
            padding: '10px 12px', cursor: 'pointer', fontSize: '14px',
            borderBottom: i < items.length - 1 ? '1px solid var(--bg-floor)' : 'none',
          }}
        >
          {s.label}
        </div>
      ))}
    </div>
  )
}

const EMPTY_FORM = {
  invited_email: '',
  venue_name: '',
  address: '',
  latitude: null,
  longitude: null,
  place_id: null,
}

const STATUS_COLORS = {
  active: 'var(--primary)',
  used: 'var(--secondary)',
  revoked: 'var(--on-surface-dim)',
  expired: 'var(--tertiary)',
}

export default function AdminInvites({ token }) {
  const [invites, setInvites] = useState([])
  const [form, setForm] = useState(EMPTY_FORM)
  const [created, setCreated] = useState(null) // the just-created invite
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)
  const [listLoading, setListLoading] = useState(true)
  const [listError, setListError] = useState(null)

  // Autocomplete state
  const [nameSug, setNameSug] = useState([])
  const [addrSug, setAddrSug] = useState([])
  const [showName, setShowName] = useState(false)
  const [showAddr, setShowAddr] = useState(false)
  const nameTimer = useRef(null)
  const addrTimer = useRef(null)

  const [reloadKey, setReloadKey] = useState(0)

  const reloadInvites = () => setReloadKey((k) => k + 1)

  useEffect(() => {
    let cancelled = false
    const run = async () => {
      setListLoading(true)
      setListError(null)
      try {
        const result = await fetchInvites(token)
        if (cancelled) return
        setInvites(result.invites || [])
      } catch (e) {
        if (cancelled) return
        setListError(e.message || 'Could not load invites')
      } finally {
        if (!cancelled) setListLoading(false)
      }
    }
    run()
    return () => { cancelled = true }
  }, [token, reloadKey])

  const onNameType = (val) => {
    setForm((f) => ({ ...f, venue_name: val }))
    setShowAddr(false)
    clearTimeout(nameTimer.current)
    if (val.trim().length < 3) { setNameSug([]); setShowName(false); return }
    nameTimer.current = setTimeout(async () => {
      const res = await geoAutocomplete(token, val.trim())
      setNameSug((res.suggestions || []).filter((s) => s.name))
      setShowName(true)
    }, 300)
  }

  const pickName = (s) => {
    setForm((f) => ({
      ...f,
      venue_name: s.name,
      address: s.address || f.address,
      latitude: s.address ? s.latitude : f.latitude,
      longitude: s.address ? s.longitude : f.longitude,
      place_id: s.address ? s.place_id : f.place_id,
    }))
    setNameSug([]); setShowName(false)
  }

  const onAddrType = (val) => {
    setForm((f) => ({ ...f, address: val, latitude: null, longitude: null, place_id: null }))
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
    setForm((f) => ({
      ...f,
      address: s.address || s.label,
      latitude: s.latitude,
      longitude: s.longitude,
      place_id: s.place_id,
    }))
    setAddrSug([]); setShowAddr(false)
  }

  const handleCreate = async () => {
    setError(null)
    if (!form.invited_email.trim()) { setError('Invited email is required.'); return }
    if (!form.venue_name.trim()) { setError('Venue name is required.'); return }
    setLoading(true)
    try {
      const body = {
        invited_email: form.invited_email.trim(),
        venue_name: form.venue_name.trim(),
      }
      if (form.address) body.address = form.address.trim() || null
      if (form.latitude !== null) body.latitude = form.latitude
      if (form.longitude !== null) body.longitude = form.longitude
      if (form.place_id) body.place_id = form.place_id
      const result = await createInvite(token, body)
      setCreated(result)
      setForm(EMPTY_FORM)
      reloadInvites()
    } catch (e) {
      setError(e.message || 'Could not create invite.')
    } finally {
      setLoading(false)
    }
  }

  const handleRevoke = async (inviteId) => {
    try {
      await revokeInvite(token, inviteId)
      reloadInvites()
    } catch (e) {
      setListError(e.message || 'Could not revoke invite.')
    }
  }

  const inviteUrl = created
    ? `${window.location.origin}/dashboard?invite=${created.code}`
    : ''

  if (created) {
    return (
      <div>
        <h2 style={{ fontFamily: 'var(--font-headline)', fontSize: '18px', marginTop: 0, marginBottom: '16px' }}>
          Invite Generated
        </h2>
        <div style={{ ...cardStyle, maxWidth: '480px', textAlign: 'center' }}>
          <p style={{ marginBottom: '16px', fontWeight: 700 }}>{created.venue_name}</p>
          <p style={{ fontSize: '13px', color: 'var(--on-surface-dim)', marginBottom: '16px' }}>
            {created.invited_email}
          </p>
          <div style={{ display: 'flex', justifyContent: 'center', marginBottom: '20px' }}>
            <QRCodeSVG value={inviteUrl} size={200} level="M" />
          </div>
          <p style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--on-surface-dim)', wordBreak: 'break-all', marginBottom: '16px' }}>
            {inviteUrl}
          </p>
          <button
            onClick={() => navigator.clipboard.writeText(inviteUrl)}
            style={{ ...buttonStyle, marginBottom: '10px', width: '100%' }}
          >
            Copy link
          </button>
          <button
            onClick={() => setCreated(null)}
            style={{ ...buttonStyle, background: 'transparent', color: 'var(--on-surface-dim)', width: '100%' }}
          >
            Create another
          </button>
        </div>
      </div>
    )
  }

  return (
    <div>
      <h2 style={{ fontFamily: 'var(--font-headline)', fontSize: '18px', marginTop: 0, marginBottom: '16px' }}>
        Venue Invites
      </h2>

      {/* Create invite form */}
      <div style={{ ...cardStyle, marginBottom: '16px' }}>
        <h3 style={{ fontFamily: 'var(--font-headline)', fontSize: '15px', margin: '0 0 12px' }}>
          Invite a Venue
        </h3>

        <label style={labelStyle}>Invited email</label>
        <input
          type="email"
          value={form.invited_email}
          onChange={(e) => setForm((f) => ({ ...f, invited_email: e.target.value }))}
          placeholder="owner@venue.com"
          style={inputStyle}
          maxLength={320}
        />

        <label style={labelStyle}>Venue name</label>
        <div style={{ position: 'relative' }}>
          <input
            style={inputStyle}
            value={form.venue_name}
            onChange={(e) => onNameType(e.target.value)}
            onFocus={() => nameSug.length && setShowName(true)}
            placeholder="Start typing venue name…"
            autoComplete="off"
            maxLength={120}
          />
          {showName && nameSug.length > 0 && <Dropdown items={nameSug} onPick={pickName} />}
        </div>

        <label style={labelStyle}>Address (optional)</label>
        <div style={{ position: 'relative' }}>
          <input
            style={inputStyle}
            value={form.address}
            onChange={(e) => onAddrType(e.target.value)}
            onFocus={() => addrSug.length && setShowAddr(true)}
            placeholder="e.g. 55 Elizabeth St, Melbourne"
            autoComplete="off"
          />
          {showAddr && addrSug.length > 0 && <Dropdown items={addrSug} onPick={pickAddr} />}
        </div>

        {error && (
          <p style={{ color: 'var(--tertiary)', fontFamily: 'var(--font-mono)', fontSize: '13px', marginTop: '10px' }}>
            {error}
          </p>
        )}

        <button
          onClick={handleCreate}
          disabled={loading}
          style={{ ...buttonStyle, marginTop: '16px', opacity: loading ? 0.6 : 1 }}
        >
          {loading ? 'Generating…' : 'Generate Invite'}
        </button>
      </div>

      {/* Outstanding invites list */}
      <h3 style={{ fontFamily: 'var(--font-headline)', fontSize: '15px', margin: '0 0 12px' }}>
        Outstanding Invites
      </h3>

      {listLoading && (
        <p style={{ color: 'var(--on-surface-dim)', fontSize: '13px' }}>Loading…</p>
      )}

      {listError && (
        <p style={{ color: 'var(--tertiary)', fontFamily: 'var(--font-mono)', fontSize: '13px' }}>
          {listError}
        </p>
      )}

      {!listLoading && !listError && invites.length === 0 && (
        <p style={{ color: 'var(--on-surface-dim)', fontSize: '13px' }}>No invites yet.</p>
      )}

      {!listLoading && invites.map((inv) => (
        <div key={inv.id} style={{ ...cardStyle, marginBottom: '12px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <div>
              <span style={{ fontWeight: 700 }}>{inv.venue_name}</span>
              <span style={{
                marginLeft: '8px', fontSize: '11px', padding: '2px 6px', borderRadius: '8px',
                background: STATUS_COLORS[inv.status] || 'var(--on-surface-dim)',
                color: 'var(--bg-floor)', fontWeight: 700,
              }}>
                {inv.status}
              </span>
            </div>
            {inv.status === 'active' && (
              <button
                onClick={() => handleRevoke(inv.id)}
                style={{ ...buttonStyle, fontSize: '12px', padding: '4px 10px', background: 'var(--tertiary)' }}
              >
                Revoke
              </button>
            )}
          </div>
          <p style={{ fontSize: '13px', color: 'var(--on-surface-dim)', margin: '4px 0 0' }}>
            {inv.invited_email}
          </p>
          {inv.expires_at && (
            <p style={{ fontSize: '12px', color: 'var(--on-surface-dim)', margin: '2px 0 0' }}>
              Expires: {new Date(inv.expires_at).toLocaleString()}
            </p>
          )}
        </div>
      ))}
    </div>
  )
}
