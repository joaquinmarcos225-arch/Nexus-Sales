import { useCallback, useEffect, useState } from 'react'
import { Modal } from '../Modal.jsx'
import { PremiumGradientButton } from '../ui/PremiumGradientButton.jsx'
import {
  beginLinkedInAssistedSession,
  fetchLinkedInAssistQueue,
  fetchLinkedInAssistedSummary,
  markLinkedInAssistedSent,
  regenerateLinkedInAssistedReply,
} from '../../utils/api.js'
import {
  copyTextToClipboard,
  hasRealLinkedInUrl,
  linkedInOpenUrl,
} from '../../utils/linkedinAssist.js'
import { LinkedInAssistQueue } from '../outreach/LinkedInAssistQueue.jsx'

export function CampaignLinkedInAssistedSection({ campaignId, freeze, onChanged }) {
  const [summary, setSummary] = useState(null)
  const [queue, setQueue] = useState({ tasks: [], total_pending: 0 })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [modal, setModal] = useState({ open: false, prospect: null, text: '', clipboardOk: false })
  const [busyId, setBusyId] = useState(null)
  const [regeneratingId, setRegeneratingId] = useState(null)

  const load = useCallback(async () => {
    if (!campaignId || freeze) {
      setSummary(null)
      setQueue({ tasks: [], total_pending: 0 })
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const [s, q] = await Promise.all([
        fetchLinkedInAssistedSummary(campaignId),
        fetchLinkedInAssistQueue(campaignId),
      ])
      setSummary(s)
      setQueue(q && typeof q === 'object' ? q : { tasks: [], total_pending: 0 })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setSummary(null)
      setQueue({ tasks: [], total_pending: 0 })
    } finally {
      setLoading(false)
    }
  }, [campaignId, freeze])

  useEffect(() => {
    void load()
  }, [load])

  async function handleOpen(task) {
    if (!hasRealLinkedInUrl(task.linkedin_url)) {
      setError('Sin LinkedIn configurado para este prospecto.')
      return
    }
    setBusyId(task.prospect_id)
    setError(null)
    try {
      const res = await beginLinkedInAssistedSession(task.prospect_id)
      const text = (res?.message || task.message || '').trim()
      const clipboardOk = await copyTextToClipboard(text)
      const url = linkedInOpenUrl(task.linkedin_url)
      if (url) {
        window.open(url, '_blank', 'noopener,noreferrer')
      }
      setModal({
        open: true,
        prospect: { id: task.prospect_id, name: task.prospect_name },
        text,
        clipboardOk,
      })
      await load()
      onChanged?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyId(null)
    }
  }

  async function handleRegenerateReply(task) {
    setRegeneratingId(task.prospect_id)
    setError(null)
    try {
      await regenerateLinkedInAssistedReply(task.prospect_id)
      await load()
      onChanged?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setRegeneratingId(null)
    }
  }

  async function handleMarkSent(task) {
    const prospectId = task.prospect_id
    setBusyId(prospectId)
    setError(null)
    setQueue((prev) => {
      const tasks = (prev.tasks || []).filter((t) => Number(t.prospect_id) !== Number(prospectId))
      return { ...prev, tasks, total_pending: tasks.length }
    })
    try {
      await markLinkedInAssistedSent(prospectId)
      setModal({ open: false, prospect: null, text: '', clipboardOk: false })
      onChanged?.()
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      await load()
    } finally {
      setBusyId(null)
    }
  }

  const tasks = Array.isArray(queue.tasks) ? queue.tasks : []

  return (
    <section className="rounded-xl border border-nx-border bg-white p-4 shadow-sm">
      <h2 className="text-sm font-semibold text-nx-ink">LinkedIn · Copilot SDR</h2>
      <p className="mt-1 text-xs text-nx-muted">
        Nexus genera y prepara el mensaje; vos enviás manualmente en LinkedIn. Solo perfiles con URL real
        aparecen en la cola.
      </p>
      {error ? <p className="mt-2 text-xs text-red-700">{error}</p> : null}
      {loading ? (
        <p className="mt-3 text-xs text-nx-muted">Cargando cola…</p>
      ) : summary ? (
        <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-lg border border-nx-border bg-nx-card-muted px-2 py-1.5">
            <p className="text-[10px] font-semibold uppercase text-nx-muted">Cola pendiente</p>
            <p className="text-lg font-semibold text-nx-ink">{queue.total_pending ?? tasks.length}</p>
          </div>
          <div className="rounded-lg border border-nx-border bg-nx-card-muted px-2 py-1.5">
            <p className="text-[10px] font-semibold uppercase text-nx-muted">Borradores activos</p>
            <p className="text-lg font-semibold text-nx-ink">{summary.prospects_with_draft ?? 0}</p>
          </div>
          <div className="rounded-lg border border-nx-border bg-nx-card-muted px-2 py-1.5">
            <p className="text-[10px] font-semibold uppercase text-nx-muted">Confirmados hoy</p>
            <p className="text-lg font-semibold text-nx-ink">{summary.marked_sent_today ?? 0}</p>
          </div>
          <div className="rounded-lg border border-nx-border bg-nx-card-muted px-2 py-1.5">
            <p className="text-[10px] font-semibold uppercase text-nx-muted">Riesgo LinkedIn</p>
            <p className="text-lg font-semibold text-nx-ink">{summary.risk_level ?? '—'}</p>
          </div>
        </div>
      ) : null}

      <div className="mt-4">
        <LinkedInAssistQueue
          tasks={tasks}
          freeze={freeze}
          busyProspectId={busyId}
          regeneratingProspectId={regeneratingId}
          onOpenLinkedIn={(task) => void handleOpen(task)}
          onMarkSent={(task) => void handleMarkSent(task)}
          onRegenerateReply={(task) => void handleRegenerateReply(task)}
        />
        {!tasks.length && !loading ? (
          <p className="rounded-lg border border-dashed border-nx-border bg-nx-card-muted/80 px-4 py-6 text-center text-xs text-nx-muted">
            Sin tareas LinkedIn pendientes. Los prospectos sin URL real muestran «Sin LinkedIn configurado» y no
            entran a la cola.
          </p>
        ) : null}
      </div>

      {modal.open ? (
        <Modal
          title={`Mensaje LinkedIn · ${modal.prospect?.name ?? ''}`}
          onClose={() => setModal({ open: false, prospect: null, text: '', clipboardOk: false })}
          footer={
            <div className="flex flex-wrap justify-end gap-2">
              <button
                type="button"
                className="rounded-lg border border-nx-border px-3 py-1.5 text-xs"
                onClick={() => setModal({ open: false, prospect: null, text: '', clipboardOk: false })}
              >
                Seguir después
              </button>
              <PremiumGradientButton
                className="px-4 py-2 text-xs"
                disabled={!modal.prospect}
                onClick={() =>
                  modal.prospect &&
                  void handleMarkSent({
                    prospect_id: modal.prospect.id,
                    message: modal.text,
                    linkedin_url: '',
                  })
                }
              >
                Marcar como enviado (manual)
              </PremiumGradientButton>
            </div>
          }
        >
          <p className="text-[11px] text-nx-muted">
            {modal.clipboardOk === false
              ? 'Copiá el texto manualmente desde el cuadro.'
              : 'Mensaje en portapapeles. Revisá y enviá en LinkedIn antes de confirmar.'}
          </p>
          <textarea
            readOnly
            className="mt-2 w-full min-h-[10rem] rounded-lg border border-nx-border bg-nx-card-muted p-2 text-sm text-nx-ink"
            value={modal.text}
          />
        </Modal>
      ) : null}
    </section>
  )
}

