import { useCallback, useEffect, useState } from 'react'
import { fetchCapacity, patchProviderBalance } from './api.js'

const SOURCE_LABEL = {
  api: 'API',
  manual: 'Manual',
  env: 'Env',
  unknown: 'Sin dato',
}

function money(value, digits = 2) {
  const amount = Number(value ?? 0)
  return `US$ ${amount.toLocaleString('es-AR', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`
}

function when(value) {
  if (!value) return '—'
  try {
    return new Date(value).toLocaleString('es-AR', {
      day: '2-digit',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return '—'
  }
}

export default function CapacityCalculatorPanel() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [grantInput, setGrantInput] = useState('600')
  const [openaiUsd, setOpenaiUsd] = useState('')
  const [braveUsd, setBraveUsd] = useState('')
  const [saving, setSaving] = useState(null)

  const load = useCallback(async ({ refresh = false, proposedGrant = null } = {}) => {
    if (refresh) setRefreshing(true)
    else setLoading((prev) => (data ? prev : true))
    try {
      const report = await fetchCapacity({ refresh, proposedGrant })
      setData(report)
      setError(null)
      const oai = report.providers?.find((p) => p.key === 'openai')
      const brave = report.providers?.find((p) => p.key === 'brave')
      if (oai?.balance_usd != null) setOpenaiUsd(String(oai.balance_usd))
      if (brave?.balance_usd != null) setBraveUsd(String(brave.balance_usd))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [data])

  useEffect(() => {
    void load({ refresh: true })
  }, [])

  async function runSimulation() {
    const n = parseInt(grantInput, 10)
    if (!Number.isFinite(n) || n <= 0) return
    await load({ refresh: false, proposedGrant: n })
  }

  async function saveBalance(provider) {
    const raw = provider === 'openai' ? openaiUsd : braveUsd
    const val = parseFloat(raw)
    if (!Number.isFinite(val) || val < 0) return
    setSaving(provider)
    try {
      const report = await patchProviderBalance(provider, val)
      setData(report)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(null)
    }
  }

  if (loading && !data) {
    return <div className="p-8 text-center text-sm text-rose-500">Cargando calculadora…</div>
  }

  const net = data?.net_headroom_sequences
  const gross = data?.gross_capacity_sequences
  const committed = data?.client_liability?.total_credits_committed ?? 0
  const reverse = data?.reverse_plan

  return (
    <div className="space-y-5 border-b border-rose-200 pb-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-sm font-bold text-rose-950">Calculadora de capacidad</h2>
          <p className="text-xs text-rose-700/70">
            Saldo proveedores → secuencias posibles → menos créditos ya acreditados a clientes.
          </p>
        </div>
        <button
          type="button"
          disabled={refreshing}
          onClick={() => void load({ refresh: true, proposedGrant: reverse?.proposed_grant || null })}
          className="rounded-lg border border-rose-300 bg-white px-3 py-1.5 text-xs font-semibold text-rose-900 hover:bg-rose-50 disabled:opacity-50"
        >
          {refreshing ? 'Actualizando…' : 'Actualizar saldos'}
        </button>
      </div>

      {error ? <p className="rounded-xl bg-red-700 px-4 py-2 text-sm text-white">{error}</p> : null}

      {(data?.warnings || []).map((w) => (
        <p key={w} className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          {w}
        </p>
      ))}

      <div className="grid gap-3 sm:grid-cols-3">
        <div className={`rounded-xl border bg-white p-4 shadow-sm ${net != null && net < 0 ? 'border-red-400' : 'border-emerald-300'}`}>
          <p className="text-[11px] font-semibold uppercase tracking-wide text-rose-600">Headroom neto</p>
          <p className={`mt-1 text-3xl font-bold tabular-nums ${net != null && net < 0 ? 'text-red-700' : 'text-emerald-800'}`}>
            {net != null ? net.toLocaleString('es-AR') : '—'}
          </p>
          <p className="mt-1 text-xs text-rose-700/75">secuencias que podés acreditar hoy</p>
        </div>
        <div className="rounded-xl border border-rose-200 bg-white p-4 shadow-sm">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-rose-600">Capacidad bruta</p>
          <p className="mt-1 text-2xl font-bold tabular-nums text-rose-950">{gross != null ? gross.toLocaleString('es-AR') : '—'}</p>
          <p className="mt-1 text-xs text-rose-700/75">
            cuello: {data?.bottleneck?.provider || '—'}
          </p>
        </div>
        <div className="rounded-xl border border-rose-200 bg-white p-4 shadow-sm">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-rose-600">Comprometido clientes</p>
          <p className="mt-1 text-2xl font-bold tabular-nums text-rose-950">{committed.toLocaleString('es-AR')}</p>
          <p className="mt-1 text-xs text-rose-700/75">
            {data?.client_liability?.companies_with_balance ?? 0} empresas con saldo
          </p>
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-3">
        {(data?.providers || []).map((p) => (
          <article
            key={p.key}
            className={`rounded-xl border bg-white p-4 shadow-sm ${p.bottleneck ? 'border-amber-400 ring-1 ring-amber-200' : 'border-rose-200'}`}
          >
            <div className="flex items-center justify-between gap-2">
              <h3 className="text-sm font-bold text-rose-950">{p.label}</h3>
              <span className="rounded-full bg-rose-100 px-2 py-0.5 text-[10px] font-semibold text-rose-800">
                {SOURCE_LABEL[p.source] || p.source}
              </span>
            </div>
            {p.bottleneck ? (
              <p className="mt-1 text-[10px] font-semibold uppercase text-amber-700">Cuello de botella</p>
            ) : null}
            <p className="mt-2 text-xs text-rose-800/75">
              {p.balance_credits != null ? `${p.balance_credits.toLocaleString('es-AR')} créditos · ` : ''}
              {p.balance_usd != null ? money(p.balance_usd) : 'Saldo sin cargar'}
            </p>
            <p className="mt-2 text-lg font-bold tabular-nums text-rose-950">
              {p.sequences_available != null ? `${p.sequences_available.toLocaleString('es-AR')} seq` : '—'}
            </p>
            <p className="mt-1 text-[10px] text-rose-400">Actualizado {when(p.updated_at)}</p>
            {(p.key === 'openai' || p.key === 'brave') && (
              <div className="mt-3 border-t border-rose-100 pt-3">
                <label className="block text-[10px] font-medium text-rose-600">
                  Saldo USD (manual)
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    value={p.key === 'openai' ? openaiUsd : braveUsd}
                    onChange={(e) => (p.key === 'openai' ? setOpenaiUsd(e.target.value) : setBraveUsd(e.target.value))}
                    className="mt-1 w-full rounded-lg border border-rose-200 px-2 py-1.5 text-xs"
                  />
                </label>
                <button
                  type="button"
                  disabled={saving === p.key}
                  onClick={() => void saveBalance(p.key)}
                  className="mt-2 w-full rounded-lg bg-rose-700 py-1.5 text-xs font-semibold text-white hover:bg-rose-800 disabled:opacity-50"
                >
                  {saving === p.key ? 'Guardando…' : 'Guardar saldo'}
                </button>
              </div>
            )}
          </article>
        ))}
      </div>

      <div className="rounded-xl border border-rose-200 bg-white p-4 shadow-sm">
        <h3 className="text-sm font-bold text-rose-950">Simulador: acreditar a un cliente</h3>
        <p className="mt-0.5 text-xs text-rose-700/70">
          Cuánto recargar en cada proveedor (COGS ~{money(data?.economics?.cogs_per_sequence_usd, 2)}/secuencia).
        </p>
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <label className="text-xs font-medium text-rose-700">
            Créditos a dar
            <input
              type="number"
              min="1"
              value={grantInput}
              onChange={(e) => setGrantInput(e.target.value)}
              className="ml-2 w-28 rounded-lg border border-rose-200 px-2 py-1.5 text-sm"
            />
          </label>
          <button
            type="button"
            onClick={() => void runSimulation()}
            className="rounded-lg bg-rose-600 px-4 py-2 text-xs font-semibold text-white hover:bg-rose-700"
          >
            Calcular top-up
          </button>
        </div>
        {reverse ? (
          <div className="mt-4 grid gap-2 text-xs sm:grid-cols-2">
            <p>
              Top-up Prospeo: <strong>{money(reverse.topup_usd.prospeo)}</strong>
            </p>
            <p>
              Top-up Brave: <strong>{money(reverse.topup_usd.brave)}</strong>
            </p>
            <p>
              Top-up OpenAI: <strong>{money(reverse.topup_usd.openai)}</strong>
            </p>
            <p>
              Total: <strong>{money(reverse.topup_usd.total)}</strong>
              {' · '}
              {reverse.feasible_with_current_balances ? (
                <span className="text-emerald-700">Alcanza con saldo actual</span>
              ) : (
                <span className="text-red-700">
                  Faltan ~{reverse.shortfall_sequences} secuencias de capacidad
                </span>
              )}
            </p>
          </div>
        ) : null}
      </div>

      {(data?.client_liability?.top_companies || []).length > 0 ? (
        <div className="rounded-xl border border-rose-200 bg-white p-4 shadow-sm">
          <h3 className="text-sm font-bold text-rose-950">Créditos en clientes (top)</h3>
          <ul className="mt-2 space-y-1 text-xs text-rose-800">
            {data.client_liability.top_companies.map((c) => (
              <li key={c.company_id} className="flex justify-between gap-2">
                <span>{c.company_name}</span>
                <span className="tabular-nums font-semibold">{c.available_credits} disp.</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}
