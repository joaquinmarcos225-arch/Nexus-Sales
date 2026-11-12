import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useCompany } from '../context/CompanyContext.jsx'
import { AlertBanner } from '../components/AlertBanner.jsx'
import {
  explorarAddFavorite,
  explorarAddToCampaign,
  explorarPotentialCustomers,
  explorarReveal,
  explorarSearch,
  fetchCampaigns,
  fetchExplorarFilterCatalog,
  fetchExplorarUsage,
  fetchProducts,
} from '../utils/api.js'
import {
  FALLBACK_DEPARTMENTS,
  FALLBACK_HEADCOUNTS,
  FALLBACK_INDUSTRIES,
  FALLBACK_SENIORITIES,
  keepAllowed,
  pairsFromCatalog,
} from '../data/explorarFilterCatalog.js'
import { notifyCreditsChanged } from '../hooks/useMyCredits.js'
import { filtersFingerprint, searchResultsSummary } from '../utils/explorarSearchUi.js'

const INDUSTRIES_COLLAPSED = 10
/** Reintentos cuando el backend corta por tiempo/budget (more_pending) o proxy 504. */
const MORE_PENDING_RETRIES = 8
const SEARCH_TIMEOUT_RETRIES = 4

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

function isSearchInFlightError(err) {
  const status = Number(err?.status || 0)
  if (status === 504 || status === 502 || status === 503) return true
  return /sigue en curso|gateway timeout|servidor ocupado|no responde un momento/i.test(
    String(err?.message || ''),
  )
}

const QUICK_SEARCHES = [
  { label: 'Gerentes comerciales en Argentina', titles: 'Gerente comercial', location: 'Argentina', industries: [] },
  { label: 'CEOs de software en México', titles: 'CEO', location: 'México', industries: ['Software Development'] },
  { label: 'Head of Marketing en Chile', titles: 'Head of Marketing', location: 'Chile', industries: [] },
  { label: 'Directores de RRHH en Colombia', titles: 'Director de Recursos Humanos', location: 'Colombia', industries: [] },
]

const AVATAR_TONES = [
  'bg-rose-100 text-rose-800',
  'bg-amber-100 text-amber-800',
  'bg-emerald-100 text-emerald-800',
  'bg-sky-100 text-sky-800',
  'bg-violet-100 text-violet-800',
  'bg-slate-200 text-slate-800',
]

const SAVED_SEARCH_TTL_MS = 24 * 60 * 60 * 1000

function savedSearchKey(companyId) {
  return `nx-explorar-search:${companyId}`
}

function readSavedSearch(companyId) {
  try {
    const raw = window.localStorage.getItem(savedSearchKey(companyId))
    if (!raw) return null
    const saved = JSON.parse(raw)
    if (!saved || Date.now() - Number(saved.savedAt || 0) > SAVED_SEARCH_TTL_MS) {
      window.localStorage.removeItem(savedSearchKey(companyId))
      return null
    }
    return saved
  } catch {
    return null
  }
}

function writeSavedSearch(companyId, state) {
  try {
    window.localStorage.setItem(savedSearchKey(companyId), JSON.stringify({ ...state, savedAt: Date.now() }))
  } catch {
    // Sin espacio en localStorage: la búsqueda igual queda en el cache del servidor 24 h.
  }
}

function pinnedProductsKey(companyId) {
  return `nx-explorar-pinned-products:${companyId}`
}

function readPinnedProducts(companyId) {
  try {
    const raw = window.localStorage.getItem(pinnedProductsKey(companyId))
    const ids = JSON.parse(raw)
    return Array.isArray(ids) ? ids.map(String) : []
  } catch {
    return []
  }
}

function writePinnedProducts(companyId, ids) {
  try {
    window.localStorage.setItem(pinnedProductsKey(companyId), JSON.stringify(ids))
  } catch {
    // Sin espacio: la estrella vale para esta sesión.
  }
}

function splitList(text) {
  return String(text || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
}

function splitUrls(text) {
  return String(text || '')
    .split(/[\s,;]+/)
    .map((s) => s.trim())
    .filter(Boolean)
    .slice(0, 3)
}

function initials(name) {
  const parts = String(name || '').trim().split(/\s+/).filter(Boolean)
  return ((parts[0]?.[0] || '') + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase() || '?'
}

function avatarTone(id) {
  let h = 0
  for (const ch of String(id || '')) h = (h * 31 + ch.charCodeAt(0)) >>> 0
  return AVATAR_TONES[h % AVATAR_TONES.length]
}

export function favoriteSnapshot(p) {
  return {
    ...personSnapshot(p),
    city: p.city,
    company_size: p.company_size,
    has_email: Boolean(p.has_email),
    has_mobile: Boolean(p.has_mobile),
    email_verified: Boolean(p.email_verified),
    mobile_verified: Boolean(p.mobile_verified),
  }
}

const FIRMO_STATUS_LABELS = {
  exact: 'coincide',
  equivalent: 'equivalente',
  similar: 'similar',
  incomplete: 'información incompleta',
  review: 'revisar',
  contradictory: 'no coincide',
}

function firmographicChipClass(status) {
  if (status === 'exact' || status === 'equivalent') return 'bg-emerald-50 text-emerald-900'
  if (status === 'similar') return 'bg-amber-50 text-amber-900'
  if (status === 'contradictory') return 'bg-rose-50 text-rose-900'
  if (status === 'incomplete' || status === 'review') return 'bg-nx-bg text-nx-ink/70'
  return 'bg-nx-bg text-nx-ink/70'
}

export function FirmographicChip({ kind, value, status, label, reason }) {
  const text = label || FIRMO_STATUS_LABELS[status] || ''
  const emptyRequested = !value && (status === 'incomplete' || status === 'review')
  if (!value && !emptyRequested) return null
  const body = emptyRequested
    ? kind === 'size'
      ? 'Tamaño'
      : 'Industria'
    : kind === 'size'
      ? `${value} empleados`
      : value
  return (
    <span
      className={`rounded-md px-1.5 py-0.5 text-[10px] font-medium ${firmographicChipClass(status)}`}
      title={reason || undefined}
    >
      {body}
      {text ? ` · ${text}` : ''}
    </span>
  )
}

export function personSnapshot(p) {
  const personId = p.person_id || p.source_person_id || ''
  return {
    person_id: personId,
    source_person_id: p.source_person_id || personId || undefined,
    source_provider: p.source_provider || (personId ? 'prospeo' : undefined),
    name: p.name,
    first_name: p.first_name,
    last_name: p.last_name,
    role: p.role,
    company_name: p.company_name,
    company_domain: p.company_domain,
    country: p.country,
    industry: p.industry,
    linkedin_url: p.linkedin_url,
  }
}

export function Icon({ d, className = 'size-4', filled = false }) {
  return (
    <svg
      className={className}
      fill={filled ? 'currentColor' : 'none'}
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeWidth={filled ? 0 : 1.75}
      aria-hidden
    >
      <path strokeLinecap="round" strokeLinejoin="round" d={d} />
    </svg>
  )
}

export const ICONS = {
  star: 'M11.48 3.5a.56.56 0 011.04 0l2.13 5.11a.56.56 0 00.47.35l5.52.44c.5.04.7.66.32.99l-4.2 3.6a.56.56 0 00-.18.56l1.28 5.38a.56.56 0 01-.84.61l-4.73-2.89a.56.56 0 00-.58 0l-4.73 2.89a.56.56 0 01-.84-.61l1.28-5.38a.56.56 0 00-.18-.56l-4.2-3.6a.56.56 0 01.32-.99l5.52-.44a.56.56 0 00.47-.35l2.13-5.11z',
  search: 'M21 21l-4.35-4.35M10.5 17a6.5 6.5 0 110-13 6.5 6.5 0 010 13z',
  user: 'M15.75 7.5a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0zM4.5 20.25a7.5 7.5 0 0115 0',
  pin: 'M12 21s-6.75-5.4-6.75-11.25a6.75 6.75 0 1113.5 0C18.75 15.6 12 21 12 21zm0-8.75a2.5 2.5 0 100-5 2.5 2.5 0 000 5z',
  building: 'M3.75 21h16.5M5.25 21V5.25A1.5 1.5 0 016.75 3.75h6a1.5 1.5 0 011.5 1.5V21m0-12h3a1.5 1.5 0 011.5 1.5V21M8.25 7.5h3m-3 3.75h3m-3 3.75h3',
  mail: 'M3.75 6.75h16.5v10.5H3.75V6.75zm0 0L12 13.5l8.25-6.75',
  phone: 'M8.25 3.75h7.5a1.5 1.5 0 011.5 1.5v13.5a1.5 1.5 0 01-1.5 1.5h-7.5a1.5 1.5 0 01-1.5-1.5V5.25a1.5 1.5 0 011.5-1.5zM11 17.25h2',
  copy: 'M8.25 8.25V5.25A1.5 1.5 0 019.75 3.75h9a1.5 1.5 0 011.5 1.5v9a1.5 1.5 0 01-1.5 1.5h-3M5.25 8.25h9a1.5 1.5 0 011.5 1.5v9a1.5 1.5 0 01-1.5 1.5h-9a1.5 1.5 0 01-1.5-1.5v-9a1.5 1.5 0 011.5-1.5z',
  check: 'M4.5 12.75l6 6 9-13.5',
  sparkles: 'M9.75 4.5l1.5 4.5 4.5 1.5-4.5 1.5-1.5 4.5-1.5-4.5-4.5-1.5 4.5-1.5 1.5-4.5zM18 14.25l.75 2.25 2.25.75-2.25.75L18 20.25l-.75-2.25-2.25-.75 2.25-.75.75-2.25z',
  link: 'M13.19 8.47a5.25 5.25 0 00-7.42 0L3.77 10.47a5.25 5.25 0 007.42 7.42l.91-.91m-1.29-6.51a5.25 5.25 0 007.42 0l2-2a5.25 5.25 0 00-7.42-7.42l-.91.91',
}

function Chip({ active, onClick, children }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={[
        'rounded-full px-3 py-1 text-xs font-medium ring-1 transition',
        active
          ? 'bg-nx-ink text-white ring-nx-ink shadow-sm'
          : 'bg-white text-nx-ink/80 ring-nx-border hover:bg-nx-bg hover:text-nx-ink',
      ].join(' ')}
    >
      {children}
    </button>
  )
}

function filterOptionsByQuery(options, query) {
  const needle = String(query || '')
    .trim()
    .toLowerCase()
  if (!needle) return options
  return options.filter(
    ([value, text]) =>
      String(text || '')
        .toLowerCase()
        .includes(needle) ||
      String(value || '')
        .toLowerCase()
        .includes(needle),
  )
}

function ChipGroup({ label, options, values, onChange, searchable = false }) {
  const [query, setQuery] = useState('')
  const toggle = (value) => onChange(values.includes(value) ? values.filter((v) => v !== value) : [...values, value])
  const visible = filterOptionsByQuery(options, query)
  const showSearch = searchable && options.length > 10
  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-nx-muted">
          {label}
          {values.length ? ` · ${values.length}` : ''}
        </span>
        {values.length ? (
          <button type="button" onClick={() => onChange([])} className="text-xs text-nx-muted hover:text-nx-ink">
            Limpiar
          </button>
        ) : null}
      </div>
      {showSearch ? (
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={`Buscar en ${label.toLowerCase()}…`}
          className="nx-input mb-2 w-full max-w-md text-sm"
          autoComplete="off"
        />
      ) : null}
      {showSearch && query.trim() && visible.length === 0 ? (
        <p className="mb-2 text-xs text-nx-muted">Nada con ese texto. Probá otra palabra.</p>
      ) : null}
      <div className="flex flex-wrap gap-1.5">
        {visible.map(([value, text]) => (
          <Chip key={value} active={values.includes(value)} onClick={() => toggle(value)}>
            {text}
          </Chip>
        ))}
      </div>
    </div>
  )
}

