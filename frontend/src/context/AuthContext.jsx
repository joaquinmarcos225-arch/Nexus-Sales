import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react'
import { getStoredToken, setStoredToken } from '../utils/authStorage.js'
import { fetchAuthMe, login as apiLogin, registerWorkspace as apiRegisterWorkspace } from '../utils/api.js'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [token, setTokenState] = useState(() => getStoredToken())
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const setToken = useCallback((value) => {
    setTokenState(value)
    setStoredToken(value)
  }, [])

  const refreshUser = useCallback(async ({ silent = false } = {}) => {
    if (!token) {
      setUser(null)
      setLoading(false)
      return null
    }
    if (!silent) setLoading(true)
    setError(null)
    try {
      const me = await fetchAuthMe()
      setUser(me)
      return me
    } catch (e) {
      const status = e?.status
      if (status === 401) {
        setUser(null)
        setToken(null)
      } else {
        setError(e instanceof Error ? e.message : String(e))
      }
      return null
    } finally {
      if (!silent) setLoading(false)
    }
  }, [token, setToken])

  useEffect(() => {
    void refreshUser()
  }, [refreshUser])

  useEffect(() => {
    const id = window.setInterval(() => {
      void refreshUser({ silent: true })
    }, 60 * 60 * 1000)
    return () => window.clearInterval(id)
  }, [refreshUser])

  const login = useCallback(
    async (email, password, firstName) => {
      setError(null)
      const res = await apiLogin(email, password, firstName)
      setToken(res.access_token)
      setUser(res.user)
      return res.user
    },
    [setToken],
  )

  const registerWorkspace = useCallback(
    async (payload) => {
      setError(null)
      const res = await apiRegisterWorkspace(payload)
      setToken(res.access_token)
      const me = await fetchAuthMe()
      setUser(me)
      return me
    },
    [setToken],
  )

  const logout = useCallback(() => {
    setToken(null)
    setUser(null)
    setError(null)
  }, [setToken])

  const value = useMemo(
    () => ({
      token,
      user,
      loading,
      error,
      login,
      registerWorkspace,
      logout,
      refreshUser,
      isAuthenticated: Boolean(token && user),
    }),
    [token, user, loading, error, login, registerWorkspace, logout, refreshUser],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) {
    throw new Error('useAuth debe usarse dentro de AuthProvider')
  }
  return ctx
}

export { getStoredToken } from '../utils/authStorage.js'
