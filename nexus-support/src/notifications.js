import { fetchVapidPublicKey, subscribePush } from './api.js'

function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4)
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/')
  const raw = atob(base64)
  const out = new Uint8Array(raw.length)
  for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i)
  return out
}

export async function registerSupportServiceWorker() {
  if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return null
  try {
    return await navigator.serviceWorker.register('/sw.js', { scope: '/' })
  } catch {
    return null
  }
}

export async function requestSupportNotificationPermission() {
  if (typeof window === 'undefined' || !('Notification' in window)) return 'unsupported'
  if (Notification.permission === 'granted') return 'granted'
  if (Notification.permission === 'denied') return 'denied'
  try {
    return await Notification.requestPermission()
  } catch {
    return 'denied'
  }
}

export async function enableSupportPush() {
  await registerSupportServiceWorker()
  const perm = await requestSupportNotificationPermission()
  if (perm !== 'granted' || !('PushManager' in window)) return perm
  try {
    const vapid = await fetchVapidPublicKey()
    const key = vapid?.public_key
    if (!key) return perm
    const reg = await navigator.serviceWorker.ready
    let sub = await reg.pushManager.getSubscription()
    if (!sub) {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(key),
      })
    }
    const json = sub.toJSON()
    await subscribePush({
      app: 'support',
      endpoint: json.endpoint,
      keys: json.keys,
    })
  } catch {
    /* local notifications still work */
  }
  return perm
}

export async function notifySupportInbox({ title, body }) {
  if (typeof window === 'undefined' || !('Notification' in window)) return
  if (Notification.permission !== 'granted') return
  try {
    const reg = await navigator.serviceWorker?.ready
    if (reg?.showNotification) {
      await reg.showNotification(title || 'Nexus Support', {
        body: body || 'Hay un ticket nuevo.',
        icon: '/favicon.svg',
        tag: 'nexus-support',
        renotify: true,
        data: { url: '/' },
      })
      return
    }
  } catch {
    /* fallback */
  }
  try {
    new Notification(title || 'Nexus Support', { body: body || 'Hay un ticket nuevo.' })
  } catch {
    /* ignore */
  }
}
