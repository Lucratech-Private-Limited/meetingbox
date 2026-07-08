/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** REST API origin, e.g. https://api.example.com (no trailing slash). */
  readonly VITE_API_URL?: string
  /** Optional full WebSocket URL, e.g. wss://api.example.com/ws */
  readonly VITE_WS_URL?: string
  /** Optional Vite base path when hosted under a subpath, e.g. /app/ */
  readonly VITE_BASE_PATH?: string
  /** Optional direct weather city label. Defaults to Bengaluru. */
  readonly VITE_WEATHER_CITY?: string
  /** Optional direct weather latitude. Defaults to Bengaluru. */
  readonly VITE_WEATHER_LAT?: string
  /** Optional direct weather longitude. Defaults to Bengaluru. */
  readonly VITE_WEATHER_LON?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
