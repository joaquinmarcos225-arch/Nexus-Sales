import { API_BASE_URL, API_EFFECTIVE_TARGET, resolveApiUrl } from './constants.js'
import { getStoredToken } from '../utils/authStorage.js'

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
  let message = res.statusText || 'Error de red'
  if (typeof detail === 'string') {
    message = detail
  } else if (Array.isArray(detail)) {
    message = detail.map((d) => d.msg || d).join(', ')
  } else if (detail && typeof detail === 'object') {
    message = detail.summary || detail.message || message
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
  let res
  try {
    res = await fetch(url, {
      headers: {
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        ...authHeaders(options.headers),
      },
      ...options,
    })
  } catch (err) {
    if (err instanceof Error && err.name === 'AbortError') {
      throw err
    }
    const hint =
      err instanceof TypeError
        ? ` No se pudo conectar al API (${API_BASE_URL || 'proxy dev'} → ${API_EFFECTIVE_TARGET}). Reiniciá Vite tras cambiar .env; probá VITE_DEV_PROXY=0 si querés CORS directo.`
        : ''
    throw new Error(
      err instanceof Error ? `${err.message}.${hint}` : `Error de red.${hint}`,
    )
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

export async function login(email, password) {
  return request('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
}

export async function fetchAuthMe() {
  return request('/auth/me')
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

export async function simulateProspectSequenceResponse(prospectId, body) {
  return request(`/prospects/${prospectId}/sequence/simulate-response`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function enrichProspect(prospectId) {
  return request(`/prospects/${prospectId}/enrich`, { method: 'POST' })
}

export async function startProspectSequence(prospectId) {
  return request(`/prospects/${prospectId}/sequence/start`, { method: 'POST' })
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

export async function topUpWallet(companyId, amount) {
  return request(`/companies/${companyId}/wallet/top-up`, {
    method: 'POST',
    body: JSON.stringify({ amount }),
  })
}

export async function fetchCreditAllocations(companyId) {
  return request(`/companies/${companyId}/credit-allocations`)
}

export async function assignSellerCredits(companyId, sellerId, amount) {
  return request(`/companies/${companyId}/credit-allocations`, {
    method: 'POST',
    body: JSON.stringify({ seller_id: sellerId, amount }),
  })
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

export async function fetchCampaignProspects(campaignId) {
  return request(`/campaigns/${campaignId}/prospects`)
}

export async function createCampaignProspect(campaignId, payload) {
  return request(`/campaigns/${campaignId}/prospects`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
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

export async function startCampaignOutreach(campaignId) {
  return request(`/campaigns/${campaignId}/outreach/start`, {
    method: 'POST',
  })
}

export async function stopCampaignOutreach(campaignId) {
  return request(`/campaigns/${campaignId}/outreach/stop`, {
    method: 'POST',
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
      throw new Error(
        `Timeout (${Math.round(timeoutMs / 1000)}s): el servidor no respondió. Revisá que el backend esté activo.`,
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

export async function fetchCompanyMeetings(companyId) {
  return request(`/companies/${companyId}/meetings`)
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

/** Instrucciones IA por empresa (prompt “Educación”). */
export async function fetchAIInstructions(companyId) {
  return request(`/companies/${companyId}/ai-instructions`)
}

export async function createAIInstruction(companyId, payload) {
  return request(`/companies/${companyId}/ai-instructions`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function updateAIInstruction(companyId, instructionId, payload) {
  return request(`/companies/${companyId}/ai-instructions/${instructionId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export async function deleteAIInstruction(companyId, instructionId) {
  return request(`/companies/${companyId}/ai-instructions/${instructionId}`, {
    method: 'DELETE',
  })
}

/** Política de comportamiento del SDR (Educación IA — panel estructurado). */
export async function fetchAIBehaviorPolicy(companyId) {
  return request(`/companies/${companyId}/ai-behavior-policy`)
}

export async function fetchAIBehaviorPolicyFields(companyId) {
  return request(`/companies/${companyId}/ai-behavior-policy/fields`)
}

export async function saveAIBehaviorPolicy(companyId, payload) {
  return request(`/companies/${companyId}/ai-behavior-policy`, {
    method: 'PUT',
    body: JSON.stringify(payload),
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

/** Chat interno Nexus (contexto empresa + stats). */
export async function assistantChat(payload) {
  return request('/assistant/chat', {
    method: 'POST',
    body: JSON.stringify(payload),
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

export async function mockConnectUserProvider(companyId, userId, provider) {
  const p = encodeURIComponent(provider)
  return request(`/users/${userId}/connections/${p}/mock-connect?company_id=${companyId}`, {
    method: 'POST',
  })
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
  return request('/gmail/send', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
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