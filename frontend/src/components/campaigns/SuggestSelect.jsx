import { useEffect, useMemo, useRef, useState } from 'react'

const inputClass =
  'mt-1 w-full rounded-md border border-nx-border bg-white px-2.5 py-1.5 text-sm text-nx-ink shadow-none placeholder:text-nx-subtle focus:border-nx-brand/50 focus:outline-none focus:ring-1 focus:ring-nx-brand/20'

/**
 * Lista desplegable scrolleable (como zona horaria) + texto libre.
 * Lo que se escribe es el valor; las opciones son atajos, no un menú cerrado.
 */
export function SuggestSelect({
  id,
  label,
  value,
  onChange,
  suggestions = [],
  hint,
  placeholder,
  required,
}) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef(null)
  const list = useMemo(
    () => [...new Set((suggestions || []).map((s) => String(s).trim()).filter(Boolean))],
    [suggestions],
  )

  const filtered = useMemo(() => {
    const q = String(value || '').trim().toLowerCase()
    if (!q) return list
    const hits = list.filter((s) => s.toLowerCase().includes(q))
    const exact = list.some((s) => s.toLowerCase() === q)
    if (!exact && q) {
      return [`Usar «${String(value).trim()}»`, ...hits]
    }
    return hits
  }, [list, value])

  useEffect(() => {
    function onDocClick(ev) {
      if (!rootRef.current?.contains(ev.target)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [])

  function pick(raw) {
    const text = String(raw || '')
    const custom = text.match(/^Usar «(.+)»$/)
    onChange(custom ? custom[1] : text)
    setOpen(false)
  }

  return (
    <div ref={rootRef} className="relative">
      <label htmlFor={id} className="text-xs font-medium text-nx-ink">
        {label}
      </label>
      {hint ? <p className="mt-0.5 text-[11px] text-nx-subtle">{hint}</p> : null}
      <input
        id={id}
        name={id}
        type="text"
        autoComplete="off"
        required={required}
        className={inputClass}
        value={value}
        placeholder={placeholder || 'Escribí o elegí de la lista'}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            setOpen(false)
            return
          }
          if (e.key === 'Enter' && open && filtered.length > 0) {
            e.preventDefault()
            pick(filtered[0])
          }
        }}
        onChange={(e) => {
          onChange(e.target.value)
          setOpen(true)
        }}
      />
      {open ? (
        <ul
          className="absolute z-50 mt-1 max-h-56 w-full overflow-y-auto rounded-lg border border-nx-border bg-white py-1 shadow-lg"
          role="listbox"
        >
          {filtered.length === 0 ? (
            <li className="px-3 py-2 text-sm text-nx-subtle">Escribí un valor o bajá con la rueda</li>
          ) : (
            filtered.map((opt) => (
              <li key={opt}>
                <button
                  type="button"
                  className={`w-full px-3 py-2 text-left text-sm hover:bg-nx-card-muted ${
                    opt === value ? 'bg-nx-brand/10 font-medium text-nx-brand' : 'text-nx-ink'
                  }`}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => pick(opt)}
                >
                  {opt}
                </button>
              </li>
            ))
          )}
        </ul>
      ) : null}
    </div>
  )
}
