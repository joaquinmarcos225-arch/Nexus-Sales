const INTERNAL_BLOCK_LABELS = {

  probable_problem: 'Resultado que generamos',

  why_it_matters: 'Por qué escribimos',

  hypothesis: 'Qué hacemos / cómo lo hacemos',

  response_question: 'CTA / pregunta',

  selling_to_role: 'Rol objetivo',

}



const SECTION_BLOCK_LABELS = {

  greeting: 'Saludo',

  presentation: 'Presentación',

  problem: 'Por qué escribo',

  solution: 'Qué hacemos / cómo',

  benefits: 'Resultado / beneficio',

  cta: 'CTA',

}



const SECTION_ORDER = ['greeting', 'presentation', 'problem', 'solution', 'benefits', 'cta']



const DEFAULT_BLOCK_DEFS = [

  { key: 'greeting', label: 'saludo' },

  { key: 'presentation', label: 'presentación' },

  { key: 'problem', label: 'por qué escribo' },

  { key: 'solution', label: 'cómo lo hacemos' },

  { key: 'benefits', label: 'resultado / beneficio' },

  { key: 'cta', label: 'CTA' },

]



const BLOCK_ISSUE_PATTERNS = {

  greeting: /greeting|saludo/i,

  presentation: /presentation|presentaci[oó]n/i,

  problem: /sections\.problem|por_qu[eé]_escribo|why_it_matters|why_write/i,

  solution: /sections\.solution|qu[eé]_hacemos|internal\.hypothesis|hypothesis/i,

  benefits: /sections\.benefits|body\.resultado|internal\.probable_problem|probable_problem|beneficio/i,

  cta: /sections\.cta|body\.cta|internal\.response_question|response_question|primer toque:.*(?:CTA|reuni[oó]n|conversaci[oó]n)|CTA debe/i,

}



function assembleDraftFromSections(sections) {

  if (!sections || typeof sections !== 'object') {

    return ''

  }

  const opening = ['greeting', 'presentation']

    .map((key) => (sections[key] || '').trim())

    .filter(Boolean)

    .join('\n')

  const bodyBlocks = ['problem', 'solution', 'benefits', 'cta']

    .map((key) => (sections[key] || '').trim())

    .filter(Boolean)

  const parts = []

  if (opening) {

    parts.push(opening)

  }

  parts.push(...bodyBlocks)

  return parts.join('\n\n')

}



function blockValue(key, sections = {}, internal = {}) {

  if (key === 'greeting') return (sections.greeting || '').trim()

  if (key === 'presentation') return (sections.presentation || '').trim()

  if (key === 'problem') return (sections.problem || internal.why_it_matters || '').trim()

  if (key === 'solution') return (sections.solution || internal.hypothesis || '').trim()

  if (key === 'benefits') return (sections.benefits || internal.probable_problem || '').trim()

  if (key === 'cta') return (sections.cta || internal.response_question || '').trim()

  return ''

}



const HOW_WE_DO_PATTERN =
  /lo hacemos|lo logramos|mediante|utilizamos|automatizamos|centralizamos|conectamos|integramos|implementamos/i

const HOW_IT_WORKS_PATTERN =
  /automatiz|consolid|centraliz|integr|conect|plataforma|mail|whatsapp|linkedin|en un solo lugar|prospectos|campa[nñ]as|reporting|contacto/i

function mentionsHowWeDoIt(text) {
  const blob = (text || '').trim()
  if (!blob) {
    return false
  }
  if (HOW_WE_DO_PATTERN.test(blob)) {
    return true
  }
  return HOW_IT_WORKS_PATTERN.test(blob) && blob.length >= 35
}

function issueTargetsBlock(issue, blockKey) {

  const pattern = BLOCK_ISSUE_PATTERNS[blockKey]

  return pattern ? pattern.test(issue) : false

}



