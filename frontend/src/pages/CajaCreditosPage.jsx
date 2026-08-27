import { useCallback, useEffect, useMemo, useState } from 'react'
import { useCompany } from '../context/CompanyContext.jsx'
import { useAuth } from '../context/AuthContext.jsx'
import { AlertBanner } from '../components/AlertBanner.jsx'
import { PageHeader } from '../layout/PageHeader'
import { normalizeRole, ROLES, isCompanyAdmin } from '../data/navigation.js'
import { isCreditEligibleUser, currentUserId } from '../utils/campaignUsers.js'
import {
  applyPlanWalletCredits,
  fetchCreditAllocations,
  fetchCreditLedger,
  fetchUsers,
  fetchWallet,
} from '../utils/api.js'
import { formatContactCredits } from '../utils/format.js'
import { planContactCredits } from '../data/contactPlans.js'
import { MyCreditsSummary } from '../components/credits/MyCreditsSummary.jsx'
import { CreditsUsageBar } from '../components/credits/CreditsUsageBar.jsx'
import { CreditsStatCard } from '../components/credits/CreditsStatCard.jsx'
import { CreditsTransferChat } from '../components/credits/CreditsTransferChat.jsx'
import { notifyCreditsChanged, useMyCredits } from '../hooks/useMyCredits.js'

const TABS = [
  { id: 'gestion', label: 'Gestión' },
  { id: 'equipo', label: 'Equipo e historial' },
]

