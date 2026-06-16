import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react'
import { getStoredToken, setStoredToken } from '../utils/authStorage.js'
import { fetchAuthMe, login as apiLogin } from '../utils/api.js'

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

  const refreshUser = useCallback(async () => {
    if (!token) {
      setUser(null)
      setLoading(false)
      return null
    }
    setLoading(true)
    setError(null)
    try {
      const me = await fetchAuthMe()
      setUser(me)
      return me
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setUser(null)
      setToken(null)
      return null
    } finally {
      setLoading(false)
    }
  }, [token, setToken])

  useEffect(() => {
    void refreshUser()
  }, [refreshUser])

  const login = useCallback(
    async (email, password) => {
      setError(null)
      const res = await apiLogin(email, password)
      setToken(res.access_token)
      setUser(res.user)
      return res.user
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
      logout,
      refreshUser,
      isAuthenticated: Boolean(token && user),
    }),
    [token, user, loading, error, login, logout, refreshUser],
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
