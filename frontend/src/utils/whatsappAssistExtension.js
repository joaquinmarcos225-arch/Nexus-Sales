/**
 * Bridge Nexus ↔ extensión para WhatsApp Web asistido.
 */
export function isNexusWhatsAppExtensionReady() {
  return (
    typeof window !== 'undefined' &&
    Boolean(
      window.__NEXUS_WHATSAPP_EXTENSION__ ||
        window.__NEXUS_OUTREACH_EXTENSION__ ||
        window.__NEXUS_LINKEDIN_EXTENSION__,
    )
  )
}

export async function armWhatsAppOpenChatViaExtension({
  sendUrl,
  prospectId,
  message,
  phoneDigits,
  prospectName,
}) {
  if (typeof window.nexusWhatsAppArmOpenChat !== 'function') {
    return { ok: false, reason: 'extension_not_ready' }
  }
  window.nexusWhatsAppArmOpenChat({
    sendUrl,
    prospectId,
    message,
    phoneDigits,
    prospectName,
  })
  return { ok: true }
}

export async function syncWhatsAppPendingToExtension({
  sendUrl,
  message,
  prospectId,
  phoneDigits,
  prospectName,
}) {
  if (typeof window.nexusWhatsAppSetPending !== 'function') {
    return { ok: false }
  }
  try {
    await window.nexusWhatsAppSetPending({
      sendUrl,
      message,
      prospectId,
      phoneDigits,
      prospectName,
    })
    return { ok: true }
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) }
  }
}
