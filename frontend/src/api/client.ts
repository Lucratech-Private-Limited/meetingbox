// Axios API client with interceptors for auth and error handling

import axios from 'axios'
import toast from 'react-hot-toast'
import { getApiBaseUrl } from '../config/publicUrls'

/** Normal REST calls — avoid multi‑minute hangs when the API is down or misconfigured. */
export const DEFAULT_API_TIMEOUT_MS = 45_000

/** Audio upload + server transcribe/summary (same as previous global default). */
export const LONG_REQUEST_TIMEOUT_MS = 300_000

/** Auth bootstrap — fail fast so login/onboarding are usable if the server is unreachable. */
export const AUTH_REQUEST_TIMEOUT_MS = 12_000

/** Assistant intent / LLM orchestration can exceed default. */
export const ASSISTANT_INTENT_TIMEOUT_MS = 120_000

const client = axios.create({
  baseURL: '',
  timeout: DEFAULT_API_TIMEOUT_MS,
  headers: {
    'Content-Type': 'application/json',
  },
})

function isApiRequest(url: unknown): boolean {
  return typeof url === 'string' && (url === '/api' || url.startsWith('/api/'))
}

function isStaticForbiddenResponse(error: unknown): boolean {
  const err = error as {
    response?: { status?: number; data?: unknown; headers?: Record<string, unknown> }
  }
  if (err.response?.status !== 403) return false
  const data = err.response.data
  const contentType = String(err.response.headers?.['content-type'] ?? '').toLowerCase()
  if (typeof data === 'string' && /403 Forbidden|<html/i.test(data)) return true
  return contentType.includes('text/html')
}

function requestUrl(config: { baseURL?: string; url?: string } | undefined): string {
  if (!config?.url) return ''
  if (!config.baseURL) return config.url
  try {
    return new URL(config.url, config.baseURL).toString()
  } catch {
    return `${config.baseURL}${config.url}`
  }
}

// Request interceptor — API base + auth (re-resolve each request for correct same-origin deploy)
client.interceptors.request.use(
  (config) => {
    const base = getApiBaseUrl()
    if (base) {
      config.baseURL = base
    } else {
      config.baseURL = ''
    }
    const token = localStorage.getItem('auth_token')
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => Promise.reject(error)
)

// Response interceptor — global error handling
client.interceptors.response.use(
  (response) => response,
  async (error) => {
    const config = error.config as
      | (typeof error.config & { _sameOriginRetry?: boolean; baseURL?: string; url?: string })
      | undefined

    if (
      config &&
      !config._sameOriginRetry &&
      config.baseURL &&
      isApiRequest(config.url) &&
      isStaticForbiddenResponse(error)
    ) {
      config._sameOriginRetry = true
      config.baseURL = ''
      return client.request(config)
    }

    if (error.response?.status) {
      // Keep production troubleshooting actionable without changing user-facing UI.
      console.warn('[api]', error.response.status, requestUrl(config), error.response.data)
    }

    if (error.response?.status === 401) {
      const url = error.config?.url || ''
      // Don't redirect during auth initialization — let authStore handle cleanup
      if (!url.includes('/api/auth/me') && !url.includes('/api/auth/has-users')) {
        localStorage.removeItem('auth_token')
        window.location.href = '/login'
      }
    } else if (error.response?.status >= 500) {
      toast.error('Server error. Please try again later.')
    } else if (!error.response) {
      const msg = String(error.message || '')
      const timedOut =
        error.code === 'ECONNABORTED' || msg.toLowerCase().includes('timeout')
      toast.error(
        timedOut
          ? 'API request timed out — server may be wrong or unreachable. Set VITE_API_URL to your API origin, rebuild, and open firewall/security groups.'
          : 'Network error — no response from API. Check connection and VITE_API_URL.',
      )
    }
    return Promise.reject(error)
  }
)

export { client as apiClient }
export default client
