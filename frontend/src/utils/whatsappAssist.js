/** Helpers WhatsApp asistido (Web + app de escritorio). */

export function normalizeWhatsAppDigits(phone) {
  let digits = String(phone || '').replace(/\D/g, '')
  if (!digits || digits.length < 8) return null
  if (digits.startsWith('00')) digits = digits.slice(2)
  if (digits.startsWith('0') && digits.length >= 10) {
    digits = `54${digits.replace(/^0+/, '')}`
  }
  if (!digits.startsWith('54') && digits.length <= 11) {
    digits = `54${digits}`
  }
  return digits
}

/** WhatsApp Web en Chrome (misma sesión / extensión). */
export function waWebSendUrl(phone, text = '') {
  const digits = normalizeWhatsAppDigits(phone)
  if (!digits) return null
  const base = `https://web.whatsapp.com/send?phone=${digits}`
  const body = String(text || '').trim()
  if (!body) return base
  return `${base}&text=${encodeURIComponent(body)}`
}

/**
 * Abre la app de WhatsApp instalada (o el chooser del SO).
 * wa.me redirige a la app de escritorio si está instalada; si no, a Web.
 */
export function waAppSendUrl(phone, text = '') {
  const digits = normalizeWhatsAppDigits(phone)
  if (!digits) return null
  const body = String(text || '').trim()
  if (!body) return `https://wa.me/${digits}`
  return `https://wa.me/${digits}?text=${encodeURIComponent(body)}`
}

/** Deep link nativo (Windows/macOS con app instalada). */
export function waDesktopProtocolUrl(phone, text = '') {
  const digits = normalizeWhatsAppDigits(phone)
  if (!digits) return null
  const body = String(text || '').trim()
  const base = `whatsapp://send?phone=${digits}`
  if (!body) return base
  return `${base}&text=${encodeURIComponent(body)}`
}

export function whatsappAssistStatusLabel(status) {
  const map = {
    none: '—',
    suggested: 'Pendiente',
    prepared: 'Listo',
    opened: 'Abierto',
    sent: 'Enviado',
  }
  return map[status] || status || 'Pendiente'
}

export function whatsappAssistStatusClass(status) {
  if (status === 'opened') return 'bg-[#25D366]/15 text-[#075E54] ring-[#25D366]/35'
  if (status === 'prepared') return 'bg-emerald-50 text-emerald-900 ring-emerald-200'
  if (status === 'suggested') return 'bg-[#25D366]/10 text-[#128C7E] ring-[#25D366]/25'
  if (status === 'sent') return 'bg-zinc-100 text-zinc-700 ring-zinc-200'
  return 'bg-zinc-50 text-zinc-700 ring-zinc-200'
}

export function whatsappPriorityClass(priority) {
  if (priority === 'alta') return 'bg-emerald-50 text-emerald-900 ring-emerald-200'
  if (priority === 'baja') return 'bg-zinc-50 text-zinc-600 ring-zinc-200'
  return 'bg-[#25D366]/10 text-[#075E54] ring-[#25D366]/25'
}
