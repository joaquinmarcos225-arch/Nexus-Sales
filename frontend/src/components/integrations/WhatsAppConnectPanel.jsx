import { useCallback, useEffect, useRef, useState } from 'react'
import {
  cancelWhatsappMeowSession,
  createWhatsappMeowSession,
  disconnectWhatsappMeowSession,
  fetchWhatsappMeowEngineStatus,
  fetchWhatsappMeowQr,
  fetchWhatsappMeowSessions,
  fetchWhatsappMeowUsage,
  simulateWhatsappMeowPair,
} from '../../utils/api.js'
import {
  WHATSAPP_QR_FETCH_MS,
  WHATSAPP_SESSION_POLL_MS,
  isWhatsappPairingStatus,
  mergeSessionQr,
  resolvedSessionQr,
  sessionNeedsQrFetch,
  visibleWhatsappSessions,
  whatsappSessionNeedsPolling,
} from '../../utils/whatsappConnectPoll.js'
import { WhatsAppQrImage } from './WhatsAppQrImage.jsx'

const STATUS_LABEL = {
  pending_qr: 'Preparando QR…',
  qr_ready: 'Escaneá el QR',
  connecting: 'Conectando…',
  connected: 'Conectado',
  disconnected: 'Desconectado',
  cancelled: 'Cancelado',
  error: 'Error',
}

function isDevSimQr(session, qrCode) {
  const code = String(qrCode || session?.qr_code || '')
  return code.startsWith('SIM-')
}

