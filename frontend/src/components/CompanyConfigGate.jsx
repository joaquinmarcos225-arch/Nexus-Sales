import { Navigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext.jsx'
import { canAccessCompanyConfig } from '../data/navigation.js'

/** Solo Gerente / Director (permiso company.config). SDR y Manager → dashboard. */
export function CompanyConfigGate({ children }) {
  const { user, loading } = useAuth()

  if (loading) {
    return (
      <div className="flex min-h-[12rem] items-center justify-center text-sm text-nx-muted">
        Cargando configuración…
      </div>
    )
  }

  if (!canAccessCompanyConfig(user)) {
    return <Navigate to="/dashboard" replace />
  }

  return children
}
