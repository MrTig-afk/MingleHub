import { useEffect, useState } from 'react'
import { fetchTap } from '../../services/patronApi'

// Parses the public game route — minglehub.com/{venue-slug}/{table-number}
// — plus the tap's query params (tag_uid/counter/sig), exactly as a real
// NTAG 424 DNA tag's NDEF URL would deliver them.
function parseTapFromLocation() {
  const [, venueSlug, tableNumberRaw] = window.location.pathname.split('/')
  const params = new URLSearchParams(window.location.search)
  return {
    venueSlug,
    tableNumber: Number(tableNumberRaw),
    tagUid: params.get('tag_uid'),
    counter: Number(params.get('counter')),
    sig: params.get('sig'),
  }
}

const initialTap = parseTapFromLocation()

export default function PatronLanding() {
  const missingSignature = !initialTap.tagUid || !initialTap.sig
  const [status, setStatus] = useState(missingSignature ? 'error' : 'loading') // loading | success | error
  const [venue, setVenue] = useState(null)
  const [error, setError] = useState(
    missingSignature ? 'This link is missing its tap signature — tap the table tag again.' : null
  )

  useEffect(() => {
    if (missingSignature) return
    fetchTap(initialTap)
      .then((result) => {
        setVenue(result)
        setStatus('success')
      })
      .catch((e) => {
        setError(e.message)
        setStatus('error')
      })
  }, [missingSignature])

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
      {status === 'loading' && <p>Verifying tap…</p>}

      {status === 'success' && (
        <h1 className="headline" style={{ fontFamily: 'var(--font-headline)' }}>
          Playing at {venue.venue_name} 🍺
        </h1>
      )}

      {status === 'error' && (
        <>
          <h1 className="headline" style={{ fontFamily: 'var(--font-headline)' }}>
            Tap didn't go through
          </h1>
          <p style={{ fontSize: '13px', color: 'var(--on-surface-dim)', fontFamily: 'var(--font-mono)' }}>
            {error}
          </p>
        </>
      )}
    </div>
  )
}
