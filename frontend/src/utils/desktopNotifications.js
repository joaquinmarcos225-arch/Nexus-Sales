/** @typedef {{ count: number, delta?: number, href?: string, campaignName?: string, channel?: 'linkedin' | 'whatsapp' | 'meetings' | 'outreach', reason?: 'login' | 'background' }} QueueNotifyPayload */

export function canUseDesktopNotifications() {
  return typeof window !== 'undefined' && 'Notification' in window
}

export async function requestDesktopNotificationPermission() {
  if (!canUseDesktopNotifications()) {
    return 'unsupported'
  }
  if (Notification.permission === 'granted') {
    return 'granted'
  }
  if (Notification.permission === 'denied') {
    return 'denied'
  }
  try {
    return await Notification.requestPermission()
  } catch {
    return 'denied'
  }
}

/** True si el usuario está usando Nexus (pestaña visible y con foco). */
export function isNexusAppForeground() {
  if (typeof document === 'undefined') return true
  return document.visibilityState === 'visible' && document.hasFocus()
}

export function playNotificationChime() {
  if (typeof window === 'undefined') {
    return
  }
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext
    if (!Ctx) {
      return
    }
    const ctx = new Ctx()
    const now = ctx.currentTime

    function tone(freq, start, duration, volume = 0.12) {
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.type = 'sine'
      osc.frequency.value = freq
      gain.gain.setValueAtTime(0.0001, start)
      gain.gain.exponentialRampToValueAtTime(volume, start + 0.02)
      gain.gain.exponentialRampToValueAtTime(0.0001, start + duration)
      osc.connect(gain)
      gain.connect(ctx.destination)
      osc.start(start)
      osc.stop(start + duration + 0.02)
    }

    tone(784, now, 0.18)
    tone(988, now + 0.14, 0.22, 0.1)

    window.setTimeout(() => {
      void ctx.close()
    }, 500)
  } catch {
    /* ignore — sonido opcional */
  }
}

/** @type {Record<string, number>} */
const lastDesktopAlertAtByChannel = {}

/**
 * Aviso de escritorio para cola LinkedIn/WhatsApp.
 * Solo en login o con la pestaña en segundo plano (fuera de Nexus).
 * Dentro de la app: solo la campanita.
 * @param {QueueNotifyPayload} payload
 */
export function notifyQueueDesktopAlert(payload) {
  const {
    count,
    delta = 1,
    href = '/campanas',
    campaignName,
    channel = 'outreach',
    reason = 'background',
  } = payload || {}
  if (count <= 0) {
    return
  }

  // Dentro de Nexus: no molestar (la campanita ya muestra el contador).
  if (reason !== 'login' && isNexusAppForeground()) {
    return
  }

  // Evitar doble disparo del mismo canal (Header + Dashboard montan el mismo hook).
  const now = Date.now()
  const lastAt = lastDesktopAlertAtByChannel[channel] || 0
  if (now - lastAt < 2500) {
    return
  }
  lastDesktopAlertAtByChannel[channel] = now

  playNotificationChime()

  if (!canUseDesktopNotifications() || Notification.permission !== 'granted') {
    return
  }

  const channelLabel =
    channel === 'linkedin'
      ? 'LinkedIn'
      : channel === 'whatsapp'
        ? 'WhatsApp'
        : channel === 'call'
          ? 'llamada'
          : channel === 'meetings'
            ? 'reuniones'
            : 'outreach'
  let title
  if (channel === 'meetings') {
    if (reason === 'login') {
      title =
        count === 1
          ? '1 reunión agendada'
          : `${count} reuniones agendadas`
    } else if (delta > 1) {
      title = `${delta} reuniones nuevas agendadas`
    } else {
      title = 'Nueva reunión agendada'
    }
  } else if (reason === 'login') {
    title =
      count === 1
        ? `1 mensaje ${channelLabel} por enviar`
        : `${count} mensajes ${channelLabel} por enviar`
  } else if (delta > 1) {
    title = `${delta} mensajes ${channelLabel} nuevos`
  } else {
    title = `Nuevo mensaje ${channelLabel} por enviar`
  }

  const body =
    channel === 'meetings'
      ? campaignName
        ? `${campaignName}: tenés ${count} reunión${count === 1 ? '' : 'es'} próxima${count === 1 ? '' : 's'}. Abrí Nexus.`
        : `Tenés ${count} reunión${count === 1 ? '' : 'es'} pendiente${count === 1 ? '' : 's'} de revisar.`
      : campaignName
        ? `${campaignName}: tenés ${count} en cola. Abrí Nexus para enviarlos.`
        : `Tenés ${count} mensaje${count === 1 ? '' : 's'} ${channelLabel} pendiente${count === 1 ? '' : 's'}.`

  try {
    const notification = new Notification(title, {
      body,
      tag: `nx-queue-${channel}`,
      renotify: true,
    })
    notification.onclick = () => {
      window.focus()
      if (href && href.startsWith('/')) {
        window.location.assign(href)
      }
      notification.close()
    }
  } catch {
    /* ignore */
  }
}

/**
 * @deprecated Preferí notifyQueueDesktopAlert
 * @param {QueueNotifyPayload} payload
 */
export function notifyLinkedInDesktopAlert(payload) {
  notifyQueueDesktopAlert({ ...payload, channel: 'linkedin' })
}
