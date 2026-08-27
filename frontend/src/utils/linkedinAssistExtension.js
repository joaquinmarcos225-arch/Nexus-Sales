/** Resuelve la URL /messaging/compose?... para un perfil (cache o Voyager). */
export async function resolveLinkedInComposeUrlViaExtension({ profileUrl, timeoutMs = 8000 }) {
  if (
    typeof window === 'undefined' ||
    typeof window.nexusLinkedInResolveCompose !== 'function'
  ) {
    return { ok: false, composeUrl: null, reason: 'extension_not_installed' }
  }
  try {
    const result = await Promise.race([
      window.nexusLinkedInResolveCompose({ profileUrl }),
      new Promise((resolve) => {
        window.setTimeout(() => resolve({ composeUrl: null, method: 'timeout' }), timeoutMs)
      }),
    ])
    return {
      ok: Boolean(result?.composeUrl),
      composeUrl: result?.composeUrl || null,
      method: result?.method,
    }
  } catch (e) {
    return {
      ok: false,
      composeUrl: null,
      reason: e instanceof Error ? e.message : String(e),
    }
  }
}

/** @returns {boolean} */
export function isNexusLinkedInExtensionInstalled() {
  // Solo el sideload completo marca LinkedIn. El paquete Web Store es WA-only (LI-SAFE sin scripts LI).
  return Boolean(typeof window !== 'undefined' && window.__NEXUS_LINKEDIN_EXTENSION__)
}

/** Dispara armado de chat (+ pegado si hay message) al instante. */
export function armLinkedInOpenChatViaExtension({ profileUrl, prospectId, message }) {
  if (typeof window === 'undefined' || typeof window.nexusLinkedInArmOpenChat !== 'function') {
    return false
  }
  try {
    window.nexusLinkedInArmOpenChat({ profileUrl, prospectId, message })
    return true
  } catch {
    return false
  }
}

/**
 * Chat + pegado en la pestaña que Nexus ya abrió con window.open (no crea otra).
 */
export async function assistLinkedInOnExistingTabViaExtension({
  profileUrl,
  message,
  sessionId,
  prospectId,
  isReply,
  openChatOnly = true,
  /** Si true, no crea pestaña nueva (espera una existente). */
  adoptOnly = true,
  timeoutMs = 60000,
}) {
  if (!isNexusLinkedInExtensionInstalled() || typeof window.nexusLinkedInAssist !== 'function') {
    return { ok: false, reason: 'extension_not_installed' }
  }
  try {
    const result = await Promise.race([
      window.nexusLinkedInAssist({
        profileUrl,
        message: message || '',
        sessionId,
        prospectId,
        isReply,
        adoptOnly,
        openChatOnly,
      }),
      new Promise((_, reject) => {
        window.setTimeout(() => reject(new Error('timeout_extension_assist')), timeoutMs)
      }),
    ])
    return {
      ok: Boolean(result?.ok !== false),
      mode: result?.mode || 'extension',
      warning: result?.warning,
      resolveMethod: result?.resolveMethod,
      pasted: Boolean(result?.pasted || result?.mode === 'extension'),
      error: result?.error,
    }
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    return { ok: false, reason: msg || 'extension_failed' }
  }
}

/**
 * Abre perfil → chat → pega mensaje vía extensión Chrome.
 * Tiene timeout: si la extensión no responde, no bloquea la UI.
 * @returns {Promise<{ ok: boolean, mode?: string, reason?: string }>}
 */
export async function openLinkedInAssistViaExtension({
  profileUrl,
  message,
  sessionId,
  prospectId,
  isReply,
  timeoutMs = 45000,
}) {
  if (!isNexusLinkedInExtensionInstalled() || typeof window.nexusLinkedInAssist !== 'function') {
    return { ok: false, reason: 'extension_not_installed' }
  }
  try {
    const result = await Promise.race([
      window.nexusLinkedInAssist({
        profileUrl,
        message,
        sessionId,
        prospectId,
        isReply,
      }),
      new Promise((_, reject) => {
        window.setTimeout(() => reject(new Error('timeout_extension_assist')), timeoutMs)
      }),
    ])
    return {
      ok: true,
      mode: result?.mode || 'extension',
      warning: result?.warning,
      resolveMethod: result?.resolveMethod,
    }
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    return { ok: false, reason: msg || 'extension_failed' }
  }
}