function FieldWithIcon({ icon, label, ...props }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-nx-muted">{label}</span>
      <span className="relative block">
        <span className="pointer-events-none absolute inset-y-0 left-3 grid place-items-center text-nx-muted">
          <Icon d={icon} />
        </span>
        <input className="nx-input" style={{ paddingLeft: '2.25rem' }} {...props} />
      </span>
    </label>
  )
}

function Availability({ ok, verified, icon, label, revealed = false }) {
  const has = Boolean(ok)
  const isVerified = has && Boolean(verified)
  const tone = isVerified
    ? 'bg-emerald-50 text-emerald-800 ring-1 ring-emerald-200'
    : has
      ? 'bg-amber-50 text-amber-900 ring-1 ring-amber-200'
      : 'bg-nx-bg text-nx-muted'
  const answer = has ? 'Sí' : 'No'
  const text = `${label}: ${answer}`
  let title = `Sin ${label.toLowerCase()} en la base`
  if (has && revealed) {
    title = isVerified
      ? `${label} disponible (verificado). Valor abajo.`
      : `${label} disponible. Valor abajo.`
  } else if (isVerified) {
    title = `${label} disponible (verificado). El valor se ve al tocar Revelar.`
  } else if (has) {
    title = `${label} disponible. El valor se ve al tocar Revelar.`
  }
  return (
    <span
      className={['inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-semibold', tone].join(' ')}
      title={title}
    >
      <Icon d={icon} className="size-3" />
      {text}
    </span>
  )
}

function CopyValue({ icon, value, empty }) {
  const [copied, setCopied] = useState(false)
  if (!value) {
    return (
      <p className="flex items-center gap-2 text-xs text-nx-muted">
        <Icon d={icon} className="size-3.5" />
        {empty}
      </p>
    )
  }
  return (
    <button
      type="button"
      onClick={() => {
        navigator.clipboard?.writeText(value)
        setCopied(true)
        setTimeout(() => setCopied(false), 1200)
      }}
      className="group flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left text-xs text-nx-ink hover:bg-nx-bg"
      title="Copiar"
    >
      <Icon d={icon} className="size-3.5 shrink-0 text-nx-muted" />
      <span className="min-w-0 flex-1 truncate font-medium">{value}</span>
      <span className="shrink-0 text-nx-muted opacity-0 transition group-hover:opacity-100">
        {copied ? <Icon d={ICONS.check} className="size-3.5 text-emerald-600" /> : <Icon d={ICONS.copy} className="size-3.5" />}
      </span>
    </button>
  )
}

export function SkeletonCard() {
  return (
    <div className="nx-card animate-pulse p-4">
      <div className="flex gap-3">
        <div className="size-11 rounded-full bg-nx-bg" />
        <div className="flex-1 space-y-2">
          <div className="h-3.5 w-2/5 rounded bg-nx-bg" />
          <div className="h-3 w-3/5 rounded bg-nx-bg" />
          <div className="h-3 w-1/3 rounded bg-nx-bg" />
        </div>
      </div>
    </div>
  )
}

function StarButton({ active, busy, onClick }) {
  return (
    <button
      type="button"
      disabled={busy}
      onClick={onClick}
      title={active ? 'Sacar de Guardados' : 'Guardar en favoritos (deja de aparecerte en las búsquedas)'}
      aria-label={active ? 'Sacar de Guardados' : 'Guardar en favoritos'}
      className={[
        'grid size-7 shrink-0 place-items-center rounded-md transition disabled:opacity-50',
        active ? 'text-amber-500 hover:bg-amber-50' : 'text-nx-muted hover:bg-nx-bg hover:text-amber-500',
      ].join(' ')}
    >
      <svg
        className="size-[18px]"
        viewBox="0 0 24 24"
        fill={active ? 'currentColor' : 'none'}
        stroke="currentColor"
        strokeWidth={1.75}
        aria-hidden
      >
        <path strokeLinejoin="round" d={ICONS.star} />
      </svg>
    </button>
  )
}

