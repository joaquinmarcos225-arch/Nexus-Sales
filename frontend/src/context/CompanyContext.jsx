import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react'
import { useAuth } from './AuthContext.jsx'
import { fetchCompanies } from '../utils/api.js'

const STORAGE_KEY = 'nexus_sales_company_id'

const CompanyContext = createContext(null)

export function CompanyProvider({ children }) {
  const { user, isAuthenticated } = useAuth()
  const [companies, setCompanies] = useState([])
  const [companyId, setCompanyIdState] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const refreshCompanies = useCallback(async () => {
    if (!isAuthenticated) {
      setCompanies([])
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const list = await fetchCompanies()
      setCompanies(Array.isArray(list) ? list : [])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setCompanies([])
    } finally {
      setLoading(false)
    }
  }, [isAuthenticated])

  useEffect(() => {
    void refreshCompanies()
  }, [refreshCompanies])

  const setCompanyId = useCallback((id) => {
    setCompanyIdState(id)
    if (id != null) {
      localStorage.setItem(STORAGE_KEY, String(id))
    } else {
      localStorage.removeItem(STORAGE_KEY)
    }
  }, [])

  useEffect(() => {
    if (!isAuthenticated || !user) {
      setCompanyIdState(null)
      return
    }
    if (user.company_id) {
      setCompanyIdState(user.company_id)
    }
  }, [isAuthenticated, user])

  useEffect(() => {
    if (loading || companies.length === 0) {
      return
    }
    if (companyId == null || !companies.some((c) => c.id === companyId)) {
      setCompanyId(companies[0]?.id ?? user?.company_id ?? null)
    }
  }, [companies, companyId, loading, setCompanyId, user?.company_id])

  const company = useMemo(
    () => companies.find((c) => c.id === companyId) ?? null,
    [companies, companyId],
  )

  const value = useMemo(
    () => ({
      companies,
      company,
      companyId,
      setCompanyId,
      loading,
      error,
      refreshCompanies,
    }),
    [companies, company, companyId, error, loading, refreshCompanies, setCompanyId],
  )

  return (
    <CompanyContext.Provider value={value}>{children}</CompanyContext.Provider>
  )
}

export function useCompany() {
  const ctx = useContext(CompanyContext)
  if (!ctx) {
    throw new Error('useCompany debe usarse dentro de CompanyProvider')
  }
  return ctx
}
