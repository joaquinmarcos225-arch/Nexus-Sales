import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../../context/AuthContext.jsx'
import { useCompany } from '../../context/CompanyContext.jsx'
import { sidebarNavForRole } from '../../data/navigation.js'
import { fetchCampaigns, fetchCampaignProspects } from '../../utils/api.js'

/**
 * @typedef {{ id: string, label: string, meta?: string, to: string, keywords?: string }} SearchItem
 */

function normalize(text) {
  return String(text || '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
}

/**
 * @param {SearchItem[]} items
 * @param {string} query
 */
function filterItems(items, query) {
  const q = normalize(query.trim())
  if (!q) {
    return items.slice(0, 14)
  }
  return items
    .filter((item) => {
      const hay = normalize(`${item.label} ${item.meta || ''} ${item.keywords || ''}`)
      return hay.includes(q)
    })
    .slice(0, 20)
}

export function useGlobalSearchHotkey(openSearch) {
  useEffect(() => {
    function onKeyDown(event) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        openSearch()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [openSearch])
}

/**
 * @param {{ open: boolean, onClose: () => void }} props
 */
export function GlobalSearch({ open, onClose }) {
  const navigate = useNavigate()
  const { user } = useAuth()
  const { companyId } = useCompany()
  const [query, setQuery] = useState('')
  const [items, setItems] = useState(/** @type {SearchItem[]} */ ([]))
  const [loading, setLoading] = useState(false)
  const [activeIndex, setActiveIndex] = useState(0)
  const inputRef = useRef(null)

  useEffect(() => {
    if (!open) {
      setQuery('')
      setActiveIndex(0)
      return
    }
    inputRef.current?.focus()
  }, [open])

  useEffect(() => {
    if (!open || !companyId) {
      return undefined
    }
    let cancelled = false
    ;(async () => {
      setLoading(true)
      try {
        /** @type {SearchItem[]} */
        const index = []

        for (const nav of sidebarNavForRole(user)) {
          index.push({
            id: `nav-${nav.to}`,
            label: nav.label,
            meta: 'Navegación',
            to: nav.to,
            keywords: nav.label,
          })
        }

        index.push({
          id: 'dash-go-live',
          label: 'Go-live',
          meta: 'Consola',
          to: '/dashboard/go-live',
          keywords: 'go live checklist',
        })

        const campaigns = await fetchCampaigns(companyId)
        const list = Array.isArray(campaigns) ? campaigns : []

        for (const campaign of list) {
          index.push({
            id: `camp-${campaign.id}`,
            label: campaign.name,
            meta: 'Campaña',
            to: `/campanas/${campaign.id}`,
            keywords: `campaña campaign ${campaign.status || ''}`,
          })
        }

        const prospectLists = await Promise.all(
          list.map((c) => fetchCampaignProspects(c.id).catch(() => [])),
        )

        list.forEach((campaign, i) => {
          for (const prospect of prospectLists[i] || []) {
            const name = prospect.name || prospect.full_name || `Prospecto #${prospect.id}`
            const company = prospect.company_name || prospect.company || ''
            index.push({
              id: `prospect-${prospect.id}`,
              label: name,
              meta: company ? `${company} · ${campaign.name}` : campaign.name,
              to: `/campanas/${campaign.id}?prospect=${prospect.id}&focus=outreach`,
              keywords: `${name} ${company} prospecto lead ${prospect.email || ''}`,
            })
          }
        })

        if (!cancelled) {
          setItems(index)
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    })()

    return () => {
      cancelled = true
    }
  }, [open, companyId, user])

  const results = useMemo(() => filterItems(items, query), [items, query])

  useEffect(() => {
    setActiveIndex(0)
  }, [query, open])

  const go = useCallback(
    (item) => {
      if (!item) {
        return
      }
      onClose()
      navigate(item.to)
    },
    [navigate, onClose],
  )

  useEffect(() => {
    if (!open) {
      return undefined
    }
    function onKeyDown(event) {
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
        return
      }
      if (event.key === 'ArrowDown') {
        event.preventDefault()
        setActiveIndex((i) => Math.min(i + 1, Math.max(0, results.length - 1)))
        return
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault()
        setActiveIndex((i) => Math.max(i - 1, 0))
        return
      }
      if (event.key === 'Enter') {
        event.preventDefault()
        go(results[activeIndex])
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open, onClose, results, activeIndex, go])

  if (!open) {
    return null
  }

  return (
    <div className="nx-search-overlay" role="presentation" onClick={onClose}>
      <div
        className="nx-search-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Búsqueda global"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="border-b border-white/[0.08] px-4 py-3">
          <input
            ref={inputRef}
            type="search"
            className="nx-search-input"
            placeholder="Buscar campañas y páginas…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            autoComplete="off"
            spellCheck={false}
          />
        </div>
        <ul className="max-h-[min(24rem,50vh)] overflow-y-auto py-2" role="listbox">
          {loading && items.length === 0 ? (
            <li className="px-4 py-6 text-center text-sm text-zinc-500">Indexando…</li>
          ) : null}
          {!loading && results.length === 0 ? (
            <li className="px-4 py-6 text-center text-sm text-zinc-500">Sin resultados</li>
          ) : null}
          {results.map((item, index) => (
            <li key={item.id} role="option" aria-selected={index === activeIndex}>
              <button
                type="button"
                className={[
                  'nx-search-result flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left',
                  index === activeIndex ? 'nx-search-result--active' : '',
                ]
                  .filter(Boolean)
                  .join(' ')}
                onMouseEnter={() => setActiveIndex(index)}
                onClick={() => go(item)}
              >
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium text-zinc-100">{item.label}</span>
                  {item.meta ? (
                    <span className="block truncate text-xs text-zinc-500">{item.meta}</span>
                  ) : null}
                </span>
                <span className="shrink-0 text-[10px] uppercase tracking-wide text-zinc-600">Ir →</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
