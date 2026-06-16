import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  editLeadProfileOutreach,
  generateLeadProfileOutreach,
  generateLeadProfileOutreachTest,
  generateLeadProfilePlaybookPreview,
  resetLeadProfileOutreachSequence,
} from '../../utils/api.js'
import { linkedInOpenUrl } from '../../utils/linkedinAssist.js'

const CHANNEL_LABELS = {
  email: 'Email',
  linkedin: 'LinkedIn',
  whatsapp: 'WhatsApp',
}

function IcpScoreBreakdownPanel({ breakdown, title = 'Score ICP — auditoría' }) {
  if (!breakdown) return null

  const rows = [
    ['Industria', breakdown.industry_score],
    ['Cargo', breakdown.role_score],
    ['Tamaño empresa', breakdown.company_size_score],
    ['País', breakdown.country_score],
    ['Señales adicionales', breakdown.additional_signals_score],
  ]

  return (
    <div className="rounded-lg border border-sky-200 bg-sky-50/60 p-3 text-[11px] text-sky-950">
      <p className="font-semibold text-sky-900">{title}</p>
      {breakdown.formula_explanation ? (
        <p className="mt-0.5 text-[10px] text-sky-800">Fórmula: {breakdown.formula_explanation}</p>
      ) : null}
      <dl className="mt-2 space-y-1">
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-center justify-between gap-2">
            <dt className="text-sky-800">{label}</dt>
            <dd className="font-semibold tabular-nums">{value ?? 0}%</dd>
          </div>
        ))}
        <div className="flex items-center justify-between gap-2 border-t border-sky-200 pt-1.5">
          <dt className="font-bold text-sky-900">Score final (contacto)</dt>
          <dd className="text-sm font-bold tabular-nums text-sky-950">{breakdown.final_score}%</dd>
        </div>
      </dl>
      {breakdown.company_only_score != null ? (
        <p className="mt-2 text-[10px] text-amber-900">
          Score solo empresa (sin cargo): {breakdown.company_only_score}% — explica scores altos con rol
          incorrecto.
        </p>
      ) : null}
      {breakdown.legacy_compatibility_score != null &&
      breakdown.legacy_compatibility_score > (breakdown.final_score ?? 0) + 10 ? (
        <p className="mt-1 text-[10px] font-medium text-rose-800">
          Score legacy del pipeline: {breakdown.legacy_compatibility_score}% (sobrevaloraba sin considerar
          cargo).
        </p>
      ) : null}
      {breakdown.role_mismatch_cap_applied ? (
        <p className="mt-1 text-[10px] font-medium text-amber-950">
          Se aplicó tope por mismatch de cargo ICP vs contacto.
        </p>
      ) : null}
      {breakdown.notes?.length ? (
        <ul className="mt-2 list-inside list-disc space-y-0.5 text-[10px] text-zinc-700">
          {breakdown.notes.map((note, i) => (
            <li key={`icp-note-${i}`}>{note}</li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

function ContactDetailPanel({ profile, extra }) {
  const person = profile?.person || {}
  const company = profile?.company || {}
  const alignment = profile?.role_alignment
  const li = linkedInOpenUrl(person.linkedin_url || extra?.linkedin_url)
  const phone = person.phone || extra?.phone || '—'
  const whatsapp = person.whatsapp_number || extra?.whatsapp_number || extra?.whatsapp || '—'

  return (
    <div className="rounded-xl border border-zinc-200 bg-zinc-50/80 p-3">
      <p className="text-xs font-semibold text-zinc-900">Outreach — contacto</p>
      <dl className="mt-2 grid gap-1.5 text-[11px] text-zinc-700">
        <div>
          <dt className="font-medium text-zinc-500">Nombre</dt>
          <dd className="font-semibold text-zinc-900">{person.name || '—'}</dd>
        </div>
        <div>
          <dt className="font-medium text-zinc-500">Empresa</dt>
          <dd>{company.name || '—'}</dd>
        </div>
        <div>
          <dt className="font-medium text-zinc-500">Cargo ICP objetivo</dt>
          <dd>{alignment?.icp_target_role || '—'}</dd>
        </div>
        <div>
          <dt className="font-medium text-zinc-500">Cargo real encontrado</dt>
          <dd>{alignment?.prospect_actual_role || person.role || '—'}</dd>
        </div>
        {alignment?.selling_to_role ? (
          <div>
            <dt className="font-medium text-zinc-500">Rol al que vende el mensaje</dt>
            <dd className="font-semibold text-violet-900">{alignment.selling_to_role}</dd>
          </div>
        ) : null}
        {alignment?.warning ? (
          <div className="rounded-md border border-amber-300 bg-amber-50 px-2 py-1.5 text-amber-950">
            <dt className="font-semibold text-amber-900">Advertencia de rol</dt>
            <dd className="mt-0.5">{alignment.warning}</dd>
          </div>
        ) : null}
        <IcpScoreBreakdownPanel breakdown={profile?.icp_score_breakdown} />
        <div>
          <dt className="font-medium text-zinc-500">Email</dt>
          <dd className="break-all">{person.email || '—'}</dd>
        </div>
        <div>
          <dt className="font-medium text-zinc-500">LinkedIn</dt>
          <dd className="break-all">
            {li ? (
              <a href={li} target="_blank" rel="noreferrer" className="font-medium text-sky-800 hover:underline">
                {li}
              </a>
            ) : (
              '—'
            )}
          </dd>
        </div>
        <div>
          <dt className="font-medium text-zinc-500">Teléfono</dt>
          <dd>{phone}</dd>
        </div>
        <div>
          <dt className="font-medium text-zinc-500">WhatsApp</dt>
          <dd>{whatsapp}</dd>
        </div>
      </dl>
    </div>
  )
}

function currentTouch(profile) {
  const completed = profile?.playbook_state?.completed
  if (!Array.isArray(completed) || !completed.length) return null
  return completed[completed.length - 1]
}

function SdrReasoningPanel({ reasoning }) {
  if (!reasoning) return null
  const hasContent =
    reasoning.probable_problem ||
    reasoning.why_it_matters ||
    reasoning.hypothesis ||
    reasoning.response_question ||
    reasoning.selling_to_role
  if (!hasContent) return null

  return (
    <details className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50/50 px-3 py-2 text-[11px] text-emerald-950">
      <summary className="cursor-pointer font-semibold text-emerald-900">
        Razonamiento SDR (antes del mensaje)
      </summary>
      <dl className="mt-2 grid gap-2">
        {reasoning.selling_to_role ? (
          <div>
            <dt className="font-medium text-emerald-800">Rol al que vende</dt>
            <dd className="mt-0.5 font-semibold text-emerald-950">{reasoning.selling_to_role}</dd>
          </div>
        ) : null}
        {reasoning.probable_problem ? (
          <div>
            <dt className="font-medium text-emerald-800">Problema probable</dt>
            <dd className="mt-0.5 text-emerald-950">{reasoning.probable_problem}</dd>
          </div>
        ) : null}
        {reasoning.why_it_matters ? (
          <div>
            <dt className="font-medium text-emerald-800">Por qué importa</dt>
            <dd className="mt-0.5 text-emerald-950">{reasoning.why_it_matters}</dd>
          </div>
        ) : null}
        {reasoning.hypothesis ? (
          <div>
            <dt className="font-medium text-emerald-800">Hipótesis</dt>
            <dd className="mt-0.5 text-emerald-950">{reasoning.hypothesis}</dd>
          </div>
        ) : null}
        {reasoning.response_question ? (
          <div>
            <dt className="font-medium text-emerald-800">Pregunta que busca respuesta</dt>
            <dd className="mt-0.5 text-emerald-950">{reasoning.response_question}</dd>
          </div>
        ) : null}
      </dl>
    </details>
  )
}

function OpenAIGenerationDebugSection({ generationDebug }) {
  if (!generationDebug) return null

  const tokenParts = [
    generationDebug.input_tokens != null ? `in ${generationDebug.input_tokens}` : null,
    generationDebug.output_tokens != null ? `out ${generationDebug.output_tokens}` : null,
    generationDebug.total_tokens != null ? `total ${generationDebug.total_tokens}` : null,
  ].filter(Boolean)

  return (
    <details className="mt-2 rounded border border-violet-300 bg-violet-50/80">
      <summary className="cursor-pointer px-2 py-1.5 font-semibold text-violet-950">
        Generación OpenAI — depuración completa
      </summary>
      <div className="space-y-2 border-t border-violet-200 px-2 py-2 text-[10px] text-violet-950">
        <dl className="grid gap-1">
          {generationDebug.channel ? (
            <div className="flex gap-2">
              <dt className="font-medium text-violet-800">Canal</dt>
              <dd>{generationDebug.channel}</dd>
            </div>
          ) : null}
          {generationDebug.step_day != null ? (
            <div className="flex gap-2">
              <dt className="font-medium text-violet-800">Día playbook</dt>
              <dd>{generationDebug.step_day}</dd>
            </div>
          ) : null}
          {generationDebug.model ? (
            <div className="flex gap-2">
              <dt className="font-medium text-violet-800">Modelo</dt>
              <dd>{generationDebug.model}</dd>
            </div>
          ) : null}
          {tokenParts.length ? (
            <div className="flex gap-2">
              <dt className="font-medium text-violet-800">Tokens</dt>
              <dd>{tokenParts.join(' · ')}</dd>
            </div>
          ) : null}
          {generationDebug.temperature != null ? (
            <div className="flex gap-2">
              <dt className="font-medium text-violet-800">Temperature</dt>
              <dd>{generationDebug.temperature}</dd>
            </div>
          ) : null}
          {generationDebug.max_output_tokens != null ? (
            <div className="flex gap-2">
              <dt className="font-medium text-violet-800">Max output tokens</dt>
              <dd>{generationDebug.max_output_tokens}</dd>
            </div>
          ) : null}
        </dl>

        {generationDebug.parse_error ? (
          <div>
            <p className="font-medium text-red-800">Error de parsing</p>
            <pre className="mt-0.5 max-h-24 overflow-auto whitespace-pre-wrap rounded border border-red-200 bg-white px-2 py-1 text-red-950">
              {generationDebug.parse_error}
            </pre>
          </div>
        ) : null}

        {generationDebug.stacktrace ? (
          <details>
            <summary className="cursor-pointer font-medium text-violet-900">Stacktrace</summary>
            <pre className="mt-0.5 max-h-32 overflow-auto whitespace-pre-wrap rounded border border-violet-200 bg-white px-2 py-1 font-mono text-[9px] leading-relaxed">
              {generationDebug.stacktrace}
            </pre>
          </details>
        ) : null}

        {generationDebug.expected_json_schema ? (
          <details>
            <summary className="cursor-pointer font-medium text-violet-900">JSON esperado por el parser</summary>
            <pre className="mt-0.5 max-h-24 overflow-auto whitespace-pre-wrap rounded border border-violet-200 bg-white px-2 py-1 font-mono">
              {generationDebug.expected_json_schema}
            </pre>
          </details>
        ) : null}

        {generationDebug.prompt_system ? (
          <details>
            <summary className="cursor-pointer font-medium text-violet-900">Prompt system (completo)</summary>
            <pre className="mt-0.5 max-h-48 overflow-auto whitespace-pre-wrap rounded border border-violet-200 bg-white px-2 py-1 font-mono leading-relaxed">
              {generationDebug.prompt_system}
            </pre>
          </details>
        ) : null}

        {generationDebug.prompt_user ? (
          <details open>
            <summary className="cursor-pointer font-medium text-violet-900">Prompt user (completo)</summary>
            <pre className="mt-0.5 max-h-64 overflow-auto whitespace-pre-wrap rounded border border-violet-200 bg-white px-2 py-1 font-mono leading-relaxed">
              {generationDebug.prompt_user}
            </pre>
          </details>
        ) : null}

        {generationDebug.raw_response ? (
          <details open>
            <summary className="cursor-pointer font-medium text-violet-900">Respuesta RAW de OpenAI</summary>
            <pre className="mt-0.5 max-h-72 overflow-auto whitespace-pre-wrap rounded border border-amber-300 bg-amber-50/50 px-2 py-1 font-mono leading-relaxed text-zinc-900">
              {generationDebug.raw_response}
            </pre>
          </details>
        ) : null}

        {generationDebug.stripped_response &&
        generationDebug.stripped_response !== generationDebug.raw_response ? (
          <details>
            <summary className="cursor-pointer font-medium text-violet-900">Respuesta tras quitar fences markdown</summary>
            <pre className="mt-0.5 max-h-48 overflow-auto whitespace-pre-wrap rounded border border-violet-200 bg-white px-2 py-1 font-mono leading-relaxed">
              {generationDebug.stripped_response}
            </pre>
          </details>
        ) : null}
      </div>
    </details>
  )
}

function ValidationDebugPanel({ validation }) {
  if (!validation) return null

  const hasParseDebug = Boolean(validation.generation_debug)
  const isParseWarning =
    hasParseDebug &&
    validation.generation_debug?.parse_error &&
    validation.rejected_body?.length >= 20

  const panelClass = isParseWarning
    ? 'mt-2 rounded-lg border border-amber-300 bg-amber-50/80 px-3 py-2 text-[11px] text-amber-950'
    : 'mt-2 rounded-lg border border-rose-300 bg-rose-50/80 px-3 py-2 text-[11px] text-rose-950'

  const titleClass = isParseWarning ? 'font-semibold text-amber-900' : 'font-semibold text-rose-900'
  const summaryClass = isParseWarning ? 'mt-1 text-amber-800' : 'mt-1 text-rose-800'

  return (
    <div className={panelClass}>
      <p className={titleClass}>
        {hasParseDebug ? 'Depuración de validación y generación OpenAI' : 'Depuración de validación'}
      </p>
      {validation.summary ? (
        <p className={summaryClass}>{validation.summary}</p>
      ) : null}

      <OpenAIGenerationDebugSection generationDebug={validation.generation_debug} />

      <dl className="mt-2 grid gap-1">
        {validation.word_count != null ? (
          <div className="flex gap-2">
            <dt className="font-medium text-rose-700">Palabras</dt>
            <dd>{validation.word_count}</dd>
          </div>
        ) : null}
        {validation.char_count != null ? (
          <div className="flex gap-2">
            <dt className="font-medium text-rose-700">Caracteres</dt>
            <dd>{validation.char_count}</dd>
          </div>
        ) : null}
        {validation.attempts ? (
          <div className="flex gap-2">
            <dt className="font-medium text-rose-700">Intentos</dt>
            <dd>{validation.attempts}</dd>
          </div>
        ) : null}
      </dl>

      {validation.issues?.length ? (
        <div className="mt-2">
          <p className="font-medium text-rose-800">Reglas incumplidas ({validation.issues.length})</p>
          <ul className="mt-1 list-inside list-disc space-y-0.5 text-rose-900">
            {validation.issues.map((issue, i) => (
              <li key={`issue-${i}`}>{issue}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {validation.banned_matches?.length ? (
        <div className="mt-2">
          <p className="font-medium text-rose-800">Pitch / frases detectadas</p>
          <ul className="mt-1 space-y-1">
            {validation.banned_matches.map((m, i) => (
              <li key={`ban-${i}`} className="rounded bg-white/70 px-2 py-1 text-rose-900">
                <span className="font-medium">{m.field}</span> · {m.rule}:{' '}
                <span className="font-semibold">&quot;{m.phrase}&quot;</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {validation.rejected_sections ? (
        <details className="mt-2">
          <summary className="cursor-pointer font-medium text-rose-800">Bloques generados (sections)</summary>
          <dl className="mt-1 space-y-2">
            {Object.entries(validation.rejected_sections).map(([key, text]) => (
              <div key={key}>
                <dt className="font-medium capitalize text-rose-700">{key}</dt>
                <dd className="mt-0.5 whitespace-pre-wrap rounded bg-white/70 px-2 py-1">{text}</dd>
              </div>
            ))}
          </dl>
        </details>
      ) : null}

      {validation.rejected_subject ? (
        <div className="mt-2">
          <p className="font-medium text-rose-800">Asunto generado</p>
          <p className="mt-0.5 rounded bg-white/70 px-2 py-1">{validation.rejected_subject}</p>
        </div>
      ) : null}

      {validation.rejected_body ? (
        <div className="mt-2">
          <p className={`font-medium ${isParseWarning ? 'text-amber-900' : 'text-rose-800'}`}>
            {isParseWarning ? 'Texto recuperado (sin JSON válido)' : 'Borrador completo rechazado'}
          </p>
          <pre
            className={`mt-0.5 max-h-48 overflow-auto whitespace-pre-wrap rounded border px-2 py-1.5 text-[10px] leading-relaxed text-zinc-800 ${
              isParseWarning ? 'border-amber-200 bg-amber-50/70' : 'border-rose-200 bg-white'
            }`}
          >
            {validation.rejected_body}
          </pre>
        </div>
      ) : null}
    </div>
  )
}

const VALIDATION_STATUS_LABELS = {
  valid: { text: 'Válido', className: 'bg-emerald-100 text-emerald-900' },
  warning: { text: 'Advertencia', className: 'bg-amber-100 text-amber-950' },
  rejected: { text: 'Rechazado', className: 'bg-rose-100 text-rose-900' },
}

function PlaybookFullPreviewPanel({ preview, onClose }) {
  if (!preview) return null

  const audit = preview.audit

  return (
    <div className="mt-3 space-y-3 rounded-xl border border-indigo-300 bg-indigo-50/40 p-3 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-xs font-semibold text-indigo-950">Vista previa completa del playbook</p>
          <p className="mt-0.5 text-[10px] text-indigo-900/80">
            {preview.lead_name} · {preview.company_name} — simulación sin respuesta (no modifica producción)
          </p>
        </div>
        {onClose ? (
          <button
            type="button"
            className="text-[10px] font-medium text-indigo-800 underline"
            onClick={onClose}
          >
            Cerrar vista previa
          </button>
        ) : null}
      </div>

      {preview.message ? (
        <p
          className={`text-[11px] font-medium ${
            preview.ok
              ? preview.rejected_count > 0
                ? 'text-amber-900'
                : 'text-emerald-800'
              : 'text-amber-900'
          }`}
        >
          {preview.message}
        </p>
      ) : null}

      {preview.valid_count != null || preview.rejected_count != null ? (
        <p className="text-[10px] text-indigo-800">
          Resumen: {preview.valid_count ?? 0} válidos · {preview.rejected_count ?? 0} rechazados ·{' '}
          {preview.warning_count ?? 0} advertencias
          {preview.skipped_count ? ` · ${preview.skipped_count} omitidos` : ''}
        </p>
      ) : null}

      {audit ? (
        <div className="rounded-lg border border-indigo-200 bg-white/80 p-3 text-[11px] text-zinc-800">
          <p className="font-semibold text-indigo-950">Contexto de auditoría</p>
          <dl className="mt-2 grid gap-2">
            <div>
              <dt className="font-medium text-indigo-800">Producto — auditoría completa</dt>
              <dd className="mt-1 space-y-2">
                {audit.product?.name ? (
                  <p>
                    <span className="font-medium">Nombre:</span> {audit.product.name}
                  </p>
                ) : null}
                {audit.product?.original_description ? (
                  <div>
                    <p className="font-medium">Descripción original</p>
                    <pre className="mt-0.5 max-h-48 overflow-auto whitespace-pre-wrap rounded border border-indigo-100 bg-white/80 px-2 py-1 text-[10px]">
                      {audit.product.original_description}
                    </pre>
                  </div>
                ) : null}
                {audit.product?.interpreted_summary ? (
                  <div>
                    <p className="font-medium">Resumen interpretado</p>
                    <pre className="mt-0.5 whitespace-pre-wrap rounded border border-indigo-100 bg-white/80 px-2 py-1 text-[10px]">
                      {audit.product.interpreted_summary}
                    </pre>
                  </div>
                ) : null}
                {audit.product?.extracted_problems ? (
                  <div>
                    <p className="font-medium">Problemas que cree resolver</p>
                    <pre className="mt-0.5 whitespace-pre-wrap rounded border border-indigo-100 bg-white/80 px-2 py-1 text-[10px]">
                      {audit.product.extracted_problems}
                    </pre>
                  </div>
                ) : null}
                {audit.product?.extracted_benefits ? (
                  <div>
                    <p className="font-medium">Beneficios extraídos</p>
                    <pre className="mt-0.5 whitespace-pre-wrap rounded border border-indigo-100 bg-white/80 px-2 py-1 text-[10px]">
                      {audit.product.extracted_benefits}
                    </pre>
                  </div>
                ) : null}
              </dd>
            </div>
            <div>
              <dt className="font-medium text-indigo-800">Perfil / ICP</dt>
              <dd className="mt-0.5 space-y-1">
                <p>Industria ICP: {audit.icp_industry || '—'}</p>
                <p>
                  <span className="font-medium">Cargo ICP objetivo:</span>{' '}
                  {audit.icp_target_role || audit.role_alignment?.icp_target_role || '—'}
                </p>
                <p>
                  <span className="font-medium">Cargo real encontrado:</span>{' '}
                  {audit.prospect_actual_role || audit.role_alignment?.prospect_actual_role || '—'}
                </p>
                {audit.role_alignment?.selling_to_role ? (
                  <p>
                    <span className="font-medium">Rol al que vende el mensaje:</span>{' '}
                    {audit.role_alignment.selling_to_role}
                  </p>
                ) : null}
                <p>
                  Prospecto: {audit.prospect_actual_role || '—'} @ {audit.prospect_industry || '—'}
                  {audit.icp_score != null ? ` · ICP score contacto ${audit.icp_score}%` : ''}
                </p>
                {audit.role_alignment?.match_score != null ? (
                  <p className="text-[10px] text-indigo-700">
                    Coincidencia rol: {audit.role_alignment.match_score}% (
                    {audit.role_alignment.alignment_level})
                  </p>
                ) : null}
              </dd>
            </div>
            {audit.role_alignment?.warning ? (
              <div className="rounded-md border border-amber-400 bg-amber-50 px-2 py-1.5 text-amber-950">
                <dt className="font-semibold">Advertencia de rol</dt>
                <dd className="mt-0.5">{audit.role_alignment.warning}</dd>
              </div>
            ) : null}
            <IcpScoreBreakdownPanel
              breakdown={audit.icp_score_breakdown}
              title="Score ICP — desglose (modo testing)"
            />
            {audit.identified_pain ? (
              <div>
                <dt className="font-medium text-indigo-800">Resultado principal (Día 1)</dt>
                <dd className="mt-0.5 whitespace-pre-wrap">{audit.identified_pain}</dd>
              </div>
            ) : null}
            {audit.identified_benefit ? (
              <div>
                <dt className="font-medium text-indigo-800">Beneficio identificado (Día 1)</dt>
                <dd className="mt-0.5 whitespace-pre-wrap">{audit.identified_benefit}</dd>
              </div>
            ) : null}
          </dl>
        </div>
      ) : null}

      <div className="space-y-3">
        {(preview.touches || []).map((t) => {
          const statusMeta = VALIDATION_STATUS_LABELS[t.validation_status] || VALIDATION_STATUS_LABELS.valid
          return (
          <div
            key={`preview-${t.day}-${t.channel}`}
            className={`rounded-lg border p-3 text-[11px] ${
              t.skipped
                ? 'border-zinc-200 bg-zinc-50'
                : t.validation_status === 'rejected'
                  ? 'border-rose-300 bg-rose-50/60'
                  : t.validation_status === 'warning'
                    ? 'border-amber-300 bg-amber-50/60'
                    : 'border-indigo-200 bg-white'
            }`}
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-full bg-indigo-100 px-2 py-0.5 text-[10px] font-bold text-indigo-900">
                Día {t.day}
              </span>
              <span className="font-semibold text-zinc-900">
                {CHANNEL_LABELS[t.channel] || t.channel}
              </span>
              <span className="text-[10px] text-zinc-500">Toque #{t.touch_index}</span>
              <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] text-zinc-600">
                Estado: {t.expected_state || 'sin respuesta'}
              </span>
              {!t.skipped && t.generated ? (
                <span
                  className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${statusMeta.className}`}
                >
                  {statusMeta.text}
                </span>
              ) : null}
              {t.skipped ? (
                <span className="rounded bg-zinc-200 px-1.5 py-0.5 text-[10px] font-medium text-zinc-700">
                  Omitido
                </span>
              ) : null}
            </div>

            <p className="mt-1.5 text-[10px] font-medium text-indigo-800">Objetivo</p>
            <p className="text-zinc-700">{t.objective}</p>

            {t.prior_context?.length > 0 ? (
              <details className="mt-2">
                <summary className="cursor-pointer font-medium text-indigo-900">
                  Contexto previo utilizado ({t.prior_context.length} toque
                  {t.prior_context.length === 1 ? '' : 's'})
                </summary>
                <ul className="mt-2 space-y-2">
                  {t.prior_context.map((p, i) => (
                    <li
                      key={`prior-${t.day}-${p.day}-${p.channel}-${i}`}
                      className="rounded border border-indigo-100 bg-indigo-50/50 px-2 py-1.5"
                    >
                      <p className="font-medium text-indigo-900">
                        Día {p.day} · {CHANNEL_LABELS[p.channel] || p.channel}
                        {p.subject ? ` · Asunto: ${p.subject}` : ''}
                      </p>
                      <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap text-[10px] leading-relaxed text-zinc-800">
                        {p.body}
                      </pre>
                    </li>
                  ))}
                </ul>
              </details>
            ) : (
              <p className="mt-2 text-[10px] italic text-zinc-500">Sin toques anteriores (primer contacto).</p>
            )}

            {t.skipped ? (
              <p className="mt-2 text-amber-900">{t.skip_reason || 'Canal no disponible.'}</p>
            ) : t.body ? (
              <>
                <SdrReasoningPanel reasoning={t.sdr_reasoning} />
                {t.subject ? (
                  <p className="mt-2">
                    <span className="font-medium text-zinc-700">Asunto:</span> {t.subject}
                  </p>
                ) : null}
                <p className="mt-1 font-medium text-zinc-700">Mensaje completo</p>
                <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded border border-zinc-200 bg-zinc-50 px-2 py-1.5 text-[10px] leading-relaxed text-zinc-900">
                  {t.body}
                </pre>
              </>
            ) : null}

            {t.validation ? <ValidationDebugPanel validation={t.validation} /> : null}
          </div>
        )})}
      </div>
    </div>
  )
}

function pendingTouch(profile) {
  return profile?.playbook_state?.pending || null
}

function productionPlaybookSummary(profile) {
  const state = profile?.playbook_state
  const completed = Array.isArray(state?.completed) ? state.completed : []
  const pending = state?.pending
  const last = completed.length ? completed[completed.length - 1] : null

  if (state?.paused) {
    return {
      completedCount: completed.length,
      statusLabel: 'Secuencia pausada',
      statusDetail: state.pause_reason || 'El prospecto respondió.',
      nextChannel: null,
      nextDay: null,
      nextTouchIndex: null,
    }
  }

  if (pending) {
    return {
      completedCount: completed.length,
      statusLabel: last ? `Último toque: Día ${last.day}` : 'Sin toques generados',
      statusDetail: last
        ? `${CHANNEL_LABELS[last.channel] || last.channel} · toque #${last.touch_index || completed.length}`
        : 'Listo para iniciar secuencia',
      nextChannel: pending.channel,
      nextDay: pending.day,
      nextTouchIndex: pending.touch_index,
    }
  }

  if (last) {
    return {
      completedCount: completed.length,
      statusLabel: `Secuencia completa · último Día ${last.day}`,
      statusDetail: `${CHANNEL_LABELS[last.channel] || last.channel}`,
      nextChannel: null,
      nextDay: null,
      nextTouchIndex: null,
    }
  }

  return {
    completedCount: 0,
    statusLabel: 'Sin toques',
    statusDetail: 'Listo para Día 1',
    nextChannel: null,
    nextDay: null,
    nextTouchIndex: null,
  }
}

export function MvpOutreachWorkspace({
  campaignId,
  profiles = [],
  prospectingRows = [],
  freeze = false,
  onPipelineUpdate,
  initialSelectedId = '',
  onSelectLead,
}) {
  const [selectedId, setSelectedId] = useState(initialSelectedId || '')
  const [editBody, setEditBody] = useState('')
  const [editSubject, setEditSubject] = useState('')
  const [busy, setBusy] = useState('')
  const [status, setStatus] = useState(null)
  const [testDraft, setTestDraft] = useState(null)
  const [playbookPreview, setPlaybookPreview] = useState(null)

  const prospectingById = useMemo(() => {
    const map = new Map()
    for (const row of prospectingRows) {
      if (row?.external_id) map.set(row.external_id, row)
    }
    return map
  }, [prospectingRows])

  const readyProfiles = useMemo(() => {
    const byId = new Map()
    for (const p of profiles) {
      if (p?.ready_for_outreach && p?.has_real_contact && p.external_id) {
        byId.set(p.external_id, p)
      }
    }
    return Array.from(byId.values()).sort((a, b) =>
      (a.person?.name || '').localeCompare(b.person?.name || ''),
    )
  }, [profiles])

  const selected = useMemo(
    () => readyProfiles.find((p) => p.external_id === selectedId) || null,
    [readyProfiles, selectedId],
  )

  const selectedExtra = selected ? prospectingById.get(selected.external_id) : null
  const touch = selected ? currentTouch(selected) : null
  const pending = selected ? pendingTouch(selected) : null
  const prodSummary = selected ? productionPlaybookSummary(selected) : null
  const activeTouch = testDraft || touch
  const isTestingView = Boolean(testDraft)
  const activeChannel = activeTouch?.channel || 'email'

  const syncEditorFromTouch = useCallback((t) => {
    if (!t) {
      setEditBody('')
      setEditSubject('')
      return
    }
    setEditBody(t.body || '')
    setEditSubject(t.subject || '')
  }, [])

  const applyResult = useCallback(
    (result, { testing = false } = {}) => {
      if (result?.pipeline) {
        onPipelineUpdate?.(result.pipeline)
      }
      if (result?.ok && result?.touch) {
        if (testing || result.testing) {
          setTestDraft(result.touch)
          syncEditorFromTouch(result.touch)
        } else {
          setTestDraft(null)
          syncEditorFromTouch(result.touch)
        }
        setStatus({ type: 'ok', text: result.message || 'Borrador generado.' })
      } else if (result && result.ok === false) {
        const detail = result.detail || result.message || 'Error desconocido'
        console.error('[Outreach] generate failed:', result)
        if (result.validation?.rejected_body) {
          syncEditorFromTouch({
            body: result.validation.rejected_body,
            subject: result.validation.rejected_subject || '',
            channel: result.validation.channel || 'email',
          })
        }
        setStatus({
          type: 'error',
          text: result.message || detail,
          validation: result.validation || null,
          backend: result,
        })
      }
    },
    [onPipelineUpdate, syncEditorFromTouch],
  )

  const handleGeneratePlaybookPreview = async () => {
    if (!selected || !campaignId) return
    setBusy('preview')
    setPlaybookPreview(null)
    setStatus({
      type: 'loading',
      text: 'Generando vista previa completa del playbook (7 toques)… puede tardar varios minutos.',
    })
    try {
      const result = await generateLeadProfilePlaybookPreview(campaignId, selected.external_id)
      setPlaybookPreview(result)
      setStatus({
        type: 'ok',
        text: result.message || 'Vista previa generada.',
      })
    } catch (e) {
      const text = e instanceof Error ? e.message : String(e)
      console.error('[Outreach] playbook preview error:', e)
      setStatus({ type: 'error', text })
    } finally {
      setBusy('')
    }
  }

  const handleGenerateTest = async (channel) => {
    if (!selected || !campaignId) return
    setBusy(`test-${channel}`)
    setStatus({ type: 'loading', text: `Generando borrador de prueba · ${CHANNEL_LABELS[channel]}…` })
    try {
      const result = await generateLeadProfileOutreachTest(
        campaignId,
        selected.external_id,
        channel,
      )
      applyResult(result, { testing: true })
    } catch (e) {
      const text = e instanceof Error ? e.message : String(e)
      console.error('[Outreach] test generate error:', e)
      setStatus({ type: 'error', text })
    } finally {
      setBusy('')
    }
  }

  const handleResetSequence = async () => {
    if (!selected || !campaignId) return
    const name = selected.person?.name || 'este lead'
    if (
      !window.confirm(
        `¿Reiniciar la secuencia de ${name}? Se borrarán los toques del playbook. Los datos de prospección no se eliminan.`,
      )
    ) {
      return
    }
    setBusy('reset')
    setStatus(null)
    try {
      const pipe = await resetLeadProfileOutreachSequence(campaignId, selected.external_id)
      onPipelineUpdate?.(pipe)
      setTestDraft(null)
      syncEditorFromTouch(null)
      setStatus({ type: 'ok', text: 'Secuencia reiniciada — vuelve al Día 1.' })
    } catch (e) {
      const text = e instanceof Error ? e.message : String(e)
      console.error('[Outreach] reset error:', e)
      setStatus({ type: 'error', text })
    } finally {
      setBusy('')
    }
  }

  const handleGenerateNext = async (regenerate = false) => {
    if (!selected || !campaignId) return
    setBusy(regenerate ? 'regen' : 'gen')
    setStatus(regenerate ? null : { type: 'loading', text: 'Generando próximo toque del playbook…' })
    try {
      const result = await generateLeadProfileOutreach(campaignId, selected.external_id, { regenerate })
      applyResult(result)
    } catch (e) {
      const text = e instanceof Error ? e.message : String(e)
      console.error('[Outreach] request error:', e)
      setStatus({ type: 'error', text })
    } finally {
      setBusy('')
    }
  }

  const handleSaveEdit = async () => {
    if (!selected || !campaignId || !touch || isTestingView) return
    setBusy('save')
    setStatus(null)
    try {
      const pipe = await editLeadProfileOutreach(campaignId, selected.external_id, {
        channel: touch.channel,
        slot: 'initial',
        subject: touch.channel === 'email' ? editSubject : null,
        body: editBody,
      })
      onPipelineUpdate?.(pipe)
      setStatus({ type: 'ok', text: 'Borrador guardado.' })
    } catch (e) {
      const text = e instanceof Error ? e.message : String(e)
      console.error('[Outreach] save error:', e)
      setStatus({ type: 'error', text })
    } finally {
      setBusy('')
    }
  }

  const handleCopy = async () => {
    const text =
      activeChannel === 'email' ? `Asunto: ${editSubject}\n\n${editBody}` : editBody
    try {
      await navigator.clipboard.writeText(text)
      setStatus({ type: 'ok', text: 'Copiado al portapapeles.' })
    } catch {
      setStatus({ type: 'error', text: 'No se pudo copiar.' })
    }
  }

  const pickProfile = (id) => {
    setSelectedId(id)
    onSelectLead?.(id)
    setStatus(null)
    setTestDraft(null)
    setPlaybookPreview(null)
    const p = readyProfiles.find((x) => x.external_id === id)
    syncEditorFromTouch(currentTouch(p))
  }

  useEffect(() => {
    if (initialSelectedId) setSelectedId(initialSelectedId)
  }, [initialSelectedId])

  useEffect(() => {
    if (!selectedId && readyProfiles[0]?.external_id) {
      pickProfile(readyProfiles[0].external_id)
    }
  }, [selectedId, readyProfiles])

  useEffect(() => {
    if (!isTestingView) {
      syncEditorFromTouch(touch)
    }
  }, [touch, syncEditorFromTouch, isTestingView])

  if (!readyProfiles.length) {
    const pending = prospectingRows.filter((r) => r.outreach_ready).length
    return (
      <p className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-center text-xs text-amber-900">
        {pending > 0
          ? `${pending} lead(s) Outreach Ready — recargá el pipeline.`
          : 'Sin leads Outreach Ready.'}
      </p>
    )
  }

  const hasPending = Boolean(pending)
  const hasProductionTouch = Boolean(touch)
  const hasActiveDraft = Boolean(activeTouch)

  return (
    <div className="mt-4 space-y-2">
      <p className="rounded-lg border border-sky-200 bg-sky-50/60 px-3 py-2 text-[11px] text-sky-950">
        Playbook SDR — 7 toques (Día 1 Email → 4 LinkedIn → 7 WhatsApp → 10 Email valor → 13
        LinkedIn → 16 WhatsApp → 19 Email cierre). Un toque por vez en producción; en testing podés
        auditar la secuencia completa. Sin envío automático.
      </p>

      <div className="grid gap-4 lg:grid-cols-[minmax(220px,1fr)_minmax(280px,1.4fr)]">
        <div className="rounded-xl border border-violet-200 bg-white p-3 shadow-sm">
          <p className="text-xs font-semibold text-violet-950">Nexus Outreach — leads listos</p>
          <ul className="mt-2 max-h-72 space-y-1 overflow-y-auto">
            {readyProfiles.map((p) => {
              const isActive = selectedId === p.external_id
              const done = p.playbook_state?.completed?.length || 0
              return (
                <li key={p.external_id}>
                  <button
                    type="button"
                    onClick={() => pickProfile(p.external_id)}
                    className={`w-full cursor-pointer rounded-lg border px-2 py-2 text-left text-xs ${
                      isActive
                        ? 'border-violet-400 bg-violet-50 ring-1 ring-violet-200'
                        : 'border-zinc-200 hover:border-violet-200 hover:bg-violet-50/40'
                    }`}
                  >
                    <span className="font-semibold text-zinc-900">{p.person.name}</span>
                    <span className="block text-zinc-600">{p.company.name}</span>
                    <span className="mt-1 text-[10px] text-zinc-500">
                      {done > 0 ? `${done} toque(s) generado(s)` : 'Sin toques aún'}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
        </div>

        <div className="space-y-3">
          {selected ? <ContactDetailPanel profile={selected} extra={selectedExtra} /> : null}

          <div className="rounded-xl border border-violet-200 bg-violet-50/30 p-3 shadow-sm">
            <p className="text-xs font-semibold text-violet-950">Estado real — Producción</p>
            {selected?.playbook_state?.available_channels?.length ? (
              <p className="mt-1 text-[10px] text-zinc-500">
                Canales del lead: {selected.playbook_state.available_channels.join(', ')}
              </p>
            ) : null}

            {prodSummary ? (
              <dl className="mt-2 grid gap-1 text-[11px] text-zinc-700">
                <div>
                  <dt className="font-medium text-zinc-500">Estado secuencia</dt>
                  <dd className="font-semibold text-zinc-900">{prodSummary.statusLabel}</dd>
                  <dd className="text-zinc-600">{prodSummary.statusDetail}</dd>
                </div>
                <div className="flex gap-2">
                  <dt className="font-medium text-zinc-500">Toques en playbook</dt>
                  <dd>{prodSummary.completedCount}</dd>
                </div>
                {prodSummary.nextChannel ? (
                  <>
                    <div className="flex gap-2">
                      <dt className="font-medium text-zinc-500">Próximo canal</dt>
                      <dd className="font-semibold text-violet-900">
                        {CHANNEL_LABELS[prodSummary.nextChannel] || prodSummary.nextChannel}
                      </dd>
                    </div>
                    <div className="flex gap-2">
                      <dt className="font-medium text-zinc-500">Próximo día</dt>
                      <dd>Día {prodSummary.nextDay}</dd>
                    </div>
                    <div className="flex gap-2">
                      <dt className="font-medium text-zinc-500">Próximo toque</dt>
                      <dd>#{prodSummary.nextTouchIndex}</dd>
                    </div>
                  </>
                ) : null}
              </dl>
            ) : null}

            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                disabled={freeze || Boolean(busy) || !selected || !hasPending}
                className="rounded-lg bg-violet-600 px-3 py-1.5 text-[11px] font-semibold text-white hover:bg-violet-700 disabled:opacity-40"
                onClick={() => void handleGenerateNext(false)}
              >
                {busy === 'gen' ? 'Generando…' : 'Generar próximo toque'}
              </button>
              <button
                type="button"
                disabled={freeze || Boolean(busy) || !hasProductionTouch}
                className="rounded-lg border border-violet-200 px-3 py-1.5 text-[11px] font-semibold text-violet-900 hover:bg-violet-50 disabled:opacity-40"
                onClick={() => void handleGenerateNext(true)}
              >
                {busy === 'regen' ? 'Regenerando…' : 'Regenerar (producción)'}
              </button>
            </div>
          </div>

          <div className="rounded-xl border border-amber-200 bg-amber-50/40 p-3 shadow-sm">
            <p className="text-xs font-semibold text-amber-950">Modo testing — validación de mensajes</p>
            <p className="mt-1 text-[10px] text-amber-900/80">
              Genera borradores por canal sin avanzar el playbook, o la secuencia completa de 7 toques.
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                disabled={freeze || Boolean(busy) || !selected}
                className="rounded-lg bg-indigo-600 px-3 py-1.5 text-[11px] font-semibold text-white hover:bg-indigo-700 disabled:opacity-40"
                onClick={() => void handleGeneratePlaybookPreview()}
              >
                {busy === 'preview' ? 'Generando secuencia…' : 'Vista previa completa del playbook'}
              </button>
              {(['email', 'linkedin', 'whatsapp']).map((ch) => (
                <button
                  key={ch}
                  type="button"
                  disabled={freeze || Boolean(busy) || !selected}
                  className="rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-[11px] font-semibold text-amber-950 hover:bg-amber-50 disabled:opacity-40"
                  onClick={() => void handleGenerateTest(ch)}
                >
                  {busy === `test-${ch}` ? 'Generando…' : `Generar ${CHANNEL_LABELS[ch]}`}
                </button>
              ))}
              <button
                type="button"
                disabled={freeze || Boolean(busy) || !selected}
                className="rounded-lg border border-rose-300 bg-white px-3 py-1.5 text-[11px] font-semibold text-rose-900 hover:bg-rose-50 disabled:opacity-40"
                onClick={() => void handleResetSequence()}
              >
                {busy === 'reset' ? 'Reiniciando…' : 'Reiniciar secuencia'}
              </button>
            </div>
            {isTestingView ? (
              <button
                type="button"
                className="mt-2 text-[10px] font-medium text-amber-900 underline"
                onClick={() => {
                  setTestDraft(null)
                  syncEditorFromTouch(touch)
                  setStatus({ type: 'ok', text: 'Volviste al borrador de producción.' })
                }}
              >
                Volver al borrador de producción
              </button>
            ) : null}
            <PlaybookFullPreviewPanel
              preview={playbookPreview}
              onClose={() => setPlaybookPreview(null)}
            />
          </div>

          <div className="rounded-xl border border-zinc-200 bg-white p-3 shadow-sm">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-xs font-semibold text-zinc-900">Borrador</p>
              {isTestingView ? (
                <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-900">
                  Modo testing · Día {testDraft?.day} · {CHANNEL_LABELS[testDraft?.channel] || testDraft?.channel}
                </span>
              ) : hasProductionTouch ? (
                <span className="rounded-full bg-violet-100 px-2 py-0.5 text-[10px] font-semibold text-violet-900">
                  Producción · Día {touch?.day} · {CHANNEL_LABELS[touch?.channel] || touch?.channel}
                </span>
              ) : null}
            </div>

            {status?.type === 'loading' ? (
              <p className="mt-2 text-[11px] font-medium text-violet-800">{status.text}</p>
            ) : null}

            {status?.type === 'error' ? (
              <div className="mt-2 rounded-lg border border-rose-200 bg-rose-50 px-2 py-2 text-[11px] text-rose-900">
                <p className="font-semibold">{status.validation ? 'Borrador rechazado' : 'Error'}</p>
                <p className="mt-0.5">{status.text}</p>
                <ValidationDebugPanel validation={status.validation} />
                {status.backend?.openai_configured === false ? (
                  <p className="mt-1 font-medium">Configurá OPENAI_API_KEY en el backend.</p>
                ) : null}
                {import.meta.env.DEV && status.backend && !status.validation ? (
                  <pre className="mt-1 max-h-24 overflow-auto text-[9px] opacity-80">
                    {JSON.stringify(status.backend, null, 2)}
                  </pre>
                ) : null}
              </div>
            ) : null}

            {status?.type === 'ok' ? (
              <p className="mt-2 text-[11px] text-emerald-800">{status.text}</p>
            ) : null}

            {hasActiveDraft || status?.validation?.rejected_body ? (
              <>
                <SdrReasoningPanel reasoning={activeTouch?.sdr_reasoning} />
                {activeChannel === 'email' ||
                status?.validation?.channel === 'email' ||
                status?.validation?.rejected_subject ? (
                  <input
                    className="mt-3 w-full rounded border border-zinc-200 px-2 py-1.5 text-xs"
                    value={editSubject}
                    disabled={freeze || Boolean(busy)}
                    onChange={(e) => setEditSubject(e.target.value)}
                    placeholder="Asunto"
                  />
                ) : null}
                <textarea
                  className="mt-2 min-h-[180px] w-full rounded border border-zinc-200 px-2 py-2 text-xs leading-relaxed"
                  value={editBody}
                  disabled={freeze || Boolean(busy)}
                  onChange={(e) => setEditBody(e.target.value)}
                  placeholder="Borrador del toque…"
                />
                <div className="mt-2 flex flex-wrap gap-2">
                  {!isTestingView ? (
                    <button
                      type="button"
                      disabled={freeze || Boolean(busy) || !editBody.trim()}
                      className="rounded-lg border border-zinc-200 px-3 py-1.5 text-[11px] font-semibold text-zinc-800 hover:bg-zinc-50 disabled:opacity-40"
                      onClick={() => void handleSaveEdit()}
                    >
                      {busy === 'save' ? 'Guardando…' : 'Guardar edición'}
                    </button>
                  ) : null}
                  <button
                    type="button"
                    disabled={!editBody.trim()}
                    className="rounded-lg border border-zinc-200 px-3 py-1.5 text-[11px] font-semibold text-zinc-800 hover:bg-zinc-50 disabled:opacity-40"
                    onClick={() => void handleCopy()}
                  >
                    Copiar
                  </button>
                </div>
              </>
            ) : (
              <p className="mt-3 text-[11px] text-zinc-500">
                {hasPending
                  ? 'Usá «Generar próximo toque» (producción) o el modo testing para probar un canal.'
                  : 'Secuencia completa o sin canales disponibles.'}
              </p>
            )}

            {selected?.playbook_state?.completed?.length > 0 ? (
              <details className="mt-3 text-[10px] text-zinc-600">
                <summary className="cursor-pointer font-medium">
                  Historial real del playbook ({selected.playbook_state.completed.length} toque
                  {selected.playbook_state.completed.length === 1 ? '' : 's'})
                </summary>
                <ul className="mt-2 space-y-2">
                  {selected.playbook_state.completed.map((t, i) => (
                    <li key={`${t.day}-${t.channel}-${i}`} className="rounded border border-zinc-100 bg-zinc-50 px-2 py-1.5">
                      <p className="font-medium text-zinc-800">
                        Día {t.day} · {CHANNEL_LABELS[t.channel] || t.channel}
                        {t.subject ? ` · ${t.subject}` : ''}
                      </p>
                      <p className="mt-0.5 whitespace-pre-wrap text-zinc-600">{t.body}</p>
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  )
}
