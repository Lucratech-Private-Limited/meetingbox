/**
 * Ensure nginx/www-data can read dist/ after npm run build on the server.
 * Fixes 403 Forbidden when files were created with a restrictive umask (e.g. 600).
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const distDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'dist')

if (!fs.existsSync(distDir)) {
  process.exit(0)
}

function walk(dir) {
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, ent.name)
    if (ent.isDirectory()) {
      fs.chmodSync(p, 0o755)
      walk(p)
    } else {
      fs.chmodSync(p, 0o644)
    }
  }
}

try {
  fs.chmodSync(distDir, 0o755)
  walk(distDir)
  console.log('[open-dist-perms] dist/ is world-readable (755 dirs, 644 files)')
} catch (err) {
  console.warn('[open-dist-perms] skipped:', err instanceof Error ? err.message : err)
}
