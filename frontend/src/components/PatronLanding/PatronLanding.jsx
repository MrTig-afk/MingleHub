import { useEffect, useState } from 'react'
import JoinOrNewChooser from '../JoinOrNewChooser/JoinOrNewChooser'
import Lobby from '../Lobby/Lobby'
import RoundOrigin from '../RoundOrigin/RoundOrigin'
import { fetchTap } from '../../services/patronApi'

const PHONE_ID_KEY = 'minglehub_phone_id'

// Real flow: generated once per browser and persisted, so re-taps from the
// same physical phone are recognized as the same phone (idempotent lobby
// joins, "you are the host" across reloads).
//
// Dev/testing override: a `?phone_id=` query param takes priority over the
// stored value. A real tag's NDEF URL never carries this (the signed
// payload only has tag_uid/counter/sig) — it exists purely so PairTags.jsx's
// "Open Game" simulator can assign a distinct phone_id per simulated tap,
// letting multiple browser tabs simulate multiple phones at one table
// without sharing localStorage.
function resolvePhoneId() {
  const fromUrl = new URLSearchParams(window.location.search).get('phone_id')
  if (fromUrl) return fromUrl

  let stored = localStorage.getItem(PHONE_ID_KEY)
  if (!stored) {
    stored = crypto.randomUUID()
    localStorage.setItem(PHONE_ID_KEY, stored)
  }
  return stored
}

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
    phoneId: resolvePhoneId(),
  }
}

const initialTap = parseTapFromLocation()

export default function PatronLanding() {
  const missingSignature = !initialTap.tagUid || !initialTap.sig
  const [status, setStatus] = useState(missingSignature ? 'error' : 'loading') // loading | error | <table_state.phase> | joined | started
  const [venue, setVenue] = useState(null)
  const [tableState, setTableState] = useState(null)
  const [joinedInfo, setJoinedInfo] = useState(null)
  const [error, setError] = useState(
    missingSignature ? 'This link is missing its tap signature — tap the table tag again.' : null
  )

  useEffect(() => {
    if (missingSignature) return
    fetchTap(initialTap)
      .then((result) => {
        setVenue(result)
        setTableState(result.table_state ?? null)
        setStatus(result.table_state?.phase ?? 'success')
      })
      .catch((e) => {
        setError(e.message)
        setStatus('error')
      })
  }, [missingSignature])

  if (status === 'lobby') {
    return (
      <Lobby
        venueName={venue.venue_name}
        lobbyId={tableState.lobby_id}
        phoneId={initialTap.phoneId}
        // gamespec: Adults Only Toggle — venue-wide restrict_adult_content
        // overrides the table's own content_ceiling. Server re-validates
        // this on start (lobby_service.adults_only_allowed); this only
        // controls whether the host even sees the toggle.
        adultsOnlyAllowed={!venue.restrict_adult_content && venue.content_ceiling === 'adults_allowed'}
        onGameStarted={(result) => { setJoinedInfo(result); setStatus('started') }}
      />
    )
  }

  if (status === 'join_or_new' || status === 'table_full') {
    return (
      <JoinOrNewChooser
        tableNumber={initialTap.tableNumber}
        tableId={tableState.table_id}
        phoneId={initialTap.phoneId}
        groups={tableState.groups}
        onJoined={(result) => { setJoinedInfo(result); setStatus('joined') }}
        onNewGroup={(lobby) => { setTableState({ phase: 'lobby', ...lobby }); setStatus('lobby') }}
      />
    )
  }

  // gamespec: "Players place fingers on session-origin phone" — only the
  // phone that started the game (host) runs the finger picker. Every other
  // phone at the table — whether it was in the lobby before start ('started')
  // or joined a session already in progress ('joined') — just watches.
  // `joinedInfo` is shaped differently per path: the lobby-poll object
  // (host_phone_id/converted_session_id) for 'started', or the join
  // response (name/session_id) for 'joined' — never both, so this is safe.
  if (status === 'started' && joinedInfo.host_phone_id === initialTap.phoneId) {
    return (
      <RoundOrigin
        venueName={venue.venue_name}
        sessionId={joinedInfo.converted_session_id}
        phoneId={initialTap.phoneId}
      />
    )
  }

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

      {/* Non-origin phones — the finger picker only runs on the table device
          (RoundOrigin, above). What happens after a Hot Seat player is
          picked (Chooser/Trivia/Roulette) is later backlog work. */}
      {status === 'started' && (
        <>
          <h1 className="headline" style={{ fontFamily: 'var(--font-headline)' }}>
            Game started 🎉
          </h1>
          <p style={{ fontSize: '13px', color: 'var(--on-surface-dim)', fontFamily: 'var(--font-mono)' }}>
            Watch the table phone — place your finger when asked 👀
          </p>
        </>
      )}

      {status === 'joined' && (
        <>
          <h1 className="headline" style={{ fontFamily: 'var(--font-headline)' }}>
            You're in! 🎉
          </h1>
          <p style={{ fontSize: '13px', color: 'var(--on-surface-dim)', fontFamily: 'var(--font-mono)' }}>
            Playing as {joinedInfo.name} — watch the table phone 👀
          </p>
        </>
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
