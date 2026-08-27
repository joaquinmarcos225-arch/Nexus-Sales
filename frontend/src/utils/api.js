import { API_BASE_URL, API_EFFECTIVE_TARGET, resolveApiUrl } from './constants.js'
import { getStoredToken, setStoredToken } from '../utils/authStorage.js'

export { resolveApiUrl }

export class ApiRequestError extends Error {
  constructor(message, { status, detail } = {}) {
    super(message)
    this.name = 'ApiRequestError'
    this.status = status
    this.detail = detail
  }

  get validation() {
    return this.detail?.validation ?? null
  }

  get testing() {
    return this.detail?.testing ?? null
  }

  get openai() {
    return this.detail?.openai ?? null
  }

  get retryable() {
    return Boolean(this.detail?.retryable || this.openai?.retryable)
  }
}

function authHeaders(extra = {}) {
  const token = getStoredToken()
  return {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...extra,
  }
}

async function parseErrorResponse(res) {
  let body = null
  try {
    body = await res.json()
  } catch {
    // ignore
  }
  const detail = body?.detail
  let message = 'No se pudo completar la acción. Reintentá en unos segundos.'
  if (typeof detail === 'string') {
    message = detail
  } else if (Array.isArray(detail)) {
    message = detail.map((d) => d.msg || d).join(', ')
  } else if (detail && typeof detail === 'object') {
    message = detail.summary || detail.message || message
  }

  // No mostrar jerga técnica / infra al usuario final
  const raw = String(message || '').trim()
  const lower = raw.toLowerCase()
  const loginFailure =
    lower.includes('incorrectos') ||
    lower.includes('incorrect') ||
    lower.includes('inválid') ||
    lower.includes('invalid credentials')
  if (
    (res.status === 401 ||
      lower.includes('autenticación requerida') ||
      lower.includes('authentication required') ||
      lower.includes('not authenticated')) &&
    !loginFailure
  ) {
    message = 'Tu sesión expiró. Volvé a iniciar sesión.'
  } else if (
    res.status === 502 ||
    res.status === 503 ||
    res.status === 504 ||
    lower === 'bad gateway' ||
    lower.includes('bad gateway') ||
    lower.includes('econnrefused') ||
    lower.includes('service unavailable') ||
    lower.includes('gateway timeout')
  ) {
    message =
      res.status === 504
        ? 'La operación sigue en curso en el servidor. Esperá un momento; no hace falta reintentar de inmediato.'
        : 'Nexus no responde un momento (servidor ocupado o reiniciando). Esperá unos segundos y volvé a intentar.'
  } else if (!raw || raw === 'Error' || /^[A-Z][a-z]+(\s+[A-Z][a-z]+)+$/.test(raw)) {
    // statusText genéricos tipo "Bad Gateway", "Internal Server Error"
    if (res.status >= 500) {
      message = 'Nexus no responde un momento. Esperá unos segundos y volvé a intentar.'
    }
  }

  const structuredDetail =
    detail && typeof detail === 'object' && !Array.isArray(detail) ? detail : null
  return { message, detail: structuredDetail, body }
}

async function parseErrorMessage(res) {
  const { message } = await parseErrorResponse(res)
  return message
}

async function request(path, options = {}) {
  const url = `${API_BASE_URL}${path}`
  const { formData, headers: extraHeaders, body, ...rest } = options
  const isForm = formData === true || (typeof FormData !== 'undefined' && body instanceof FormData)
  let res
  try {
    res = await fetch(url, {
      ...rest,
      body,
      headers: {
        ...(body && !isForm ? { 'Content-Type': 'application/json' } : {}),
        ...authHeaders(extraHeaders),
      },
    })
  } catch (err) {
    if (err instanceof Error && err.name === 'AbortError') {
      throw err
    }
    throw new Error('No se pudo conectar con Nexus. Revisá tu conexión o esperá unos segundos si el servidor está reiniciando.')
  }

  if (res.status === 204) {
    return null
  }

  if (!res.ok) {
    const { message, detail } = await parseErrorResponse(res)
    throw new ApiRequestError(message, { status: res.status, detail })
  }

  const text = await res.text()
  if (!text) {
    return null
  }

  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

export async function fetchCompanies() {
  return request('/companies')
}

export async function login(email, password, firstName) {
  return request('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password, first_name: firstName }),
  })
}

export async function requestPasswordReset(email) {
  return request('/auth/password-reset/request', {
    method: 'POST',
    body: JSON.stringify({ email }),
  })
}

export async function verifyPasswordResetCode(email, code) {
  return request('/auth/password-reset/verify', {
    method: 'POST',
    body: JSON.stringify({ email, code }),
  })
}

export async function confirmPasswordReset(email, code, password, passwordConfirm) {
  return request('/auth/password-reset/confirm', {
    method: 'POST',
    body: JSON.stringify({
      email,
      code,
      password,
      password_confirm: passwordConfirm,
    }),
  })
}

