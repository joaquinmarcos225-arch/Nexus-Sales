import { useEffect, useMemo, useRef, useState } from 'react'
import {
  buildTimezoneOptions,
  labelForTimezoneId,
  resolveTimezoneQuery,
} from '../../utils/timezones.js'

const inputClass =
  'mt-1 w-full rounded-md border border-nx-border bg-white px-2.5 py-1.5 text-sm text-nx-ink shadow-none placeholder:text-nx-subtle focus:border-nx-subtle focus:outline-none focus:ring-1 focus:ring-nx-subtle/25'

export function TimezoneSelect({ id, value, onChange, required }) {
  const options = useMemo(() => buildTimezoneOptions(), [])
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const rootRef = useRef(null)

  const selectedLabel = useMemo(
    () => (value ? labelForTimezoneId(value, options) : ''),
    [value, options],
  )

  useEffect(() => {
    if (!open) {
      setQuery(selectedLabel)
    }
  }, [open, selectedLabel])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return options.slice(0, 80)
    return options.filter((opt) => opt.searchText.includes(q)).slice(0, 80)
  }, [options, query])

  useEffect(() => {
    function onDocClick(ev) {
      if (!rootRef.current?.contains(ev.target)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [])

  function pick(opt) {
    onChange(opt.id)
    setQuery(opt.label)
    setOpen(false)
  }

  function tryResolveQuery(text) {
    const resolved = resolveTimezoneQuery(text, options)
    if (resolved) {
      onChange(resolved)
      setQuery(labelForTimezoneId(resolved, options))
      return true
    }
    return false
  }

  function handleBlur() {
    setOpen(false)
    const text = query.trim()
    if (!text) {
      onChange('')
      return
    }
    if (value && text === selectedLabel) {
      return
    }
    tryResolveQuery(text)
  }

  return (
    <div ref={rootRef} className="relative">
      <label htmlFor={id} className="text-xs font-medium text-nx-ink">
        Zona horaria del usuario
      </label>
      <p className="mt-0.5 text-[11px] text-nx-subtle">
        Buscá tu región (ej. LATAM, Brasil, Argentina). Si tiene varias zonas, elegí la de tu ciudad.
      </p>
      <input
        id={id}
        type="text"
        autoComplete="off"
        className={inputClass}
        value={query}
        placeholder="Ej. LATAM, Brasil, Argentina, España…"
        aria-required={required ? 'true' : undefined}
        onFocus={() => {
          setOpen(true)
          if (query === selectedLabel) {
            setQuery('')
          }
        }}
        onBlur={handleBlur}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            setOpen(false)
            setQuery(selectedLabel)
            return
          }
          if (e.key === 'Enter' && open && filtered.length > 0) {
            e.preventDefault()
            pick(filtered[0])
          }
        }}
        onChange={(e) => {
          setQuery(e.target.value)
          setOpen(true)
          if (!e.target.value.trim()) {
            onChange('')
          }
        }}
      />
      <input type="hidden" name={id} value={value || ''} readOnly />
      {open ? (
        <ul
          className="absolute z-50 mt-1 max-h-56 w-full overflow-y-auto rounded-lg border border-nx-border bg-white py-1 shadow-lg"
          role="listbox"
        >
          {filtered.length === 0 ? (
            <li className="px-3 py-2 text-sm text-nx-subtle">Sin resultados</li>
          ) : (
            filtered.map((opt) => (
              <li key={opt.id}>
                <button
                  type="button"
                  className={`w-full px-3 py-2 text-left text-sm hover:bg-nx-card-muted ${
                    opt.id === value ? 'bg-nx-brand/10 font-medium text-nx-brand' : 'text-nx-ink'
                  }`}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => pick(opt)}
                >
                  {opt.label}
                </button>
              </li>
            ))
          )}
        </ul>
      ) : null}
    </div>
  )
}
