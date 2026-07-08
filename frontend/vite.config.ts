import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/** Same-origin deploy: '/'. Subpath hosting: VITE_BASE_PATH=/your-prefix/ */
const rawBase = (process.env.VITE_BASE_PATH ?? '/').trim()
const base =
  rawBase === '' || rawBase === '/'
    ? '/'
    : rawBase.endsWith('/')
      ? rawBase
      : `${rawBase}/`

export default defineConfig({
  base,
  plugins: [react()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    assetsDir: 'assets',
  },
  server: {
    port: 3000,
    host: true,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
