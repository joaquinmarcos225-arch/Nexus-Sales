import { useCallback, useEffect, useMemo, useState } from 'react'
import { useCompany } from '../context/CompanyContext.jsx'
import { AlertBanner } from '../components/AlertBanner.jsx'
import { PageHeader } from '../layout/PageHeader'
import {
  assignSellerCredits,
  fetchCreditAllocations,
  fetchUsers,
  fetchWallet,
  topUpWallet,
} from '../utils/api.js'
import { formatUsd } from '../utils/format.js'

export default function CajaCreditosPage() {
  const { companyId, loading: ctxLoading } = useCompany()
  const [wallet, setWallet] = useState(null)
  const [allocations, setAllocations] = useState([])
  const [users, setUsers] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const [topUpAmount, setTopUpAmount] = useState('')
  const [sellerId, setSellerId] = useState('')
  const [assignAmount, setAssignAmount] = useState('')
  const [busy, setBusy] = useState(false)
  const [clientError, setClientError] = useState(null)

  const sellers = useMemo(
    () => users.filter((u) => u.role === 'seller'),
    [users],
  )

  const loadAll = useCallback(async () => {
    if (!companyId) {
      return
    }
    setLoading(true)
    setError(null)
    try {
      const [w, a, u] = await Promise.all([
        fetchWallet(companyId),
        fetchCreditAllocations(companyId),
        fetchUsers(companyId),
      ])
      setWallet(w)
      setAllocations(Array.isArray(a) ? a : [])
      setUsers(Array.isArray(u) ? u : [])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [companyId])

  useEffect(() => {
    void loadAll()
  }, [loadAll])

  async function handleTopUp(ev) {
    ev.preventDefault()
    const amount = Number(topUpAmount)
    if (!companyId || !Number.isFinite(amount) || amount <= 0) {
      setClientError('Ingresá un monto entero válido (> 0).')
      return
    }
    setClientError(null)
    setBusy(true)
    try {
      const w = await topUpWallet(companyId, amount)
      setWallet(w)
      setTopUpAmount('')
      setClientError(null)
      await loadAll()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function handleAssign(ev) {
    ev.preventDefault()
    const amount = Number(assignAmount)
    const sid = Number(sellerId)
    const unassigned = wallet?.unassigned_balance ?? 0

    if (!companyId) {
      return
    }
    if (!Number.isFinite(sid) || sid <= 0 || !sellerId) {
      setClientError('Seleccioná un vendedor válido.')
      return
    }
    if (!Number.isFinite(amount) || amount <= 0) {
      setClientError('Ingresá un monto válido (> 0).')
      return
    }
    if (amount > unassigned) {
      setClientError(
        `No hay saldo suficiente sin asignar. Disponible: ${formatUsd(unassigned)}.`,
      )
      return
    }

    setClientError(null)
    setBusy(true)
    try {
      await assignSellerCredits(companyId, sid, amount)
      setAssignAmount('')
      setSellerId('')
      setClientError(null)
      await loadAll()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const unassigned = wallet?.unassigned_balance ?? 0
  const assigned = wallet?.assigned_to_sellers ?? 0
  const total = wallet?.total_balance ?? 0

  return (
    <>
      <PageHeader
        title="Caja / Créditos"
        description="Simulación de carga de créditos y asignaciones a vendedores."
      />
      <AlertBanner message={error} onDismiss={() => setError(null)} />

      {(ctxLoading || loading) && companyId ? (
        <p className="text-sm text-slate-500">Cargando caja...</p>
      ) : null}

      {!companyId && !ctxLoading ? (
        <p className="rounded-lg border border-dashed border-slate-300 bg-white px-4 py-8 text-center text-sm text-slate-600">
          Sin empresa seleccionada.
        </p>
      ) : null}

      {wallet ? (
        <div className="mb-8 grid gap-4 sm:grid-cols-3">
          <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <p className="text-xs font-medium uppercase text-slate-500">
              Total empresa (créditos)
            </p>
            <p className="mt-2 text-2xl font-semibold">{formatUsd(total)}</p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <p className="text-xs font-medium uppercase text-slate-500">
              Asignado a vendedores
            </p>
            <p className="mt-2 text-2xl font-semibold">{formatUsd(assigned)}</p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <p className="text-xs font-medium uppercase text-slate-500">
              Sin asignar
            </p>
            <p className="mt-2 text-2xl font-semibold">{formatUsd(unassigned)}</p>
          </div>
        </div>
      ) : null}

      <div className="mb-8 grid gap-4 lg:grid-cols-2">
        <form
          onSubmit={handleTopUp}
          className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm space-y-3"
        >
          <div>
            <h3 className="text-sm font-semibold text-slate-900">
              Simular carga de saldo
            </h3>
            <p className="text-xs text-slate-500">
              Incrementa los créditos totales disponibles para la empresa.
            </p>
          </div>
          <label className="block text-xs font-medium text-slate-600">
            Monto (USD entero)
            <input
              inputMode="numeric"
              min={1}
              className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm"
              placeholder="Ej. 100"
              value={topUpAmount}
              disabled={busy}
              onChange={(e) => setTopUpAmount(e.target.value)}
            />
          </label>
          <button
            type="submit"
            disabled={busy || !companyId}
            className="w-full rounded-lg bg-slate-900 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
          >
            Cargar saldo (simulación)
          </button>
        </form>

        <form
          onSubmit={handleAssign}
          className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm space-y-3"
        >
          <div>
            <h3 className="text-sm font-semibold text-slate-900">
              Asignar a vendedor
            </h3>
            <p className="text-xs text-slate-500">
              Consume del saldo sin asignar. El backend rechaza sobregiros.
            </p>
          </div>
          <label className="block text-xs font-medium text-slate-600">
            Vendedor
            <select
              required
              className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm"
              value={sellerId}
              disabled={busy}
              onChange={(e) => setSellerId(e.target.value)}
            >
              <option value="">Seleccionar…</option>
              {sellers.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} ({s.email})
                </option>
              ))}
            </select>
          </label>
          <label className="block text-xs font-medium text-slate-600">
            Monto a asignar (USD entero)
            <input
              inputMode="numeric"
              min={1}
              className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm"
              placeholder={`Máximo ${formatUsd(unassigned)}`}
              value={assignAmount}
              disabled={busy}
              onChange={(e) => setAssignAmount(e.target.value)}
            />
          </label>
          {clientError ? (
            <p className="text-xs font-medium text-red-700">{clientError}</p>
          ) : null}
          <button
            type="submit"
            disabled={busy || !companyId}
            className="w-full rounded-lg border border-slate-200 bg-white py-2 text-sm font-semibold hover:bg-slate-50 disabled:opacity-50"
          >
            Asignar saldo
          </button>
        </form>
      </div>

      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <div className="border-b border-slate-100 px-4 py-3">
          <h3 className="text-sm font-semibold text-slate-900">
            Vendedores y créditos
          </h3>
          <p className="text-xs text-slate-500">
            Asignado vs usado y saldo disponible por vendedor.
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-200 text-sm">
            <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-600">
              <tr>
                <th className="px-4 py-3">Nombre</th>
                <th className="px-4 py-3">Email</th>
                <th className="px-4 py-3 text-right">Asignado</th>
                <th className="px-4 py-3 text-right">Usado</th>
                <th className="px-4 py-3 text-right">Disponible</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 text-slate-700">
              {allocations.map((row) => {
                const available = Number(row.allocated_balance) - Number(row.used_balance)
                return (
                  <tr key={row.id}>
                    <td className="px-4 py-3 font-medium">{row.seller_name}</td>
                    <td className="px-4 py-3 text-slate-600">{row.seller_email}</td>
                    <td className="px-4 py-3 text-right">{formatUsd(row.allocated_balance)}</td>
                    <td className="px-4 py-3 text-right">{formatUsd(row.used_balance)}</td>
                    <td className="px-4 py-3 text-right">{formatUsd(available)}</td>
                  </tr>
                )
              })}
              {allocations.length === 0 ? (
                <tr>
                  <td
                    colSpan={5}
                    className="px-4 py-10 text-center text-sm text-slate-500"
                  >
                    Todavía no hay asignaciones registradas para esta empresa.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </div>
    </>
  )
}
