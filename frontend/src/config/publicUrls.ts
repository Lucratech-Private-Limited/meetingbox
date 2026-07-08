/**
 * Build-time URL config for split deployment (e.g. SPA on S3, API on AWS).
 *
 * - VITE_API_URL: REST API origin only (e.g. https://api.example.com). Never the static SPA/CDN URL.
 * - VITE_WS_URL: optional full WebSocket URL (e.g. wss://api.example.com/ws).
 * - If both unset: same-origin as the page (nginx + API on one host).
 */

/**
 * REST base for axios.
 * - Unset VITE_API_URL: relative /api/* (same host as the SPA — correct behind nginx).
 * - Set VITE_API_URL: explicit API origin only (split hosting).
 */
export function getApiBaseUrl(): string {
  const explicit = (import.meta.env.VITE_API_URL as string | undefined)?.trim()
  if (explicit) {
    try {
      const explicitUrl = new URL(explicit)
      if (typeof window !== 'undefined' && explicitUrl.origin === window.location.origin) {
        return ''
      }
      return explicitUrl.origin
    } catch {
      return explicit.replace(/\/$/, '')
    }
  }
  return ''
}

export function getWebSocketUrl(): string {
  const explicit = (import.meta.env.VITE_WS_URL as string | undefined)?.trim()
  if (explicit) {
    return explicit
  }

  const api = (import.meta.env.VITE_API_URL as string | undefined)?.trim()
  if (api) {
    try {
      const u = new URL(api)
      const wsProto = u.protocol === 'https:' ? 'wss:' : 'ws:'
      return `${wsProto}//${u.host}/ws`
    } catch {
      // fall through
    }
  }

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws`
}
