import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { useCompany } from './CompanyContext.jsx'
import { fetchCompanyAnalytics } from '../utils/api.js'

const DashboardAnalyticsContext = createContext(null)

export function DashboardAnalyticsProvider({ children }) {
  const { companyId } = useCompany()
  const location = useLocation()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const refresh = useCallback(async () => {
    if (!companyId) {
      setData(null)
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const res = await fetchCompanyAnalytics(companyId)
      setData(res)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [companyId])

  useEffect(() => {
    if (!companyId) {
      setData(null)
      setLoading(false)
      return
    }
    if (!location.pathname.startsWith('/dashboard')) {
      return
    }
    void refresh()
  }, [companyId, location.pathname, refresh])

  const value = {
    data,
    loading,
    error,
    refresh,
    companyId,
  }

  return (
    <DashboardAnalyticsContext.Provider value={value}>
      {children}
    </DashboardAnalyticsContext.Provider>
  )
}

export function useDashboardAnalytics() {
  const ctx = useContext(DashboardAnalyticsContext)
  if (!ctx) {
    throw new Error('useDashboardAnalytics debe usarse dentro de DashboardAnalyticsProvider')
  }
  return ctx
}