export function inUseText(block) {
  if (!block) return ''
  if (block.reason === 'opt_out') return 'Pidió que no lo contacten más. No se puede sumar a campañas.'
  const where = block.campaign_name ? `la campaña «${block.campaign_name}»` : 'otra campaña'
  const by = block.seller_name ? ` (${block.seller_name})` : ''
  if (block.reason === 'company') {
    return `Ya están prospectando a otra persona de ${block.company_name || 'esta empresa'} en ${where}${by}.`
  }
  return `Lo están prospectando en ${where}${by}.`
}

export function PersonCard({ p, selected, onSelect, revealing, onReveal, favorite = false, starring = false, onToggleFavorite, ownerLabel }) {
  const inCampaign = Boolean(p.prospect_id)
  const blocked = Boolean(p.in_use)
  return (
    <article
      className={[
        'nx-card group relative flex flex-col gap-3 p-4 transition',
        selected ? 'ring-2 ring-nx-ink/80' : 'hover:shadow-md',
      ].join(' ')}
    >
      <div className="flex items-start gap-3">
        <div className={`grid size-11 shrink-0 place-items-center rounded-full text-sm font-bold ${avatarTone(p.person_id)}`}>
          {initials(p.name)}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-[15px] font-semibold text-nx-ink">{p.name}</h3>
            {p.linkedin_url ? (
              <a
                href={p.linkedin_url}
                target="_blank"
                rel="noreferrer"
                title="Ver en LinkedIn"
                className="grid size-5 shrink-0 place-items-center rounded bg-[#0a66c2] text-[10px] font-bold text-white hover:opacity-85"
              >
                in
              </a>
            ) : null}
          </div>
          <p className="line-clamp-2 text-sm text-nx-ink/80">{p.role || 'Cargo no informado'}</p>
          <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-nx-muted">
            {p.company_name ? (
              <span className="inline-flex items-center gap-1">
                <Icon d={ICONS.building} className="size-3.5" />
                {p.company_name}
              </span>
            ) : null}
            {p.geo_status === 'incomplete' || p.geo_status === 'contradictory' ? (
              <span className="inline-flex items-center gap-1">
                <Icon d={ICONS.pin} className="size-3.5" />
                {p.city ? `${p.city} · ubicación no confirmada` : 'Ubicación no confirmada'}
              </span>
            ) : p.city || p.country ? (
              <span className="inline-flex items-center gap-1">
                <Icon d={ICONS.pin} className="size-3.5" />
                {[p.city, p.country].filter(Boolean).join(', ')}
                {p.geo_status === 'review' ? ' · revisar' : ''}
              </span>
            ) : null}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {onToggleFavorite && !inCampaign && !blocked ? (
            <StarButton active={favorite} busy={starring} onClick={onToggleFavorite} />
          ) : null}
          <input
            type="checkbox"
            aria-label={`Seleccionar ${p.name}`}
            className="size-4 shrink-0 accent-[#111]"
            disabled={inCampaign || blocked}
            checked={selected}
            onChange={(e) => onSelect(e.target.checked)}
          />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        <FirmographicChip
          kind="industry"
          value={p.industry}
          status={p.industry_status}
          label={p.industry_status_label}
          reason={p.industry_reason}
        />
        <FirmographicChip
          kind="size"
          value={p.company_size}
          status={p.size_status}
          label={p.size_status_label}
          reason={p.size_reason}
        />
        {p.size_status === 'contradictory' || p.size_status === 'review' ? (
          <span
            className="rounded-md bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-900"
            title={p.size_reason || 'El tamaño en LinkedIn no calza con el filtro'}
          >
            Tamaño LinkedIn ≠ filtro
          </span>
        ) : null}
        {p.geo_status === 'review' ? (
          <span className="rounded-md bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-900">
            País a revisar
          </span>
        ) : null}
        {p.firmographic_status === 'contradictory' ? (
          <span className="rounded-md bg-rose-50 px-1.5 py-0.5 text-[10px] font-semibold text-rose-900">
            {p.firmographic_status_label || 'no compatible'}
          </span>
        ) : null}
        {Number(p.filter_total_soft) > 0 ? (
          <span
            className="rounded-md bg-nx-bg px-1.5 py-0.5 text-[10px] font-semibold text-nx-ink/70"
            title="Filtros opcionales (industria / seniority). Depto, tamaño y país son obligatorios si los tildaste."
          >
            Cumple {Number(p.filter_matched) || 0}/{Number(p.filter_total_soft)}
          </span>
        ) : null}
        {inCampaign ? (
          <span className="rounded-md bg-emerald-600 px-1.5 py-0.5 text-[10px] font-semibold text-white">En campaña</span>
        ) : null}
        {ownerLabel ? (
          <span className="rounded-md bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-800">{ownerLabel}</span>
        ) : null}
        {!blocked && !inCampaign && p.contacted_before ? (
          <span
            className="rounded-md bg-sky-50 px-1.5 py-0.5 text-[10px] font-semibold text-sky-800"
            title="Tu empresa ya le escribió en una campaña anterior que terminó."
          >
            Contactado antes
          </span>
        ) : null}
      </div>

      {blocked ? (
        <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs font-medium text-amber-900 ring-1 ring-amber-200">
          {p.in_use.reason === 'opt_out' ? '' : 'Está siendo prospectado. '}
          {inUseText(p.in_use)}
        </p>
      ) : null}

      <div className="mt-auto border-t border-nx-border pt-3">
        <div className="flex flex-wrap gap-1.5">
          <Availability
            ok={Boolean(p.has_email) || Boolean(p.email)}
            verified={p.email_verified}
            icon={ICONS.mail}
            label="Email"
            revealed={Boolean(p.revealed)}
          />
          <Availability
            ok={Boolean(p.has_mobile) || Boolean(p.phone)}
            verified={p.mobile_verified}
            icon={ICONS.phone}
            label="Celular"
            revealed={Boolean(p.revealed)}
          />
          <Availability
            ok={Boolean(p.linkedin_url)}
            verified={Boolean(p.linkedin_url)}
            icon={ICONS.link}
            label="LinkedIn"
            revealed={Boolean(p.revealed)}
          />
        </div>
        {p.revealed ? (
          <div className="mt-2 space-y-1.5">
            {p.revealed_by_nexus ? (
              <p className="text-[10px] text-nx-muted">Ya revelado en Nexus · sin costo extra</p>
            ) : (
              <p className="text-[10px] text-nx-muted">Revelado · tocá para copiar</p>
            )}
            <CopyValue icon={ICONS.mail} value={p.email} empty="Sin email en la base" />
            <CopyValue icon={ICONS.phone} value={p.phone} empty="Sin celular en la base" />
          </div>
        ) : blocked ? null : (
          <div className="mt-2 flex justify-end">
            <button
              type="button"
              disabled={revealing}
              onClick={onReveal}
              className="nx-btn nx-btn-secondary px-3 py-1.5 text-xs"
            >
              {revealing ? 'Revelando…' : 'Revelar · 1 crédito'}
            </button>
          </div>
        )}
      </div>
    </article>
  )
}