/** Registra borrador pendiente para auto-detectar envío manual en LinkedIn (sin auto-enviar). */
export async function syncLinkedInPendingToExtension({ profileUrl, message, prospectId, isReply }) {
  if (!isNexusLinkedInExtensionInstalled() || typeof window.nexusLinkedInSetPending !== 'function') {
    return { ok: false, reason: 'extension_not_installed' }
  }
  try {
    await window.nexusLinkedInSetPending({ profileUrl, message, prospectId, isReply })
    return { ok: true }
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) }
  }
}

/**
 * DEPRECADO (producto Outreach-style): inbound LinkedIn = SDR pega en Respondieron.
 * No-op a propósito — no armar vigilancia DOM / Messaging.
 */
export async function armLinkedInInboundWatchViaExtension(_opts) {
  return { ok: false, reason: 'disabled_respondieron_manual' }
}

/** DEPRECADO: poll DOM LI-IN desactivado. Usar Respondieron + registerLinkedInInbound. */
export async function pollLinkedInInboundNowViaExtension(_opts = {}) {
  return { ok: false, reason: 'disabled_respondieron_manual', candidates: 0 }
}

/**
 * Abre el perfil en background y fuerza verificación 1º grado (connected / not_connected).
 * @param {{ profileUrl: string, prospectId?: number, connectionStatus?: string }} opts
 * connectionStatus: default 'checking' (verificar 1º grado antes de encolar)
 */
export async function probeLinkedInConnectionViaExtension({
  profileUrl,
  prospectId,
  prospectName,
  connectionStatus = 'checking',
  timeoutMs = 40_000,
}) {
  if (!isNexusLinkedInExtensionInstalled() || typeof window.nexusLinkedInProbeConnection !== 'function') {
    return { ok: false, readOk: false, reason: 'extension_not_installed', error: 'extension_not_installed' }
  }
  try {
    const result = await Promise.race([
      window.nexusLinkedInProbeConnection({
        profileUrl,
        prospectId,
        prospectName,
        connectionStatus,
      }),
      new Promise((resolve) => {
        window.setTimeout(
          () =>
            resolve({
              ok: false,
              readOk: false,
              error: 'timeout_extension_probe',
              prospectId,
              prospectName,
            }),
          timeoutMs,
        )
      }),
    ])
    const base = result && typeof result === 'object' ? result : {}
    // Nunca devolver "Leyendo…" como resultado final.
    const error =
      base.error ||
      (!base.ok && !base.verdict && !base.degree ? 'sin_grado' : null)
    return {
      ...base,
      ok: Boolean(base.ok && (base.verdict || base.degree || base.readOk)),
      readOk: Boolean(
        base.readOk ||
          base.verdict === 'connected' ||
          base.verdict === 'not_connected' ||
          (base.ok && base.degree),
      ),
      error: base.readOk || base.verdict ? null : error,
      phase: undefined,
      summary: undefined,
    }
  } catch (e) {
    return {
      ok: false,
      readOk: false,
      error: e instanceof Error ? e.message : String(e),
    }
  }
}

/** Dispara YA el sondeo de checking pendientes. */
export async function probeLinkedInPendingNowViaExtension() {
  if (
    !isNexusLinkedInExtensionInstalled() ||
    typeof window.nexusLinkedInProbePendingNow !== 'function'
  ) {
    return { ok: false, reason: 'extension_not_installed' }
  }
  try {
    const result = await window.nexusLinkedInProbePendingNow()
    return { ok: true, ...(result && typeof result === 'object' ? result : {}) }
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) }
  }
}
