import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '../context/AuthContext.jsx'
import { useAuthEnterTransition } from '../context/AuthEnterTransition.jsx'

export function ProtectedRoute({ children }) {
  const { isAuthenticated, loading } = useAuth()
  const { isActive } = useAuthEnterTransition()
  const location = useLocation()

  if (loading && !isActive) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-black text-sm text-zinc-400">
        Cargando sesión…
      </div>
    )
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }

  return children
}

export function GuestRoute({ children }) {
  const { isAuthenticated, loading } = useAuth()
  const { isActive } = useAuthEnterTransition()
  const location = useLocation()

  if (loading && !isActive) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-black text-sm text-zinc-400">
        Cargando…
      </div>
    )
  }

  if (isAuthenticated && location.pathname !== '/login' && !isActive) {
    return <Navigate to="/dashboard" replace />
  }

  return children
}
