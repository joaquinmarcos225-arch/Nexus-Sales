import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useCompany } from '../context/CompanyContext.jsx'
import { useAuth } from '../context/AuthContext.jsx'
import { isCompanyAdmin } from '../data/navigation.js'
import { AlertBanner } from '../components/AlertBanner.jsx'
import { PageHeader } from '../layout/PageHeader'
import {
  fetchBillingOpsBoard,
  fetchCompanyBillingOps,
  grantBillingOpsCredits,
  markBillingOpsPaid,
  markBillingOpsTool,
  patchBillingOpsCustomCredits,
  patchBillingOpsPlan,
} from '../utils/api.js'
import { formatContactCredits } from '../utils/format.js'
import { notifyCreditsChanged } from '../hooks/useMyCredits.js'

function fmtMoney(n) {
  const v = Number(n)
  if (!Number.isFinite(v)) return '—'
  return `USD ${v.toLocaleString('es-AR', { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`
}

function fmtWhen(iso) {
  if (!iso) return null
  try {
    return new Date(iso).toLocaleString('es-AR', { dateStyle: 'short', timeStyle: 'short' })
  } catch {
    return String(iso)
  }
}

function StepBadge({ done, label }) {
  return (
    <span
      className={[
        'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide',
        done ? 'bg-red-50 text-red-800' : 'bg-nx-card-muted text-nx-muted',
      ].join(' ')}
    >
      {done ? '✓' : '·'} {label}
    </span>
  )
}