export default function ExplorarPage() {
  const { companyId } = useCompany()

  const [industryOptions, setIndustryOptions] = useState(FALLBACK_INDUSTRIES)
  const [seniorityOptions, setSeniorityOptions] = useState(FALLBACK_SENIORITIES)
  const [departmentOptions, setDepartmentOptions] = useState(FALLBACK_DEPARTMENTS)
  const [headcountOptions, setHeadcountOptions] = useState(FALLBACK_HEADCOUNTS)

  const [names, setNames] = useState('')
  const [titles, setTitles] = useState('')
  const [location, setLocation] = useState('')
  const [companies, setCompanies] = useState('')
  const [lookalikeUrls, setLookalikeUrls] = useState('')
  const [lookalikeSeeds, setLookalikeSeeds] = useState([])
  const [industries, setIndustries] = useState([])
  const [showAllIndustries, setShowAllIndustries] = useState(false)
  const [industryQuery, setIndustryQuery] = useState('')
  const [headcounts, setHeadcounts] = useState([])
  const [seniorities, setSeniorities] = useState([])
  const [departments, setDepartments] = useState([])
  const [showMore, setShowMore] = useState(false)

  const [products, setProducts] = useState([])
  const [pickerOpen, setPickerOpen] = useState(false)
  const [productQuery, setProductQuery] = useState('')
  const [pinnedProductIds, setPinnedProductIds] = useState([])
  const productSearchRef = useRef(null)
  const pickerRootRef = useRef(null)
  const [potentialLoading, setPotentialLoading] = useState(false)
  const [potentialInfo, setPotentialInfo] = useState(null)

  const [usage, setUsage] = useState(null)
  const [data, setData] = useState(null)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [revealing, setRevealing] = useState({})
  const [starring, setStarring] = useState({})
  const [selected, setSelected] = useState({})
  const [campaigns, setCampaigns] = useState([])
  const [campaignId, setCampaignId] = useState('')
  const [adding, setAdding] = useState(false)
  const [restoredFor, setRestoredFor] = useState(null)
  /** Descarta respuestas de búsquedas viejas si el tester cambió filtros y volvió a buscar. */
  const searchGenRef = useRef(0)
  /** Filtros con los que se generó `data`. Si el UI diverge → invalidar resultados. */
  const [appliedFingerprint, setAppliedFingerprint] = useState('')
  const skipStaleClearRef = useRef(false)

  // Recargar la pestaña no tiene que perder resultados ya pagados.
  useEffect(() => {
    if (!companyId) return
    const saved = readSavedSearch(companyId)
    if (saved) {
      setNames(saved.names || '')
      setTitles(saved.titles || '')
      setLocation(saved.location || '')
      setCompanies(saved.companies || '')
      setLookalikeUrls(saved.lookalikeUrls || '')
      setLookalikeSeeds(Array.isArray(saved.lookalikeSeeds) ? saved.lookalikeSeeds : [])
      // Sanitizar contra catálogo Prospeo (por si quedó Retail/etc. en localStorage).
      setIndustries(keepAllowed(saved.industries || [], industryOptions))
      setHeadcounts(keepAllowed(saved.headcounts || [], headcountOptions))
      setSeniorities(keepAllowed(saved.seniorities || [], seniorityOptions))
      setDepartments(keepAllowed(saved.departments || [], departmentOptions))
      setPotentialInfo(saved.potentialInfo || null)
      // No restaurar una página vacía: parece “search roto” aunque Prospeo ahora sí traiga gente.
      const savedResults = Array.isArray(saved.data?.results) ? saved.data.results : []
      setData(savedResults.length > 0 ? saved.data : null)
      setPage(savedResults.length > 0 ? saved.page || 1 : 1)
      if (savedResults.length > 0) {
        skipStaleClearRef.current = true
        setAppliedFingerprint(
          saved.appliedFingerprint ||
            filtersFingerprint({
              names: saved.names || '',
              titles: saved.titles || '',
              location: saved.location || '',
              companies: saved.companies || '',
              lookalikeUrls: saved.lookalikeUrls || '',
              industries: saved.industries || [],
              headcounts: saved.headcounts || [],
              seniorities: saved.seniorities || [],
              departments: saved.departments || [],
            }),
        )
      } else {
        setAppliedFingerprint('')
      }
      if ((saved.headcounts || []).length + (saved.seniorities || []).length + (saved.departments || []).length) {
        setShowMore(true)
      }
    }
    setRestoredFor(companyId)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- restore once per company; catalog sanitize below
  }, [companyId])

  // Cambió un chip/campo y todavía se ven personas de la búsqueda anterior → vaciar.
  useEffect(() => {
    if (!companyId || restoredFor !== companyId) return
    if (skipStaleClearRef.current) {
      skipStaleClearRef.current = false
      return
    }
    if (!data || !appliedFingerprint) return
    const current = filtersFingerprint({
      names,
      titles,
      location,
      companies,
      lookalikeUrls,
      industries,
      headcounts,
      seniorities,
      departments,
    })
    if (current === appliedFingerprint) return
    // Invalidar gen + spinner: si no, Buscar queda trabado «Buscando…» para siempre.
    searchGenRef.current += 1
    setLoading(false)
    setData(null)
    setSelected({})
    setError('')
    setNotice('Cambiaste los filtros. Tocá Buscar para ver resultados nuevos.')
    setAppliedFingerprint('')
  }, [
    companyId,
    restoredFor,
    names,
    titles,
    location,
    companies,
    lookalikeUrls,
    industries,
    headcounts,
    seniorities,
    departments,
    data,
    appliedFingerprint,
  ])

  useEffect(() => {
    if (!companyId || restoredFor !== companyId) return
    writeSavedSearch(companyId, {
      names,
      titles,
      location,
      companies,
      lookalikeUrls,
      lookalikeSeeds,
      industries,
      headcounts,
      seniorities,
      departments,
      potentialInfo,
      data,
      page,
      appliedFingerprint,
    })
  }, [companyId, restoredFor, names, titles, location, companies, lookalikeUrls, lookalikeSeeds, industries, headcounts, seniorities, departments, potentialInfo, data, page, appliedFingerprint])

  useEffect(() => {
    if (!companyId) return
    fetchExplorarUsage(companyId).then(setUsage).catch(() => {})
    fetchCampaigns(companyId)
      .then((rows) => setCampaigns((Array.isArray(rows) ? rows : []).filter((c) => !c.is_individual_container)))
      .catch(() => {})
    setPinnedProductIds(readPinnedProducts(companyId))
    fetchProducts(companyId)
      .then((rows) => setProducts(Array.isArray(rows) ? rows : []))
      .catch(() => {})
    fetchExplorarFilterCatalog(companyId)
      .then((cat) => {
        const nextInd = pairsFromCatalog(cat?.industries) || FALLBACK_INDUSTRIES
        const nextSen = pairsFromCatalog(cat?.seniorities) || FALLBACK_SENIORITIES
        const nextDep = pairsFromCatalog(cat?.departments) || FALLBACK_DEPARTMENTS
        const nextHc = pairsFromCatalog(cat?.headcounts) || FALLBACK_HEADCOUNTS
        setIndustryOptions(nextInd)
        setSeniorityOptions(nextSen)
        setDepartmentOptions(nextDep)
        setHeadcountOptions(nextHc)
        setIndustries((prev) => keepAllowed(prev, nextInd))
        setSeniorities((prev) => keepAllowed(prev, nextSen))
        setDepartments((prev) => keepAllowed(prev, nextDep))
        setHeadcounts((prev) => keepAllowed(prev, nextHc))
      })
      .catch(() => {})
  }, [companyId])

  const mergeUsage = (u) => {
    if (u) {
      setUsage((prev) => ({ ...(prev || {}), ...u }))
      // Header / Créditos escuchan este evento; sin él el badge queda viejo hasta F5.
      notifyCreditsChanged()
    }
  }

  const runSearch = useCallback(
    async (targetPage, override = null) => {
      if (!companyId) {
        setError('Todavía estamos cargando tu empresa. Probá de nuevo en un segundo.')
        return
      }
      const q = {
        names,
        titles,
        location,
        companies,
        lookalikeUrls,
        industries,
        headcounts,
        seniorities,
        departments,
        ...(override || {}),
      }
      if (splitUrls(q.lookalikeUrls).length && !splitList(q.titles).length) {
        setError('Elegí el cargo de a quién querés escribirle.')
        return
      }
      const nameList = splitList(q.names)
      const hasStructured =
        splitList(q.titles).length > 0 ||
        (q.industries || []).length > 0 ||
        splitList(q.companies).length > 0 ||
        splitUrls(q.lookalikeUrls).length > 0 ||
        (q.headcounts || []).length > 0 ||
        (q.seniorities || []).length > 0 ||
        (q.departments || []).length > 0
      // Solo nombre (o link /in/): ignorar región pegada de una búsqueda anterior.
      const namePrimary = nameList.length > 0 && !hasStructured
      const hasAnyFilter = nameList.length > 0 || hasStructured || (q.location || '').trim()
      if (!hasAnyFilter) {
        setError('Elegí al menos un filtro (cargo, región, industria…) antes de buscar.')
        return
      }
      const gen = ++searchGenRef.current
      const stillMine = () => gen === searchGenRef.current
      const fpNow = filtersFingerprint({
        names: q.names,
        titles: q.titles,
        location: q.location,
        companies: q.companies,
        lookalikeUrls: q.lookalikeUrls,
        industries: q.industries,
        headcounts: q.headcounts,
        seniorities: q.seniorities,
        departments: q.departments,
      })
      // Página 2+: no vaciar la grilla (parece “trabado” varios minutos). Solo skeleton en búsqueda nueva.
      const keepGrid =
        targetPage > 1 && Boolean(data?.results?.length) && appliedFingerprint === fpNow
      setLoading(true)
      setError('')
      setNotice(keepGrid ? `Cargando página ${targetPage}…` : 'Buscando…')
      if (!keepGrid) {
        setData(null)
        setSelected({})
      }
      setPage(targetPage)
      try {
        const body = {
          names: nameList,
          titles: namePrimary ? [] : splitList(q.titles),
          location: namePrimary ? null : (q.location || '').trim() || null,
          industries: namePrimary ? [] : q.industries,
          companies: namePrimary ? [] : splitList(q.companies),
          lookalike_linkedin_urls: namePrimary ? [] : splitUrls(q.lookalikeUrls),
          headcounts: namePrimary ? [] : q.headcounts,
          seniorities: namePrimary ? [] : q.seniorities,
          departments: namePrimary ? [] : q.departments,
          // Prioridad fija: cuentas con mail + celular (valores solo al Revelar).
          require_email: true,
          require_mobile: true,
          page: targetPage,
        }

        const fetchOnce = async () => {
          let lastErr = null
          for (let attempt = 0; attempt < SEARCH_TIMEOUT_RETRIES; attempt += 1) {
            if (!stillMine()) return null
            try {
              return await explorarSearch(companyId, body)
            } catch (e) {
              lastErr = e
              if (!isSearchInFlightError(e) || attempt === SEARCH_TIMEOUT_RETRIES - 1) throw e
              if (!stillMine()) return null
              setNotice('Seguimos buscando con tus filtros…')
              await sleep(700 * (attempt + 1))
            }
          }
          throw lastErr || new Error('No se pudo buscar.')
        }

        let out = await fetchOnce()
        if (!stillMine() || !out) return
        let charged = Boolean(out?.charged_credit)
        let partialNotice = ''
        // Página 2+: si ya hay una hoja usable, no seguir minutos con more_pending.
        const pageFull = Array.isArray(out?.results) && out.results.length >= 15
        const pendingTries = keepGrid ? (pageFull ? 0 : 2) : MORE_PENDING_RETRIES
        for (let tries = 0; out?.more_pending && tries < pendingTries; tries += 1) {
          if (!stillMine()) return
          setNotice(
            keepGrid
              ? `Cargando página ${targetPage}… (más resultados)`
              : 'Buscando más resultados para esta página…',
          )
          try {
            const next = await fetchOnce()
            if (!stillMine() || !next) break
            out = next
            charged = charged || Boolean(out?.charged_credit)
          } catch (partialErr) {
            // Ya hay gente en pantalla: no tirar el error rojo de “sigue en curso”.
            if (Array.isArray(out?.results) && out.results.length > 0) {
              partialNotice = 'Mostramos lo encontrado. Tocá Buscar de nuevo si querés más.'
              break
            }
            throw partialErr
          }
        }
        if (!stillMine()) return
        setError('')
        setNotice(partialNotice)
        skipStaleClearRef.current = true
        setAppliedFingerprint(
          filtersFingerprint({
            names: q.names,
            titles: q.titles,
            location: q.location,
            companies: q.companies,
            lookalikeUrls: q.lookalikeUrls,
            industries: q.industries,
            headcounts: q.headcounts,
            seniorities: q.seniorities,
            departments: q.departments,
          }),
        )
        setData(out)
        setLookalikeSeeds(Array.isArray(out?.lookalike_seeds) ? out.lookalike_seeds : [])
        setPage(targetPage)
        setSelected({})
        mergeUsage(out?.usage)
        if (charged) setNotice('Se descontó 1 crédito por el acumulado de búsquedas.')
        if (out?.lookalike_unresolved?.length) {
          setNotice(
            (charged ? 'Se descontó 1 crédito por el acumulado de búsquedas. ' : '') +
              `No encontramos ${out.lookalike_unresolved.length === 1 ? 'un link' : 'algunos links'}. Revisá que sea la página de empresa.`,
          )
        }
      } catch (e) {
        if (!stillMine()) return
        setError(
          isSearchInFlightError(e)
            ? keepGrid
              ? `La página ${targetPage} tardó demasiado. Tocá → de nuevo (no volvemos a la anterior).`
              : 'La búsqueda tardó demasiado. Tocá Buscar de nuevo: suele completar en el segundo intento.'
            : e?.message || 'No se pudo buscar.',
        )
        setNotice('')
        if (keepGrid) {
          // Nos quedamos en la página pedida; la grilla anterior sigue visible hasta que responda.
          setPage(targetPage)
        } else {
          setData(null)
          setAppliedFingerprint('')
        }
      } finally {
        if (stillMine()) setLoading(false)
      }
    },
    [
      companyId,
      names,
      titles,
      location,
      industries,
      companies,
      lookalikeUrls,
      headcounts,
      seniorities,
      departments,
      data,
      appliedFingerprint,
      page,
    ],
  )

  const quickSearch = (q) => {
    const next = {
      names: '',
      titles: q.titles,
      location: q.location,
      companies: '',
      lookalikeUrls: '',
      industries: q.industries,
      headcounts: [],
      seniorities: [],
      departments: [],
    }
    setNames('')
    setTitles(next.titles)
    setLocation(next.location)
    setCompanies('')
    setLookalikeUrls('')
    setLookalikeSeeds([])
    setIndustries(next.industries)
    setHeadcounts([])
    setSeniorities([])
    setDepartments([])
    setPotentialInfo(null)
    runSearch(1, next)
  }

  const loadPotentialCustomers = async (product) => {
    setPickerOpen(false)
    setPotentialLoading(true)
    setError('')
    try {
      const f = await explorarPotentialCustomers(companyId, product.id)
      const next = {
        names: '',
        titles: (f.titles || []).join(', '),
        location: (f.location || '').trim() || 'LATAM (sin Brasil)',
        companies: '',
        lookalikeUrls,
        industries: f.industries || [],
        headcounts: f.headcounts || [],
        seniorities: f.seniorities || [],
        departments: f.departments || [],
      }
      const safeIndustries = keepAllowed(next.industries, industryOptions)
      const safeHeadcounts = keepAllowed(next.headcounts, headcountOptions)
      const safeSeniorities = keepAllowed(next.seniorities, seniorityOptions)
      const safeDepartments = keepAllowed(next.departments, departmentOptions)
      next.industries = safeIndustries
      next.headcounts = safeHeadcounts
      next.seniorities = safeSeniorities
      next.departments = safeDepartments
      setNames('')
      setTitles(next.titles)
      setLocation(next.location)
      setCompanies('')
      setIndustries(safeIndustries)
      setHeadcounts(safeHeadcounts)
      setSeniorities(safeSeniorities)
      setDepartments(safeDepartments)
      if (safeIndustries.some((v) => industryOptions.findIndex(([iv]) => iv === v) >= INDUSTRIES_COLLAPSED)) {
        setShowAllIndustries(true)
      }
      if (safeHeadcounts.length || safeSeniorities.length || safeDepartments.length) setShowMore(true)
      setPotentialInfo({ name: f.product_name || product.name, summary: f.summary, productId: product.id })
      await runSearch(1, next)
    } catch (e) {
      setError(e?.message || 'No pudimos armar la búsqueda de posibles clientes.')
    } finally {
      setPotentialLoading(false)
    }
  }

  const onPotentialClick = () => {
    if (products.length === 0) {
      setError('Primero cargá un producto o servicio en Productos para buscar sus posibles clientes.')
      return
    }
    setProductQuery('')
    setPickerOpen((v) => !v)
  }

  const togglePinnedProduct = (productId) => {
    const id = String(productId)
    setPinnedProductIds((prev) => {
      const next = prev.includes(id) ? prev.filter((x) => x !== id) : [id, ...prev]
      if (companyId) writePinnedProducts(companyId, next)
      return next
    })
  }

  const visibleProducts = useMemo(() => {
    const q = productQuery.trim().toLowerCase()
    const filtered = q
      ? products.filter((p) => {
          const name = String(p.name || '').toLowerCase()
          const desc = String(p.description || '').toLowerCase()
          return name.includes(q) || desc.includes(q)
        })
      : products
    const pinned = new Set(pinnedProductIds)
    return [...filtered].sort((a, b) => {
      const ap = pinned.has(String(a.id)) ? 0 : 1
      const bp = pinned.has(String(b.id)) ? 0 : 1
      if (ap !== bp) return ap - bp
      return pinnedProductIds.indexOf(String(a.id)) - pinnedProductIds.indexOf(String(b.id))
    })
  }, [products, productQuery, pinnedProductIds])

  useEffect(() => {
    if (pickerOpen) productSearchRef.current?.focus()
  }, [pickerOpen])

  useEffect(() => {
    if (!pickerOpen) return undefined
    const onDoc = (ev) => {
      if (!pickerRootRef.current?.contains(ev.target)) setPickerOpen(false)
    }
    const onKey = (ev) => {
      if (ev.key === 'Escape') setPickerOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [pickerOpen])

  const updatePerson = (personId, patch) => {
    setData((d) =>
      d ? { ...d, results: d.results.map((r) => (r.person_id === personId ? { ...r, ...patch } : r)) } : d,
    )
  }

  const reveal = async (p) => {
    setRevealing((m) => ({ ...m, [p.person_id]: true }))
    setError('')
    try {
      const out = await explorarReveal(companyId, personSnapshot(p))
      const person = out?.person || {}
      updatePerson(p.person_id, {
        revealed: true,
        revealed_by_nexus: !person.paid,
        email: person.email,
        phone: person.phone,
        linkedin_url: person.linkedin_url || p.linkedin_url,
        // Chips no mienten tras revelar: si vino valor ⇒ Sí.
        has_email: Boolean(person.email) || Boolean(p.has_email),
        has_mobile: Boolean(person.phone) || Boolean(p.has_mobile),
      })
      if (!out?.charged_credit && !person.paid) {
        setNotice(`${p.name} ya estaba revelado en Nexus: no se descontó ningún crédito.`)
      }
      mergeUsage(out?.usage)
    } catch (e) {
      setError(e?.message || 'No se pudo revelar.')
    } finally {
      setRevealing((m) => ({ ...m, [p.person_id]: false }))
    }
  }

  const saveFavorite = async (p) => {
    setStarring((m) => ({ ...m, [p.person_id]: true }))
    setError('')
    try {
      await explorarAddFavorite(companyId, favoriteSnapshot(p))
      setData((d) => (d ? { ...d, results: d.results.filter((r) => r.person_id !== p.person_id) } : d))
      setSelected((s) => {
        const next = { ...s }
        delete next[p.person_id]
        return next
      })
      setNotice(`Guardamos a ${p.name} en Guardados. Ya no te va a aparecer en las búsquedas.`)
    } catch (e) {
      setError(e?.message || 'No se pudo guardar.')
    } finally {
      setStarring((m) => ({ ...m, [p.person_id]: false }))
    }
  }

  const results = data?.results || []
  const selectedPeople = useMemo(() => results.filter((r) => selected[r.person_id]), [results, selected])
  const selectedRevealed = selectedPeople.filter((p) => p.revealed && !p.revealed_by_nexus).length
  const selectedToCharge = selectedPeople.length - selectedRevealed
  const selectable = results.filter((r) => !r.prospect_id && !r.in_use)
  const allSelected = selectable.length > 0 && selectable.every((r) => selected[r.person_id])

  const addToCampaign = async () => {
    if (!campaignId || selectedPeople.length === 0) return
    setAdding(true)
    setError('')
    setNotice('')
    try {
      const out = await explorarAddToCampaign(companyId, Number(campaignId), selectedPeople.map(personSnapshot))
      for (const r of out?.results || []) {
        if (r.ok) updatePerson(r.person_id, { prospect_id: r.prospect_id || true, campaign_id: out.campaign_id })
      }
      const failed = (out?.results || []).filter((r) => !r.ok)
      const msg = `Sumamos ${out?.added || 0} a «${out?.campaign_name || 'la campaña'}».`
      setNotice(
        failed.length ? `${msg} No se pudieron sumar: ${failed.map((f) => `${f.name} (${f.error})`).join('; ')}` : msg,
      )
      setSelected({})
      mergeUsage(out?.usage)
    } catch (e) {
      setError(e?.message || 'No se pudo sumar a la campaña.')
    } finally {
      setAdding(false)
    }
  }

  const toggleIndustry = (value) =>
    setIndustries((list) => (list.includes(value) ? list.filter((v) => v !== value) : [...list, value]))

  const totalPages = data?.total_pages || 0
  const visibleIndustries = useMemo(() => {
    const base = showAllIndustries ? industryOptions : industryOptions.slice(0, INDUSTRIES_COLLAPSED)
    return filterOptionsByQuery(base, showAllIndustries ? industryQuery : '')
  }, [industryOptions, showAllIndustries, industryQuery])
  return (
    <div className="pb-24">
      <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-nx-ink sm:text-[1.65rem]">Explorar</h1>
          <p className="mt-1.5 max-w-2xl text-sm text-nx-ink/75">
            Buscá por filtros o por el nombre de una persona puntual. Nexus prioriza gente con email y celular:
            antes de pagar ves si los tiene (Celular: Sí / No); el número y el mail se ven al tocar Revelar.
            Ver resultados es libre; pagás al revelar o al sumar a una campaña. El volumen depende de Prospeo
            (LinkedIn indexado), no es una cobertura tipo Lusha.
          </p>
        </div>
        {usage ? (
          <div className="rounded-xl border border-nx-border bg-white px-3.5 py-2 shadow-sm">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-nx-muted">Créditos</p>
            <p className="text-lg font-semibold tabular-nums text-nx-ink">{usage.credits_available}</p>
          </div>
        ) : null}
      </header>

      <form
        className="nx-card-elevated mb-6 space-y-5 p-5"
        onSubmit={(e) => {
          e.preventDefault()
          runSearch(1)
        }}
      >
        <div
          ref={pickerRootRef}
          className="relative flex flex-wrap items-center justify-between gap-3 rounded-xl bg-gradient-to-r from-violet-50 via-white to-sky-50 px-4 py-3 ring-1 ring-violet-100"
        >
          <div className="min-w-0">
            <p className="text-sm font-semibold text-nx-ink">Posibles clientes</p>
            <p className="text-xs text-nx-muted">Armamos la búsqueda de quién te compraría tu producto o servicio.</p>
          </div>
          <button
            type="button"
            disabled={potentialLoading || loading}
            onClick={onPotentialClick}
            className="nx-btn nx-btn-primary h-9 px-4 text-sm"
          >
            <Icon d={ICONS.sparkles} />
            {potentialLoading ? 'Buscando posibles clientes…' : 'Buscar posibles clientes'}
          </button>
          {pickerOpen ? (
            <div className="absolute right-4 top-full z-20 mt-2 w-80 overflow-hidden rounded-xl bg-white shadow-xl ring-1 ring-nx-border">
              <div className="border-b border-nx-border px-3 py-2">
                <label className="relative block">
                  <span className="pointer-events-none absolute inset-y-0 left-2 grid place-items-center text-nx-muted">
                    <Icon d={ICONS.search} className="size-3.5" />
                  </span>
                  <input
                    ref={productSearchRef}
                    type="search"
                    value={productQuery}
                    onChange={(e) => setProductQuery(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') e.preventDefault()
                    }}
                    placeholder="¿Qué querés vender?"
                    className="w-full rounded-lg border-0 bg-nx-bg py-1.5 pl-7 pr-2 text-sm text-nx-ink outline-none ring-1 ring-transparent placeholder:text-nx-muted focus:bg-white focus:ring-nx-ink/20"
                  />
                </label>
              </div>
              <ul className="max-h-64 overflow-y-auto py-1">
                {visibleProducts.length === 0 ? (
                  <li className="px-3 py-3 text-sm text-nx-muted">No hay productos con eso.</li>
                ) : (
                  visibleProducts.map((prod) => {
                    const pinned = pinnedProductIds.includes(String(prod.id))
                    return (
                      <li key={prod.id} className="flex items-stretch">
                        <button
                          type="button"
                          onClick={() => loadPotentialCustomers(prod)}
                          className="min-w-0 flex-1 px-3 py-2 text-left text-sm text-nx-ink hover:bg-nx-bg"
                        >
                          <span className="block font-medium">{prod.name}</span>
                          {prod.description ? (
                            <span className="line-clamp-1 text-xs text-nx-muted">{prod.description}</span>
                          ) : null}
                        </button>
                        <button
                          type="button"
                          title={pinned ? 'Sacar de arriba' : 'Dejar arriba'}
                          aria-label={pinned ? `Sacar ${prod.name} de arriba` : `Dejar ${prod.name} arriba`}
                          onClick={(e) => {
                            e.preventDefault()
                            e.stopPropagation()
                            togglePinnedProduct(prod.id)
                          }}
                          className={[
                            'grid w-10 shrink-0 place-items-center hover:bg-nx-bg',
                            pinned ? 'text-amber-500' : 'text-nx-muted hover:text-nx-ink',
                          ].join(' ')}
                        >
                          <Icon d={ICONS.star} filled={pinned} className="size-4" />
                        </button>
                      </li>
                    )
                  })
                )}
              </ul>
            </div>
          ) : null}
        </div>

        <div className="grid gap-3 md:grid-cols-[1.1fr_1.2fr_1fr_1fr_auto] md:items-end">
          <FieldWithIcon
            icon={ICONS.user}
            label="Nombre"
            placeholder="Nombre y apellido, o link linkedin.com/in/…"
            value={names}
            onChange={(e) => setNames(e.target.value)}
          />
          <FieldWithIcon
            icon={ICONS.search}
            label="Cargo"
            placeholder="Varios: Gerente de ops, e-commerce, logística…"
            value={titles}
            onChange={(e) => setTitles(e.target.value)}
          />
          <FieldWithIcon
            icon={ICONS.pin}
            label="Región"
            placeholder="Varios países: Chile, Panamá, Costa Rica…"
            value={location}
            onChange={(e) => setLocation(e.target.value)}
          />
          <FieldWithIcon
            icon={ICONS.building}
            label="Empresa"
            placeholder="Varias: acme.com, otra.com, Mercado Libre…"
            value={companies}
            onChange={(e) => setCompanies(e.target.value)}
          />
          <button type="submit" disabled={loading} className="nx-btn nx-btn-primary h-[38px] px-6 text-sm">
            <Icon d={ICONS.search} />
            {loading ? 'Buscando…' : 'Buscar'}
          </button>
        </div>

        <FieldWithIcon
          icon={ICONS.link}
          label="Empresas parecidas a"
          placeholder="https://www.linkedin.com/company/…  (1 a 3 links de la página de empresa)"
          value={lookalikeUrls}
          onChange={(e) => setLookalikeUrls(e.target.value)}
        />
        <p className="-mt-2 text-xs text-nx-muted">
          Pegá el link de la empresa y elegí el cargo. Nexus busca esa gente en compañías del mismo palo.
        </p>
        <p className="text-xs text-nx-muted">
          Cargo, región y empresa admiten varios valores separados por coma (ej. Chile, Panamá). Buscamos en
          LinkedIn vía Prospeo: el techo es esa base (no Lusha). Depto y tamaño dependen de lo cargado ahí.
        </p>

        <div>
          <div className="mb-2 flex items-center justify-between">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-nx-muted">
              Industria{industries.length ? ` · ${industries.length}` : ''}
            </span>
            {industries.length ? (
              <button type="button" onClick={() => setIndustries([])} className="text-xs text-nx-muted hover:text-nx-ink">
                Limpiar
              </button>
            ) : null}
          </div>
          {showAllIndustries && industryOptions.length > 10 ? (
            <input
              type="search"
              value={industryQuery}
              onChange={(e) => setIndustryQuery(e.target.value)}
              placeholder="Buscar industria…"
              className="nx-input mb-2 w-full max-w-md text-sm"
              autoComplete="off"
            />
          ) : null}
          <div className="flex flex-wrap gap-1.5">
            {visibleIndustries.map(([value, label]) => (
              <Chip key={value} active={industries.includes(value)} onClick={() => toggleIndustry(value)}>
                {label}
              </Chip>
            ))}
            {showAllIndustries && industryQuery.trim() && visibleIndustries.length === 0 ? (
              <p className="text-xs text-nx-muted">Ninguna industria con ese texto.</p>
            ) : null}
            <button
              type="button"
              onClick={() => {
                setShowAllIndustries((v) => {
                  if (v) setIndustryQuery('')
                  return !v
                })
              }}
              className="rounded-full px-3 py-1 text-xs font-medium text-nx-muted hover:text-nx-ink"
            >
              {showAllIndustries
                ? 'Ver menos'
                : `+${Math.max(0, industryOptions.length - INDUSTRIES_COLLAPSED)} más`}
            </button>
          </div>
        </div>

        <div className="border-t border-nx-border pt-4">
          <button
            type="button"
            onClick={() => setShowMore((v) => !v)}
            className="text-xs font-semibold text-nx-ink/80 hover:text-nx-ink"
          >
            {showMore ? 'Menos filtros ▴' : 'Más filtros ▾'}
            {!showMore && headcounts.length + seniorities.length + departments.length > 0
              ? ` · ${headcounts.length + seniorities.length + departments.length} activos`
              : ''}
          </button>
          {showMore ? (
            <div className="mt-4 space-y-4">
              <ChipGroup
                label="Tamaño de empresa (empleados)"
                options={headcountOptions}
                values={headcounts}
                onChange={setHeadcounts}
                searchable
              />
              <ChipGroup
                label="Nivel"
                options={seniorityOptions}
                values={seniorities}
                onChange={setSeniorities}
                searchable
              />
              <ChipGroup
                label="Área"
                options={departmentOptions}
                values={departments}
                onChange={setDepartments}
                searchable
              />
            </div>
          ) : null}
        </div>
      </form>

      {potentialInfo ? (
        <div className="mb-4 flex items-start gap-3 rounded-xl bg-violet-50 px-4 py-3 text-sm text-violet-950 ring-1 ring-violet-200">
          <Icon d={ICONS.sparkles} className="mt-0.5 size-4 shrink-0" />
          <div className="min-w-0 flex-1">
            <p className="font-semibold">Posibles clientes de «{potentialInfo.name}»</p>
            {potentialInfo.summary ? <p className="mt-0.5 text-violet-900/80">{potentialInfo.summary}</p> : null}
            <p className="mt-1 text-xs text-violet-900/60">Podés ajustar los filtros de arriba y volver a buscar.</p>
          </div>
          <button type="button" onClick={() => setPotentialInfo(null)} className="text-xs text-violet-900/60 hover:text-violet-950">
            ✕
          </button>
        </div>
      ) : null}

      {lookalikeSeeds.length ? (
        <div className="mb-4 flex items-start gap-3 rounded-xl bg-sky-50 px-4 py-3 text-sm text-sky-950 ring-1 ring-sky-200">
          <Icon d={ICONS.link} className="mt-0.5 size-4 shrink-0" />
          <div className="min-w-0 flex-1">
            <p className="font-semibold">
              Empresas parecidas a{' '}
              {lookalikeSeeds.map((s) => s.name || 'esa empresa').join(', ')}
            </p>
            <p className="mt-0.5 text-sky-900/80">
              Personas a las que les puede interesar tu producto, en compañías del mismo palo.
            </p>
          </div>
          <button
            type="button"
            onClick={() => {
              setLookalikeUrls('')
              setLookalikeSeeds([])
            }}
            className="text-xs text-sky-900/60 hover:text-sky-950"
          >
            ✕
          </button>
        </div>
      ) : null}

      <AlertBanner message={error} />
      {notice ? (
        <p className="mb-4 rounded-lg bg-emerald-50 px-4 py-2.5 text-sm text-emerald-900 ring-1 ring-emerald-200">
          {notice}
        </p>
      ) : null}

      {!data && !loading ? (
        <section className="rounded-2xl border border-dashed border-nx-border bg-white/60 px-6 py-10 text-center">
          <div className="mx-auto grid size-12 place-items-center rounded-full bg-nx-bg text-nx-ink">
            <Icon d={ICONS.sparkles} className="size-6" />
          </div>
          <h2 className="mt-3 text-base font-semibold text-nx-ink">¿A quién querés encontrar?</h2>
          <p className="mx-auto mt-1 max-w-md text-sm text-nx-muted">
            Combiná cargo, región e industria, o tocá «Buscar posibles clientes». También podés probar con:
          </p>
          <div className="mt-4 flex flex-wrap justify-center gap-2">
            {QUICK_SEARCHES.map((q) => (
              <button
                key={q.label}
                type="button"
                onClick={() => quickSearch(q)}
                className="rounded-full bg-white px-3.5 py-1.5 text-xs font-medium text-nx-ink shadow-sm ring-1 ring-nx-border transition hover:ring-nx-ink/40"
              >
                {q.label}
              </button>
            ))}
          </div>
        </section>
      ) : null}

      {loading && !data ? (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      ) : null}

      {data ? (
        <section>
          {results.length > 0 ? (
            <p className="mb-3 rounded-lg bg-amber-50 px-3 py-1.5 text-[11px] leading-snug text-amber-950/85 ring-1 ring-amber-100/80">
              LinkedIn: no abras más de <strong>20 perfiles en 1 hora</strong>. Si vas más rápido con los links, la
              cuenta puede quedar restringida.
            </p>
          ) : null}
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <p className="text-sm text-nx-ink">
                {(() => {
                  const shown = results.length
                  const s = searchResultsSummary({
                    shown,
                    pageSize: data.page_size || 25,
                    rawTotal: data.total_count || 0,
                    pages: data.total_pages || 0,
                    morePending: Boolean(data.more_pending),
                    totalIsEstimate: Boolean(data.total_is_estimate),
                  })
                  const tilde = s.totalIsEstimate ? '~' : ''
                  if (s.label === 'pending') {
                    return (
                      <>
                        <strong className="text-base">{shown}</strong>
                        {' en pantalla'}
                        {s.total > shown ? (
                          <>
                            {' · buscando más · ICP '}
                            <strong className="text-base">
                              {tilde}
                              {s.total.toLocaleString('es-AR')}
                            </strong>
                          </>
                        ) : null}
                      </>
                    )
                  }
                  return (
                    <>
                      <strong className="text-base">
                        {tilde}
                        {s.total.toLocaleString('es-AR')}
                      </strong>{' '}
                      {s.totalIsEstimate ? 'en el ICP' : 'personas encontradas'}
                      {s.showPageHint ? ` · ${shown} en esta página` : null}
                    </>
                  )
                })()}
              </p>
              {data.from_cache ? (
                <span className="rounded-full bg-nx-bg px-2 py-0.5 text-[10px] font-medium text-nx-muted">
                  Página ya vista · no suma
                </span>
              ) : null}
            </div>
            <div className="flex items-center gap-3 text-xs">
              {selectable.length > 0 ? (
                <label className="flex items-center gap-1.5 text-nx-ink/80">
                  <input
                    type="checkbox"
                    className="accent-[#111]"
                    checked={allSelected}
                    onChange={(e) => {
                      const next = {}
                      if (e.target.checked) selectable.forEach((r) => (next[r.person_id] = true))
                      setSelected(next)
                    }}
                  />
                  Seleccionar página
                </label>
              ) : null}
              {totalPages > 1 ? (
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    disabled={loading || page <= 1}
                    onClick={() => runSearch(page - 1)}
                    className="nx-btn nx-btn-secondary px-2.5 py-1"
                  >
                    ←
                  </button>
                  <span className="px-2 tabular-nums text-nx-ink/80">
                    {page} / {totalPages}
                  </span>
                  <button
                    type="button"
                    disabled={loading || page >= totalPages}
                    onClick={() => runSearch(page + 1)}
                    className="nx-btn nx-btn-secondary px-2.5 py-1"
                  >
                    →
                  </button>
                </div>
              ) : null}
            </div>
          </div>
          {data.coverage_note ? (
            <p className="mb-3 max-w-3xl text-[11px] leading-snug text-nx-muted">{data.coverage_note}</p>
          ) : null}

          {results.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-nx-border px-6 py-10 text-center text-sm text-nx-muted">
              {data?.more_pending ? (
                <>
                  <p>Todavía estamos recorriendo resultados para esta página.</p>
                  <button
                    type="button"
                    disabled={loading}
                    onClick={() => runSearch(page)}
                    className="nx-btn nx-btn-secondary mt-3 px-3 py-1.5"
                  >
                    Seguir buscando
                  </button>
                </>
              ) : page > 1 ? (
                <>
                  <p>No hay más personas a partir de esta página.</p>
                  <button
                    type="button"
                    disabled={loading}
                    onClick={() => runSearch(1)}
                    className="nx-btn nx-btn-secondary mt-3 px-3 py-1.5"
                  >
                    Volver a la página 1
                  </button>
                </>
              ) : (
                data?.empty_hint ||
                'No encontramos personas con esos filtros. Probá ampliando la región, el cargo o sacando algún filtro.'
              )}
            </div>
          ) : (
            <div className={['grid gap-3 md:grid-cols-2 xl:grid-cols-3', loading ? 'opacity-60' : ''].join(' ')}>
              {results.map((p) => (
                <PersonCard
                  key={p.person_id}
                  p={p}
                  selected={Boolean(selected[p.person_id])}
                  onSelect={(v) => setSelected((s) => ({ ...s, [p.person_id]: v }))}
                  revealing={Boolean(revealing[p.person_id])}
                  onReveal={() => reveal(p)}
                  starring={Boolean(starring[p.person_id])}
                  onToggleFavorite={() => saveFavorite(p)}
                />
              ))}
            </div>
          )}
        </section>
      ) : null}

      {selectedPeople.length > 0 ? (
        <div className="fixed inset-x-0 bottom-4 z-30 flex justify-center px-4">
          <div className="flex w-full max-w-3xl flex-wrap items-center gap-3 rounded-2xl bg-nx-ink px-4 py-3 text-white shadow-2xl">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold">{selectedPeople.length} seleccionados</p>
              <p className="text-[11px] text-white/70">
                {selectedRevealed} ya revelados (sin costo extra) · {selectedToCharge} usan 1 crédito c/u
              </p>
            </div>
            <select
              className="h-9 rounded-lg border border-white/20 bg-white/10 px-2 text-sm text-white outline-none focus:border-white/50"
              value={campaignId}
              onChange={(e) => setCampaignId(e.target.value)}
            >
              <option value="" className="text-nx-ink">
                Elegí campaña…
              </option>
              {campaigns.map((c) => (
                <option key={c.id} value={c.id} className="text-nx-ink">
                  {c.name}
                </option>
              ))}
            </select>
            <button
              type="button"
              disabled={adding || !campaignId}
              onClick={addToCampaign}
              className="h-9 rounded-lg bg-white px-4 text-sm font-semibold text-nx-ink transition hover:bg-white/90 disabled:opacity-50"
            >
              {adding ? 'Sumando…' : 'Sumar a campaña'}
            </button>
            <button
              type="button"
              onClick={() => setSelected({})}
              className="text-xs text-white/70 hover:text-white"
            >
              Cancelar
            </button>
          </div>
        </div>
      ) : null}
    </div>
  )
}
