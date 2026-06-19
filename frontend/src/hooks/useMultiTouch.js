import { useState, useRef, useCallback, useEffect } from 'react'

export const STATES = {
  IDLE: 'IDLE',
  WAITING: 'WAITING',
  COUNTDOWN: 'COUNTDOWN',
  CHOSEN: 'CHOSEN',
  CARD_DRAW: 'CARD_DRAW',
}

const HOLD_MS = 300
const COUNTDOWN_MS = 3000

/**
 * Cryptographically unbiased array pick using rejection sampling. Used for the
 * very first pick of a session (before there's any winner history to spread
 * away from) and as the building block for weighted selection.
 *
 * Why rejection sampling:
 *   crypto.getRandomValues gives a uniform Uint32 (0–4294967295).
 *   Naively doing buf[0] % n introduces modulo bias whenever n doesn't
 *   divide 2^32 evenly (e.g. n=3: values 0,1 are ~1.4 in a billion more
 *   likely than 2). Rejection sampling discards values in the remainder
 *   region so the surviving values map to indices with equal probability.
 *   For n=2 there is no bias (2^32 % 2 === 0), but we use the same path
 *   everywhere for consistency.
 *
 * Winner selection itself is done by spreadPick (below): a position-weighted
 * draw that steers the pick away from recent winners' screen areas so it moves
 * around the table instead of clustering on one person.
 */
function cryptoPick(pool) {
  const n = pool.length
  // Largest multiple of n that fits in a Uint32, eliminating the biased tail
  const limit = Math.floor(0x100000000 / n) * n
  const buf = new Uint32Array(1)
  do {
    crypto.getRandomValues(buf)
  } while (buf[0] >= limit)
  return pool[buf[0] % n]
}

/** Weighted random choice using a single crypto-random draw in [0, total). */
function weightedPick(ids, weights) {
  const total = weights.reduce((a, b) => a + b, 0)
  const buf = new Uint32Array(1)
  crypto.getRandomValues(buf)
  let r = (buf[0] / 0x100000000) * total
  for (let i = 0; i < ids.length; i++) {
    r -= weights[i]
    if (r < 0) return ids[i]
  }
  return ids[ids.length - 1]
}

/**
 * "Spread across the table" winner pick. Weights each candidate finger by how
 * far it sits from the most recent winners' positions, so consecutive picks
 * move around the screen instead of clustering on one person/area.
 *
 *   - First round (no history): unbiased uniform pick via cryptoPick.
 *   - After that: weight = (normalised distance to nearest recent winner)^2,
 *     plus a small floor so a finger near a recent winner is unlikely but never
 *     impossible. It stays random — just steered away from recent spots.
 */
function spreadPick(ids, fingersMap, recentPositions) {
  if (ids.length <= 1) return ids[0]
  if (!recentPositions || recentPositions.length === 0) return cryptoPick(ids)

  const diag = Math.hypot(window.innerWidth, window.innerHeight) || 1
  const FLOOR = 0.15
  const weights = ids.map((id) => {
    const f = fingersMap.get(id)
    if (!f) return FLOOR
    let minDist = Infinity
    for (const p of recentPositions) {
      const d = Math.hypot(f.x - p.x, f.y - p.y)
      if (d < minDist) minDist = d
    }
    const norm = Math.min(1, minDist / diag)
    return norm * norm + FLOOR
  })
  return weightedPick(ids, weights)
}

