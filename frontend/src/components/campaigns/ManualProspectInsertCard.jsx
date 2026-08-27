import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { PageSection } from '../ui/PageSection.jsx'
import { ChannelEnrichCountdown } from './ChannelEnrichCountdown.jsx'
import {
  ApiRequestError,
  startIndividualProspectSequence,
  fetchCampaignProspects,
  fetchCampaigns,
} from '../../utils/api.js'
import { SequenceTemplatePicker } from './SequenceTemplatePicker.jsx'
import {
  isNexusLinkedInExtensionInstalled,
  probeLinkedInConnectionViaExtension,
  probeLinkedInPendingNowViaExtension,
} from '../../utils/linkedinAssistExtension.js'

/** Tras insert con LinkedIn: un sondeo inicial (+1 refuerzo corto por si el kickoff tarda). */
function scheduleLinkedInVerifyProbes({ profileUrl, prospectId }) {
  if (!isNexusLinkedInExtensionInstalled()) return
  const url = String(profileUrl || '').trim()
  const pid = Number(prospectId) || 0
  const run = () => {
    void probeLinkedInPendingNowViaExtension().catch(() => ({ ok: false }))
    if (url && pid) {
      void probeLinkedInConnectionViaExtension({
        profileUrl: url,
        prospectId: pid,
        connectionStatus: 'checking',
      }).catch(() => ({ ok: false }))
    }
  }
  run()
  // Un solo refuerzo: la extensión no reabre si ya abrió / ya resolvió.
  window.setTimeout(run, 4_000)
}

const EMPTY = {
  name: '',
  company_name: '',
  role: '',
  email: '',
  linkedin_url: '',
  phone: '',
}

function normText(v) {
  return String(v || '')
    .trim()
    .toLowerCase()
}

/**
 * Tras un 504 el insert suele haber funcionado: buscamos el prospecto recién guardado.
 */
async function findRecentlySavedIndividualProspect({
  companyId,
  name,
  email,
  linkedin_url,
  phone,
}) {
  const campaigns = await fetchCampaigns(companyId)
  const list = Array.isArray(campaigns) ? campaigns : []
  const individual = list.find((c) => {
    const n = String(c?.name || '')
    return n === 'Secuencias individuales' || n.startsWith('Nexus · Secuencias individuales')
  })
  if (!individual?.id) return null
  const rows = await fetchCampaignProspects(individual.id)
  const prospects = Array.isArray(rows) ? rows : []
  const wantName = normText(name)
  const wantEmail = normText(email)
  const wantLi = normText(linkedin_url).replace(/\/$/, '')
  const wantPhone = String(phone || '').replace(/\D+/g, '')
  const match = prospects.find((p) => {
    if (wantEmail && normText(p.email) === wantEmail) return true
    if (wantLi && normText(p.linkedin_url).replace(/\/$/, '') === wantLi) return true
    if (wantPhone) {
      const ph = String(p.phone || p.whatsapp || '').replace(/\D+/g, '')
      if (ph && (ph.endsWith(wantPhone) || wantPhone.endsWith(ph))) return true
    }
    if (wantName && normText(p.name) === wantName) return true
    return false
  })
  return match || null
}

/**
 * Inserta un prospecto en el contenedor de secuencias individuales.
 * Si faltan canales del plan, muestra countdown mientras busca en Prospeo.
 */
