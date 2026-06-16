/**
 * Flujo de automatización Nexus (visual): integraciones simuladas / preparadas.
 */

const STEPS = [
  { id: 'icp', label: 'OpenAI analiza ICP', desc: 'Define encaje y tono según producto y mercado.' },
  { id: 'sourcing', label: 'Lead Sourcing Engine', desc: 'ICP → Web Search → PhantomBuster → Prospeo → import.' },
  { id: 'verify', label: 'Verificador valida datos', desc: 'Deduplicación, email/LinkedIn/teléfono.' },
  { id: 'draft', label: 'OpenAI redacta mensajes', desc: 'Primer touch y seguimientos según canal.' },
  { id: 'email', label: 'Email automático', desc: 'Orquestación preparada (Gmail no conectado).' },
  { id: 'wa', label: 'WhatsApp automático', desc: 'Orquestación preparada (API no conectada).' },
  { id: 'li', label: 'LinkedIn asistido por SDR', desc: 'Nexus prepara; el humano envía desde LinkedIn.' },
  { id: 'reply', label: 'OpenAI gestiona respuestas', desc: 'Clasificación, objeciones y siguiente mejor acción.' },
  { id: 'human', label: 'SDR cierra reunión/venta', desc: 'Agenda, llamada y cierre comercial humano.' },
]

function stepState(stepId, campaign) {
  const st = campaign?.status
  const ap = campaign?.autopilot_status
  if (stepId === 'icp') {
    if (st && st !== 'draft') {
      return 'ejecutado'
    }
    return 'listo'
  }
  if (stepId === 'sourcing' || stepId === 'verify') {
    if (st === 'running' || st === 'ready' || st === 'completed') {
      return 'ejecutado'
    }
    if (st === 'paused') {
      return 'requiere acción humana'
    }
    return 'pendiente'
  }
  if (stepId === 'draft' || stepId === 'reply') {
    if (ap === 'running' || ap === 'completed') {
      return 'ejecutado'
    }
    if (ap === 'paused') {
      return 'requiere acción humana'
    }
    return st === 'draft' ? 'pendiente' : 'listo'
  }
  if (stepId === 'email' || stepId === 'wa') {
    return 'pendiente'
  }
  if (stepId === 'li') {
    return 'listo'
  }
  if (stepId === 'human') {
    return 'requiere acción humana'
  }
  return 'pendiente'
}

const STATE_STYLE = {
  pendiente: 'border-slate-200 bg-slate-50 text-slate-600',
  listo: 'border-sky-200 bg-sky-50 text-sky-900',
  ejecutado: 'border-emerald-200 bg-emerald-50 text-emerald-900',
  'requiere acción humana': 'border-amber-200 bg-amber-50 text-amber-950',
}

export function CampaignNexusFlowSection({ campaign }) {
  if (!campaign) {
    return null
  }
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <h2 className="text-sm font-semibold text-slate-900">Flujo de automatización Nexus</h2>
      <p className="mt-1 text-xs text-slate-500">
        Vista del pipeline real del producto. Los conectores externos están preparados pero no activos.
      </p>
      <ol className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {STEPS.map((s, i) => {
          const state = stepState(s.id, campaign)
          return (
            <li
              key={s.id}
              className={`rounded-lg border px-3 py-2 text-xs ${STATE_STYLE[state] ?? STATE_STYLE.pendiente}`}
            >
              <p className="font-semibold text-[11px] text-slate-500">
                {i + 1}. {s.label}
              </p>
              <p className="mt-1 text-[11px] leading-snug text-slate-700">{s.desc}</p>
              <p className="mt-2 text-[10px] font-bold uppercase tracking-wide text-slate-700">{state}</p>
            </li>
          )
        })}
      </ol>
    </section>
  )
}
