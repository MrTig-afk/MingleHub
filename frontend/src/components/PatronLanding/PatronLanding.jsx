import { useEffect, useState } from 'react'
import JoinOrNewChooser from '../JoinOrNewChooser/JoinOrNewChooser'
import Lobby from '../Lobby/Lobby'
import RoundOrigin from '../RoundOrigin/RoundOrigin'
import SessionParticipant from '../Trivia/SessionParticipant'
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
  const [status, setStatus] = useState('loading') // loading | error | <table_state.phase> | joined | started
  const [venue, setVenue] = useState(null)
  const [tableState, setTableState] = useState(null)
  const [joinedInfo, setJoinedInfo] = useState(null)
  const [sessionAdultsOnly, setSessionAdultsOnly] = useState(false)
  const [sessionPlayerCount, setSessionPlayerCount] = useState(2)
  const [error, setError] = useState(null)

  // #root's global padding-bottom (main.css) reserves space for the
  // original card game's bottom thumb-zone nav — it has no business on
  // any patron route, and on the Finger Picker screen it actively breaks
  // things: it makes #root taller than the viewport, so the page becomes
  // scrollable, and the touch zone's "100dvh minus header" math no longer
  // matches what's actually visible. Reset it for the lifetime of this
  // mount rather than touching the shared CSS rule (used by the live
  // legacy game) at all.
  useEffect(() => {
    const root = document.getElementById('root')
    const prevPadding = root.style.paddingBottom
    root.style.paddingBottom = '0px'
    return () => { root.style.paddingBottom = prevPadding }
  }, [])

  useEffect(() => {
    let cancelled = false
    // The serverless backend cold-starts when idle, so the very first tap can be
    // slow or fail. Retry a few times before giving up, rather than leaving the
    // screen looking frozen (which previously needed a manual refresh).
    const attempt = async (triesLeft) => {
      try {
        const result = await fetchTap(initialTap)
        if (cancelled) return
        setVenue(result)
        setTableState(result.table_state ?? null)
        setStatus(result.table_state?.phase ?? 'success')
      } catch (e) {
        if (cancelled) return
        if (triesLeft > 0) {
          setTimeout(() => attempt(triesLeft - 1), 2500)
        } else {
          setError(e.message)
          setStatus('error')
        }
      }
    }
    attempt(3)
    return () => { cancelled = true }
  }, [])

  if (status === 'lobby') {
    return (
      <Lobby
        venueName={venue.venue_name}
        lobbyId={tableState.lobby_id}
        phoneId={initialTap.phoneId}
        tableId={venue.table_id}
        // gamespec: Adults Only Toggle — venue-wide restrict_adult_content
        // overrides the table's own content_ceiling. Server re-validates
        // this on start (lobby_service.adults_only_allowed); this only
        // controls whether the host even sees the toggle.
        adultsOnlyAllowed={!venue.restrict_adult_content && venue.content_ceiling === 'adults_allowed'}
        onGameStarted={(result) => {
          setJoinedInfo(result)
          setSessionAdultsOnly(result.adults_only ?? false)
          setSessionPlayerCount(result.player_count ?? 2)
          setStatus('started')
        }}
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

  // Re-tap resume: phone belongs to an active session it already started or
  // joined. Routes straight back into that session without showing join-or-new.
  if (status === 'resume') {
    const ts = tableState
    if (ts.is_origin) {
      return (
        <RoundOrigin
          venueName={venue.venue_name}
          sessionId={ts.session_id}
          phoneId={initialTap.phoneId}
          tableId={venue.table_id}
          adultsOnly={ts.adults_only}
          playerCount={ts.player_count}
        />
      )
    }
    // Non-origin participant resuming — back into the between-rounds / Trivia view.
    return (
      <SessionParticipant
        venueName={venue.venue_name}
        sessionId={ts.session_id}
        phoneId={initialTap.phoneId}
        tableId={venue.table_id}
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
        tableId={venue.table_id}
        adultsOnly={sessionAdultsOnly}
        playerCount={sessionPlayerCount}
      />
    )
  }

  // Non-origin phones — once a session is live they get the participant view
  // (between-rounds leaderboard, and Trivia gather/answer when the origin runs
  // a Trivia round). 'started' = was in the lobby; 'joined' = joined an
  // in-progress group via Join-or-New.
  if (status === 'started') {
    return (
      <SessionParticipant
        venueName={venue.venue_name}
        sessionId={joinedInfo.converted_session_id}
        phoneId={initialTap.phoneId}
        tableId={venue.table_id}
      />
    )
  }

  if (status === 'joined') {
    return (
      <SessionParticipant
        venueName={venue.venue_name}
        sessionId={joinedInfo.session_id}
        phoneId={initialTap.phoneId}
        tableId={venue.table_id}
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
      {status === 'loading' && (
        <>
          <span style={{ fontSize: '36px' }}>🍺</span>
          <h1 className="headline" style={{ fontFamily: 'var(--font-headline)', fontSize: '22px', margin: 0 }}>
            Setting up the game…
          </h1>
          <p style={{ fontSize: '13px', color: 'var(--on-surface-dim)', fontFamily: 'var(--font-mono)', margin: 0 }}>
            First load can take a few seconds
          </p>
        </>
      )}

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
