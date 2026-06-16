import { useCallback, useEffect, useState } from 'react'

const STORAGE_KEY = 'nx-sidebar-collapsed'

export function useSidebarCollapsed() {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(STORAGE_KEY) === '1'
    } catch {
      return false
    }
  })

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, collapsed ? '1' : '0')
    } catch {
      /* ignore */
    }
  }, [collapsed])

  const toggle = useCallback(() => {
    setCollapsed((v) => !v)
  }, [])

  return { collapsed, setCollapsed, toggle }
}
