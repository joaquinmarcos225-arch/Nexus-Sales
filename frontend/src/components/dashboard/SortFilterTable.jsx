import { useMemo, useState } from 'react'

/**
 * @param {{ key: string, label: string, className?: string, render?: (row: object) => React.ReactNode, sortValue?: (row: object) => string | number }[]} columns
 */
export function SortFilterTable({ columns, rows, filterPlaceholder = 'Buscar…' }) {
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

  return (
    <div className="space-y-3">
      <input
        type="search"
        className="w-full max-w-md rounded-lg border border-nx-border bg-white px-3 py-2 text-sm text-nx-ink placeholder:text-nx-muted/70 shadow-sm focus:outline-none focus:ring-2 focus:ring-nx-brand/25"
        placeholder={filterPlaceholder}
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      <div className="overflow-hidden rounded-xl border border-nx-border bg-nx-card shadow-sm">
        <div className="overflow-x-auto">
          <table className="min-w-[720px] w-full divide-y divide-nx-border text-sm">
            <thead className="bg-nx-card-muted text-left text-[11px] font-semibold uppercase tracking-wide text-nx-muted">
              <tr>
                {columns.map((c) => (
                  <th key={c.key} className={`whitespace-nowrap px-4 py-3 ${c.className ?? ''}`}>
                    <button
                      type="button"
                      className="inline-flex items-center gap-1 font-semibold text-nx-muted hover:text-nx-ink"
                      onClick={() => toggleSort(c.key)}
                    >
                      {c.label}
                      {sort.key === c.key ? (sort.dir === 'asc' ? ' ↑' : ' ↓') : ''}
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-nx-border text-nx-ink">
              {sorted.length === 0 ? (
                <tr>
                  <td
                    colSpan={columns.length}
                    className="px-4 py-8 text-center text-sm text-nx-muted"
                  >
                    Sin filas para mostrar.
                  </td>
                </tr>
              ) : (
                sorted.map((row, idx) => (
                  <tr
                    key={row.id != null ? String(row.id) + String(idx) : idx}
                    className="hover:bg-nx-card-muted/80"
                  >
                    {columns.map((c) => (
                      <td key={c.key} className={`px-4 py-3 ${c.className ?? ''}`}>
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
