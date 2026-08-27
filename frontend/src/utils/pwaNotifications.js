import { fetchVapidPublicKey, subscribePush } from './api.js'

function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4)
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/')
  const raw = atob(base64)
  const out = new Uint8Array(raw.length)
  for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i)
  return out
}

export async function registerSalesServiceWorker() {
  if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return null
  try {
    return await navigator.serviceWorker.register('/sw.js', { scope: '/' })
  } catch {
    return null
  }
}

export async function requestSalesNotificationPermission() {
  if (typeof window === 'undefined' || !('Notification' in window)) return 'unsupported'
  if (Notification.permission === 'granted') return 'granted'
  if (Notification.permission === 'denied') return 'denied'
  try {
    return await Notification.requestPermission()
  } catch {
    return 'denied'
  }
}

export async function enableSalesSupportPush() {
  await registerSalesServiceWorker()
  const perm = await requestSalesNotificationPermission()
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
      app: 'sales',
      endpoint: json.endpoint,
      keys: json.keys,
    })
  } catch {
    /* push opcional: igual avisa con Notification local */
  }
  return perm
}

export async function notifySalesSupportReply(text) {
  if (typeof window === 'undefined' || !('Notification' in window)) return
  if (Notification.permission !== 'granted') return
  const body = (text || '').trim().slice(0, 180) || 'Nexus Support te respondió.'
  try {
    const reg = await navigator.serviceWorker?.ready
    if (reg?.showNotification) {
      await reg.showNotification('Nexus Support te respondió', {
        body,
        icon: '/favicon.svg',
        tag: 'nexus-sales-support',
        renotify: true,
        data: { url: '/soporte' },
      })
      return
    }
  } catch {
    /* fallback */
  }
  try {
    new Notification('Nexus Support te respondió', { body, tag: 'nexus-sales-support' })
  } catch {
    /* ignore */
  }
}
