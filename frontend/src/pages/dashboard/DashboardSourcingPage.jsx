import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useCompany } from '../../context/CompanyContext.jsx'
import { AlertBanner } from '../../components/AlertBanner.jsx'
import { LeadSourcingPanel } from '../../components/sourcing/LeadSourcingPanel.jsx'
import { PageHeader } from '../../layout/PageHeader'
import { fetchCampaigns } from '../../utils/api.js'

export default function DashboardSourcingPage() {
  const { companyId } = useCompany()
  const [campaigns, setCampaigns] = useState([])
  const [campaignId, setCampaignId] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    if (!companyId) {
      setCampaigns([])
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const rows = await fetchCampaigns(companyId)
      const list = Array.isArray(rows) ? rows : []
      setCampaigns(list)
      if (!campaignId && list.length) {
        setCampaignId(String(list[0].id))
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [companyId])

  useEffect(() => {
    void load()
  }, [load])

  const selected = campaigns.find((c) => String(c.id) === String(campaignId))

  return (
    <div className="space-y-4">
      <PageHeader
        title="Lead Sourcing"
        subtitle="Encontrá prospectos reales según ICP y alimentá el motor AI SDR de Nexus."
      />
      <AlertBanner message={error} />

      <div className="flex flex-wrap items-end gap-3 rounded-lg border border-nx-border bg-white p-3 shadow-sm">
        <label className="block text-xs">
          <span className="font-semibold text-nx-ink">Campaña destino</span>
          <select
            className="mt-1 block min-w-[14rem] rounded-lg border border-nx-border px-2 py-1.5 text-sm"
            value={campaignId}
            onChange={(e) => setCampaignId(e.target.value)}
            disabled={loading || !campaigns.length}
          >
            {campaigns.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        {selected ? (
          <Link
            to={`/campanas/${selected.id}`}
            className="text-xs font-semibold text-zinc-800 hover:underline"
          >
            Abrir campaña →
          </Link>
        ) : null}
      </div>

      {loading ? <p className="text-sm text-nx-muted">Cargando campañas…</p> : null}

      {selected ? (
        <LeadSourcingPanel campaignId={selected.id} campaign={selected} onImported={() => void load()} />
      ) : null}

      {!loading && !campaigns.length ? (
        <p className="text-sm text-nx-muted">Creá una campaña con ICP para empezar a buscar prospectos.</p>
      ) : null}
    </div>
  )
}
