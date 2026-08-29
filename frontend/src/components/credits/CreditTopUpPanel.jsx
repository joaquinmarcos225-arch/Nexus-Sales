import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useCompany } from '../../context/CompanyContext.jsx'
import { useAuth } from '../../context/AuthContext.jsx'
import { AlertBanner } from '../AlertBanner.jsx'
import { isCompanyAdmin, normalizeRole, ROLES } from '../../data/navigation.js'
import { formatContactCredits } from '../../utils/format.js'
import {
  fetchCreditPacks,
  fetchCreditPacksInfo,
  requestCreditPurchase,
} from '../../utils/api.js'

/**
 * Self-serve top-up: elige paquete → solicitud a Nexus Support → Ops acredita tras pago.
 */
export function CreditTopUpPanel({ availableBalance = 0, className = '' }) {
  const { companyId } = useCompany()
  const { user } = useAuth()
  const role = normalizeRole(user?.role)
  const canPurchase =
    isCompanyAdmin(user) || role === ROLES.manager

  const [packs, setPacks] = useState([])
  const [info, setInfo] = useState(null)
  const [selected, setSelected] = useState('m')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [success, setSuccess] = useState(null)

  const threshold = Number(info?.low_credit_threshold) || 10
  const low = Number(availableBalance) <= threshold

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      setLoading(true)
      try {
        const [p, i] = await Promise.all([fetchCreditPacks(), fetchCreditPacksInfo()])
        if (!cancelled) {
          setPacks(Array.isArray(p) ? p : [])
          setInfo(i)
          if (Array.isArray(p) && p.length > 0 && !p.find((x) => x.key === selected)) {
            setSelected(p[1]?.key || p[0].key)
          }
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e))
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [selected])

  const selectedPack = packs.find((p) => p.key === selected)

  const submit = useCallback(async () => {
    if (!companyId || !selectedPack) return
    setBusy(true)
    setError(null)
    setSuccess(null)
    try {
      const res = await requestCreditPurchase(companyId, selectedPack.key)
      setSuccess(res?.message || 'Solicitud enviada.')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }, [companyId, selectedPack])

  if (loading) {
    return <p className="text-sm text-nx-muted">Cargando paquetes…</p>
  }

  return (
    <section
      className={[
        'rounded-2xl border p-5 shadow-sm',
        low ? 'border-amber-300 bg-amber-50/80' : 'border-nx-border bg-white shadow-nx-ink/5',
        className,
      ].join(' ')}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-nx-ink">Recargar créditos</h2>
          <p className="mt-1 text-xs leading-relaxed text-nx-muted">
            1 crédito = 1 persona en secuencia completa. USD 0,50 por crédito.
            {low ? (
              <span className="ml-1 font-medium text-amber-900">
                Te quedan {formatContactCredits(availableBalance)} — conviene recargar.
              </span>
            ) : null}
          </p>
        </div>
        {low ? (
          <span className="rounded-full bg-amber-200 px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide text-amber-950">
            Saldo bajo
          </span>
        ) : null}
      </div>

      <AlertBanner message={error} onDismiss={() => setError(null)} />

      {success ? (
        <div className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-950">
          <p>{success}</p>
          {info?.payment_instructions ? (
            <p className="mt-2 text-xs opacity-90">{info.payment_instructions}</p>
          ) : null}
          <Link
            to="/soporte"
            className="mt-3 inline-flex rounded-lg bg-emerald-800 px-3 py-1.5 text-xs font-semibold text-white hover:opacity-90"
          >
            Ver conversación en Soporte
          </Link>
        </div>
      ) : null}

      {!canPurchase ? (
        <p className="mt-4 text-sm text-nx-muted">
          Pedile a tu director o manager que solicite la recarga desde esta pantalla.
        </p>
      ) : (
        <>
          <div className="mt-4 grid gap-3 sm:grid-cols-3">
            {packs.map((pack) => {
              const active = pack.key === selected
              return (
                <button
                  key={pack.key}
                  type="button"
                  onClick={() => setSelected(pack.key)}
                  className={[
                    'rounded-xl border px-4 py-3 text-left transition',
                    active
                      ? 'border-nx-brand bg-nx-brand/5 ring-2 ring-nx-brand/30'
                      : 'border-nx-border bg-nx-bg/30 hover:border-nx-brand/40',
                  ].join(' ')}
                >
                  <p className="text-xs font-semibold uppercase tracking-wide text-nx-muted">
                    {pack.label}
                  </p>
                  <p className="mt-1 text-lg font-bold tabular-nums text-nx-ink">
                    {formatContactCredits(pack.credits)}
                  </p>
                  <p className="text-xs text-nx-muted">USD {Number(pack.price_usd).toFixed(0)}</p>
                  {pack.description ? (
                    <p className="mt-2 text-[10px] leading-snug text-nx-muted">{pack.description}</p>
                  ) : null}
                </button>
              )
            })}
          </div>

          {selectedPack ? (
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <button
                type="button"
                disabled={busy || !companyId}
                onClick={() => void submit()}
                className="rounded-lg bg-nx-ink px-4 py-2 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-50"
              >
                {busy ? 'Enviando…' : `Solicitar ${selectedPack.label} · USD ${selectedPack.price_usd}`}
              </button>
              <p className="text-[11px] text-nx-muted">
                Enviamos la solicitud a Nexus Support. Acreditamos tras confirmar el pago.
              </p>
            </div>
          ) : null}
        </>
      )}
    </section>
  )
}