export function buildBlockChecklist(validation) {

  if (validation?.block_checklist?.length) {

    return validation.block_checklist

  }

  const sections = validation?.rejected_sections || {}

  const internal = validation?.rejected_internal || {}

  const issues = validation?.issues || []

  return DEFAULT_BLOCK_DEFS.map(({ key, label }) => {

    const value = blockValue(key, sections, internal)

    let blockIssues = issues.filter((issue) => issueTargetsBlock(issue, key))

    if (key === 'solution') {
      const solutionSection = (sections.solution || '').trim()
      if (solutionSection && mentionsHowWeDoIt(solutionSection)) {
        blockIssues = blockIssues.filter(
          (issue) => !/internal\.hypothesis|body\.qu[eé]_hacemos/i.test(issue),
        )
      }
    }

    let hasContent = value.length >= 8
    if (key === 'solution' && !hasContent) {
      hasContent = mentionsHowWeDoIt(value)
    }

    const ok = hasContent && blockIssues.length === 0

    return {

      key,

      label,

      ok,

      value,

      issue: blockIssues[0] || null,

    }

  })

}



function resolveFullDraft(validation) {

  const assembled = assembleDraftFromSections(validation?.rejected_sections)

  return (

    (validation?.rejected_body || '').trim() ||

    assembled ||

    (validation?.generation_debug?.raw_response || '').trim()

  )

}



function OpenAIGenerationDebugSection({ generationDebug }) {

  if (!generationDebug) {

    return null

  }



  const tokenParts = [

    generationDebug.input_tokens != null ? `in ${generationDebug.input_tokens}` : null,

    generationDebug.output_tokens != null ? `out ${generationDebug.output_tokens}` : null,

    generationDebug.total_tokens != null ? `total ${generationDebug.total_tokens}` : null,

  ].filter(Boolean)



  return (

    <details className="mt-3 rounded border border-violet-300 bg-violet-50/80">

      <summary className="cursor-pointer px-2 py-1.5 text-xs font-semibold text-violet-950">

        OpenAI — prompts y respuesta raw

      </summary>

      <div className="space-y-2 border-t border-violet-200 px-2 py-2 text-[10px] text-violet-950">

        {generationDebug.model ? (

          <p>

            <span className="font-medium">Modelo:</span> {generationDebug.model}

            {tokenParts.length ? ` · ${tokenParts.join(' · ')}` : ''}

          </p>

        ) : null}

        {generationDebug.parse_error ? (

          <div>

            <p className="font-medium text-red-800">Error de parsing</p>

            <pre className="mt-0.5 max-h-24 overflow-auto whitespace-pre-wrap rounded border border-red-200 bg-white px-2 py-1">

              {generationDebug.parse_error}

            </pre>

          </div>

        ) : null}

        {generationDebug.prompt_user ? (

          <details>

            <summary className="cursor-pointer font-medium">Prompt user</summary>

            <pre className="mt-0.5 max-h-48 overflow-auto whitespace-pre-wrap rounded border border-violet-200 bg-white px-2 py-1 font-mono">

              {generationDebug.prompt_user}

            </pre>

          </details>

        ) : null}

        {generationDebug.raw_response ? (

          <details open>

            <summary className="cursor-pointer font-medium">Respuesta RAW</summary>

            <pre className="mt-0.5 max-h-72 overflow-auto whitespace-pre-wrap rounded border border-amber-300 bg-amber-50/50 px-2 py-1 font-mono">

              {generationDebug.raw_response}

            </pre>

          </details>

        ) : null}

      </div>

    </details>

  )

}



