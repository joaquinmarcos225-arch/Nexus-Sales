/**
 * Genera manifest.json con el origen de Nexus para producción.
 * Uso: NEXUS_FRONTEND_URL=https://app.example.com node scripts/build-extension-manifest.mjs
 */
import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const extDir = path.join(root, 'browser-extension')
const manifestPath = path.join(extDir, 'manifest.json')

const frontend = (process.env.NEXUS_FRONTEND_URL || '').trim().replace(/\/$/, '')
const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))

const localMatches = ['http://127.0.0.1/*', 'http://localhost/*']
const prodMatches = []
if (frontend) {
  try {
    const u = new URL(frontend)
    prodMatches.push(`${u.origin}/*`)
  } catch {
    console.error('NEXUS_FRONTEND_URL inválida:', frontend)
    process.exit(1)
  }
}

const allMatches = [...new Set([...localMatches, ...prodMatches])]

for (const block of manifest.content_scripts) {
  if (block.js?.includes('page-bridge.js') || block.js?.includes('content-nexus.js')) {
    block.matches = allMatches
  }
}

if (!manifest.icons) {
  manifest.icons = {
    16: 'icons/icon16.png',
    48: 'icons/icon48.png',
    128: 'icons/icon128.png',
  }
}

writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`)
console.log('manifest.json actualizado. matches:', allMatches.join(', '))
