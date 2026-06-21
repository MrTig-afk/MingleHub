import { useCallback, useEffect, useRef, useState } from 'react'

// Shared fetch-on-mount + interval poll + visibility-pause hook.
// All setState calls happen after await (react-hooks/set-state-in-effect compliant).
// The interval effect depends only on intervalMs (constant), so it never tears
// down due to status changes — fixing the churn in the old per-component polls.
export default function usePolling(fetchFn, { intervalMs = 7000, tokenKey = 'mh_dashboard_token' } = {}) {
  const [data, setData] = useState(null)
  const [status, setStatus] = useState('loading')
  const [error, setError] = useState(null)
  const [lastUpdatedAt, setLastUpdatedAt] = useState(null)
  const [reloadKey, setReloadKey] = useState(0)

  // Always call the latest fetchFn without putting it in effect deps.
  const fetchFnRef = useRef(fetchFn)
  useEffect(() => { fetchFnRef.current = fetchFn })

  // Pause interval when the tab is backgrounded.
  const pausedRef = useRef(false)

  function handleLogout() {
    localStorage.removeItem(tokenKey)
    window.location.reload()
  }

  // Effect 1 — initial fetch. Runs on mount and whenever reload() bumps reloadKey.
  // No synchronous setState in effect body: all setState is inside run() after await.
  useEffect(() => {
    let cancelled = false
    const run = async () => {
      try {
        const d = await fetchFnRef.current()
        if (cancelled) return
        setData(d)
        setStatus('ready')
        setLastUpdatedAt(new Date())
        setError(null)
      } catch (e) {
        if (cancelled) return
        const msg = e.message || ''
        if (msg.includes('401') || msg.includes('token') || msg.includes('expired')) {
          handleLogout()
          return
        }
        setStatus('error')
        setError(msg)
      }
    }
    run()
    return () => { cancelled = true }
  }, [reloadKey]) // eslint-disable-line react-hooks/exhaustive-deps

  // Effect 2 — interval poll. Depends only on intervalMs (constant in practice),
  // so the setInterval is never recreated due to status changes.
  useEffect(() => {
    const id = setInterval(() => {
      if (pausedRef.current) return
      fetchFnRef.current()
        .then((d) => {
          setData(d)
          setStatus('ready')
          setLastUpdatedAt(new Date())
          setError(null)
        })
        .catch((e) => {
          const msg = e.message || ''
          if (msg.includes('401') || msg.includes('token') || msg.includes('expired')) {
            handleLogout()
            return
          }
          // Keep last good data visible; signal reconnecting state.
          setStatus('reconnecting')
        })
    }, intervalMs)
    return () => clearInterval(id)
  }, [intervalMs]) // eslint-disable-line react-hooks/exhaustive-deps

  // Effect 3 — visibility pause/resume. Pauses the interval while hidden;
  // on becoming visible, immediately refetches.
  useEffect(() => {
    const handler = () => {
      if (document.visibilityState === 'hidden') {
        pausedRef.current = true
      } else {
        pausedRef.current = false
        fetchFnRef.current()
          .then((d) => {
            setData(d)
            setStatus('ready')
            setLastUpdatedAt(new Date())
            setError(null)
          })
          .catch((e) => {
            const msg = e.message || ''
            if (msg.includes('401') || msg.includes('token') || msg.includes('expired')) {
              handleLogout()
            }
          })
      }
    }
    document.addEventListener('visibilitychange', handler)
    return () => document.removeEventListener('visibilitychange', handler)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const reload = useCallback(() => {
    setStatus('loading')
    setError(null)
    setReloadKey((k) => k + 1)
  }, [])

  return { data, status, error, lastUpdatedAt, reload }
}
