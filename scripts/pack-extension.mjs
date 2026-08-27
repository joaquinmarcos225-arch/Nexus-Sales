/**
 * Empaqueta la extensión Nexus Outreach Assist.
 *
 * Uso (desde la raíz del repo):
 *   node scripts/pack-extension.mjs
 *   NEXUS_FRONTEND_URL=https://nexus.costguard.com.ar node scripts/pack-extension.mjs
 *   NEXUS_EXTENSION_STORE_BUILD=1 NEXUS_FRONTEND_URL=https://nexus.costguard.com.ar \
 *     NEXUS_API_PUBLIC_URL=https://api-production-21aa.up.railway.app node scripts/pack-extension.mjs
 *
 * Salida normal: dist/nexus-linkedin-assist.zip  (árbol completo, instalación manual)
 * Salida Web Store: dist/nexus-outreach-assist-store.zip  (allowlist sin LinkedIn)
 */
import { cpSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { execSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const fullExtDir = path.join(root, 'browser-extension')
const storeExtDir = path.join(root, 'browser-extension-store')
const distDir = path.join(root, 'dist')
const stagingDir = path.join(distDir, 'nexus-linkedin-assist')
const storeBuild = process.env.NEXUS_EXTENSION_STORE_BUILD === '1'
const zipPath = path.join(
  distDir,
  storeBuild ? 'nexus-outreach-assist-store.zip' : 'nexus-linkedin-assist.zip',
)

const SKIP_NAMES = new Set(['README.md', 'TEAM_INSTALL.md', '.DS_Store', 'Thumbs.db'])

/** Solo estos paths relativos entran al ZIP de Chrome Web Store. */
const STORE_ALLOWLIST = [
  'manifest.json',
  'background.js',
  'content-nexus.js',
  'content-whatsapp.js',
  'page-bridge.js',
  'icons/icon16.png',
  'icons/icon48.png',
  'icons/icon128.png',
]

const STORE_FORBIDDEN_RE =
  /\b(linkedin|voyager|jsessionid|cookies\.get|degree|probe|messaging\/|wa-store-reader|__NEXUS_LI_|NEXUS_LI_)/i

function patchManifest(manifestPath) {
  const frontend = (process.env.NEXUS_FRONTEND_URL || '').trim().replace(/\/$/, '')
  const apiPublic = (process.env.NEXUS_API_PUBLIC_URL || '').trim().replace(/\/$/, '')
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))

  if (storeBuild) {
    if (!frontend || !apiPublic) {
      console.error('Web Store requiere NEXUS_FRONTEND_URL y NEXUS_API_PUBLIC_URL')
      process.exit(1)
    }
    let frontendOrigin
    let apiOrigin
    try {
      frontendOrigin = new URL(frontend).origin
      apiOrigin = new URL(apiPublic).origin
    } catch {
      console.error('URL inválida en NEXUS_FRONTEND_URL / NEXUS_API_PUBLIC_URL')
      process.exit(1)
    }

    for (const block of manifest.content_scripts || []) {
      if (block.js?.includes('page-bridge.js') || block.js?.includes('content-nexus.js')) {
        block.matches = [`${frontendOrigin}/*`]
      }
    }

    const hosts = new Set(['https://web.whatsapp.com/*', `${frontendOrigin}/*`, `${apiOrigin}/*`])
    for (const h of hosts) {
      if (/linkedin/i.test(h) || h.startsWith('http://')) {
        console.error('host_permissions inválido para Web Store:', h)
        process.exit(1)
      }
    }
    manifest.host_permissions = [...hosts]
    manifest.description =
      'Asiste tareas de outreach iniciadas por el usuario desde Nexus Sales en WhatsApp Web.'

    if (!manifest.icons) {
      manifest.icons = {
        16: 'icons/icon16.png',
        48: 'icons/icon48.png',
        128: 'icons/icon128.png',
      }
    }

    writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`)
    return { allMatches: [`${frontendOrigin}/*`], frontend }
  }

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

  const hostPermissions = [
    'https://www.linkedin.com/*',
    'https://web.whatsapp.com/*',
    'http://127.0.0.1:8002/*',
    'http://localhost:8002/*',
  ]
  for (const raw of [frontend, apiPublic]) {
    if (!raw) continue
    try {
      hostPermissions.push(`${new URL(raw).origin}/*`)
    } catch {
      console.error('URL inválida para host_permissions:', raw)
      process.exit(1)
    }
  }
  manifest.host_permissions = [...new Set(hostPermissions)]
  manifest.description =
    'Asistente de Nexus Sales para abrir conversaciones y ejecutar tareas manuales en LinkedIn y WhatsApp Web.'

  if (!manifest.icons) {
    manifest.icons = {
      16: 'icons/icon16.png',
      48: 'icons/icon48.png',
      128: 'icons/icon128.png',
    }
  }

  writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`)
  return { allMatches, frontend }
}

function copyFullExtension() {
  rmSync(stagingDir, { recursive: true, force: true })
  mkdirSync(stagingDir, { recursive: true })
  cpSync(fullExtDir, stagingDir, {
    recursive: true,
    filter: (src) => {
      const base = path.basename(src)
      return !SKIP_NAMES.has(base) && !base.endsWith('.test.mjs')
    },
  })
}

function copyStoreAllowlist() {
  rmSync(stagingDir, { recursive: true, force: true })
  mkdirSync(stagingDir, { recursive: true })

  for (const rel of STORE_ALLOWLIST) {
    const src = path.join(storeExtDir, rel)
    const dest = path.join(stagingDir, rel)
    try {
      statSync(src)
    } catch {
      console.error('Falta archivo allowlist Web Store:', rel)
      process.exit(1)
    }
    mkdirSync(path.dirname(dest), { recursive: true })
    cpSync(src, dest)
  }
}

function assertStorePackageClean() {
  const forbiddenPerms = new Set(['cookies', 'webRequest', 'debugger'])
  const manifest = JSON.parse(readFileSync(path.join(stagingDir, 'manifest.json'), 'utf8'))
  for (const p of manifest.permissions || []) {
    if (forbiddenPerms.has(p)) {
      console.error('Permiso prohibido en Web Store:', p)
      process.exit(1)
    }
  }
  for (const h of manifest.host_permissions || []) {
    if (/linkedin/i.test(h) || h.includes('*://*/*') || h.startsWith('http://')) {
      console.error('host_permissions inválido:', h)
      process.exit(1)
    }
  }

  const found = []
  function walk(dir, prefix = '') {
    for (const name of readdirSync(dir)) {
      const full = path.join(dir, name)
      const rel = prefix ? `${prefix}/${name}` : name
      if (statSync(full).isDirectory()) {
        walk(full, rel)
        continue
      }
      if (!STORE_ALLOWLIST.includes(rel.replace(/\\/g, '/'))) {
        found.push(`archivo fuera de allowlist: ${rel}`)
        continue
      }
      if (/\.(js|json|mjs|md|txt|html)$/i.test(name)) {
        const text = readFileSync(full, 'utf8')
        if (STORE_FORBIDDEN_RE.test(text) && !/__NEXUS_LINKEDIN_EXTENSION__/.test(text.slice(0, 400))) {
          // Compat marker is allowed once in page-bridge; scan rest carefully.
        }
        const matches = text.match(STORE_FORBIDDEN_RE)
        if (matches) {
          found.push(`${rel}: ${matches[0]}`)
        }
      }
    }
  }
  walk(stagingDir)
  if (found.length) {
    console.error('Paquete Web Store rechazado (código/host prohibido):')
    for (const line of found) console.error(' -', line)
    process.exit(1)
  }
}

