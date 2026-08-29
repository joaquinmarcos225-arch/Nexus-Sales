import { Link } from 'react-router-dom'
import { useMemo, useState } from 'react'
import { useCompany } from '../../context/CompanyContext.jsx'
import { useAuth } from '../../context/AuthContext.jsx'
import { AlertBanner } from '../../components/AlertBanner.jsx'
import { PageHeader } from '../../layout/PageHeader'
import { SortFilterTable } from '../../components/dashboard/SortFilterTable.jsx'
import { useFlattenedProspects } from '../../hooks/useFlattenedProspects.js'
import { isManagerOrGerente } from '../../data/navigation.js'
import {
  PIPELINE_STAGES,
  countByPipelineStage,
  pipelineStageKey,
  pipelineStageLabel,
} from '../../utils/pipelineStages.js'
import { formatLocalDateTime } from '../../utils/instantFormat.js'

function stageBadgeClass(key) {
  const tone = PIPELINE_STAGES.find((s) => s.key === key)?.tone
  if (tone === 'hot') return 'bg-red-100 text-red-800 ring-red-200/80'
  if (tone === 'warm') return 'bg-amber-100 text-amber-900 ring-amber-200/80'
  if (tone === 'muted') return 'bg-nx-bg text-nx-muted ring-nx-border'
  return 'bg-sky-50 text-sky-900 ring-sky-200/70'
}

export default function DashboardPipelinePage() {
  const { user } = useAuth()
  const { companyId } = useCompany()
  const { rows, loading, error } = useFlattenedProspects(companyId)
  const [stageFilter, setStageFilter] = useState('all')

  const managerView = isManagerOrGerente(user)

  const counts = useMemo(() => countByPipelineStage(rows), [rows])

  const filteredRows = useMemo(() => {
    const base = rows.map((p) => ({
      ...p,
      pipeline_stage: pipelineStageKey(p),
      pipeline_stage_label: pipelineStageLabel(pipelineStageKey(p)),
    }))
    if (stageFilter === 'all') {
      return base
    }
    return base.filter((p) => p.pipeline_stage === stageFilter)
  }, [rows, stageFilter])

  const columns = [
    {
      key: 'name',
      label: 'Prospecto',
      render: (r) => (
        <Link
          to={`/campanas/${r.campaign_id}?prospect=${r.id}`}
          className="font-medium text-nx-brand hover:underline"
        >
          {r.name || 'Sin nombre'}
        </Link>
      ),
    },
    {
      key: 'pipeline_stage_label',
      label: 'Etapa',
      render: (r) => (
        <span
          className={[
            'inline-flex rounded-full px-2 py-0.5 text-[10px] font-semibold ring-1',
            stageBadgeClass(r.pipeline_stage),
          ].join(' ')}
        >
          {r.pipeline_stage_label}
        </span>
      ),
    },
    { key: 'campaign_name', label: 'Campaña', className: 'max-w-[8rem] truncate' },
    { key: 'seller_name', label: 'SDR', className: 'max-w-[6rem] truncate' },
    {
      key: 'last_inbound_at',
      label: 'Último inbound',
      render: (r) => formatLocalDateTime(r.last_inbound_at, 'es-AR') || '—',
    },
  ]

  if (!managerView) {
    return (
      <>
        <PageHeader title="Pipeline" description="Vista de equipo para managers y directores." />
        <p className="rounded-xl border border-dashed border-nx-border px-4 py-8 text-center text-sm text-nx-muted">
          Tu rol no tiene acceso a esta vista. Pedile a un manager o director de la empresa.
        </p>
      </>
    )
  }

  return (
    <>
      <PageHeader
        title="Pipeline del equipo"
        description="Prospectos por etapa comercial — filtrable por SDR y campaña."
      />
      <AlertBanner message={error} />

      <div className="mb-4 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => setStageFilter('all')}
          className={[
            'rounded-full px-3 py-1 text-xs font-semibold ring-1 transition',
            stageFilter === 'all'
              ? 'bg-nx-ink text-white ring-nx-ink'
              : 'bg-white text-nx-ink ring-nx-border hover:bg-nx-bg',
          ].join(' ')}
        >
          Todos ({rows.length})
        </button>
        {PIPELINE_STAGES.filter((s) => (counts[s.key] || 0) > 0).map((s) => (
          <button
            key={s.key}
            type="button"
            onClick={() => setStageFilter(s.key)}
            className={[
              'rounded-full px-3 py-1 text-xs font-semibold ring-1 transition',
              stageFilter === s.key
                ? 'bg-nx-ink text-white ring-nx-ink'
                : 'bg-white text-nx-ink ring-nx-border hover:bg-nx-bg',
            ].join(' ')}
          >
            {s.label} ({counts[s.key]})
          </button>
        ))}
      </div>

      {loading ? (
        <p className="text-sm text-nx-muted">Cargando pipeline…</p>
      ) : (
        <SortFilterTable
          columns={columns}
          rows={filteredRows}
          filterPlaceholder="Buscar prospecto, campaña o SDR…"
        />
      )}
    </>
  )
}