export async function downloadChromeExtensionZip() {
  const url = `${API_BASE_URL}/extension/chrome-zip`
  let res
  try {
    res = await fetch(url, { headers: authHeaders() })
  } catch {
    throw new Error('No se pudo conectar con Nexus. Revisá tu conexión o esperá unos segundos si el servidor está reiniciando.')
  }
  if (!res.ok) {
    const { message } = await parseErrorResponse(res)
    throw new ApiRequestError(message, { status: res.status })
  }
  const blob = await res.blob()
  const href = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = href
  a.download = 'nexus-linkedin-assist.zip'
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(href)
}

export async function registerWorkspace(payload) {
  return request('/onboarding/workspace', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function fetchAuthMe() {
  const data = await request('/auth/me')
  if (data?.access_token) {
    setStoredToken(data.access_token)
  }
  return data
}

export async function uploadMyAvatar(file) {
  const form = new FormData()
  form.append('file', file)
  return request('/auth/me/avatar', {
    method: 'POST',
    body: form,
    // request() sets JSON Content-Type by default — skip for FormData
    headers: {},
    formData: true,
  })
}

export async function deleteMyAvatar() {
  return request('/auth/me/avatar', { method: 'DELETE' })
}

export async function fetchProspectsOwnership(companyId, { includeTesting = false } = {}) {
  const qs = includeTesting ? '?include_testing=true' : ''
  return request(`/companies/${companyId}/prospects/ownership${qs}`)
}

export async function claimProspect(prospectId) {
  return request(`/prospects/${prospectId}/claim`, { method: 'POST' })
}

export async function releaseProspect(prospectId) {
  return request(`/prospects/${prospectId}/release`, { method: 'POST' })
}

export async function fetchProspectOutreachContext(prospectId) {
  return request(`/prospects/${prospectId}/outreach-context`)
}

export async function generateProspectSequencePreview(prospectId, { forceRegenerate = false } = {}) {
  const qs = forceRegenerate ? '?force_regenerate=true' : ''
  return request(`/prospects/${prospectId}/sequence/generate-preview${qs}`, { method: 'POST' })
}

export async function resetProspectSequenceDraft(prospectId) {
  return request(`/prospects/${prospectId}/sequence/reset-draft`, { method: 'POST' })
}

export async function fetchProspectSequencePreview(prospectId) {
  return request(`/prospects/${prospectId}/sequence/preview`)
}

export async function fetchProspectSequenceTracking(prospectId) {
  return request(`/prospects/${prospectId}/sequence/tracking`)
}

export async function fetchActiveSequences(companyId) {
  return request(`/companies/${companyId}/prospects/active-sequences`)
}

export async function executeProspectSequenceTouch(prospectId, day) {
  return request(`/prospects/${prospectId}/sequence/touches/${day}/execute`, { method: 'POST' })
}

export async function skipProspectSequenceTouch(prospectId, day) {
  return request(`/prospects/${prospectId}/sequence/touches/${day}/skip`, { method: 'POST' })
}

export async function markProspectSequenceTouchSent(prospectId, day) {
  return request(`/prospects/${prospectId}/sequence/touches/${day}/mark-sent`, { method: 'POST' })
}

export async function simulateProspectSequenceResponse(prospectId, body) {
  return request(`/prospects/${prospectId}/sequence/simulate-response`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function enrichProspect(prospectId) {
  return request(`/prospects/${prospectId}/enrich`, { method: 'POST' })
}

export async function startProspectSequence(prospectId, { consumeCredit = false } = {}) {
  const qs = consumeCredit ? '?consume_credit=true' : ''
  return request(`/prospects/${prospectId}/sequence/start${qs}`, { method: 'POST' })
}

export async function reassignProspect(prospectId, toUserId) {
  return request(`/prospects/${prospectId}/reassign`, {
    method: 'POST',
    body: JSON.stringify({ to_user_id: toUserId }),
  })
}

export async function fetchUsers(companyId) {
  return request(`/companies/${companyId}/users`)
}

export async function fetchEquipoWorkspace(companyId) {
  return request(`/companies/${companyId}/equipo`)
}

export async function createTeam(companyId, payload) {
  return request(`/companies/${companyId}/teams`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function updateTeam(teamId, payload) {
  return request(`/teams/${teamId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export async function updateUser(userId, payload) {
  return request(`/users/${userId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export async function fetchProducts(companyId) {
  return request(`/companies/${companyId}/products?include_inactive=false`)
}

export async function fetchProductsAll(companyId) {
  return request(`/companies/${companyId}/products?include_inactive=true`)
}

export async function createProduct(companyId, payload) {
  return request(`/companies/${companyId}/products`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function interpretProductDocument(companyId, documentText) {
  return request(`/companies/${companyId}/products/interpret`, {
    method: 'POST',
    body: JSON.stringify({ document_text: documentText }),
  })
}

/** Preferido: `POST /products/interpret` con company_id + raw_text (misma respuesta enriquecida). */
export async function interpretProductRaw(companyId, rawText) {
  return request('/products/interpret', {
    method: 'POST',
    body: JSON.stringify({ company_id: companyId, raw_text: rawText }),
  })
}

/** Extrae texto de PDF/DOCX/TXT/etc. en el backend. */
export async function extractProductDocument(companyId, file) {
  const form = new FormData()
  form.append('file', file)
  const url = `${API_BASE_URL}/companies/${companyId}/products/extract-document`
  let res
  try {
    res = await fetch(url, {
      method: 'POST',
      headers: authHeaders(),
      body: form,
    })
  } catch (err) {
    throw new Error(err instanceof Error ? err.message : 'Error de red')
  }
  if (!res.ok) {
    const { message } = await parseErrorResponse(res)
    throw new ApiRequestError(message, { status: res.status })
  }
  return res.json()
}

export async function updateProduct(productId, payload) {
  return request(`/products/${productId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export async function deleteProduct(productId) {
  return request(`/products/${productId}`, {
    method: 'DELETE',
  })
}

export async function fetchWallet(companyId) {
  return request(`/companies/${companyId}/wallet`)
}

export async function fetchCompanyGoLive(companyId) {
  return request(`/companies/${companyId}/go-live`)
}

export async function fetchSupportThread() {
  return request('/support/thread')
}

export async function postSupportMessage(text) {
  return request('/support/messages', {
    method: 'POST',
    body: JSON.stringify({ text }),
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

/** Créditos disponibles del usuario logueado (directora: pool sin asignar; manager/SDR: saldo personal). */
export async function fetchMyCredits() {
  return request('/users/me/credits')
}

export async function applyPlanWalletCredits(companyId) {
  return request(`/companies/${companyId}/wallet/apply-plan`, { method: 'POST' })
}

export async function fetchBillingOpsBoard(cycleKey) {
  const q = cycleKey ? `?cycle_key=${encodeURIComponent(cycleKey)}` : ''
  return request(`/billing-ops/board${q}`)
}

export async function fetchCompanyBillingOps(companyId, cycleKey) {
  const q = cycleKey ? `?cycle_key=${encodeURIComponent(cycleKey)}` : ''
  return request(`/companies/${companyId}/billing-ops${q}`)
}

export async function patchBillingOpsPlan(companyId, payload) {
  return request(`/companies/${companyId}/billing-ops/plan`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export async function patchBillingOpsCustomCredits(companyId, credits) {
  return request(`/companies/${companyId}/billing-ops/custom-credits`, {
    method: 'PATCH',
    body: JSON.stringify({ credits }),
  })
}

export async function markBillingOpsPaid(companyId, payload = {}) {
  return request(`/companies/${companyId}/billing-ops/mark-paid`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function markBillingOpsTool(companyId, tool, payload = {}) {
  return request(`/companies/${companyId}/billing-ops/tools/${encodeURIComponent(tool)}`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function grantBillingOpsCredits(companyId, payload = {}) {
  return request(`/companies/${companyId}/billing-ops/grant-credits`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function fetchCreditAllocations(companyId) {
  return request(`/companies/${companyId}/credit-allocations`)
}

export async function fetchCreditLedger(companyId, limit = 60) {
  const q = Number.isFinite(limit) ? `?limit=${limit}` : ''
  return request(`/companies/${companyId}/credit-ledger${q}`)
}

export async function assignSellerCredits(companyId, sellerId, amount) {
  return request(`/companies/${companyId}/credit-allocations`, {
    method: 'POST',
    body: JSON.stringify({ seller_id: sellerId, amount }),
  })
}

export async function transferSellerCredits(companyId, fromUserId, toUserId, amount) {
  return request(`/companies/${companyId}/credit-transfers`, {
    method: 'POST',
    body: JSON.stringify({
      from_user_id: fromUserId,
      to_user_id: toUserId,
      amount,
    }),
  })
}

export async function fetchCreditPeerTransfers(companyId, peerUserId, limit = 80) {
  const q = new URLSearchParams({
    peer_user_id: String(peerUserId),
  })
  if (Number.isFinite(limit)) q.set('limit', String(limit))
  return request(`/companies/${companyId}/credit-peer-transfers?${q}`)
}

export async function previewCampaignEstimates(companyId, prospectCount) {
  return request(`/companies/${companyId}/campaigns/preview-estimates`, {
    method: 'POST',
    body: JSON.stringify({ prospect_count: prospectCount }),
  })
}

export async function fetchCampaigns(companyId) {
  return request(`/companies/${companyId}/campaigns`)
}

export async function fetchResponderInbox(companyId) {
  return request(`/companies/${companyId}/responder-inbox`)
}

export async function fetchCompanyAnalytics(companyId) {
  const qs = new URLSearchParams({ company_id: String(companyId) })
  return request(`/analytics?${qs.toString()}`)
}

export async function createCampaign(companyId, payload) {
  return request(`/companies/${companyId}/campaigns`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function fetchCampaign(campaignId) {
  return request(`/campaigns/${campaignId}`)
}

export async function updateCampaign(campaignId, payload) {
  return request(`/campaigns/${campaignId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export async function deleteCampaign(campaignId) {
  return request(`/campaigns/${campaignId}`, {
    method: 'DELETE',
  })
}

export async function fetchSequenceTemplates(companyId) {
  return request(`/companies/${companyId}/sequence-templates`)
}

export async function createSequenceTemplate(companyId, payload) {
  return request(`/companies/${companyId}/sequence-templates`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function deleteSequenceTemplate(companyId, templateId) {
  return request(`/companies/${companyId}/sequence-templates/${templateId}`, {
    method: 'DELETE',
  })
}

export async function fetchCampaignProspects(campaignId, { compact = true } = {}) {
  const qs = compact ? '?compact=1' : ''
  return request(`/campaigns/${campaignId}/prospects${qs}`)
}

export async function createCampaignProspect(campaignId, payload) {
  return request(`/campaigns/${campaignId}/prospects`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

/** Secuencia individual: guarda el prospecto (kickoff al Iniciar secuencia). */
export async function startIndividualProspectSequence(companyId, payload) {
  const controller = new AbortController()
  // Solo guarda (o arranca enrich en background si la secuencia ya está activa).
  const timer = setTimeout(() => controller.abort(), 60_000)
  try {
    return await request(`/companies/${companyId}/prospects/start-individual`, {
      method: 'POST',
      body: JSON.stringify(payload),
      signal: controller.signal,
    })
  } catch (err) {
    if (err instanceof Error && err.name === 'AbortError') {
      throw new Error(
        'No se pudo guardar el prospecto a tiempo. Revisá que el backend esté activo e intentá de nuevo.',
      )
    }
    throw err
  } finally {
    clearTimeout(timer)
  }
}

export async function bulkCreateCampaignProspects(campaignId, prospects) {
  return request(`/campaigns/${campaignId}/prospects/bulk`, {
    method: 'POST',
    body: JSON.stringify({ prospects }),
  })
}

export async function simulateCampaignProspects(campaignId, body = {}) {
  const payload =
    body && typeof body === 'object' && Object.keys(body).length > 0 ? body : {}
  return request(`/campaigns/${campaignId}/prospects/simulate`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function patchProspect(prospectId, payload) {
  return request(`/prospects/${prospectId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export async function deleteProspect(prospectId) {
  return request(`/prospects/${prospectId}`, {
    method: 'DELETE',
  })
}

export async function fetchCampaignOutreach(campaignId) {
  return request(`/campaigns/${campaignId}/outreach`)
}

export async function startCampaignOutreach(campaignId, { timeoutMs = 180000 } = {}) {
  return fetchWithTimeout(
    `/campaigns/${campaignId}/outreach/start`,
    { method: 'POST' },
    timeoutMs,
  )
}

export async function stopCampaignOutreach(campaignId) {
  return request(`/campaigns/${campaignId}/outreach/stop`, {
    method: 'POST',
  })
}

/** Omite un canal bloqueado (extensión/Gmail) y sigue con el resto del plan. Requiere confirm=true. */
export async function continueCampaignWithoutChannel(campaignId, channel, { confirm = true } = {}) {
  return request(`/campaigns/${campaignId}/outreach/continue-without-channel`, {
    method: 'POST',
    body: JSON.stringify({ channel, confirm: Boolean(confirm) }),
  })
}

export async function activateCampaignAutopilot(campaignId) {
  return request(`/campaigns/${campaignId}/autopilot/activate`, {
    method: 'POST',
  })
}

export async function pauseCampaignAutopilot(campaignId) {
  return request(`/campaigns/${campaignId}/autopilot/pause`, {
    method: 'POST',
  })
}

export async function runCampaignAutopilotCycle(campaignId) {
  return request(`/campaigns/${campaignId}/autopilot/run-cycle`, {
    method: 'POST',
  })
}

export async function analyzeCampaignIcp(campaignId) {
  return request(`/campaigns/${campaignId}/analyze-icp`, {
    method: 'POST',
  })
}

export async function fetchLinkedInAssistedSummary(campaignId) {
  return request(`/campaigns/${campaignId}/linkedin-assisted/summary`)
}

export async function fetchLinkedInAssistQueue(campaignId) {
  return request(`/campaigns/${campaignId}/linkedin-assisted/queue`)
}

export async function fetchWhatsAppAssistQueue(campaignId) {
  return request(`/campaigns/${campaignId}/whatsapp-assisted/queue`)
}

export async function fetchMailQueue(campaignId) {
  return request(`/campaigns/${campaignId}/mail-queue`)
}

export async function beginWhatsAppAssistedSession(prospectId) {
  return request(`/prospects/${prospectId}/whatsapp-assisted/assist`, {
    method: 'POST',
  })
}

export async function abandonWhatsAppAssistedSession(prospectId) {
  return request(`/prospects/${prospectId}/whatsapp-assisted/abandon`, {
    method: 'POST',
  })
}

export async function markWhatsAppAssistedSent(prospectId) {
  return request(`/prospects/${prospectId}/whatsapp-assisted/mark-sent`, {
    method: 'POST',
  })
}

/** Prospectos en checking / invite_*: la extensión (o Nexus) verifica 1º grado sola. */
export async function fetchLinkedInPendingConnectChecks(companyId) {
  return request(`/companies/${companyId}/linkedin-assisted/pending-connect-checks`)
}

export async function fetchWithTimeout(path, options = {}, timeoutMs = 20000) {
  const url = resolveApiUrl(path)
  if (import.meta.env.DEV && String(path).includes('lead-sourcing')) {
    console.log('[Lead Sourcing] fetch →', url, `(timeout ${timeoutMs}ms, backend ${API_EFFECTIVE_TARGET})`)
  }
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    return await request(path, { ...options, signal: controller.signal })
  } catch (err) {
    if (err instanceof Error && err.name === 'AbortError') {
      const startPath = String(path || '').includes('/outreach/start')
      throw new Error(
        startPath
          ? 'La campaña sigue arrancando en el servidor. Nexus busca e importa prospectos; no hace falta tocar de nuevo.'
          : `Esto está tardando más de lo habitual (${Math.round(timeoutMs / 1000)}s). Recargá en unos segundos; si ya inició, no hace falta repetir.`,
      )
    }
    throw err
  } finally {
    clearTimeout(timer)
  }
}

export async function fetchLeadSourcingStatus({ timeoutMs = 10000 } = {}) {
  return fetchWithTimeout('/lead-sourcing/status', {}, timeoutMs)
}

export async function fetchLeadSourcingPipeline(campaignId, { timeoutMs = 15000 } = {}) {
  return fetchWithTimeout(`/campaigns/${campaignId}/lead-sourcing/pipeline`, {}, timeoutMs)
}

export async function runLeadSourcingPipeline(campaignId, body, { timeoutMs = 120000 } = {}) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    return await request(`/campaigns/${campaignId}/lead-sourcing/run`, {
      method: 'POST',
      body: JSON.stringify(body),
      signal: controller.signal,
    })
  } catch (err) {
    if (err instanceof Error && err.name === 'AbortError') {
      const step = body?.step || 'pipeline'
      throw new Error(
        `Timeout: el paso «${step}» tardó más de ${Math.round(timeoutMs / 1000)}s. Revisá logs abajo o reintentá.`,
      )
    }
    throw err
  } finally {
    clearTimeout(timer)
  }
}

export async function importCampaignLeads(campaignId, externalIds) {
  return request(`/campaigns/${campaignId}/lead-sourcing/import`, {
    method: 'POST',
    body: JSON.stringify({ external_ids: externalIds }),
  })
}

export async function generateReadyOutreachDrafts(campaignId, { timeoutMs = 300000 } = {}) {
  return fetchWithTimeout(
    `/campaigns/${campaignId}/lead-sourcing/outreach/generate-ready`,
    { method: 'POST', body: JSON.stringify({}) },
    timeoutMs,
  )
}

export async function generateLeadProfileOutreach(
  campaignId,
  externalId,
  { regenerate = false, timeoutMs = 180000 } = {},
) {
  const path = `/campaigns/${campaignId}/lead-sourcing/profiles/${encodeURIComponent(externalId)}/outreach/generate`
  if (import.meta.env.DEV) {
    console.info('[Outreach] POST generate', { path, externalId, regenerate })
  }
  const result = await fetchWithTimeout(
    path,
    { method: 'POST', body: JSON.stringify({ regenerate }) },
    timeoutMs,
  )
  if (import.meta.env.DEV) {
    console.info('[Outreach] generate response', {
      ok: result?.ok,
      message: result?.message,
      detail: result?.detail,
      touch: result?.touch?.channel,
      testing: result?.testing,
      openai_configured: result?.openai_configured,
    })
  }
  return result
}

export async function generateLeadProfileOutreachTest(
  campaignId,
  externalId,
  channel,
  { timeoutMs = 180000 } = {},
) {
  const path = `/campaigns/${campaignId}/lead-sourcing/profiles/${encodeURIComponent(externalId)}/outreach/test-generate`
  return fetchWithTimeout(
    path,
    { method: 'POST', body: JSON.stringify({ channel }) },
    timeoutMs,
  )
}

/** Genera los 7 toques del playbook en cadena (modo testing, no modifica estado). */
export async function generateLeadProfilePlaybookPreview(
  campaignId,
  externalId,
  { timeoutMs = 900000 } = {},
) {
  const path = `/campaigns/${campaignId}/lead-sourcing/profiles/${encodeURIComponent(externalId)}/outreach/test-playbook-preview`
  return fetchWithTimeout(path, { method: 'POST', body: JSON.stringify({}) }, timeoutMs)
}

export async function resetLeadProfileOutreachSequence(campaignId, externalId) {
  return request(
    `/campaigns/${campaignId}/lead-sourcing/profiles/${encodeURIComponent(externalId)}/outreach/reset`,
    { method: 'POST' },
  )
}

export async function editLeadProfileOutreach(campaignId, externalId, payload) {
  return request(
    `/campaigns/${campaignId}/lead-sourcing/profiles/${encodeURIComponent(externalId)}/outreach`,
    {
      method: 'PATCH',
      body: JSON.stringify(payload),
    },
  )
}

export async function prepareLinkedInAssistedMessage(prospectId) {
  return request(`/prospects/${prospectId}/linkedin-assisted/prepare`, {
    method: 'POST',
  })
}

export async function reactivateProspectSequence(prospectId) {
  return request(`/prospects/${prospectId}/sequence/reactivate`, {
    method: 'POST',
  })
}

export async function pauseProspectSequence(prospectId) {
  return request(`/prospects/${prospectId}/sequence/pause`, {
    method: 'POST',
  })
}

export async function resumeProspectSequence(prospectId) {
  return request(`/prospects/${prospectId}/sequence/resume`, {
    method: 'POST',
  })
}

export async function beginLinkedInAssistedSession(prospectId) {
  return request(`/prospects/${prospectId}/linkedin-assisted/assist`, {
    method: 'POST',
  })
}

export async function abandonLinkedInAssistedSession(prospectId) {
  return request(`/prospects/${prospectId}/linkedin-assisted/abandon`, {
    method: 'POST',
  })
}

export async function markLinkedInAssistedSent(prospectId) {
  return request(`/prospects/${prospectId}/linkedin-assisted/mark-sent`, {
    method: 'POST',
  })
}

export async function markLinkedInConnectSent(prospectId) {
  return request(`/prospects/${prospectId}/linkedin-assisted/mark-connect-sent`, {
    method: 'POST',
  })
}

export async function reportLinkedInConnectionStatus(prospectId, status) {
  return request(`/prospects/${prospectId}/linkedin-connection-status`, {
    method: 'POST',
    body: JSON.stringify({ status }),
  })
}

export async function regenerateLinkedInAssistedReply(prospectId) {
  return request(`/prospects/${prospectId}/linkedin-assisted/regenerate-reply`, {
    method: 'POST',
  })
}

export async function registerLinkedInInbound(prospectId, body) {
  return request(`/prospects/${prospectId}/linkedin-inbound`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function simulateProspectResponse(prospectId) {
  return request(`/prospects/${prospectId}/simulate-response`, {
    method: 'POST',
  })
}

/** Simula respuestas para toda la campaña en el servidor (evita muchos fetch paralelos y Failed to fetch). */
export async function simulateCampaignResponsesBatch(campaignId) {
  return request(`/campaigns/${campaignId}/outreach/simulate-responses`, {
    method: 'POST',
  })
}

export async function fetchProspectConversation(prospectId) {
  return request(`/prospects/${prospectId}/conversation`)
}

export async function fetchProspectConversationWorkspace(prospectId, { includeTesting = true } = {}) {
  const qs = includeTesting ? '?include_testing=true' : '?include_testing=false'
  return request(`/prospects/${prospectId}/conversation-workspace${qs}`)
}

export async function generateNextProspectReply(prospectId) {
  return request(`/prospects/${prospectId}/generate-next-reply`, {
    method: 'POST',
  })
}

export async function generateProspectFollowupNow(prospectId) {
  return request(`/prospects/${prospectId}/outreach/generate-followup-now`, {
    method: 'POST',
  })
}

export async function sendProspectFollowupSimulated(prospectId) {
  return request(`/prospects/${prospectId}/outreach/send-followup-simulated`, {
    method: 'POST',
  })
}

export async function markProspectFollowupSent(prospectId) {
  return request(`/prospects/${prospectId}/outreach/mark-followup-sent`, {
    method: 'POST',
  })
}

export async function reprogramProspectFollowup(prospectId, days = 3) {
  return request(`/prospects/${prospectId}/outreach/reprogram-followup`, {
    method: 'POST',
    body: JSON.stringify({ days }),
  })
}

export async function reanalyzeProspectState(prospectId) {
  return request(`/prospects/${prospectId}/reanalyze-state`, {
    method: 'POST',
  })
}

export async function fetchCompanyMeetings(companyId, { includeCanceled = false } = {}) {
  const qs = includeCanceled ? '?include_canceled=true' : ''
  return request(`/companies/${companyId}/meetings${qs}`)
}

export async function fetchCampaignMeetings(campaignId) {
  return request(`/campaigns/${campaignId}/meetings`)
}

export async function createCampaignMeeting(campaignId, payload) {
  return request(`/campaigns/${campaignId}/meetings`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function updateCompanyMeeting(companyId, meetingId, payload) {
  return request(`/companies/${companyId}/meetings/${meetingId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export async function acceptProspectMeetingSuggestion(prospectId) {
  return request(`/prospects/${prospectId}/meetings/accept-suggestion`, {
    method: 'POST',
  })
}

/** Tareas de outreach por empresa (mock de “cron”: hasta que exista scheduler real). */
export async function fetchCompanyOutreachTasks(
  companyId,
  { status, campaignId, limit } = {},
) {
  const params = new URLSearchParams()
  if (status) {
    params.set('status', status)
  }
  if (campaignId != null && campaignId !== '') {
    params.set('campaign_id', String(campaignId))
  }
  if (limit != null && limit !== '') {
    params.set('limit', String(limit))
  }
  const qs = params.toString() ? `?${params.toString()}` : ''
  return request(`/companies/${companyId}/outreach-tasks${qs}`)
}

/** Ejecuta follow-ups IA cuya fecha `due_at` ya venció (simula worker programado). */
export async function runScheduledCampaignFollowups(campaignId) {
  return request(`/campaigns/${campaignId}/outreach/run-scheduled-followups`, {
    method: 'POST',
  })
}

/** Centro de operaciones — observabilidad y control. */
export async function fetchOperationsOverview(companyId) {
  return request(`/companies/${companyId}/operations/overview`)
}

export async function fetchOperationsActivityFeed(companyId, limit = 50) {
  return request(`/companies/${companyId}/operations/activity-feed?limit=${limit}`)
}

export async function fetchProspectAiTimeline(companyId, prospectId, limit = 80) {
  return request(`/companies/${companyId}/prospects/${prospectId}/ai-timeline?limit=${limit}`)
}

export async function postEmergencyStop(companyId, stop = true) {
  return request(`/companies/${companyId}/operations/emergency-stop`, {
    method: 'POST',
    body: JSON.stringify({ stop }),
  })
}

export async function patchCampaignAutomationMode(companyId, campaignId, mode) {
  return request(`/companies/${companyId}/campaigns/${campaignId}/automation-mode`, {
    method: 'PATCH',
    body: JSON.stringify({ mode }),
  })
}

export async function patchProspectAiPause(companyId, prospectId, paused) {
  return request(`/companies/${companyId}/prospects/${prospectId}/ai-pause`, {
    method: 'PATCH',
    body: JSON.stringify({ paused }),
  })
}

export async function retryOutreachTask(companyId, taskId) {
  return request(`/companies/${companyId}/outreach-tasks/${taskId}/retry`, {
    method: 'POST',
  })
}

/** Conexiones por usuario (Fase 1: mock, sin OAuth real). */
export async function fetchUserConnections(companyId, userId) {
  return request(`/users/${userId}/connections?company_id=${companyId}`)
}

export async function fetchGoogleIntegrationVerify(companyId, userId, { deep = true } = {}) {
  const qs = new URLSearchParams({
    company_id: String(companyId),
    deep: deep ? 'true' : 'false',
  })
  return request(`/users/${userId}/integrations/google/verify?${qs.toString()}`)
}

export async function fetchWhatsAppIntegrationVerify(companyId, userId, { deep = true } = {}) {
  const qs = new URLSearchParams({
    company_id: String(companyId),
    deep: deep ? 'true' : 'false',
  })
  return request(`/users/${userId}/integrations/whatsapp/verify?${qs.toString()}`)
}

export async function fetchHubSpotIntegrationVerify(companyId, { deep = true } = {}) {
  const qs = new URLSearchParams({ deep: deep ? 'true' : 'false' })
  return request(`/companies/${companyId}/integrations/hubspot/verify?${qs.toString()}`)
}

export async function fetchSalesforceIntegrationVerify(companyId, { deep = true } = {}) {
  const qs = new URLSearchParams({ deep: deep ? 'true' : 'false' })
  return request(`/companies/${companyId}/integrations/salesforce/verify?${qs.toString()}`)
}

export async function fetchHubSpotOAuthStartUrl(companyId, userId) {
  const qs = new URLSearchParams({
    company_id: String(companyId),
    user_id: String(userId),
  })
  return request(`/auth/hubspot/start-url?${qs.toString()}`)
}

export async function fetchSalesforceOAuthStartUrl(companyId, userId) {
  const qs = new URLSearchParams({
    company_id: String(companyId),
    user_id: String(userId),
  })
  return request(`/auth/salesforce/start-url?${qs.toString()}`)
}

export async function disconnectHubSpotIntegration(companyId) {
  return request(`/companies/${companyId}/integrations/hubspot/disconnect`, { method: 'POST' })
}

export async function disconnectSalesforceIntegration(companyId) {
  return request(`/companies/${companyId}/integrations/salesforce/disconnect`, { method: 'POST' })
}

export async function fetchCrmSyncStatus(companyId) {
  return request(`/companies/${companyId}/integrations/crm/sync-status`)
}

export async function retryCrmSync(companyId) {
  return request(`/companies/${companyId}/integrations/crm/retry`, { method: 'POST' })
}

export async function fetchCrmExclusions(companyId) {
  return request(`/companies/${companyId}/integrations/crm/exclusions`)
}

export async function syncCrmExclusions(companyId, provider) {
  const qs = new URLSearchParams()
  if (provider) qs.set('provider', provider)
  const suffix = qs.toString() ? `?${qs.toString()}` : ''
  return request(`/companies/${companyId}/integrations/crm/exclusions/sync${suffix}`, {
    method: 'POST',
  })
}

export async function importCrmExclusions(companyId, { file, text } = {}) {
  const form = new FormData()
  if (file) form.append('file', file)
  if (text) form.append('text', text)
  const url = `${API_BASE_URL}/companies/${companyId}/integrations/crm/exclusions/import`
  let res
  try {
    res = await fetch(url, {
      method: 'POST',
      headers: authHeaders(),
      body: form,
    })
  } catch (err) {
    throw new Error(err instanceof Error ? err.message : 'Error de red')
  }
  if (!res.ok) {
    const { message } = await parseErrorResponse(res)
    throw new ApiRequestError(message, { status: res.status })
  }
  return res.json()
}

export async function clearCrmManualExclusions(companyId) {
  return request(`/companies/${companyId}/integrations/crm/exclusions/manual`, {
    method: 'DELETE',
  })
}

export async function mockConnectUserProvider(companyId, userId, provider, extra = null) {
  const p = encodeURIComponent(provider)
  const opts = { method: 'POST' }
  if (extra && typeof extra === 'object') {
    opts.body = JSON.stringify(extra)
  }
  return request(`/users/${userId}/connections/${p}/mock-connect?company_id=${companyId}`, opts)
}

export async function disconnectUserProvider(companyId, userId, provider) {
  const p = encodeURIComponent(provider)
  return request(`/users/${userId}/connections/${p}/disconnect?company_id=${companyId}`, {
    method: 'POST',
  })
}

export async function mockErrorUserProvider(companyId, userId, provider) {
  const p = encodeURIComponent(provider)
  return request(`/users/${userId}/connections/${p}/mock-error?company_id=${companyId}`, {
    method: 'POST',
  })
}

/** URL de consentimiento Google — requiere JWT (no usar window.location al endpoint /start). */
export async function fetchGoogleOAuthStartUrl(companyId, userId) {
  const qs = new URLSearchParams({
    company_id: String(companyId),
    user_id: String(userId),
  })
  return request(`/auth/google/start-url?${qs.toString()}`)
}

/** @deprecated Usar fetchGoogleOAuthStartUrl — /auth/google/start no recibe Bearer en navegación directa. */
export function getGoogleOAuthStartUrl(companyId, userId) {
  const qs = new URLSearchParams({
    company_id: String(companyId),
    user_id: String(userId),
  })
  return `${API_BASE_URL}/auth/google/start?${qs.toString()}`
}

export async function createGmailDraft(payload) {
  return request('/gmail/drafts', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

/** Envío real por Gmail API (users.messages.send). Requiere confirm_send: true en el payload. */
export async function sendGmailMessage(payload) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 120_000)
  try {
    return await request('/gmail/send', {
      method: 'POST',
      body: JSON.stringify(payload),
      signal: controller.signal,
    })
  } finally {
    clearTimeout(timer)
  }
}

export async function syncGmailInbound(payload) {
  return request('/gmail/sync-inbound', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

/** Sincroniza eventos reales de Google Calendar (primary) con prospectos por email asistente. */
export async function syncGoogleCalendar(payload) {
  return request('/google-calendar/sync', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function fetchAnalytics(companyId = 1) {
  return request(`/analytics?company_id=${companyId}`)
}

export async function fetchServerHealth() {
  return request('/health')
}

export async function fetchAutomationHealth() {
  return request('/health/automation')
}

export async function fetchOpenAIDiagnostics({ probe = false } = {}) {
  const qs = probe ? '?probe=true' : ''
  return request(`/health/openai${qs}`)
}

export async function fetchTestingResetAvailability(companyId) {
  return request(`/companies/${companyId}/dev/testing-reset-availability`)
}

export async function resetTestingData(companyId) {
  return request(`/companies/${companyId}/dev/reset-testing-data`, {
    method: 'POST',
  })
}