export default function CajaCreditosPage() {
  const { companyId, loading: ctxLoading } = useCompany()
  const { user } = useAuth()
  const role = normalizeRole(user?.role)
  const isGerente = isCompanyAdmin(user)
  const isManager = role === ROLES.manager
  const isSdr = role === ROLES.sdr

  const {
    available: myCreditsAvailable,
    allocated: myCreditsAllocated,
    used: myCreditsUsed,
    roleScope: myCreditsScope,
    showCredits: showMyCredits,
    loading: myCreditsLoading,
  } = useMyCredits()

  const [tab, setTab] = useState('gestion')
  const [wallet, setWallet] = useState(null)
  const [users, setUsers] = useState([])
  const [allocations, setAllocations] = useState([])
  const [ledger, setLedger] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [clientError, setClientError] = useState(null)

  const eligibleUsers = useMemo(() => users.filter(isCreditEligibleUser), [users])

  const managers = useMemo(
    () => eligibleUsers.filter((u) => normalizeRole(u.role) === ROLES.manager),
    [eligibleUsers],
  )

  const myUserId = currentUserId(user)

  const transferPeers = useMemo(() => {
    if (!myUserId) return []
    return eligibleUsers.filter((u) => u.id !== myUserId)
  }, [eligibleUsers, myUserId])

  const canPeerTransfer = (isManager || isSdr) && Boolean(myUserId)

  const visibleAllocations = useMemo(() => {
    if (isGerente) return allocations
    if ((isManager || isSdr) && myUserId) {
      const peerIds = new Set(transferPeers.map((u) => u.id))
      peerIds.add(myUserId)
      return allocations.filter((a) => peerIds.has(a.seller_id))
    }
    return allocations
  }, [allocations, isGerente, isManager, isSdr, transferPeers, myUserId])

  const myAllocation = useMemo(() => {
    if (!myUserId) return null
    return allocations.find((a) => a.seller_id === myUserId) ?? null
  }, [allocations, myUserId])

  const myAvailable = useMemo(() => {
    if (!myAllocation) return 0
    return Math.max(0, Number(myAllocation.allocated_balance) - Number(myAllocation.used_balance))
  }, [myAllocation])

  const loadAll = useCallback(async () => {
    if (!companyId) return
    setLoading(true)
    setError(null)
    try {
      const [w, a, u, l] = await Promise.all([
        fetchWallet(companyId),
        fetchCreditAllocations(companyId),
        fetchUsers(companyId),
        isGerente ? fetchCreditLedger(companyId) : Promise.resolve([]),
      ])
      setWallet(w)
      setAllocations(Array.isArray(a) ? a : [])
      setUsers(Array.isArray(u) ? u : [])
      setLedger(Array.isArray(l) ? l : [])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
      notifyCreditsChanged()
    }
  }, [companyId, isGerente])

  useEffect(() => {
    void loadAll()
  }, [loadAll])

  async function handleApplyPlan() {
    if (!companyId) return
    setClientError(null)
    setBusy(true)
    try {
      await applyPlanWalletCredits(companyId)
      await loadAll()
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      if (msg.toLowerCase().includes('ya fue acreditado')) {
        setClientError(msg)
      } else {
        setError(msg)
      }
    } finally {
      setBusy(false)
    }
  }

  const total = wallet?.total_balance ?? 0
  const planCreditsPerCycle = planContactCredits(wallet?.plan, wallet?.plan_contact_credits)

  const assignedToManagers = useMemo(() => {
    if (!isGerente) return 0
    const managerIds = new Set(managers.map((u) => u.id))
    return allocations
      .filter((a) => managerIds.has(a.seller_id))
      .reduce((sum, a) => sum + Number(a.allocated_balance || 0), 0)
  }, [allocations, isGerente, managers])

  const assignedToSdrs = useMemo(() => {
    if (!isGerente) return 0
    const sdrIds = new Set(
      eligibleUsers.filter((u) => normalizeRole(u.role) === ROLES.sdr).map((u) => u.id),
    )
    return allocations
      .filter((a) => sdrIds.has(a.seller_id))
      .reduce((sum, a) => sum + Number(a.allocated_balance || 0), 0)
  }, [allocations, eligibleUsers, isGerente])

  const directorAvailable = Math.max(0, total - assignedToManagers - assignedToSdrs)

  const pageDescription = isGerente
    ? 'Pool de la empresa. Asigná créditos a cualquier SDR o Manager desde el chat. 1 crédito = 1 persona en secuencia completa.'
    : isManager
      ? 'Recibís créditos del pool y podés enviarlos a cualquier SDR o Manager. 1 crédito = 1 persona en secuencia completa.'
      : 'Tus créditos de contacto. Podés enviarlos a cualquier compañero SDR o Manager. 1 crédito = 1 persona en secuencia completa.'

  const visibleTabs = isGerente || isManager || isSdr ? TABS : TABS.filter((t) => t.id === 'gestion')

  return (
    <>
      <PageHeader title="Créditos de contacto" description={pageDescription} />

      <nav
        className="mb-6 flex gap-1 overflow-x-auto border-b border-nx-border pb-px"
        aria-label="Secciones de créditos"
      >
        {visibleTabs.map((t) => (
          <button
            key={t.id}
            type="button"
            className={['nx-tab shrink-0', tab === t.id ? 'nx-tab-active' : ''].filter(Boolean).join(' ')}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <AlertBanner message={error} onDismiss={() => setError(null)} />

      {(ctxLoading || loading) && companyId ? (
        <p className="text-sm text-nx-ink/70">Cargando créditos…</p>
      ) : null}

      {!companyId && !ctxLoading ? (
        <p className="rounded-2xl border border-dashed border-nx-border bg-white px-4 py-8 text-center text-sm text-nx-ink">
          Sin empresa seleccionada.
        </p>
      ) : null}

      {tab === 'gestion' ? (
        <div className="space-y-8">
          {showMyCredits && !myCreditsLoading && !ctxLoading ? (
            <section className="rounded-2xl border border-nx-border bg-white p-5 shadow-sm shadow-nx-ink/5">
              <MyCreditsSummary
                available={myCreditsAvailable}
                allocated={myCreditsAllocated}
                used={myCreditsUsed}
                roleScope={myCreditsScope}
              />
            </section>
          ) : null}

          {wallet && isGerente ? (
            <section className="rounded-2xl border border-nx-border bg-white p-5 shadow-sm shadow-nx-ink/5">
              <p className="text-xs font-semibold uppercase tracking-wide text-nx-ink">
                Pool de la empresa
              </p>
              <div className="mt-4 grid gap-5 sm:grid-cols-3">
                <div className="rounded-xl bg-[#F7F4F0] px-4 py-3">
                  <CreditsStatCard
                    label="Pool total"
                    value={formatContactCredits(total)}
                    hint="Cupo comercial de la empresa"
                    accent
                  />
                </div>
                <div className="rounded-xl bg-[#F7F4F0] px-4 py-3">
                  <CreditsStatCard
                    label="En managers"
                    value={formatContactCredits(assignedToManagers)}
                  />
                </div>
                <div className="rounded-xl bg-[#F7F4F0] px-4 py-3">
                  <CreditsStatCard
                    label="En SDRs"
                    value={formatContactCredits(assignedToSdrs)}
                  />
                </div>
              </div>
              <div className="mt-5 max-w-xl">
                <CreditsUsageBar
                  used={assignedToManagers + assignedToSdrs}
                  total={total}
                  label="Pool asignado al equipo"
                />
                <p className="mt-3 text-sm text-nx-ink">
                  Disponible para asignar:{' '}
                  <span className="font-semibold text-nx-brand">
                    {formatContactCredits(directorAvailable)}
                  </span>
                </p>
                {total < 1 ? (
                  <p className="mt-3 text-sm text-nx-ink">
                    El pool está en 0. Pedile a Nexus Support que acredite créditos del plan o un top-up.
                  </p>
                ) : null}
              </div>

              <details className="group mt-5">
                <summary className="cursor-pointer list-none text-sm font-medium text-nx-ink transition [&::-webkit-details-marker]:hidden">
                  <span className="inline-flex items-center gap-2">
                    <span className="text-nx-brand transition-transform group-open:rotate-90">▸</span>
                    Acreditación del plan (backup)
                  </span>
                </summary>
                <div className="mt-3 max-w-lg space-y-3 rounded-xl border border-nx-border bg-[#FAFAF8] p-4">
                  <p className="text-sm font-semibold text-nx-ink">
                    {wallet.plan_label || 'Starter'} — {formatContactCredits(planCreditsPerCycle)} / ciclo
                  </p>
                  {wallet.plan_description ? (
                    <p className="text-sm text-nx-ink">{wallet.plan_description}</p>
                  ) : null}
                  <button
                    type="button"
                    disabled={busy || !companyId}
                    onClick={() => void handleApplyPlan()}
                    className="rounded-lg border border-nx-border bg-white px-3 py-2 text-xs font-medium text-nx-ink hover:bg-nx-card-muted disabled:opacity-50"
                  >
                    Acreditar cupo del plan al pool
                  </button>
                  {clientError ? <p className="text-xs font-medium text-red-700">{clientError}</p> : null}
                </div>
              </details>
            </section>
          ) : null}

          {(isManager || isSdr) && myCreditsAvailable <= 0 && !loading && !myCreditsLoading ? (
            <p className="rounded-xl border border-dashed border-nx-border bg-white px-4 py-3 text-sm text-nx-ink">
              {isManager
                ? 'La directora aún no te asignó créditos de contacto al pool de manager.'
                : 'Tu manager aún no te asignó créditos de contacto.'}
            </p>
          ) : null}

          {isGerente && companyId ? (
            <CreditsTransferChat
              mode="assign"
              companyId={companyId}
              myUserId={myUserId}
              peers={eligibleUsers}
              myAvailable={directorAvailable}
              disabled={busy}
              onTransferred={loadAll}
              title="Asignar créditos al equipo"
              description="Desde el pool sin asignar. Elegí cualquier SDR o Manager, escribí el monto y enviá."
              balanceLabel="Pool disponible"
              emptyPeersTitle="Sin destinatarios"
              emptyPeersHint="Cuando haya SDR o Managers en la empresa, vas a poder asignarles créditos acá."
              sendLabel="Asignar"
            />
          ) : null}

          {canPeerTransfer && companyId ? (
            <CreditsTransferChat
              mode="transfer"
              companyId={companyId}
              myUserId={myUserId}
              peers={transferPeers}
              myAvailable={myAvailable}
              disabled={busy}
              onTransferred={loadAll}
            />
          ) : null}
        </div>
      ) : null}

      {tab === 'equipo' ? (
        <div className="space-y-8">
          <section className="rounded-2xl border border-nx-border bg-white p-5 shadow-sm shadow-nx-ink/5">
            <h3 className="text-sm font-semibold text-nx-ink">Créditos por usuario</h3>
            <div className="mt-3 overflow-x-auto">
              <table className="min-w-full divide-y divide-nx-border text-sm">
                <thead className="text-left text-xs font-semibold uppercase tracking-wide text-nx-ink">
                  <tr>
                    <th className="px-0 py-3 pr-4">Nombre</th>
                    <th className="px-4 py-3">Email</th>
                    <th className="px-4 py-3 text-right">Asignado</th>
                    <th className="px-4 py-3 text-right">Usado</th>
                    <th className="px-4 py-3 text-right">Disponible</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-nx-border text-nx-ink">
                  {visibleAllocations.map((row) => {
                    const available = Number(row.allocated_balance) - Number(row.used_balance)
                    return (
                      <tr key={row.id}>
                        <td className="px-0 py-3 pr-4 font-medium">{row.seller_name}</td>
                        <td className="px-4 py-3 text-nx-ink/80">{row.seller_email}</td>
                        <td className="px-4 py-3 text-right">{formatContactCredits(row.allocated_balance)}</td>
                        <td className="px-4 py-3 text-right">{formatContactCredits(row.used_balance)}</td>
                        <td className="px-4 py-3 text-right">{formatContactCredits(available)}</td>
                      </tr>
                    )
                  })}
                  {visibleAllocations.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="px-0 py-10 text-sm text-nx-ink">
                        Todavía no hay asignaciones registradas.
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </section>

          {isGerente ? (
            <section className="rounded-2xl border border-nx-border bg-white p-5 shadow-sm shadow-nx-ink/5">
              <h3 className="text-sm font-semibold text-nx-ink">Historial de movimientos</h3>
              <p className="mt-0.5 text-sm text-nx-ink">
                Renovaciones del plan, asignaciones, campañas y ajustes.
              </p>
              <div className="mt-3 overflow-x-auto">
                <table className="min-w-full divide-y divide-nx-border text-sm">
                  <thead className="text-left text-xs font-semibold uppercase tracking-wide text-nx-ink">
                    <tr>
                      <th className="px-0 py-3 pr-4">Fecha</th>
                      <th className="px-4 py-3">Tipo</th>
                      <th className="px-4 py-3 text-right">Créditos</th>
                      <th className="px-4 py-3">Detalle</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-nx-border text-nx-ink">
                    {ledger.map((row) => (
                      <tr key={row.id}>
                        <td className="whitespace-nowrap px-0 py-3 pr-4 text-xs text-nx-ink/70">
                          {row.created_at ? new Date(row.created_at).toLocaleString('es-AR') : '—'}
                        </td>
                        <td className="px-4 py-3">{row.kind_label || row.kind}</td>
                        <td className="px-4 py-3 text-right font-medium">
                          +{formatContactCredits(row.amount)}
                        </td>
                        <td className="px-4 py-3 text-nx-ink/80">{row.note}</td>
                      </tr>
                    ))}
                    {ledger.length === 0 ? (
                      <tr>
                        <td colSpan={4} className="px-0 py-10 text-sm text-nx-ink">
                          Sin movimientos registrados todavía.
                        </td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
            </section>
          ) : null}
        </div>
      ) : null}
    </>
  )
}
