import { Children, useMemo, useState } from 'react'
import {
  DndContext,
  PointerSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
} from '@dnd-kit/core'
import { CSS } from '@dnd-kit/utilities'
import { patchProspect } from '../../utils/api.js'
import { AlertBanner } from '../AlertBanner.jsx'
import { columnIdForPipelineStage, KANBAN_COLUMNS, stageForDroppedColumn } from '../../utils/pipelineKanban.js'

function shorten(s, n = 42) {
  if (!s) return '—'
  return s.length > n ? `${s.slice(0, n - 1)}…` : s
}

function formatAct(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('es-AR', { dateStyle: 'short', timeStyle: 'short' })
  } catch {
    return '—'
  }
}

function KanbanCard({ prospect, dragId }) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: dragId,
    data: { prospectId: prospect.id },
  })
  const style = {
    transform: CSS.Translate.toString(transform),
    transition: isDragging ? 'none' : 'transform 180ms ease',
  }
  const last =
    prospect.last_inbound_at ||
    prospect.last_outbound_at ||
    prospect.last_followup_at ||
    prospect.updated_at

  return (
    <div
      ref={setNodeRef}
      style={style}
      {...listeners}
      {...attributes}
      className={`group cursor-grab active:cursor-grabbing rounded-xl border border-slate-200/90 bg-white px-3 py-2.5 shadow-sm transition-[box-shadow,transform] duration-200 hover:-translate-y-0.5 hover:shadow-md ${
        isDragging ? 'z-20 scale-[1.02] shadow-lg opacity-95 ring-2 ring-sky-400/40' : ''
      }`}
    >
      <p className="text-sm font-semibold text-slate-900 leading-snug">{prospect.name}</p>
      <p className="mt-0.5 text-xs text-slate-500 truncate">{prospect.company_name}</p>
      <div className="mt-2 flex flex-wrap gap-1.5">
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium capitalize text-slate-600">
          {prospect.interest_level || '—'}
        </span>
        <span className="rounded-full bg-sky-50 px-2 py-0.5 text-[10px] font-medium text-sky-800">
          {formatAct(last)}
        </span>
      </div>
      <p className="mt-2 text-[11px] leading-snug text-slate-600 line-clamp-2" title={prospect.next_best_action}>
        {shorten(prospect.next_best_action, 90)}
      </p>
    </div>
  )
}

function KanbanColumn({ col, children }) {
  const { setNodeRef, isOver } = useDroppable({ id: col.id })
  return (
    <div
      ref={setNodeRef}
      className={`flex min-h-[420px] w-[272px] shrink-0 flex-col rounded-2xl border border-slate-200/80 bg-gradient-to-b from-slate-50/95 to-white p-2.5 shadow-inner transition-all duration-200 ${
        isOver ? 'ring-2 ring-offset-2 ring-sky-400/50 scale-[1.01]' : ''
      }`}
      style={{ borderTop: `3px solid ${col.color}` }}
    >
      <div className="mb-2 flex items-center justify-between gap-2 px-1">
        <h3 className="text-xs font-bold uppercase tracking-wide text-slate-700">{col.label}</h3>
        <span className="rounded-full bg-slate-200/70 px-2 py-0.5 text-[10px] font-semibold tabular-nums text-slate-700">
          {Children.count(children)}
        </span>
      </div>
      <div className="flex flex-1 flex-col gap-2 overflow-y-auto pr-0.5 pb-2">{children}</div>
    </div>
  )
}

export function CampaignPipelineKanban({ campaignId, prospects, freeze = false, onChanged }) {
  const [error, setError] = useState(null)
  const [busyId, setBusyId] = useState(null)

  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: { distance: 6 },
    }),
  )

  const byColumn = useMemo(() => {
    const map = Object.fromEntries(KANBAN_COLUMNS.map((c) => [c.id, []]))
    for (const p of prospects) {
      const col = columnIdForPipelineStage(p.pipeline_stage || 'nuevo')
      if (!map[col]) map[col] = []
      map[col].push(p)
    }
    for (const k of Object.keys(map)) {
      map[k].sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')))
    }
    return map
  }, [prospects])

  async function handleDragEnd(event) {
    const { active, over } = event
    if (!over || freeze) return
    const prospectId = active.data.current?.prospectId
    if (!prospectId) return
    const overId = String(over.id)
    if (!KANBAN_COLUMNS.some((c) => c.id === overId)) return
    const prospect = prospects.find((p) => p.id === prospectId)
    if (!prospect) return
    const newStage = stageForDroppedColumn(overId)
    if ((prospect.pipeline_stage || 'nuevo') === newStage) return
    setBusyId(prospectId)
    setError(null)
    try {
      await patchProspect(prospectId, { pipeline_stage: newStage })
      onChanged?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyId(null)
    }
  }

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">Pipeline comercial</h2>
          <p className="mt-1 max-w-2xl text-xs text-slate-500 leading-relaxed">
            Vista tipo CRM: arrastrá prospectos entre columnas. La etapa comercial es independiente del estado
            técnico de outreach.
          </p>
        </div>
      </div>
      <AlertBanner message={error} onDismiss={() => setError(null)} />
      {freeze ? (
        <p className="mt-2 text-xs text-amber-800">Seleccioná la empresa correcta para mover tarjetas.</p>
      ) : null}
      <DndContext sensors={sensors} onDragEnd={(e) => void handleDragEnd(e)}>
        <div className="mt-4 flex gap-3 overflow-x-auto pb-4 scroll-smooth [scrollbar-width:thin]">
          {KANBAN_COLUMNS.map((col) => (
            <KanbanColumn key={col.id} col={col}>
              {(byColumn[col.id] || []).map((p) => (
                <div key={p.id} className={busyId === p.id ? 'opacity-60 pointer-events-none' : ''}>
                  <KanbanCard prospect={p} dragId={`prospect-${p.id}`} />
                </div>
              ))}
            </KanbanColumn>
          ))}
        </div>
      </DndContext>
    </section>
  )
}
