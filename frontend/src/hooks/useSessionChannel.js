import { useEffect, useRef, useState } from 'react'
import { createClient } from '@supabase/supabase-js'
import { fetchChannelAuth } from '../services/patronApi'

/**
 * Subscribe to a Supabase Realtime broadcast channel.
 *
 * @param {string} tableId - The table UUID to scope the channel to.
 * @param {string} phoneId - This phone's ID for auth.
 * @param {function} onEvent - Called with (event, payload) on each broadcast.
 * @returns {{ connected: boolean, error: string|null }}
 */
export default function useSessionChannel(tableId, phoneId, onEvent) {
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState(null)
  // Keep a stable ref to the latest onEvent callback so that callback
  // identity changes don't trigger a re-subscription.
  const onEventRef = useRef(onEvent)
  useEffect(() => {
    onEventRef.current = onEvent
  }, [onEvent])

  useEffect(() => {
    if (!tableId || !phoneId) return

    let cancelled = false
    let supabase = null
    let channel = null

    async function subscribe() {
      try {
        const response = await fetchChannelAuth(tableId, phoneId)
        if (cancelled) return

        // When realtime is not configured (CI / local dev without Supabase),
        // the backend returns {realtime_enabled: false}. Stay disconnected
        // cleanly -- the poll fallback continues to run.
        if (!response.realtime_enabled) {
          return
        }

        supabase = createClient(response.supabase_url, response.supabase_anon_key)
        // The minted token carries a `channel` claim; setAuth makes it the
        // access token Realtime validates for private-channel authorization.
        supabase.realtime.setAuth(response.token)

        // Private channel: Realtime enforces an RLS policy on
        // realtime.messages (topic must equal the token's `channel` claim),
        // so a phone can only receive broadcasts for its own table/session.
        channel = supabase
          .channel(response.channel, { config: { private: true } })
          .on('broadcast', { event: '*' }, ({ event, payload }) => {
            onEventRef.current(event, payload)
          })
          .subscribe()

        if (!cancelled) {
          setConnected(true)
        }
      } catch (e) {
        if (!cancelled) {
          setError(e.message)
        }
      }
    }

    subscribe()

    return () => {
      cancelled = true
      setConnected(false)
      if (channel && supabase) {
        supabase.removeChannel(channel)
      }
    }
  }, [tableId, phoneId])

  return { connected, error }
}