export default function OpsBillingPage() {
  const { companyId: ctxCompanyId, companies, loading: ctxLoading } = useCompany()
  const { user } = useAuth()
  const canOps = isCompanyAdmin(user)

  const [opsCompanyId, setOpsCompanyId] = useState(null)
  const [board, setBoard] = useState(null)
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [okMsg, setOkMsg] = useState(null)
  const [customCreditsInput, setCustomCreditsInput] = useState('')

  useEffect(() => {
    if (opsCompanyId == null && ctxCompanyId != null) {
      setOpsCompanyId(ctxCompanyId)
    }
  }, [ctxCompanyId, opsCompanyId])

  const cycle = detail?.cycle
  const economics = detail?.current_economics
  const plans = detail?.plans || []
  const wallet = detail?.wallet

  const selectedName = useMemo(() => {
    const fromBoard = (board?.companies || []).find((c) => c.company_id === opsCompanyId)
    if (fromBoard?.company_name) return fromBoard.company_name
    const fromCtx = (companies || []).find((c) => c.id === opsCompanyId)
    return fromCtx?.name || detail?.company_name || 'Cliente'
  }, [board, companies, detail, opsCompanyId])

  const load = useCallback(async () => {
    if (!canOps || !opsCompanyId) return
    setLoading(true)
    setError(null)
    try {
      const [boardData, ops] = await Promise.all([
        fetchBillingOpsBoard(),
        fetchCompanyBillingOps(opsCompanyId),
      ])
      setBoard(boardData)
      setDetail(ops)
      if (ops?.plan === 'custom') {
        setCustomCreditsInput(String(ops.cycle?.credits_to_grant || ''))
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [canOps, opsCompanyId])

  useEffect(() => {
    void load()
  }, [load])

  const boardRows = useMemo(() => board?.companies || [], [board])

  async function withBusy(fn) {
    setBusy(true)
    setError(null)
    setOkMsg(null)
    try {
      await fn()
      notifyCreditsChanged()
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (!canOps) {
    return (
      <p className="rounded-xl border border-dashed border-nx-border px-4 py-8 text-center text-sm text-nx-muted">
        Solo Director / Owner pueden operar cobros y acreditaciones.
      </p>
    )
  }

  return (
    <>
      <PageHeader
        kicker="Ops CostGuard"
        title="Cobros y créditos"
        description="Pagó el cliente → cargá OpenAI / Prospeo / Brave → acreditá el cupo Nexus del mes."
      />
      <AlertBanner message={error} onDismiss={() => setError(null)} />
      {okMsg ? (
        <p className="mb-4 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-900">{okMsg}</p>
      ) : null}

      {(loading || ctxLoading) && opsCompanyId ? (
        <p className="text-sm text-nx-muted">Cargando ciclo Ops…</p>
      ) : null}

      {boardRows.length > 0 || (companies?.length ?? 0) > 1 ? (
        <div className="mb-4">
          <label className="text-xs font-medium text-nx-ink">Empresa cliente</label>
          <select
            className="mt-1 w-full max-w-md rounded-lg border border-nx-border bg-white px-3 py-2 text-sm"
            value={opsCompanyId ?? ''}
            onChange={(e) => setOpsCompanyId(Number(e.target.value))}
          >
            {(boardRows.length
              ? boardRows.map((r) => ({ id: r.company_id, name: r.company_name }))
              : (companies || []).map((c) => ({ id: c.id, name: c.name }))
            ).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </div>
      ) : null}

      {detail && cycle ? (
        <div className="mb-6 space-y-4">
          <section className="rounded-xl border-2 border-nx-brand/50 bg-nx-card-muted/80 p-4 shadow-sm">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-sm font-semibold text-nx-ink">
                  {selectedName} · ciclo {cycle.cycle_key}
                </h2>
                <p className="mt-1 text-xs text-nx-muted">
                  Plan {cycle.plan_label} · {formatContactCredits(cycle.credits_to_grant)} créditos ·{' '}
                  {fmtMoney(cycle.price_usd)}
                </p>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  <StepBadge done={cycle.paid} label="Pagó" />
                  <StepBadge done={cycle.tools_ready} label="Tools" />
                  <StepBadge done={cycle.credits_granted} label="Créditos" />
                </div>
              </div>
              <div className="text-right text-xs text-nx-muted">
                <p>
                  Pool actual:{' '}
                  <span className="font-semibold text-nx-ink">
                    {wallet
                      ? formatContactCredits(wallet?.unassigned_balance ?? wallet?.total_balance ?? 0)
                      : '—'}
                  </span>
                </p>
                {opsCompanyId === ctxCompanyId ? (
                  <Link to="/creditos" className="text-nx-brand hover:underline">
                    Ver caja de créditos →
                  </Link>
                ) : (
                  <p className="text-[10px] text-nx-subtle">Pool de otra empresa (solo Ops)</p>
                )}
              </div>
            </div>

            <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <div className="rounded-lg border border-nx-border bg-white p-3">
                <p className="text-[10px] font-semibold uppercase text-nx-muted">Precio cliente</p>
                <p className="mt-1 text-lg font-semibold text-nx-ink">{fmtMoney(cycle.price_usd)}</p>
              </div>
              <div className="rounded-lg border border-nx-border bg-white p-3">
                <p className="text-[10px] font-semibold uppercase text-nx-muted">COGS tools</p>
                <p className="mt-1 text-lg font-semibold text-nx-ink">{fmtMoney(cycle.tools_cogs_usd)}</p>
              </div>
              <div className="rounded-lg border border-nx-border bg-white p-3">
                <p className="text-[10px] font-semibold uppercase text-nx-muted">Margen</p>
                <p className="mt-1 text-lg font-semibold text-red-800">{fmtMoney(cycle.margin_usd)}</p>
              </div>
              <div className="rounded-lg border border-nx-border bg-white p-3">
                <p className="text-[10px] font-semibold uppercase text-nx-muted">Cupo</p>
                <p className="mt-1 text-lg font-semibold text-nx-ink">
                  {formatContactCredits(cycle.credits_to_grant)}
                </p>
              </div>
            </div>
          </section>

          <section className="rounded-xl border border-nx-border bg-white p-4 shadow-sm">
            <h3 className="text-xs font-bold uppercase tracking-wide text-nx-ink">1. Plan del cliente</h3>
            <div className="mt-3 flex flex-wrap gap-2">
              {plans.map((p) => {
                const active = detail.plan === p.key
                return (
                  <button
                    key={p.key}
                    type="button"
                    disabled={busy || cycle.paid || cycle.credits_granted}
                    onClick={() =>
                      void withBusy(async () => {
                        await patchBillingOpsPlan(opsCompanyId, {
                          plan: p.key,
                          custom_credits:
                            p.key === 'custom'
                              ? Number(customCreditsInput) || cycle.credits_to_grant || 1000
                              : undefined,
                        })
                        setOkMsg(`Plan actualizado a ${p.label}`)
                      })
                    }
                    className={[
                      'rounded-lg border px-3 py-2 text-left text-xs transition',
                      active
                        ? 'border-nx-brand bg-nx-brand/10 ring-1 ring-nx-brand/30'
                        : 'border-nx-border hover:bg-nx-card-muted',
                      busy || cycle.paid || cycle.credits_granted ? 'opacity-60' : '',
                    ].join(' ')}
                  >
                    <span className="block font-semibold text-nx-ink">{p.label}</span>
                    <span className="block text-[10px] text-nx-muted">
                      {p.is_custom
                        ? 'USD 0,030 / crédito'
                        : `${formatContactCredits(p.monthly_contact_credits)} · ${fmtMoney(p.price_usd)}`}
                    </span>
                  </button>
                )
              })}
            </div>
            {detail.plan === 'custom' && !cycle.paid && !cycle.credits_granted ? (
              <div className="mt-3 flex flex-wrap items-end gap-2">
                <div>
                  <label className="text-xs font-medium text-nx-ink">Cupo custom (créditos)</label>
                  <input
                    className="mt-1 w-40 rounded-lg border border-nx-border px-2 py-1.5 text-sm"
                    inputMode="numeric"
                    value={customCreditsInput}
                    onChange={(e) => setCustomCreditsInput(e.target.value)}
                  />
                </div>
                <button
                  type="button"
                  disabled={busy}
                  className="nx-btn nx-btn-primary px-3 py-1.5 text-sm"
                  onClick={() =>
                    void withBusy(async () => {
                      const n = Number(customCreditsInput)
                      if (!Number.isFinite(n) || n < 1) throw new Error('Indicá un cupo custom válido')
                      await patchBillingOpsCustomCredits(opsCompanyId, n)
                      setOkMsg(`Custom: ${n} créditos · ${fmtMoney(n * 0.5)}`)
                    })
                  }
                >
                  Aplicar cupo
                </button>
              </div>
            ) : null}
          </section>

          <section className="rounded-xl border border-nx-border bg-white p-4 shadow-sm">
            <h3 className="text-xs font-bold uppercase tracking-wide text-nx-ink">2. ¿Pagó este mes?</h3>
            <p className="mt-1 text-[11px] text-nx-subtle">
              Sin pago marcado no se pueden cargar tools ni acreditar créditos.
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                disabled={busy || cycle.paid}
                className="nx-btn nx-btn-primary px-4 py-2 text-sm disabled:opacity-60"
                onClick={() =>
                  void withBusy(async () => {
                    await markBillingOpsPaid(opsCompanyId, { paid: true })
                    setOkMsg('Pago marcado. Ahora cargá las tools.')
                  })
                }
              >
                {cycle.paid ? `Pagó · ${fmtWhen(cycle.paid_at)}` : 'Sí, pagó'}
              </button>
              {cycle.paid && !cycle.credits_granted ? (
                <button
                  type="button"
                  disabled={busy}
                  className="rounded-lg border border-nx-border px-3 py-2 text-sm hover:bg-nx-card-muted"
                  onClick={() =>
                    void withBusy(async () => {
                      await markBillingOpsPaid(opsCompanyId, { paid: false })
                      setOkMsg('Pago desmarcado.')
                    })
                  }
                >
                  Desmarcar pago
                </button>
              ) : null}
            </div>
          </section>

          <section className="rounded-xl border border-nx-border bg-white p-4 shadow-sm">
            <h3 className="text-xs font-bold uppercase tracking-wide text-nx-ink">
              3. Top-up tools (cuentas CostGuard)
            </h3>
            <p className="mt-1 text-[11px] text-nx-subtle">
              Montos sugeridos para este plan/ciclo. Cargá en cada proveedor y marcá con tilde + fecha.
            </p>
            <div className="mt-3 space-y-2">
              {(cycle.tools || []).map((t) => (
                <div
                  key={t.key}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-nx-border bg-nx-card-muted/50 px-3 py-2"
                >
                  <div>
                    <p className="text-sm font-semibold text-nx-ink">{t.label}</p>
                    <p className="text-[11px] text-nx-muted">
                      Cargar {fmtMoney(t.amount_usd)}
                      {t.topped_up_at ? ` · hecho ${fmtWhen(t.topped_up_at)}` : ''}
                      {!t.required ? ' · (no requerido este ciclo)' : ''}
                    </p>
                  </div>
                  <button
                    type="button"
                    disabled={busy || !cycle.paid || (!t.required && t.amount_usd <= 0)}
                    className={[
                      'rounded-lg border px-3 py-1.5 text-xs font-semibold disabled:opacity-50',
                      t.topped_up
                        ? 'border-red-200 bg-red-50 text-red-800'
                        : 'border-nx-border bg-white text-nx-ink hover:bg-nx-card-muted',
                    ].join(' ')}
                    onClick={() =>
                      void withBusy(async () => {
                        await markBillingOpsTool(opsCompanyId, t.key, { topped_up: !t.topped_up })
                        setOkMsg(
                          t.topped_up
                            ? `${t.label} desmarcado`
                            : `${t.label} marcado como cargado`,
                        )
                      })
                    }
                  >
                    {t.topped_up ? '✓ Cargado' : 'Marcar cargado'}
                  </button>
                </div>
              ))}
            </div>
          </section>

          <section className="rounded-xl border border-nx-border bg-white p-4 shadow-sm">
            <h3 className="text-xs font-bold uppercase tracking-wide text-nx-ink">4. Acreditar créditos Nexus</h3>
            <p className="mt-1 text-[11px] text-nx-subtle">
              Solo habilitado con pago + tools listas. Una vez por mes ({cycle.cycle_key}).
            </p>
            <button
              type="button"
              disabled={busy || !cycle.can_grant_credits}
              className="mt-3 nx-btn nx-btn-primary px-4 py-2.5 text-sm disabled:opacity-50"
              onClick={() =>
                void withBusy(async () => {
                  const res = await grantBillingOpsCredits(opsCompanyId, {})
                  setOkMsg(res?.message || 'Créditos acreditados')
                })
              }
            >
              {cycle.credits_granted
                ? `Ya acreditado · ${formatContactCredits(cycle.credits_granted_amount)}`
                : `Acreditar ${formatContactCredits(cycle.credits_to_grant)} créditos`}
            </button>
          </section>

          {economics ? (
            <p className="text-[11px] text-nx-subtle">
              Referencia económica: OpenAI {fmtMoney(economics.openai_usd)} · Prospeo{' '}
              {fmtMoney(economics.prospeo_usd)} · Brave {fmtMoney(economics.brave_usd)} · margen{' '}
              {fmtMoney(economics.margin_usd)}.
            </p>
          ) : null}
        </div>
      ) : null}

      {boardRows.length > 0 ? (
        <section className="rounded-xl border border-nx-border bg-white p-4 shadow-sm">
          <h3 className="text-sm font-semibold text-nx-ink">
            Tablero del mes {board?.cycle_key}
          </h3>
          <div className="mt-3 overflow-x-auto">
            <table className="min-w-full text-left text-xs">
              <thead className="text-[10px] uppercase text-nx-muted">
                <tr>
                  <th className="px-2 py-1.5">Empresa</th>
                  <th className="px-2 py-1.5">Plan</th>
                  <th className="px-2 py-1.5">Pagó</th>
                  <th className="px-2 py-1.5">Tools</th>
                  <th className="px-2 py-1.5">Créditos</th>
                </tr>
              </thead>
              <tbody>
                {boardRows.map((row) => (
                  <tr
                    key={row.company_id}
                    className={[
                      'border-t border-nx-border cursor-pointer hover:bg-nx-card-muted/80',
                      row.company_id === opsCompanyId ? 'bg-nx-brand/5' : '',
                    ].join(' ')}
                    onClick={() => setOpsCompanyId(row.company_id)}
                  >
                    <td className="px-2 py-2 font-medium text-nx-ink">{row.company_name}</td>
                    <td className="px-2 py-2">{row.plan_label}</td>
                    <td className="px-2 py-2">{row.paid ? '✓' : '—'}</td>
                    <td className="px-2 py-2">{row.tools_ready ? '✓' : '—'}</td>
                    <td className="px-2 py-2">{row.credits_granted ? '✓' : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
    </>
  )
}
