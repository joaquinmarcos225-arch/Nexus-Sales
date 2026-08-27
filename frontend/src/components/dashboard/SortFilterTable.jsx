import { useMemo, useState } from 'react'

/**
 * @param {{
 *   key: string,
 *   label: string,
 *   className?: string,
 *   thClassName?: string,
 *   render?: (row: object) => React.ReactNode,
 *   sortValue?: (row: object) => string | number,
 * }[]} columns
 * @param {boolean} [compact]
 * @param {boolean} [stickyLast] — fija la última columna (ej. acciones) a la derecha
 */
export function SortFilterTable({
  columns,
  rows,
  filterPlaceholder = 'Buscar…',
  compact = false,
  stickyLast = false,
}) {
  const [q, setQ] = useState('')
  const [sort, setSort] = useState({ key: columns[0]?.key ?? '', dir: 'asc' })

  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase()
    if (!s) {
      return rows
    }
    return rows.filter((row) => {
      const blob = columns
        .map((c) => {
          const v = c.sortValue ? c.sortValue(row) : row[c.key]
          return v != null ? String(v).toLowerCase() : ''
        })
        .join(' ')
      return blob.includes(s)
    })
  }, [rows, q, columns])

  const sorted = useMemo(() => {
    if (!sort.key) {
      return filtered
    }
    const col = columns.find((c) => c.key === sort.key)
    const dirMul = sort.dir === 'asc' ? 1 : -1
    const copy = [...filtered]
    copy.sort((a, b) => {
      const av = col?.sortValue ? col.sortValue(a) : a[sort.key]
      const bv = col?.sortValue ? col.sortValue(b) : b[sort.key]
      const an = typeof av === 'number' ? av : String(av ?? '').toLowerCase()
      const bn = typeof bv === 'number' ? bv : String(bv ?? '').toLowerCase()
      if (an < bn) {
        return -1 * dirMul
      }
      if (an > bn) {
        return 1 * dirMul
      }
      return 0
    })
    return copy
  }, [filtered, sort, columns])

  function toggleSort(key) {
    setSort((prev) =>
      prev.key === key
        ? { key, dir: prev.dir === 'asc' ? 'desc' : 'asc' }
        : { key, dir: 'asc' },
    )
  }

  const pad = compact ? 'px-2 py-1.5' : 'px-4 py-3'
  const textSize = compact ? 'text-xs' : 'text-sm'
  const headSize = compact ? 'text-[10px]' : 'text-[11px]'
  const lastIdx = columns.length - 1

  function cellSticky(i) {
    if (!stickyLast || i !== lastIdx) return ''
    return 'sticky right-0 z-[1] bg-nx-card shadow-[-6px_0_8px_-6px_rgba(0,0,0,0.12)]'
  }

  function headSticky(i) {
    if (!stickyLast || i !== lastIdx) return ''
    return 'sticky right-0 z-[2] bg-nx-card-muted shadow-[-6px_0_8px_-6px_rgba(0,0,0,0.12)]'
  }

  return (
    <div className="space-y-3">
      <input
        type="search"
        className={[
          'w-full max-w-md rounded-lg border border-nx-border bg-white text-nx-ink placeholder:text-nx-muted/70 shadow-sm focus:outline-none focus:ring-2 focus:ring-nx-brand/25',
          compact ? 'px-2.5 py-1.5 text-xs' : 'px-3 py-2 text-sm',
        ].join(' ')}
        placeholder={filterPlaceholder}
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      <div className="overflow-hidden rounded-xl border border-nx-border bg-nx-card shadow-sm">
        <div className="overflow-x-auto">
          <table
            className={[
              'w-full divide-y divide-nx-border',
              textSize,
              compact ? 'min-w-0 table-fixed' : 'min-w-[720px]',
            ].join(' ')}
          >
            <thead className={`bg-nx-card-muted text-left ${headSize} font-semibold uppercase tracking-wide text-nx-muted`}>
              <tr>
                {columns.map((c, i) => (
                  <th
                    key={c.key}
                    className={[
                      pad,
                      compact ? '' : 'whitespace-nowrap',
                      c.thClassName ?? c.className ?? '',
                      headSticky(i),
                    ].join(' ')}
                  >
                    {c.label ? (
                      <button
                        type="button"
                        className="inline-flex max-w-full items-center gap-0.5 font-semibold text-nx-muted hover:text-nx-ink"
                        onClick={() => toggleSort(c.key)}
                      >
                        <span className={compact ? 'truncate' : ''}>{c.label}</span>
                        {sort.key === c.key ? (sort.dir === 'asc' ? ' ↑' : ' ↓') : ''}
                      </button>
                    ) : (
                      <span className="sr-only">Acciones</span>
                    )}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-nx-border text-nx-ink">
              {sorted.length === 0 ? (
                <tr>
                  <td colSpan={columns.length} className={`${pad} text-center text-nx-muted`}>
                    Sin filas para mostrar.
                  </td>
                </tr>
              ) : (
                sorted.map((row, idx) => (
                  <tr
                    key={row.id != null ? String(row.id) + String(idx) : idx}
                    className="group hover:bg-nx-card-muted/80"
                  >
                    {columns.map((c, i) => (
                      <td
                        key={c.key}
                        className={[
                          pad,
                          compact ? 'align-middle' : '',
                          c.className ?? '',
                          cellSticky(i),
                          stickyLast && i === lastIdx ? 'group-hover:bg-nx-card-muted/80' : '',
                        ].join(' ')}
                      >
                        {c.render
                          ? c.render(row)
                          : row[c.key] != null
                            ? String(row[c.key])
                            : '—'}
                      </td>
                    ))}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
