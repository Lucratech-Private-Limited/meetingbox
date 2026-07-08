/**
 * Post-build sanity check — run automatically via `npm run build`.
 * Catches incomplete dist/ (common cause of blank pages or static-host 403/404).
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const distDir = path.join(root, 'dist')
const indexPath = path.join(distDir, 'index.html')

function fail(msg) {
  console.error(`\n[verify-dist] ${msg}\n`)
  process.exit(1)
}

if (!fs.existsSync(indexPath)) {
  fail('dist/index.html is missing. Run vite build from the frontend repo root.')
}

const html = fs.readFileSync(indexPath, 'utf8')

if (html.includes('/src/main.tsx')) {
  fail(
    'dist/index.html still references /src/main.tsx (dev entry). ' +
      'The production build did not complete — do not serve this file.',
  )
}

const assetRefs = [
  ...html.matchAll(/(?:src|href)=["']([^"']+)["']/g),
].map((m) => m[1]).filter((u) => !u.startsWith('http') && !u.startsWith('data:'))

const missing = []
for (const ref of assetRefs) {
  const rel = ref.replace(/^\//, '')
  const onDisk = path.join(distDir, rel)
  if (!fs.existsSync(onDisk)) {
    missing.push(ref)
  }
}

if (missing.length > 0) {
  fail(
    'dist/index.html references files that are not on disk:\n  ' +
      missing.join('\n  ') +
      '\nCommit or deploy the full dist/ folder (including dist/assets/), not index.html alone.',
  )
}

const assetsDir = path.join(distDir, 'assets')
const jsBundles =
  fs.existsSync(assetsDir)
    ? fs.readdirSync(assetsDir).filter((f) => f.endsWith('.js'))
    : []

if (jsBundles.length === 0) {
  fail('dist/assets/*.js is empty. The JS bundle was not emitted.')
}

const iconsDir = path.join(distDir, 'icons')
if (!fs.existsSync(iconsDir)) {
  console.warn('[verify-dist] warning: dist/icons/ missing (public/icons may not have been copied).')
}

console.log(`[verify-dist] OK — ${jsBundles.length} JS bundle(s), index.html references resolve under dist/`)
console.log(`[verify-dist] Point nginx root / FRONTEND_DIST at:\n  ${distDir}`)
