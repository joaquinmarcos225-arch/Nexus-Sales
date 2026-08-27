import { useCallback, useEffect, useRef, useState } from 'react'
import {
  BTN_OUTLINE,
  BTN_SECONDARY,
} from './integrationUi.jsx'
import {
  clearCrmManualExclusions,
  fetchCrmExclusions,
  importCrmExclusions,
  syncCrmExclusions,
} from '../../utils/api.js'

function fmtSync(iso) {
  if (!iso) return 'aún no'
  try {
    return new Date(iso).toLocaleString('es-AR', {
      dateStyle: 'short',
      timeStyle: 'short',
    })
  } catch {
    return '—'
  }
}

/**
 * Exclusiones CRM: se sincronizan solas desde HubSpot/Salesforce.
 * CSV/pegar queda como fallback opcional.
 */
export function CrmExclusionsPanel({ companyId }) {
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)
  const [okMsg, setOkMsg] = useState(null)
  const [paste, setPaste] = useState('')
  const [showManual, setShowManual] = useState(false)
  const fileRef = useRef(null)

  const refresh = useCallback(async () => {
    if (!companyId) {
      setStatus(null)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const data = await fetchCrmExclusions(companyId)
      setStatus(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [companyId])

  useEffect(() => {
    void refresh()
  }, [refresh])

  async function handleSync() {
    if (!companyId) return
    setBusy('sync')
    setError(null)
    setOkMsg(null)
    try {
      const data = await syncCrmExclusions(companyId)
      setStatus(data.status)
      const parts = (data.results || []).map((r) =>
        r.ok
          ? `${r.provider}: +${r.inserted} (total ${r.total})`
          : `${r.provider}: error`,
      )
      setOkMsg(parts.length ? `Sync OK — ${parts.join(' · ')}` : 'Sync completado')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  async function handleImportFile(ev) {
    const file = ev.target.files?.[0]
    ev.target.value = ''
    if (!file || !companyId) return
    setBusy('import')
    setError(null)
    setOkMsg(null)
    try {
      const data = await importCrmExclusions(companyId, { file })
      setStatus(data.status)
      setOkMsg(
        `Importadas ${data.result?.inserted ?? 0} exclusiones (revisadas ${data.result?.total ?? 0})`,
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  async function handleImportPaste() {
    if (!companyId || !paste.trim()) return
    setBusy('paste')
    setError(null)
    setOkMsg(null)
    try {
      const data = await importCrmExclusions(companyId, { text: paste })
      setStatus(data.status)
      setOkMsg(
        `Importadas ${data.result?.inserted ?? 0} exclusiones (revisadas ${data.result?.total ?? 0})`,
      )
      setPaste('')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  async function handleClearManual() {
    const n = Number(status?.by_provider?.manual) || 0
    if (!companyId || !n) return
    if (
      !window.confirm(
        `¿Borrar las ${n} exclusiones cargadas a mano? No afecta HubSpot ni Salesforce.`,
      )
    ) {
      return
    }
    setBusy('clear')
    setError(null)
    setOkMsg(null)
    try {
      const data = await clearCrmManualExclusions(companyId)
      setStatus(data.status)
      setOkMsg(`Se borraron ${data.deleted ?? 0} exclusiones manuales`)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  const total = status?.total ?? 0
  const byType = status?.by_type || {}
  const byProvider = status?.by_provider || {}
  const manualCount = byProvider.manual || 0
  const crmConnected = Boolean(status?.hubspot_active || status?.salesforce_active)

  return (
    <section className="mt-8 rounded-xl border border-nx-border bg-white p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-nx-ink">Cuentas ya contactadas</h2>
          <p className="mt-1 max-w-2xl text-xs leading-relaxed text-nx-muted">
            Nexus sincroniza solo desde HubSpot / Salesforce (cada ~1 h) y no vuelve a prospectar
            esas cuentas. Las actividades de outreach también se empujan al CRM de la empresa.
          </p>
          <p className="mt-1 text-[11px] text-nx-muted">
            Última sync automática: {fmtSync(status?.last_synced_at)}
            {crmConnected ? '' : ' · conectá un CRM para llenar la lista'}
          </p>
        </div>
        <button
          type="button"
          className={BTN_SECONDARY}
          disabled={!companyId || loading || busy}
          onClick={() => void refresh()}
        >
          Actualizar
        </button>
      </div>

      {error ? <p className="mt-3 text-xs font-medium text-red-700">{error}</p> : null}
      {okMsg ? <p className="mt-3 text-xs font-medium text-emerald-700">{okMsg}</p> : null}

      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <div className="rounded-lg border border-nx-border bg-nx-card-muted/40 px-3 py-2">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-nx-muted">Total</p>
          <p className="mt-1 text-lg font-semibold text-nx-ink">{loading ? '…' : total}</p>
        </div>
        <div className="rounded-lg border border-nx-border bg-nx-card-muted/40 px-3 py-2">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-nx-muted">Por tipo</p>
          <p className="mt-1 text-xs text-nx-ink">
            email {byType.email || 0} · dominio {byType.domain || 0} · empresa{' '}
            {byType.company_name || 0}
          </p>
        </div>
        <div className="rounded-lg border border-nx-border bg-nx-card-muted/40 px-3 py-2">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-nx-muted">Origen</p>
          <p className="mt-1 text-xs text-nx-ink">
            HS {byProvider.hubspot || 0} · SF {byProvider.salesforce || 0} · manual{' '}
            {byProvider.manual || 0}
          </p>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <button
          type="button"
          className={BTN_OUTLINE}
          disabled={!companyId || busy || !crmConnected}
          onClick={() => void handleSync()}
          title={!crmConnected ? 'Conectá HubSpot o Salesforce' : 'Forzar sync ahora (también corre sola)'}
        >
          {busy === 'sync' ? 'Sincronizando…' : 'Sincronizar ahora'}
        </button>
        <button
          type="button"
          className={BTN_OUTLINE}
          disabled={!companyId || busy}
          onClick={() => setShowManual((v) => !v)}
        >
          {showManual ? 'Ocultar carga manual' : 'Carga manual (opcional)'}
        </button>
      </div>

      {showManual ? (
        <div className="mt-4 rounded-lg border border-dashed border-nx-border bg-nx-card-muted/20 p-4">
          <div className="flex flex-wrap gap-2">
            <input
              ref={fileRef}
              type="file"
              accept=".csv,.txt,text/csv,text/plain"
              className="hidden"
              onChange={(e) => void handleImportFile(e)}
            />
            <button
              type="button"
              className={BTN_OUTLINE}
              disabled={!companyId || busy}
              onClick={() => fileRef.current?.click()}
            >
              {busy === 'import' ? 'Importando…' : 'Subir CSV / TXT'}
            </button>
            <button
              type="button"
              className={BTN_OUTLINE}
              disabled={!companyId || busy || !manualCount}
              onClick={() => void handleClearManual()}
            >
              {busy === 'clear' ? 'Borrando…' : `Limpiar manuales (${manualCount})`}
            </button>
          </div>
          <p className="mt-2 text-[11px] text-nx-muted">
            CSV con columnas <code>email</code>, <code>domain</code>, <code>company</code> — o una
            entrada por línea.
          </p>
          <label className="mt-3 block text-xs font-medium text-nx-ink">
            Pegar lista
            <textarea
              className="nx-input mt-1.5 min-h-[88px] text-sm"
              placeholder={'cliente@empresa.com\nempresa.com\nAcme Corp'}
              value={paste}
              onChange={(e) => setPaste(e.target.value)}
              disabled={!!busy}
            />
          </label>
          <button
            type="button"
            className={`${BTN_OUTLINE} mt-2`}
            disabled={!companyId || busy || !paste.trim()}
            onClick={() => void handleImportPaste()}
          >
            {busy === 'paste' ? 'Importando…' : 'Agregar a la lista'}
          </button>
        </div>
      ) : null}
    </section>
  )
}
