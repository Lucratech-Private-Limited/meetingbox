/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** REST API origin, e.g. https://api.example.com (no trailing slash). */
  readonly VITE_API_URL?: string
  /** Optional full WebSocket URL, e.g. wss://api.example.com/ws */
  readonly VITE_WS_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
