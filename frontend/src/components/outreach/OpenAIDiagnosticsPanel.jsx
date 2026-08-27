import { useCallback, useEffect, useState } from 'react'
import { fetchOpenAIDiagnostics } from '../../utils/api.js'

function CheckRow({ label, ok, detail }) {
  return (
      <div className="flex items-start justify-between gap-2 border-b border-nx-border/80 py-1.5 text-xs">
      <span className="text-nx-muted">{label}</span>
      <span className={`text-right font-medium ${ok ? 'text-red-800' : 'text-zinc-900'}`}>
        {detail ?? (ok ? 'Sí' : 'No')}
      </span>
    </div>
  )
}

export function OpenAIDiagnosticsPanel({ autoLoad = true, compact = false }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const load = useCallback(async (probe = false) => {
    setLoading(true)
    try {
      setError(null)
      const res = await fetchOpenAIDiagnostics({ probe })
      setData(res)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (autoLoad) {
      void load(false)
    }
  }, [autoLoad, load])

  if (error && !data) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-900">
        No se pudo cargar diagnóstico OpenAI: {error}
      </div>
    )
  }

  if (!data && loading) {
    return <p className="text-xs text-nx-muted">Cargando diagnóstico OpenAI…</p>
  }

  if (!data) {
    return null
  }

  const checks = data.checks || {}
  const lastErr = data.recent_errors?.[0]

  return (
    <div
      className={`rounded-lg border border-zinc-200 bg-zinc-50/80 text-xs text-zinc-950 ${
        compact ? 'p-2' : 'p-3'
      }`}
    >
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <p className="font-semibold uppercase tracking-wide text-zinc-800">
          Diagnóstico OpenAI
        </p>
        <div className="flex gap-2">
          <button
            type="button"
            disabled={loading}
            onClick={() => void load(false)}
            className="rounded border border-zinc-300 bg-white px-2 py-0.5 text-[10px] hover:bg-zinc-100 disabled:opacity-50"
          >
            Actualizar
          </button>
          <button
            type="button"
            disabled={loading}
            onClick={() => void load(true)}
            className="rounded border border-zinc-300 bg-white px-2 py-0.5 text-[10px] hover:bg-zinc-100 disabled:opacity-50"
          >
            Verificar API key
          </button>
        </div>
      </div>

      <dl className="grid gap-1 sm:grid-cols-2">
        <div>
          <dt className="text-zinc-600">Modelo</dt>
          <dd className="font-mono font-medium">{data.model}</dd>
        </div>
        <div>
          <dt className="text-zinc-600">Endpoint</dt>
          <dd className="font-mono">{data.endpoint}</dd>
        </div>
        <div>
          <dt className="text-zinc-600">API key (máscara)</dt>
          <dd className="font-mono">{data.api_key?.masked || '—'}</dd>
        </div>
        <div>
          <dt className="text-zinc-600">Tipo de clave</dt>
          <dd>{data.api_key?.key_type || '—'}</dd>
        </div>
        <div>
          <dt className="text-zinc-600">Requests / minuto</dt>
          <dd className="font-semibold">{data.requests_per_minute ?? 0}</dd>
        </div>
        <div>
          <dt className="text-zinc-600">Requests / 5 min</dt>
          <dd>{data.requests_last_5_minutes ?? 0}</dd>
        </div>
        <div>
          <dt className="text-zinc-600">Fallback dev</dt>
          <dd className={data.fallback_enabled ? 'font-semibold text-red-800' : 'text-zinc-900'}>
            {data.fallback_enabled ? 'Activo' : 'Inactivo'}
            {data.fallback_uses ? ` · ${data.fallback_uses} usos` : ''}
          </dd>
        </div>
        <div>
          <dt className="text-zinc-600">Último éxito</dt>
          <dd className="font-mono text-[10px]">{data.last_success_at || '—'}</dd>
        </div>
      </dl>

      {data.possible_request_loop ? (
        <p className="mt-2 rounded border border-zinc-300 bg-zinc-50 px-2 py-1 text-zinc-950">
          Posible loop de requests: {data.loop_hint}
        </p>
      ) : null}

      <div className="mt-2 rounded border border-zinc-200 bg-white/70 px-2 py-1">
        <CheckRow label="API key configurada" ok={checks.api_key_configured} />
        <CheckRow label="Formato de key válido" ok={checks.api_key_format_valid} />
        <CheckRow
          label="Cuenta/proyecto (tipo)"
          ok={checks.likely_correct_key_type}
          detail={data.api_key?.project_hint}
        />
        {checks.credit_or_quota_hint ? (
          <CheckRow label="Crédito/cuota" ok={false} detail={checks.credit_or_quota_hint} />
        ) : null}
        {checks.rpm_tpm_hint ? (
          <CheckRow label="RPM/TPM" ok={false} detail={checks.rpm_tpm_hint} />
        ) : null}
      </div>

      {data.last_probe ? (
        <div className="mt-2 rounded border border-zinc-200 bg-white/70 px-2 py-1">
          <p className="font-medium text-zinc-800">Última verificación</p>
          <p className={data.last_probe.ok ? 'text-red-800' : 'text-red-800'}>
            {data.last_probe.message}
          </p>
          {data.last_probe.target_model_listed === false ? (
            <p className="text-zinc-900">
              El modelo {data.model} no apareció en models.list — revisá OPENAI_MODEL.
            </p>
          ) : null}
        </div>
      ) : null}

      {lastErr ? (
        <details className="mt-2 rounded border border-red-200 bg-red-50/80 px-2 py-1">
          <summary className="cursor-pointer font-medium text-red-900">
            Último error OpenAI ({lastErr.error_type})
          </summary>
          <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap font-mono text-[10px] text-red-950">
            {lastErr.error_full}
          </pre>
          {lastErr.error_body ? (
            <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap font-mono text-[10px]">
              {typeof lastErr.error_body === 'string'
                ? lastErr.error_body
                : JSON.stringify(lastErr.error_body, null, 2)}
            </pre>
          ) : null}
          {lastErr.rate_limit_headers && Object.keys(lastErr.rate_limit_headers).length > 0 ? (
            <pre className="mt-1 font-mono text-[10px]">
              {JSON.stringify(lastErr.rate_limit_headers, null, 2)}
            </pre>
          ) : null}
        </details>
      ) : null}

      <p className="mt-2 text-[10px] text-zinc-700">
        Key desde {data.api_key_source}. Modelo desde {data.model_source}.
      </p>
    </div>
  )
}
