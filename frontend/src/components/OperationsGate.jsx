import { Navigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext.jsx'
import { canAccessOperations } from '../data/navigation.js'

/** Centro de Operaciones — solo Manager. */
export function OperationsGate({ children }) {
  const { user, loading } = useAuth()

  if (loading) {
    return (
      <div className="flex min-h-[12rem] items-center justify-center text-sm text-nx-muted">
        Cargando operaciones…
      </div>
    )
  }

  if (!canAccessOperations(user)) {
    return <Navigate to="/dashboard" replace />
  }

  return children
}
