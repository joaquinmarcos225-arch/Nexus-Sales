/** LI-SAFE: sin probe/Voyager/inyección. Solo click humano + clipboard. */
export const LI_SAFE_NO_PROFILE_PROBE = true

/** Nombre fijo: reusa la pestaña que Nexus abrió (sin extensión). */
export const LI_SAFE_WINDOW_NAME = 'nexus_linkedin_assist'

const CONTACTAR_STORAGE_KEY = 'nexus_li_contactar_done'

function readMap() {
  try {
    const raw = localStorage.getItem(CONTACTAR_STORAGE_KEY)
    const parsed = raw ? JSON.parse(raw) : {}
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

export function wasLiContactarDone(prospectId) {
  const id = String(Number(prospectId) || 0)
  if (!id || id === '0') return false
  return Boolean(readMap()[id])
}

export function markLiContactarDone(prospectId) {
  const id = String(Number(prospectId) || 0)
  if (!id || id === '0') return
  const map = readMap()
  map[id] = Date.now()
  try {
    localStorage.setItem(CONTACTAR_STORAGE_KEY, JSON.stringify(map))
  } catch {
    /* ignore */
  }
}

export function clearLiContactarDone(prospectId) {
  const id = String(Number(prospectId) || 0)
  if (!id || id === '0') return
  const map = readMap()
  if (!(id in map)) return
  delete map[id]
  try {
    localStorage.setItem(CONTACTAR_STORAGE_KEY, JSON.stringify(map))
  } catch {
    /* ignore */
  }
}

/** DEPRECADO: vigilancia silenciosa LI-IN off. Inbound = Respondieron (pegar mensaje). */
export function rememberLiAwaitingReply(_opts) {
  /* no-op */
}

export function listLiAwaitingReplies() {
  return []
}

/** Respondieron dismiss / handoff: sale de la lista hasta nuevo mark-sent o TTL. */
const RESPONDIERON_DISMISS_KEY = 'nexus_li_respondieron_dismiss'
const RESPONDIERON_OMIT_TTL_MS = 3 * 24 * 60 * 60 * 1000
const RESPONDIERON_HANDOFF_TTL_MS = 90 * 24 * 60 * 60 * 1000

function readRespondieronDismissMap() {
  try {
    const raw = localStorage.getItem(RESPONDIERON_DISMISS_KEY)
    const parsed = raw ? JSON.parse(raw) : {}
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

function writeRespondieronDismissMap(map) {
  try {
    localStorage.setItem(RESPONDIERON_DISMISS_KEY, JSON.stringify(map))
  } catch {
    /* ignore */
  }
}

function dismissEntryAt(val) {
  if (val && typeof val === 'object') return Number(val.at || 0)
  return Number(val || 0)
}

function dismissEntryKind(val) {
  if (val && typeof val === 'object' && val.kind === 'handoff') return 'handoff'
  return 'omit'
}

function dismissEntryTtl(val) {
  return dismissEntryKind(val) === 'handoff'
    ? RESPONDIERON_HANDOFF_TTL_MS
    : RESPONDIERON_OMIT_TTL_MS
}

function pruneRespondieronDismissMap(map, now = Date.now()) {
  const next = {}
  for (const [id, val] of Object.entries(map || {})) {
    const at = dismissEntryAt(val)
    if (at > now - dismissEntryTtl(val)) {
      next[id] =
        val && typeof val === 'object' ? val : { at, kind: 'omit' }
    }
  }
  return next
}

export function isLiRespondieronDismissed(prospectId) {
  const id = String(Number(prospectId) || 0)
  if (!id || id === '0') return false
  const map = pruneRespondieronDismissMap(readRespondieronDismissMap())
  return Boolean(map[id])
}

/** @deprecated usar handoffLiRespondieron — mismo efecto omit corto. */
export function dismissLiRespondieron(prospectId) {
  handoffLiRespondieron(prospectId, { kind: 'omit' })
}

export function handoffLiRespondieron(prospectId, { kind = 'handoff' } = {}) {
  const id = String(Number(prospectId) || 0)
  if (!id || id === '0') return
  const map = pruneRespondieronDismissMap(readRespondieronDismissMap())
  map[id] = { at: Date.now(), kind: kind === 'omit' ? 'omit' : 'handoff' }
  writeRespondieronDismissMap(map)
}

export function clearLiRespondieronDismiss(prospectId) {
  const id = String(Number(prospectId) || 0)
  if (!id || id === '0') return
  const map = readRespondieronDismissMap()
  if (!(id in map)) return
  delete map[id]
  writeRespondieronDismissMap(map)
}

/**
 * Abre el perfil sin extensión: window.open con nombre fijo.
 * IMPORTANTE: no pasar noopener/noreferrer — Chrome ignora el nombre y abre otra tab.
 * Reusa la pestaña que Nexus ya abrió con este mismo nombre.
 */
export async function openLiSafeProfile(profileUrl) {
  const url = String(profileUrl || '').trim()
  if (!url) return { ok: false, reason: 'no_url' }
  try {
    const w = window.open(url, LI_SAFE_WINDOW_NAME)
    // Cortar window.opener sin romper el reuso del nombre.
    try {
      if (w) w.opener = null
    } catch {
      /* ignore */
    }
    if (w) {
      try {
        w.focus()
      } catch {
        /* ignore */
      }
    }
    return { ok: Boolean(w), reused: true, via: 'window_open_named' }
  } catch (e) {
    return {
      ok: false,
      reason: e instanceof Error ? e.message : String(e),
    }
  }
}
