/**
 * Build-time URL config for split deployment (e.g. SPA on S3, API on AWS).
 *
 * - VITE_API_URL: REST base (e.g. https://api.example.com) — passed to axios in api/client.ts
 * - VITE_WS_URL: optional full WebSocket URL (e.g. wss://api.example.com/ws).
 *   If unset, derived from VITE_API_URL. If both unset, same-origin as the SPA (dev/nginx).
 */

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
