import { useCallback, useEffect, useRef, useState } from 'react'

// Module-level stale-while-revalidate cache, shared across mounts. Keyed by the
// caller's cacheKey. Re-opening a tab seeds state from here so it renders the last
// value INSTANTLY (no shimmer) while a background fetch revalidates. Lives for the
// SPA session; cleared on logout/reload. Opt-in: no cacheKey -> no caching (old
// behavior, e.g. for per-table-detail views you don't want to cross-show).
const swrCache = new Map()

export function clearDashboardCache() {
  swrCache.clear()
}

// For components with their own fetch effect (e.g. range-dependent Insights) to
// share the same SWR cache: seed from readCache(key), persist with writeCache.
export function readCache(key) {
  return swrCache.has(key) ? swrCache.get(key) : undefined
}
export function writeCache(key, value) {
  swrCache.set(key, value)
}

// Shared fetch-on-mount + interval poll + visibility-pause hook, with optional SWR.
// All setState calls happen after await (react-hooks/set-state-in-effect compliant).
// The interval effect depends only on intervalMs (constant), so it never tears
// down due to status changes — fixing the churn in the old per-component polls.
// intervalMs <= 0 disables the background poll (for non-live tabs: settings, billing).
export default function usePolling(
  fetchFn,
  { intervalMs = 7000, tokenKey = 'mh_dashboard_token', cacheKey = null } = {},
) {
  const cached = cacheKey ? swrCache.get(cacheKey) : undefined
  const [data, setData] = useState(cached ?? null)
  const [status, setStatus] = useState(cached !== undefined ? 'ready' : 'loading')
  const [error, setError] = useState(null)
  const [lastUpdatedAt, setLastUpdatedAt] = useState(null)
  const [reloadKey, setReloadKey] = useState(0)

  // Write-through: every fresh value updates the shared cache.
  const store = useCallback((d) => {
    if (cacheKey) swrCache.set(cacheKey, d)
  }, [cacheKey])

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
        store(d)
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
    if (intervalMs <= 0) return undefined // non-live tab: cache + on-demand only
    const id = setInterval(() => {
      if (pausedRef.current) return
      fetchFnRef.current()
        .then((d) => {
          setData(d)
          store(d)
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
            store(d)
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
