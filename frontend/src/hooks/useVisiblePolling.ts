import { useEffect, useRef } from 'react'

const DEFAULT_INTERVAL_MS = 60_000

/**
 * Runs `reload` on mount and on an interval while the document is visible.
 * Also refreshes on tab focus / visibility regain.
 */
export function useVisiblePolling(
  reload: () => void | Promise<void>,
  options?: { intervalMs?: number; enabled?: boolean }
) {
  const intervalMs = options?.intervalMs ?? DEFAULT_INTERVAL_MS
  const enabled = options?.enabled !== false
  const reloadRef = useRef(reload)
  reloadRef.current = reload

  useEffect(() => {
    if (!enabled) return

    let cancelled = false
    let intervalId: ReturnType<typeof setInterval> | undefined

    const run = async () => {
      if (cancelled || document.visibilityState !== 'visible') return
      try {
        await reloadRef.current()
      } catch {
        /* caller/state handles */
      }
    }

    const onFocus = () => {
      void run()
    }

    const onVisibility = () => {
      void run()
      if (intervalId !== undefined) clearInterval(intervalId)
      intervalId = setInterval(run, intervalMs)
    }

    void run()
    intervalId = setInterval(run, intervalMs)
    document.addEventListener('visibilitychange', onVisibility)
    window.addEventListener('focus', onFocus)

    return () => {
      cancelled = true
      if (intervalId !== undefined) clearInterval(intervalId)
      document.removeEventListener('visibilitychange', onVisibility)
      window.removeEventListener('focus', onFocus)
    }
  }, [enabled, intervalMs])
}
