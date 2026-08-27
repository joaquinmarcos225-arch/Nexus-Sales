const TOKEN_KEY = 'nexus_support_auth_token'

const SAME_ORIGIN_TOKENS = new Set(['same', 'relative', 'same-origin', '/', '.'])

function normalizeApiBaseUrl(raw) {
  let s = String(raw ?? '').trim()
  if (!s) return ''
  s = s.replace(/\/+$/, '')
  if (SAME_ORIGIN_TOKENS.has(s.toLowerCase())) return ''
  return s
}

const API_BASE = normalizeApiBaseUrl(import.meta.env.VITE_API_URL)

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export function getToken() {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(value) {
  if (value) localStorage.setItem(TOKEN_KEY, value)
  else localStorage.removeItem(TOKEN_KEY)
}

async function request(path, options = {}) {
  const token = getToken()
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      Accept: 'application/json',
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {}),
    },
  })
  const text = await res.text()
  let data = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = text
  }
  if (!res.ok) {
    if (res.status === 401) setToken(null)
    const detail = data?.detail
    const message =
      typeof detail === 'string'
        ? detail
        : Array.isArray(detail)
          ? detail.map((d) => d.msg || d).join(', ')
          : detail?.message || `Error ${res.status}`
    throw new ApiError(message, res.status)
  }
  return data
}

export async function supportLogin(email, password) {
  return request('/auth/support-login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
}

export async function fetchMe() {
  return request('/auth/me')
}

export async function fetchThreads() {
  return request('/support/ops/threads')
}

export async function fetchObservability({ refreshProspeo = false } = {}) {
  const query = refreshProspeo ? '?refresh_prospeo=true' : ''
  return request(`/support/ops/observability${query}`)
}

export async function fetchCapacity({ refresh = false, proposedGrant = null } = {}) {
  const params = new URLSearchParams()
  if (refresh) params.set('refresh', 'true')
  if (proposedGrant != null && proposedGrant > 0) params.set('proposed_grant', String(proposedGrant))
  const q = params.toString()
  return request(`/support/ops/capacity${q ? `?${q}` : ''}`)
}

export async function patchProviderBalance(provider, balanceUsd, notes = null) {
  return request(`/support/ops/capacity/balances/${encodeURIComponent(provider)}`, {
    method: 'PATCH',
    body: JSON.stringify({ balance_usd: balanceUsd, notes }),
  })
}

export async function fetchThread(threadId) {
  return request(`/support/ops/threads/${encodeURIComponent(threadId)}`)
}

export async function replyThread(threadId, text) {
  return request(`/support/ops/threads/${encodeURIComponent(threadId)}/messages`, {
    method: 'POST',
    body: JSON.stringify({ text }),
  })
}

export async function patchThreadStatus(threadId, status) {
  return request(`/support/ops/threads/${encodeURIComponent(threadId)}/status`, {
    method: 'PATCH',
    body: JSON.stringify({ status }),
  })
}

export async function fetchVapidPublicKey() {
  return request('/notifications/push/vapid-public')
}

export async function subscribePush(payload) {
  return request('/notifications/push/subscribe', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}
