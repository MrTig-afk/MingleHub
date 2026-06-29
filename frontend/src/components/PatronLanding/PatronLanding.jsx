import { useEffect, useState } from 'react'
import JoinOrNewChooser from '../JoinOrNewChooser/JoinOrNewChooser'
import Lobby from '../Lobby/Lobby'
import Recap from '../Recap/Recap'
import RoundOrigin from '../RoundOrigin/RoundOrigin'
import SwitchConfirm from '../SwitchConfirm/SwitchConfirm'
import SessionParticipant from '../Trivia/SessionParticipant'
import { fetchTap } from '../../services/patronApi'

const PHONE_ID_KEY = 'minglehub_phone_id'

// Real flow: generated once per browser and persisted, so re-taps from the
// same physical phone are recognized as the same phone (idempotent lobby
// joins, "you are the host" across reloads).
//
// Dev/testing override: a `?phone_id=` query param takes priority over the
// stored value. A real tag's NDEF URL never carries this — it exists purely so
// a tester can open `…/<slug>/<table>?phone_id=<x>` in multiple browser tabs to
// simulate several phones at one table without sharing localStorage.
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
    // "New game" button on the recap screen navigates here with ?newgame=1 so
    // the tap skips the recap-lock and starts a fresh lobby.
    newGame: params.get('newgame') === '1',
  }
}

const initialTap = parseTapFromLocation()

// Neon table tag — the After Dark signature, fixed top-right across every game
// phase so a patron always knows which table they're on. Cyan = the live/signal color.
function TableTag({ n }) {
  return (
    <div style={{
      position: 'fixed',
      top: 'calc(env(safe-area-inset-top, 0px) + 12px)',
      right: 'calc(env(safe-area-inset-right, 0px) + 14px)',
      zIndex: 60,
      fontFamily: 'var(--font-mono)',
      fontSize: '12px',
      fontWeight: 700,
      letterSpacing: '0.08em',
      color: 'var(--secondary)',
      background: 'rgba(45, 226, 230, 0.10)',
      border: '1px solid rgba(45, 226, 230, 0.45)',
      borderRadius: '7px',
      padding: '4px 9px',
      boxShadow: '0 0 16px rgba(45, 226, 230, 0.30)',
      pointerEvents: 'none',
    }}>
      T{n}
    </div>
  )
}

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

  // Neon table tag overlay — rendered alongside every game-phase view below.
  const tn = initialTap.tableNumber
  const tag = Number.isFinite(tn) && tn > 0 ? <TableTag n={tn} /> : null

  // Venue inactive: the venue has been cancelled or suspended — no new games.
  if (status === 'venue_inactive') {
    return (
      <div style={{ padding: '32px 20px', textAlign: 'center', color: 'var(--on-surface-dim)' }}>
        <p>This venue is not currently active. Games are temporarily unavailable.</p>
      </div>
    )
  }

  // Single active seat: this phone tapped a different table while still in a live
  // game elsewhere — let it choose to switch (leave the old game) or keep playing.
  if (status === 'switch_confirm') {
    return (
      <SwitchConfirm
        thisTableNumber={initialTap.tableNumber}
        other={tableState.other}
        phoneId={initialTap.phoneId}
      />
    )
  }

  if (status === 'lobby') {
    return (
      <>
      {tag}
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
      </>
    )
  }

  if (status === 'join_or_new' || status === 'table_full') {
    return (
      <>
      {tag}
      <JoinOrNewChooser
        tableNumber={initialTap.tableNumber}
        tableId={tableState.table_id}
        phoneId={initialTap.phoneId}
        groups={tableState.groups}
        onJoined={(result) => { setJoinedInfo(result); setStatus('joined') }}
        onNewGroup={(lobby) => { setTableState({ phase: 'lobby', ...lobby }); setStatus('lobby') }}
      />
      </>
    )
  }

  // Re-tap resume: phone belongs to an active session it already started or
  // joined. Routes straight back into that session without showing join-or-new.
  if (status === 'resume') {
    const ts = tableState
    if (ts.is_origin) {
      return (
        <>
        {tag}
        <RoundOrigin
          venueName={venue.venue_name}
          sessionId={ts.session_id}
          phoneId={initialTap.phoneId}
          tableId={venue.table_id}
          adultsOnly={ts.adults_only}
          playerCount={ts.player_count}
          initialRoundNumber={
            ts.current_round_number != null
              ? ts.current_round_number + 1
              : undefined
          }
        />
        </>
      )
    }
    // Non-origin participant resuming — back into the between-rounds / Trivia view.
    return (
      <>
      {tag}
      <SessionParticipant
        venueName={venue.venue_name}
        sessionId={ts.session_id}
        phoneId={initialTap.phoneId}
        tableId={venue.table_id}
      />
      </>
    )
  }

  // Re-tap on a recently-ended session (within retap_interval_minutes) or an
  // idle-expired session: show the Recap screen instead of lobby.
  if (status === 'recap') {
    return <>{tag}<Recap sessionId={tableState.session_id} venueName={venue.venue_name} tableId={venue.table_id} phoneId={initialTap.phoneId} /></>
  }

  // gamespec: "Players place fingers on session-origin phone" — only the
  // phone that started the game (host) runs the finger picker. Every other
  // phone at the table — whether it was in the lobby before start ('started')
  // or joined a session already in progress ('joined') — just watches.
  // `joinedInfo` is shaped differently per path: the lobby-poll object
  // (is_host/converted_session_id) for 'started', or the join
  // response (name/session_id) for 'joined' — never both, so this is safe.
  if (status === 'started' && joinedInfo.is_host) {
    return (
      <>
      {tag}
      <RoundOrigin
        venueName={venue.venue_name}
        sessionId={joinedInfo.converted_session_id}
        phoneId={initialTap.phoneId}
        tableId={venue.table_id}
        adultsOnly={sessionAdultsOnly}
        playerCount={sessionPlayerCount}
      />
      </>
    )
  }

  // Non-origin phones — once a session is live they get the participant view
  // (between-rounds leaderboard, and Trivia gather/answer when the origin runs
  // a Trivia round). 'started' = was in the lobby; 'joined' = joined an
  // in-progress group via Join-or-New.
  if (status === 'started') {
    return (
      <>
      {tag}
      <SessionParticipant
        venueName={venue.venue_name}
        sessionId={joinedInfo.converted_session_id}
        phoneId={initialTap.phoneId}
        tableId={venue.table_id}
      />
      </>
    )
  }

  if (status === 'joined') {
    return (
      <>
      {tag}
      <SessionParticipant
        venueName={venue.venue_name}
        sessionId={joinedInfo.session_id}
        phoneId={initialTap.phoneId}
        tableId={venue.table_id}
      />
      </>
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
          <div style={{ width: '12px', height: '12px', borderRadius: '50%', background: 'var(--primary)', boxShadow: '0 0 16px var(--primary)', animation: 'pulse-dot 1.4s infinite' }} />
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
          Playing at {venue.venue_name}
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