function patchRuntimeConfig() {
  const apiPublic = (process.env.NEXUS_API_PUBLIC_URL || '').trim().replace(/\/$/, '')
  const frontend = (process.env.NEXUS_FRONTEND_URL || '').trim().replace(/\/$/, '')
  if (!apiPublic && !storeBuild) return
  if (!apiPublic) {
    console.error('NEXUS_API_PUBLIC_URL es obligatoria para el paquete Web Store')
    process.exit(1)
  }

  const backgroundPath = path.join(stagingDir, 'background.js')
  const nexusPath = path.join(stagingDir, 'content-nexus.js')
  const apiLiteral = JSON.stringify(apiPublic)
  const tabMatches = JSON.stringify([
    ...(storeBuild ? [] : ['http://127.0.0.1/*', 'http://localhost/*']),
    ...(frontend ? [`${frontend}/*`] : []),
  ])

  let background = readFileSync(backgroundPath, 'utf8')
  background = background.replace(
    /const DEFAULT_API = ['"][^'"]+['"]/,
    `const DEFAULT_API = ${apiLiteral}`,
  )
  if (storeBuild) {
    background = background.replace(
      /const NEXUS_ORIGINS = \[[^\]]*\]/,
      `const NEXUS_ORIGINS = ${tabMatches}`,
    )
  } else {
    background = background.replaceAll(
      "['http://127.0.0.1/*', 'http://localhost/*']",
      tabMatches,
    )
  }
  writeFileSync(backgroundPath, background)

  let nexus = readFileSync(nexusPath, 'utf8')
  nexus = nexus.replace(
    /apiBaseUrl:\s*['"][^'"]+['"]/,
    `apiBaseUrl: ${apiLiteral}`,
  )
  nexus = nexus.replace(
    /const DEFAULT_API = ['"][^'"]+['"]/,
    `const DEFAULT_API = ${apiLiteral}`,
  )
  writeFileSync(nexusPath, nexus)
}

function createZip() {
  rmSync(zipPath, { force: true })
  mkdirSync(distDir, { recursive: true })
  const zipper = path.join(root, 'scripts', 'zip-flat.py')
  execSync(`python "${zipper}" "${stagingDir}" "${zipPath}"`, { stdio: 'inherit' })
}

mkdirSync(distDir, { recursive: true })

if (storeBuild) {
  copyStoreAllowlist()
} else {
  copyFullExtension()
}

patchRuntimeConfig()

const manifestPath = path.join(stagingDir, 'manifest.json')
const { allMatches, frontend } = patchManifest(manifestPath)

if (storeBuild) {
  assertStorePackageClean()
}

createZip()

console.log('')
console.log('Extensión empaquetada:', zipPath)
console.log('Versión:', JSON.parse(readFileSync(manifestPath, 'utf8')).version)
console.log('Orígenes Nexus en manifest:', allMatches.join(', '))
console.log('Paquete:', storeBuild ? 'Chrome Web Store (allowlist limpia)' : 'instalación manual')
if (frontend) {
  console.log('Producción:', frontend)
} else {
  console.log('Solo localhost (dev). Para prod: NEXUS_FRONTEND_URL=https://app.tuempresa.com node scripts/pack-extension.mjs')
}
console.log('')
if (storeBuild) {
  console.log('Siguiente paso: subir dist/nexus-outreach-assist-store.zip a Chrome Web Store (Unlisted)')
} else {
  console.log('Entrega temporal: dist/nexus-linkedin-assist.zip → descomprimir → chrome://extensions → Cargar descomprimida')
}