export function SdrValidationDebugPanel({ validation, onDismiss, compact = false }) {

  if (!validation) {

    return null

  }



  const fullDraft = resolveFullDraft(validation)

  const blockChecklist = buildBlockChecklist(validation)

  const missingBlocks =

    validation.missing_blocks?.length > 0

      ? validation.missing_blocks

      : blockChecklist.filter((b) => !b.ok).map((b) => b.issue || `falta ${b.label}`)



  const sectionEntries = validation.rejected_sections

    ? SECTION_ORDER.map((key) => [key, validation.rejected_sections[key] ?? ''])

    : []



  const internalEntries = validation.rejected_internal

    ? Object.entries(validation.rejected_internal)

    : []



  return (

    <div

      className={`rounded-lg border border-rose-300 bg-rose-50/90 text-sm text-rose-950 ${

        compact ? 'px-2 py-2' : 'px-3 py-3'

      }`}

    >

      <div className="flex items-start justify-between gap-2">

        <p className="font-semibold text-rose-900">Borrador rechazado — depuración completa</p>

        {onDismiss ? (

          <button

            type="button"

            onClick={onDismiss}

            className="shrink-0 text-xs text-rose-700 hover:underline"

          >

            Cerrar

          </button>

        ) : null}

      </div>



      {validation.step_day != null ? (

        <p className="mt-1 text-xs text-rose-700">

          Día {validation.step_day}

          {validation.channel ? ` · ${validation.channel}` : ''}

          {validation.attempts ? ` · ${validation.attempts} intentos IA` : ''}

        </p>

      ) : null}



      <div className="mt-3 space-y-3 rounded border border-rose-200 bg-white/90 p-3 text-xs">

        <div>

          <p className="font-semibold text-rose-900">Asunto:</p>

          <pre className="mt-1 whitespace-pre-wrap text-[#374151]">

            {validation.rejected_subject?.trim() || '—'}

          </pre>

        </div>



        <div>

          <p className="font-semibold text-rose-900">Mensaje:</p>

          <pre className="mt-1 max-h-80 overflow-auto whitespace-pre-wrap leading-relaxed text-[#374151]">

            {fullDraft || '— (la IA no devolvió body ni sections parseables)'}

          </pre>

        </div>



        <div>

          <p className="font-semibold text-rose-900">Bloques detectados:</p>

          <ul className="mt-1 space-y-1">

            {blockChecklist.map((block) => (

              <li key={block.key} className="flex flex-wrap items-start gap-2">

                <span className={block.ok ? 'text-emerald-700' : 'text-red-700'}>

                  {block.ok ? '✓' : '✗'}

                </span>

                <span className="font-medium text-rose-800">{block.label}</span>

                {block.value ? (

                  <span className="min-w-0 flex-1 whitespace-pre-wrap text-[#4b5563]">

                    — {block.value}

                  </span>

                ) : (

                  <span className="text-rose-500">— vacío</span>

                )}

              </li>

            ))}

          </ul>

        </div>



        {missingBlocks.length ? (

          <div>

            <p className="font-semibold text-rose-900">Bloques faltantes / incumplidos:</p>

            <ul className="mt-1 list-inside list-disc space-y-0.5 text-rose-900">

              {missingBlocks.map((item, i) => (

                <li key={`missing-${i}`}>{item}</li>

              ))}

            </ul>

          </div>

        ) : null}



        {validation.warnings?.length ? (

          <div>

            <p className="font-semibold text-amber-900">Advertencias (no bloquean envío):</p>

            <ul className="mt-1 list-inside list-disc space-y-0.5 text-amber-900">

              {validation.warnings.map((warning, i) => (

                <li key={`warn-${i}`}>{warning}</li>

              ))}

            </ul>

          </div>

        ) : null}



        {validation.issues?.length ? (

          <div>

            <p className="font-semibold text-rose-900">Errores de validación:</p>

            <ul className="mt-1 list-inside list-disc space-y-0.5 text-rose-900">

              {validation.issues.map((issue, i) => (

                <li key={`issue-${i}`}>{issue}</li>

              ))}

            </ul>

          </div>

        ) : null}

      </div>



      {validation.how_we_do_trace ? (

        <details className="mt-3" open>

          <summary className="cursor-pointer text-xs font-medium text-rose-800">

            Trazas validador — cómo lo hacemos ({validation.how_we_do_trace.result})

          </summary>

          <div className="mt-2 space-y-2 text-xs">

            <p>

              <span className="font-medium">Campo:</span> {validation.how_we_do_trace.field}

              {validation.how_we_do_trace.fail_reason ? (

                <>

                  {' '}

                  · <span className="font-medium text-red-800">fallo:</span>{' '}

                  {validation.how_we_do_trace.fail_reason}

                </>

              ) : null}

            </p>

            {validation.how_we_do_trace.issues_attributed_to_solution_block?.length ? (

              <div>

                <p className="font-medium text-rose-800">Issues atribuidos al bloque solution en UI:</p>

                <ul className="list-inside list-disc">

                  {validation.how_we_do_trace.issues_attributed_to_solution_block.map((issue, i) => (

                    <li key={`attr-${i}`}>{issue}</li>

                  ))}

                </ul>

              </div>

            ) : null}

            {validation.how_we_do_trace.checks?.map((check, i) => (

              <div key={`how-check-${i}`} className="rounded bg-white/80 px-2 py-1">

                <span className={check.ok ? 'text-emerald-700' : 'text-red-700'}>

                  {check.ok ? 'PASS' : 'FAIL'}

                </span>{' '}

                <span className="font-medium">{check.name}</span>

                {check.matches?.length ? (

                  <span className="text-[#4b5563]"> — matches: {check.matches.join(', ')}</span>

                ) : null}

                {check.issues?.length ? (

                  <ul className="mt-0.5 list-inside list-disc text-red-800">

                    {check.issues.map((issue, j) => (

                      <li key={`how-issue-${i}-${j}`}>{issue}</li>

                    ))}

                  </ul>

                ) : null}

              </div>

            ))}

          </div>

        </details>

      ) : null}



      {sectionEntries.length ? (

        <details className="mt-3" open={!compact}>

          <summary className="cursor-pointer text-xs font-medium text-rose-800">

            Parser — sections (JSON)

          </summary>

          <dl className="mt-2 space-y-2">

            {sectionEntries.map(([key, text]) => (

              <div key={key}>

                <dt className="text-xs font-medium text-rose-700">

                  {SECTION_BLOCK_LABELS[key] || key}

                  {!text ? <span className="ml-1 font-normal text-rose-500">(vacío)</span> : null}

                </dt>

                <dd className="mt-0.5 whitespace-pre-wrap rounded bg-white/80 px-2 py-1 text-xs">

                  {text || '—'}

                </dd>

              </div>

            ))}

          </dl>

        </details>

      ) : null}



      {internalEntries.length ? (

        <details className="mt-3" open={!compact}>

          <summary className="cursor-pointer text-xs font-medium text-rose-800">

            Parser — internal (razonamiento IA)

          </summary>

          <dl className="mt-2 space-y-2">

            {internalEntries.map(([key, text]) => (

              <div key={key}>

                <dt className="text-xs font-medium text-rose-700">

                  {INTERNAL_BLOCK_LABELS[key] || key}

                  {!text ? <span className="ml-1 font-normal text-rose-500">(vacío)</span> : null}

                </dt>

                <dd className="mt-0.5 whitespace-pre-wrap rounded bg-white/80 px-2 py-1 text-xs">

                  {text || '—'}

                </dd>

              </div>

            ))}

          </dl>

        </details>

      ) : null}



      {validation.banned_matches?.length ? (

        <details className="mt-3">

          <summary className="cursor-pointer text-xs font-medium text-rose-800">

            Frases prohibidas detectadas ({validation.banned_matches.length})

          </summary>

          <ul className="mt-1 space-y-1 text-xs">

            {validation.banned_matches.map((m, i) => (

              <li key={`ban-${i}`} className="rounded bg-white/80 px-2 py-1">

                <span className="font-medium">{m.field}</span> · {m.rule}: &quot;{m.phrase}&quot;

              </li>

            ))}

          </ul>

        </details>

      ) : null}



      <OpenAIGenerationDebugSection generationDebug={validation.generation_debug} />

    </div>

  )

}


