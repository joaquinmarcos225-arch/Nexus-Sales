const POLL_STATUSES = new Set(['pending_qr', 'qr_ready', 'connecting'])
const PAIRING_QR_STATUSES = new Set(['pending_qr', 'qr_ready'])

export function whatsappSessionNeedsPolling(status) {
  return POLL_STATUSES.has(String(status || '').toLowerCase())
}

export function isWhatsappPairingStatus(status) {
  return PAIRING_QR_STATUSES.has(String(status || '').toLowerCase())
}

const HIDDEN_SESSION_STATUSES = new Set(['cancelled', 'disconnected'])

export function visibleWhatsappSessions(sessions) {
  // Desconectar = fuera de la lista (no dejar filas «Desconectado»).
  return (Array.isArray(sessions) ? sessions : []).filter(
    (s) => !HIDDEN_SESSION_STATUSES.has(String(s?.status || '').toLowerCase()),
  )
}

export function mergeSessionQr(prevById, session) {
  if (!session?.session_uuid) return prevById || {}
  const prev = (prevById && prevById[session.session_uuid]) || {}
  const png = session.qr_png_base64 || prev.qr_png_base64 || null
  const code = session.qr_code || prev.qr_code || null
  if (!png && !code) return prevById || {}
  return {
    ...(prevById || {}),
    [session.session_uuid]: { qr_png_base64: png, qr_code: code },
  }
}

export function resolvedSessionQr(session, qrById) {
  const cached = (qrById && session?.session_uuid && qrById[session.session_uuid]) || {}
  return {
    qr_png_base64: session?.qr_png_base64 || cached.qr_png_base64 || null,
    qr_code: session?.qr_code || cached.qr_code || null,
  }
}

export function sessionNeedsQrFetch(session, qrById) {
  if (!isWhatsappPairingStatus(session?.status)) return false
  const resolved = resolvedSessionQr(session, qrById)
  return !resolved.qr_png_base64 && !resolved.qr_code
}

export const WHATSAPP_SESSION_POLL_MS = 4000
export const WHATSAPP_QR_FETCH_MS = 2000
