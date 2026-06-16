import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { ProtectedRoute, GuestRoute } from './components/ProtectedRoute.jsx'
import { AuthProvider } from './context/AuthContext.jsx'
import { CompanyProvider } from './context/CompanyContext.jsx'
import { DashboardAnalyticsProvider } from './context/DashboardAnalyticsContext.jsx'
import { CompanyConfigGate } from './components/CompanyConfigGate.jsx'
import { AppShell } from './layout/AppShell'
import CampanaDetallePage from './pages/CampanaDetallePage.jsx'
import CampanasPage from './pages/CampanasPage.jsx'
import IntegracionesPage from './pages/IntegracionesPage.jsx'
import { ConfiguracionLayout } from './layout/ConfiguracionLayout.jsx'
import EquipoPage from './pages/EquipoPage.jsx'
import LoginPage from './pages/LoginPage.jsx'
import MiPerfilPage from './pages/MiPerfilPage.jsx'
import ProductosPage from './pages/ProductosPage.jsx'
import ProspectosPage from './pages/ProspectosPage.jsx'
import DashboardLayout from './pages/dashboard/DashboardLayout.jsx'
import DashboardOverview from './pages/dashboard/DashboardOverview.jsx'
import DashboardSectionProspects from './pages/dashboard/DashboardSectionProspects.jsx'
import DashboardSectionOutreach from './pages/dashboard/DashboardSectionOutreach.jsx'
import DashboardSourcingPage from './pages/dashboard/DashboardSourcingPage.jsx'

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route
            path="/login"
            element={
              <GuestRoute>
                <LoginPage />
              </GuestRoute>
            }
          />
          <Route
            path="/"
            element={
              <ProtectedRoute>
                <CompanyProvider>
                  <DashboardAnalyticsProvider>
                    <AppShell />
                  </DashboardAnalyticsProvider>
                </CompanyProvider>
              </ProtectedRoute>
            }
          >
            <Route index element={<Navigate to="/dashboard" replace />} />
            <Route path="dashboard" element={<DashboardLayout />}>
              <Route index element={<DashboardOverview />} />
              <Route path="sourcing" element={<DashboardSourcingPage />} />
              <Route path="prospectos" element={<DashboardSectionProspects />} />
              <Route path="outreach" element={<DashboardSectionOutreach />} />
            </Route>
            <Route path="campanas" element={<CampanasPage />} />
            <Route path="campanas/:campaignId" element={<CampanaDetallePage />} />
            <Route path="prospectos" element={<ProspectosPage />} />
            <Route path="equipo" element={<EquipoPage />} />
            <Route
              path="productos"
              element={
                <CompanyConfigGate>
                  <ProductosPage />
                </CompanyConfigGate>
              }
            />
            <Route path="configuracion" element={<ConfiguracionLayout />}>
              <Route index element={<Navigate to="integraciones" replace />} />
              <Route path="integraciones" element={<IntegracionesPage />} />
            </Route>
            <Route path="conexiones" element={<Navigate to="/configuracion/integraciones" replace />} />
            <Route path="mi-perfil" element={<MiPerfilPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