// requiredFingers: null (default) preserves the original FirstMove
// behavior — any 2+ fingers, no upper bound, since that game has no
// concept of a fixed roster. Passing a number (MingleHub's session
// integration) makes it exact: the picker waits for precisely that many
// fingers and ignores any extra, since the result has to map onto one of
// a known, fixed list of named players.
export function useMultiTouch(onChosen, requiredFingers = null, recentWinnerPositions = []) {
  const [fingers, setFingers] = useState(new Map())
  const [phase, setPhase] = useState(STATES.IDLE)
  const [chosenId, setChosenId] = useState(null)
  const [countdown, setCountdown] = useState(3)

  const holdTimer = useRef(null)
  const countdownTimer = useRef(null)
  const tickTimer = useRef(null)
  const fingersRef = useRef(fingers)
  const phaseRef = useRef(phase)
  const chosenIdRef = useRef(null)
  const requiredFingersRef = useRef(requiredFingers)
  // Recent winners' screen positions (sliding window, owned by RoundOrigin so
  // it survives the picker remounting each round). spreadPick weights away from
  // these so the selection doesn't keep landing in the same area.
  const recentWinnerPositionsRef = useRef(recentWinnerPositions)
  // Mirrors latest state into refs so the touch-event callbacks below
  // (useCallback with stable deps, attached once via `attach`) always read
  // current values without forcing the listeners to be torn down and
  // reattached on every fingers/phase change — required here since touch
  // listeners reattaching mid-gesture would drop in-flight touches.
  // eslint-disable-next-line react-hooks/refs
  fingersRef.current = fingers
  // eslint-disable-next-line react-hooks/refs
  phaseRef.current = phase
  // eslint-disable-next-line react-hooks/refs
  requiredFingersRef.current = requiredFingers
  // eslint-disable-next-line react-hooks/refs
  recentWinnerPositionsRef.current = recentWinnerPositions

  const clearTimers = useCallback(() => {
    clearTimeout(holdTimer.current)
    holdTimer.current = null
    clearTimeout(countdownTimer.current)
    countdownTimer.current = null
    clearInterval(tickTimer.current)
    tickTimer.current = null
  }, [])

  const startCountdown = useCallback(() => {
    setPhase(STATES.COUNTDOWN)
    setCountdown(3)

    tickTimer.current = setInterval(() => {
      setCountdown(c => Math.max(0, c - 1))
    }, 1000)

    countdownTimer.current = setTimeout(() => {
      clearInterval(tickTimer.current)
      tickTimer.current = null
      const ids = [...fingersRef.current.keys()]
      if (ids.length < (requiredFingersRef.current ?? 2)) {
        setPhase(STATES.WAITING)
        return
      }

      // Position-weighted "spread across the table" pick — steers away from
      // recent winners' areas (see spreadPick). recentWinnerPositions is owned
      // by RoundOrigin so the memory persists even though this hook remounts
      // every round.
      const winner = spreadPick(ids, fingersRef.current, recentWinnerPositionsRef.current)
      chosenIdRef.current = winner
      setChosenId(winner)
      setFingers(prev => {
        const next = new Map(prev)
        for (const [id, f] of next) {
          next.set(id, { ...f, state: id === winner ? 'chosen' : 'eliminated' })
        }
        return next
      })
      setPhase(STATES.CHOSEN)
      onChosen?.(winner, ids.indexOf(winner))
    }, COUNTDOWN_MS)
  }, [onChosen])

  const onStart = useCallback((e) => {
    e.preventDefault()
    if (phaseRef.current === STATES.CHOSEN) {
      const chosen = fingersRef.current.get(chosenIdRef.current)
      if (chosen) {
        for (const t of e.changedTouches) {
          const dx = t.clientX - chosen.x
          const dy = t.clientY - chosen.y
          if (Math.sqrt(dx * dx + dy * dy) < 100) {
            setPhase(STATES.CARD_DRAW)
            return
          }
        }
      }
      return
    }
    if (phaseRef.current === STATES.CARD_DRAW) return
    setFingers(prev => {
      const next = new Map(prev)
      const required = requiredFingersRef.current
      for (const t of e.changedTouches) {
        // Caps registration at exactly the session's player count — an
        // extra finger beyond that (a curious friend, a forgotten thumb)
        // is ignored rather than joining the pool, since the picker's
        // result has to map onto one of the actual named players.
        if (next.size >= required && !next.has(t.identifier)) continue
        next.set(t.identifier, { x: t.clientX, y: t.clientY, state: 'waiting' })
      }
      return next
    })
  }, [])

  const onEnd = useCallback((e) => {
    if (phaseRef.current === STATES.CHOSEN || phaseRef.current === STATES.CARD_DRAW) return
    setFingers(prev => {
      const next = new Map(prev)
      for (const t of e.changedTouches) {
        next.delete(t.identifier)
      }
      return next
    })
  }, [])

  const onMove = useCallback((e) => {
    if (phaseRef.current === STATES.CHOSEN || phaseRef.current === STATES.CARD_DRAW) return
    setFingers(prev => {
      const next = new Map(prev)
      for (const t of e.changedTouches) {
        if (next.has(t.identifier)) {
          next.set(t.identifier, { ...next.get(t.identifier), x: t.clientX, y: t.clientY })
        }
      }
      return next
    })
  }, [])

  useEffect(() => {
    if (phase === STATES.CHOSEN || phase === STATES.CARD_DRAW) return

    const count = fingers.size

    // 0 fingers → always IDLE, cancel any pending timers
    if (count === 0) {
      if (phase === STATES.COUNTDOWN) {
        clearTimers()
        // Reacting to fingers dropping to 0 mid-countdown (an external
        // touch event) — resets the countdown display back to its start value.
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setCountdown(3)
      }
      if (phase !== STATES.IDLE) setPhase(STATES.IDLE)
      if (holdTimer.current) {
        clearTimeout(holdTimer.current)
        holdTimer.current = null
      }
      return
    }

    // Any finger present → leave IDLE and enter WAITING
    if (phase === STATES.IDLE) {
      setPhase(STATES.WAITING)
      return
    }

    // Countdown running but finger count dropped below the required
    // amount → abort, back to WAITING. (onStart already caps registration
    // at requiredFingers, so "dropped below" is the only direction this
    // can move once countdown starts — it can never exceed it.)
    if (phase === STATES.COUNTDOWN && count < requiredFingers) {
      clearTimers()
      setPhase(STATES.WAITING)
      setCountdown(3)
      return
    }

    if (phase === STATES.WAITING) {
      if (count >= requiredFingers && holdTimer.current === null) {
        // Exactly requiredFingers held: start the brief hold timer, then fire countdown
        holdTimer.current = setTimeout(() => {
          holdTimer.current = null
          if (fingersRef.current.size >= requiredFingers) startCountdown()
        }, HOLD_MS)
      } else if (count < requiredFingers && holdTimer.current !== null) {
        // Dropped a finger: cancel hold so countdown doesn't fire
        clearTimeout(holdTimer.current)
        holdTimer.current = null
      }
    }
  }, [fingers, phase, clearTimers, startCountdown, requiredFingers])

  const reset = useCallback(() => {
    clearTimers()
    setFingers(new Map())
    setPhase(STATES.IDLE)
    setChosenId(null)
    setCountdown(3)
  }, [clearTimers])

  const attach = useCallback((el) => {
    if (!el) return
    el.addEventListener('touchstart', onStart, { passive: false })
    el.addEventListener('touchend', onEnd)
    // iOS Safari fires touchcancel instead of touchend when a system
    // gesture (edge-swipe back/forward, Control Center, an accidental
    // pinch interpretation) interrupts a touch — without this, that
    // finger never leaves the map, leaving a stale dot and a phantom
    // participant in the count. Same cleanup as a normal lift.
    el.addEventListener('touchcancel', onEnd)
    el.addEventListener('touchmove', onMove, { passive: true })
    return () => {
      el.removeEventListener('touchstart', onStart)
      el.removeEventListener('touchend', onEnd)
      el.removeEventListener('touchcancel', onEnd)
      el.removeEventListener('touchmove', onMove)
    }
  }, [onStart, onEnd, onMove])

  return {
    fingers,
    phase,
    chosenId,
    countdown,
    attach,
    reset,
  }
}