export function ManualProspectInsertCard({
  products = [],
  companyId,
  onDone,
}) {
  const productOptions = useMemo(
    () => (Array.isArray(products) ? products : []).filter((p) => p?.id != null),
    [products],
  )

  const [productId, setProductId] = useState(() =>
    productOptions[0]?.id != null ? String(productOptions[0].id) : '',
  )
  const [sequencePlan, setSequencePlan] = useState(null)
  const [followUpEnabled, setFollowUpEnabled] = useState('yes')
  const [followUpDays, setFollowUpDays] = useState('')
  const [form, setForm] = useState(EMPTY)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [ok, setOk] = useState(null)
  /** @type {[{prospectId:number, campaignId:number, deadlineAt?:string, maxSeconds:number, label:string, detail?:string}|null, function]} */
  const [enrichJob, setEnrichJob] = useState(null)
  const [enrichDone, setEnrichDone] = useState(false)
  const [enrichResult, setEnrichResult] = useState(null)

  useEffect(() => {
    if (!productId && productOptions[0]?.id != null) {
      setProductId(String(productOptions[0].id))
    }
  }, [productId, productOptions])

  useEffect(() => {
    if (!enrichJob?.prospectId || !enrichJob?.campaignId) return undefined
    let cancelled = false

    async function poll() {
      try {
        const rows = await fetchCampaignProspects(enrichJob.campaignId)
        if (cancelled) return
        const list = Array.isArray(rows) ? rows : []
        const row = list.find((p) => Number(p.id) === Number(enrichJob.prospectId))
        const status = String(row?.channel_enrich_status || '').toLowerCase()
        if (status && status !== 'searching') {
          setEnrichDone(true)
          setEnrichResult(
            row?.channel_enrich_message ||
              (status === 'done'
                ? 'Datos actualizados. Ya podés iniciar la secuencia.'
                : 'Búsqueda terminada. La secuencia usará los datos disponibles.'),
          )
          setEnrichJob(null)
          onDone?.({ prospect: row, channelEnrich: { status }, enrichFinished: true })
          window.setTimeout(() => {
            if (!cancelled) {
              setEnrichDone(false)
              setEnrichResult(null)
            }
          }, 8000)
          return
        }
      } catch {
        /* keep polling */
      }
    }

    void poll()
    const timer = window.setInterval(() => void poll(), 1500)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [enrichJob, onDone])

  function setField(key, value) {
    setForm((f) => ({ ...f, [key]: value }))
    setError(null)
    setOk(null)
  }

  function hasAtLeastOneChannel() {
    const email = form.email.trim()
    const linkedin = form.linkedin_url.trim()
    const phone = form.phone.trim()
    return Boolean(
      (email && email.includes('@')) ||
        (linkedin && linkedin.includes('linkedin.com')) ||
        phone,
    )
  }

  async function handleSubmit(ev) {
    ev.preventDefault()
    const name = form.name.trim()
    const company_name = form.company_name.trim()
    const email = form.email.trim()
    const linkedin_url = form.linkedin_url.trim()
    if (!hasAtLeastOneChannel()) {
      setError('Indicá al menos un canal: email, LinkedIn o teléfono/WhatsApp.')
      return
    }
    const pid = Number(productId)
    if (!Number.isFinite(pid) || pid < 1) {
      setError('Elegí qué producto le vamos a vender.')
      return
    }
    if (!sequencePlan || !Array.isArray(sequencePlan.steps) || sequencePlan.steps.length < 1) {
      setError('Elegí o armá una secuencia de contacto.')
      return
    }
    if (!companyId) {
      setError('Sin empresa seleccionada.')
      return
    }

    let followup_delay_days = null
    if (followUpEnabled === 'yes' && followUpDays.trim()) {
      const n = Number(followUpDays)
      if (!Number.isFinite(n) || n < 1 || n > 365) {
        setError('Días hasta el follow-up: entre 1 y 365, o vacío para 30.')
        return
      }
      followup_delay_days = n
    }

    const plan = {
      ...sequencePlan,
      follow_up: {
        ...(sequencePlan.follow_up || {}),
        enabled: followUpEnabled === 'yes',
        channel:
          followUpEnabled === 'yes'
            ? sequencePlan.follow_up?.channel || 'auto'
            : sequencePlan.follow_up?.channel || 'auto',
      },
    }

    setBusy(true)
    setError(null)
    setOk(null)
    setEnrichJob(null)
    setEnrichDone(false)
    setEnrichResult(null)
    try {
      const result = await startIndividualProspectSequence(companyId, {
        product_id: pid,
        sequence_plan: plan,
        post_sequence_followup_enabled: followUpEnabled === 'yes',
        followup_delay_days: followUpEnabled === 'yes' ? followup_delay_days : null,
        name: name || null,
        company_name: company_name || '—',
        role: form.role.trim() || null,
        email: email || null,
        linkedin_url: linkedin_url || null,
        phone: form.phone.trim() || null,
        whatsapp: form.phone.trim() || null,
        source_provider: 'manual',
        notes: 'Carga manual — secuencia individual (fuera de campaña).',
      })
      const prospect = result?.prospect
      const enrich = result?.channel_enrich
      const savedLi =
        linkedin_url ||
        String(prospect?.linkedin_url || '').trim() ||
        ''
      const savedPid = Number(result?.prospect_id || prospect?.id) || 0
      if (savedLi && /linkedin\.com/i.test(savedLi)) {
        scheduleLinkedInVerifyProbes({ profileUrl: savedLi, prospectId: savedPid })
      }
      setForm(EMPTY)
      setError(null)
      if (enrich?.enriching || enrich?.status === 'searching') {
        const missing = Array.isArray(enrich?.missing) ? enrich.missing : []
        const labels = missing
          .map((m) => (m === 'phone' ? 'WhatsApp' : m === 'email' ? 'email' : m === 'linkedin' ? 'LinkedIn' : m))
          .filter(Boolean)
        setEnrichJob({
          prospectId: Number(result.prospect_id || prospect?.id),
          campaignId: Number(result.campaign_id || prospect?.campaign_id),
          deadlineAt: enrich.deadline_at || null,
          maxSeconds: 120,
          label: labels.length
            ? `Buscando información de canales (${labels.join(', ')})`
            : 'Buscando información de canales',
          detail: 'Nexus consulta Prospeo. La secuencia arranca al terminar (hasta ~2 min).',
        })
        setOk({
          text: 'Prospecto guardado. La secuencia está activa: buscando datos faltantes…',
          prospectId: result?.prospect_id || prospect?.id,
          campaignId: Number(result.campaign_id || prospect?.campaign_id) || null,
        })
      } else {
        setOk({
          text:
            result?.message ||
            'Prospecto guardado. Iniciá la secuencia para buscar datos faltantes y arrancar.',
          prospectId: result?.prospect_id || prospect?.id,
          campaignId: Number(result?.campaign_id || prospect?.campaign_id) || null,
        })
      }
      onDone?.({ prospect, channelEnrich: enrich, result })
    } catch (e) {
      // 504 / timeout: el prospecto suele haberse guardado igual (kickoff en background).
      const status = e instanceof ApiRequestError ? Number(e.status) : 0
      const msg = e instanceof Error ? e.message : String(e)
      const maybeSaved =
        status === 504 ||
        /sigue en curso|gateway timeout|tardó demasiado|timeout/i.test(msg)
      if (maybeSaved && companyId) {
        try {
          const found = await findRecentlySavedIndividualProspect({
            companyId,
            name,
            email,
            linkedin_url,
            phone: form.phone.trim(),
          })
          if (found) {
            setForm(EMPTY)
            setError(null)
            setOk({
              text: 'Prospecto guardado. La secuencia ya está en curso (el servidor tardó en responder).',
              prospectId: found.id,
              campaignId: Number(found.campaign_id) || null,
            })
            const foundLi = String(found.linkedin_url || linkedin_url || '').trim()
            if (foundLi && /linkedin\.com/i.test(foundLi)) {
              scheduleLinkedInVerifyProbes({
                profileUrl: foundLi,
                prospectId: Number(found.id),
              })
            }
            onDone?.({ prospect: found, channelEnrich: null, result: { prospect: found } })
            return
          }
        } catch {
          /* caer al error normal */
        }
      }
      setError(msg)
    } finally {
      setBusy(false)
    }
  }

  if (productOptions.length === 0) {
    return (
      <PageSection
        title="Insertar prospecto"
        description="Secuencia individual: insertá contactos acá."
        defaultOpen={false}
        className="mb-2"
      >
        <p className="text-sm text-nx-muted">Creá un producto primero para poder venderle algo.</p>
      </PageSection>
    )
  }

  return (
    <PageSection
      title="Insertar prospecto"
      description="Producto/servicio + secuencia + al menos un canal (email, LinkedIn o WhatsApp). El resto se busca al iniciar."
      defaultOpen={false}
      className="mb-2"
    >
      <form className="space-y-4" onSubmit={(e) => void handleSubmit(e)}>
        <div>
          <label className="text-xs font-medium text-nx-muted" htmlFor="manual-prospect-product">
            ¿Qué producto le vamos a vender? *
          </label>
          <select
            id="manual-prospect-product"
            className="mt-1 w-full rounded-md border border-nx-border bg-white px-2.5 py-1.5 text-sm"
            value={productId || String(productOptions[0]?.id ?? '')}
            onChange={(e) => setProductId(e.target.value)}
            disabled={busy || Boolean(enrichJob)}
            required
          >
            {productOptions.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </div>

        <div className="space-y-3 rounded-lg border border-nx-border bg-nx-card-muted/30 px-3 py-3">
          <div>
            <p className="text-xs font-medium text-nx-ink">
              ¿Generar follow-up después de la secuencia?
            </p>
            <div className="mt-2 flex flex-wrap gap-4">
              <label className="flex cursor-pointer items-center gap-2 text-sm text-nx-ink">
                <input
                  type="radio"
                  name="manual-post-sequence-followup"
                  checked={followUpEnabled === 'yes'}
                  onChange={() => setFollowUpEnabled('yes')}
                  disabled={busy || Boolean(enrichJob)}
                />
                Sí
              </label>
              <label className="flex cursor-pointer items-center gap-2 text-sm text-nx-ink">
                <input
                  type="radio"
                  name="manual-post-sequence-followup"
                  checked={followUpEnabled === 'no'}
                  onChange={() => setFollowUpEnabled('no')}
                  disabled={busy || Boolean(enrichJob)}
                />
                No
              </label>
            </div>
          </div>
          {followUpEnabled === 'yes' ? (
            <div>
              <label className="text-xs font-medium text-nx-muted" htmlFor="manual-followup-days">
                Días hasta el follow-up (vacío = 30)
              </label>
              <input
                id="manual-followup-days"
                type="number"
                min={1}
                max={365}
                className="mt-1 w-full rounded-md border border-nx-border bg-white px-2.5 py-1.5 text-sm"
                value={followUpDays}
                onChange={(e) => setFollowUpDays(e.target.value)}
                disabled={busy || Boolean(enrichJob)}
                placeholder="30"
              />
            </div>
          ) : null}
        </div>

        <SequenceTemplatePicker
          companyId={companyId}
          value={sequencePlan}
          onChange={setSequencePlan}
          campaignFollowupEnabled={followUpEnabled === 'yes'}
        />

        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label className="text-xs font-medium text-nx-muted" htmlFor="manual-prospect-name">
              Nombre completo (opcional)
            </label>
            <input
              id="manual-prospect-name"
              className="mt-1 w-full rounded-lg border border-nx-border px-3 py-2 text-sm"
              value={form.name}
              onChange={(e) => setField('name', e.target.value)}
              placeholder="Si no lo tenés, con un canal alcanza"
              disabled={busy || Boolean(enrichJob)}
            />
          </div>
          <div>
            <label className="text-xs font-medium text-nx-muted" htmlFor="manual-prospect-company">
              Empresa (opcional)
            </label>
            <input
              id="manual-prospect-company"
              className="mt-1 w-full rounded-lg border border-nx-border px-3 py-2 text-sm"
              value={form.company_name}
              onChange={(e) => setField('company_name', e.target.value)}
              placeholder="Ej. Acme SA"
              disabled={busy || Boolean(enrichJob)}
            />
          </div>
          <div>
            <label className="text-xs font-medium text-nx-muted" htmlFor="manual-prospect-role">
              Rol / cargo (opcional)
            </label>
            <input
              id="manual-prospect-role"
              className="mt-1 w-full rounded-lg border border-nx-border px-3 py-2 text-sm"
              value={form.role}
              onChange={(e) => setField('role', e.target.value)}
              placeholder="Ej. Head of Sales"
              disabled={busy || Boolean(enrichJob)}
            />
          </div>
          <div>
            <label className="text-xs font-medium text-nx-muted" htmlFor="manual-prospect-email">
              Email
            </label>
            <input
              id="manual-prospect-email"
              type="email"
              className="mt-1 w-full rounded-lg border border-nx-border px-3 py-2 text-sm"
              value={form.email}
              onChange={(e) => setField('email', e.target.value)}
              placeholder="nombre@empresa.com"
              disabled={busy || Boolean(enrichJob)}
            />
          </div>
          <div className="sm:col-span-2">
            <label className="text-xs font-medium text-nx-muted" htmlFor="manual-prospect-linkedin">
              LinkedIn URL
            </label>
            <input
              id="manual-prospect-linkedin"
              className="mt-1 w-full rounded-lg border border-nx-border px-3 py-2 text-sm"
              value={form.linkedin_url}
              onChange={(e) => setField('linkedin_url', e.target.value)}
              placeholder="https://www.linkedin.com/in/…"
              disabled={busy || Boolean(enrichJob)}
            />
          </div>
          <div>
            <label className="text-xs font-medium text-nx-muted" htmlFor="manual-prospect-phone">
              Teléfono / WhatsApp
            </label>
            <input
              id="manual-prospect-phone"
              className="mt-1 w-full rounded-lg border border-nx-border px-3 py-2 text-sm"
              value={form.phone}
              onChange={(e) => setField('phone', e.target.value)}
              placeholder="+54 9 11 …"
              disabled={busy || Boolean(enrichJob)}
            />
          </div>
        </div>
        <p className="text-xs text-nx-muted">
          Con un solo canal alcanza (solo email, solo WhatsApp o solo LinkedIn). Buscamos el resto al
          iniciar la secuencia.
        </p>

        {error ? (
          <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800">{error}</p>
        ) : null}

        <ChannelEnrichCountdown
          active={Boolean(enrichJob)}
          done={enrichDone}
          label={enrichJob?.label || 'Buscando datos faltantes…'}
          detail={enrichJob?.detail}
          deadlineAt={enrichJob?.deadlineAt}
          maxSeconds={120}
          resultText={enrichResult}
        />

        {ok ? (
          <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-900">
            <p className="font-medium">{ok.text}</p>
            {ok.prospectId && ok.campaignId ? (
              <Link
                className="mt-2 inline-block font-semibold text-nx-brand hover:underline"
                to={`/campanas/${ok.campaignId}`}
              >
                Ver en campaña →
              </Link>
            ) : null}
          </div>
        ) : null}

        <button
          type="submit"
          disabled={busy || !sequencePlan || Boolean(enrichJob)}
          className="nx-btn nx-btn-primary px-4 py-2 text-sm disabled:opacity-50"
        >
          {busy ? 'Guardando…' : enrichJob ? 'Buscando datos…' : 'Guardar prospecto'}
        </button>
      </form>
    </PageSection>
  )
}