export function WhatsAppConnectPanel({ companyId, userId }) {
  const [sessions, setSessions] = useState([])
  const [qrById, setQrById] = useState({})
  const [engineStatus, setEngineStatus] = useState(null)
  const [loading, setLoading] = useState(false)
  const [busyId, setBusyId] = useState(null)
  const [error, setError] = useState(null)
  const [pollError, setPollError] = useState(null)
  const [usage, setUsage] = useState(null)
  const qrFetchInFlight = useRef(new Set())

  const canRealQr = Boolean(engineStatus?.can_pair_real_qr)
  const devSimulateTools = Boolean(engineStatus?.dev_simulate_tools)
  const engineBanner = engineStatus?.user_message || null

  const rememberQr = useCallback((session) => {
    if (!session) return
    setQrById((prev) => mergeSessionQr(prev, session))
  }, [])

  const reload = useCallback(async ({ silent = false } = {}) => {
    if (!companyId || userId == null) {
      setSessions([])
      setEngineStatus(null)
      return
    }
    if (!silent) {
      setLoading(true)
      setError(null)
    }
    try {
      const [data, usageSnap, engineSnap] = await Promise.all([
        fetchWhatsappMeowSessions(companyId, userId),
        fetchWhatsappMeowUsage(companyId, userId).catch(() => null),
        fetchWhatsappMeowEngineStatus(companyId, userId).catch(() => null),
      ])
      const list = visibleWhatsappSessions(data)
      setSessions(list)
      list.forEach((row) => rememberQr(row))
      setUsage(usageSnap && typeof usageSnap === 'object' ? usageSnap : null)
      setEngineStatus(engineSnap && typeof engineSnap === 'object' ? engineSnap : null)
      if (silent) {
        setPollError(null)
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      if (silent) {
        setPollError(msg)
      } else {
        setError(msg)
      }
    } finally {
      if (!silent) {
        setLoading(false)
      }
    }
  }, [companyId, rememberQr, userId])

  useEffect(() => {
    void reload()
  }, [reload])

  const shouldPoll = sessions.some((s) => whatsappSessionNeedsPolling(s.status))

  useEffect(() => {
    if (!shouldPoll || !companyId || userId == null) {
      return undefined
    }

    function tick() {
      if (document.visibilityState !== 'visible') {
        return
      }
      void reload({ silent: true })
    }

    const id = window.setInterval(tick, WHATSAPP_SESSION_POLL_MS)
    const onVis = () => {
      if (document.visibilityState === 'visible') {
        void reload({ silent: true })
      }
    }
    document.addEventListener('visibilitychange', onVis)
    return () => {
      window.clearInterval(id)
      document.removeEventListener('visibilitychange', onVis)
    }
  }, [shouldPoll, companyId, userId, reload])

  const pullQr = useCallback(
    async (sessionUuid, { reloadAfter = false } = {}) => {
      if (!companyId || userId == null || !sessionUuid) return null
      if (qrFetchInFlight.current.has(sessionUuid)) return null
      qrFetchInFlight.current.add(sessionUuid)
      try {
        const view = await fetchWhatsappMeowQr(companyId, userId, sessionUuid)
        rememberQr(view)
        if (reloadAfter) {
          await reload({ silent: true })
        }
        return view
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
        return null
      } finally {
        qrFetchInFlight.current.delete(sessionUuid)
      }
    },
    [companyId, rememberQr, reload, userId],
  )

  useEffect(() => {
    if (!companyId || userId == null) return undefined
    const missing = sessions.filter((s) => sessionNeedsQrFetch(s, qrById))
    if (missing.length === 0) return undefined

    missing.forEach((s) => {
      void pullQr(s.session_uuid)
    })
    const id = window.setInterval(() => {
      sessions
        .filter((s) => sessionNeedsQrFetch(s, qrById))
        .forEach((s) => {
          void pullQr(s.session_uuid)
        })
    }, WHATSAPP_QR_FETCH_MS)
    return () => window.clearInterval(id)
  }, [companyId, pullQr, qrById, sessions, userId])

  async function handleConnect() {
    if (!companyId || userId == null) return
    if (engineStatus && !canRealQr && !devSimulateTools) {
      setError(engineStatus.user_message || 'Motor WhatsApp no disponible.')
      return
    }
    setBusyId('new')
    setError(null)
    try {
      const created = await createWhatsappMeowSession(companyId, userId, {})
      rememberQr(created)
      await reload()
      if (created?.session_uuid) {
        await pullQr(created.session_uuid)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyId(null)
    }
  }

  async function refreshQr(sessionUuid) {
    setBusyId(sessionUuid)
    setError(null)
    try {
      await pullQr(sessionUuid, { reloadAfter: true })
    } finally {
      setBusyId(null)
    }
  }

  async function handleCancel(sessionUuid) {
    setBusyId(sessionUuid)
    try {
      await cancelWhatsappMeowSession(companyId, userId, sessionUuid)
      setQrById((prev) => {
        const next = { ...prev }
        delete next[sessionUuid]
        return next
      })
      await reload()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyId(null)
    }
  }

  async function handleDisconnect(sessionUuid) {
    setBusyId(sessionUuid)
    try {
      await disconnectWhatsappMeowSession(companyId, userId, sessionUuid)
      await reload()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyId(null)
    }
  }

  async function handleSimulatePair(sessionUuid) {
    setBusyId(sessionUuid)
    try {
      await simulateWhatsappMeowPair(companyId, userId, sessionUuid)
      await reload()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyId(null)
    }
  }

  const pairingInProgress = sessions.some((s) =>
    ['pending_qr', 'qr_ready', 'connecting'].includes(String(s.status || '')),
  )

  return (
    <section className="rounded-xl border border-nx-border bg-nx-card p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-nx-ink">WhatsApp (tu número)</h2>
          <p className="mt-1 text-xs text-nx-muted">
            Vinculá uno o más números con QR. Sesiones aisladas por usuario. Basado en WhatsApp Web
            (whatsmeow) — no es la API oficial de Meta; hay riesgo de límites o suspensión.
          </p>
        </div>
        <button
          type="button"
          className="rounded-lg bg-emerald-700 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50"
          disabled={
            busyId === 'new' ||
            !companyId ||
            (engineStatus && !canRealQr && !devSimulateTools)
          }
          onClick={() => void handleConnect()}
        >
          {busyId === 'new' ? 'Creando…' : 'Conectar número'}
        </button>
      </div>

      {engineBanner ? (
        <div
          className={`mt-3 rounded-lg border px-3 py-2 text-xs ${
            canRealQr
              ? 'border-emerald-300 bg-emerald-50 text-emerald-950'
              : devSimulateTools
                ? 'border-amber-300 bg-amber-50 text-amber-950'
                : 'border-red-300 bg-red-50 text-red-900'
          }`}
        >
          <span className="font-semibold">
            {canRealQr
              ? 'Listo para vincular'
              : devSimulateTools
                ? 'Motor en configuración'
                : 'Motor no listo — reintentá en unos minutos'}
          </span>
          <p className="mt-1">{engineBanner}</p>
        </div>
      ) : null}

      {usage && Number(usage.packet_units) > 0 ? (
        <div className="mt-3 rounded-lg border border-nx-border/80 bg-white/50 px-3 py-2 text-xs text-nx-muted">
          <span className="font-semibold text-nx-ink">Consumo WhatsApp (empresa)</span>
          <span className="ml-2">
            {usage.packet_used ?? 0} / {usage.packet_units} unidades
          </span>
          {usage.estimated_spend_usd != null && usage.spend_cap_usd != null ? (
            <span className="ml-2">
              · ≈USD {(usage.estimated_spend_usd ?? 0).toFixed(2)} /{' '}
              {Number(usage.spend_cap_usd).toFixed(2)}
            </span>
          ) : null}
          {usage.spend_alert_level ? (
            <span className="ml-2 font-semibold text-amber-900">
              Alerta {usage.spend_alert_level}%
            </span>
          ) : null}
          {usage.auto_paused ? (
            <p className="mt-1 text-red-800">
              Automático pausado: {usage.paused_reason || 'revisá Support'}
            </p>
          ) : null}
        </div>
      ) : null}

      {error ? <p className="mt-3 text-xs text-red-700">{error}</p> : null}
      {pollError ? (
        <p className="mt-2 text-xs text-amber-800">Actualización automática: {pollError}</p>
      ) : null}
      {loading ? <p className="mt-3 text-xs text-nx-muted">Cargando sesiones…</p> : null}

      {pairingInProgress ? (
        <p className="mt-2 text-xs text-nx-muted">
          Abrí WhatsApp en el teléfono → Dispositivos vinculados → Vincular dispositivo → escaneá el
          QR de abajo. La conexión se confirma solo cuando el motor reporte «Conectado».
        </p>
      ) : null}

      <ul className="mt-4 space-y-3">
        {sessions.map((s) => {
          const st = String(s.status || '')
          const qr = resolvedSessionQr(s, qrById)
          const showQr = isWhatsappPairingStatus(st) && Boolean(qr.qr_png_base64 || qr.qr_code)
          const waitingQr = isWhatsappPairingStatus(st) && !showQr
          const devQr = isDevSimQr(s, qr.qr_code)
          return (
            <li
              key={s.session_uuid}
              className="rounded-lg border border-nx-border/80 bg-white/60 p-3 text-xs"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <span className="font-semibold text-nx-ink">
                    {s.phone_e164 || s.label || 'Nuevo número'}
                  </span>
                  <span className="ml-2 text-nx-muted">{STATUS_LABEL[st] || st}</span>
                  {devQr ? (
                    <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-amber-950">
                      QR simulado
                    </span>
                  ) : null}
                </div>
                <div className="flex flex-wrap gap-1">
                  {isWhatsappPairingStatus(st) ? (
                    <button
                      type="button"
                      className="rounded border px-2 py-1"
                      disabled={busyId === s.session_uuid}
                      onClick={() => void refreshQr(s.session_uuid)}
                    >
                      Actualizar QR
                    </button>
                  ) : null}
                  {st === 'qr_ready' || st === 'pending_qr' ? (
                    <button
                      type="button"
                      className="rounded border px-2 py-1"
                      disabled={busyId === s.session_uuid}
                      onClick={() => void handleCancel(s.session_uuid)}
                    >
                      Cancelar
                    </button>
                  ) : null}
                  {st === 'connected' ? (
                    <button
                      type="button"
                      className="rounded border px-2 py-1 text-red-800"
                      disabled={busyId === s.session_uuid}
                      onClick={() => void handleDisconnect(s.session_uuid)}
                    >
                      Desconectar
                    </button>
                  ) : null}
                  {devSimulateTools && !canRealQr && (st === 'qr_ready' || st === 'pending_qr') ? (
                    <button
                      type="button"
                      className="rounded border border-amber-400 px-2 py-1 text-amber-950"
                      disabled={busyId === s.session_uuid}
                      title="Solo modo desarrollo; no reemplaza el escaneo real"
                      onClick={() => void handleSimulatePair(s.session_uuid)}
                    >
                      Simular escaneo (solo dev)
                    </button>
                  ) : null}
                </div>
              </div>
              {s.last_error ? (
                <p className="mt-1 text-red-700">{s.last_error}</p>
              ) : null}
              {showQr ? (
                <WhatsAppQrImage pngBase64={qr.qr_png_base64} qrCode={qr.qr_code} />
              ) : null}
              {waitingQr ? (
                <p className="mt-2 text-nx-muted">
                  Generando QR en el motor… Si tarda más de unos segundos, tocá Actualizar QR. Si
                  sigue vacío, Cancelá y volvé a Conectar número.
                </p>
              ) : null}
            </li>
          )
        })}
      </ul>
      {!loading && sessions.length === 0 ? (
        <p className="mt-3 text-xs text-nx-muted">Todavía no conectaste ningún número.</p>
      ) : null}
    </section>
  )
}
