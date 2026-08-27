import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { ProtectedRoute, GuestRoute } from './components/ProtectedRoute.jsx'
import { AuthProvider } from './context/AuthContext.jsx'
import { AuthEnterTransitionProvider } from './context/AuthEnterTransition.jsx'
import { CompanyProvider } from './context/CompanyContext.jsx'
import { DashboardAnalyticsProvider } from './context/DashboardAnalyticsContext.jsx'
import { CompanyConfigGate } from './components/CompanyConfigGate.jsx'
import { AppShell } from './layout/AppShell'
import CampanaDetallePage from './pages/CampanaDetallePage.jsx'
import CampanasPage from './pages/CampanasPage.jsx'
import IntegracionesPage from './pages/IntegracionesPage.jsx'
import { ConfiguracionLayout } from './layout/ConfiguracionLayout.jsx'
import CajaCreditosPage from './pages/CajaCreditosPage.jsx'
import EquipoPage from './pages/EquipoPage.jsx'
import LoginPage from './pages/LoginPage.jsx'
import ForgotPasswordPage from './pages/ForgotPasswordPage.jsx'
import RegisterWorkspacePage from './pages/RegisterWorkspacePage.jsx'
import ExtensionPrivacyPage from './pages/ExtensionPrivacyPage.jsx'
import PrivacyPage from './pages/PrivacyPage.jsx'
import ProductHomePage from './pages/ProductHomePage.jsx'
import MiPerfilPage from './pages/MiPerfilPage.jsx'
import SoportePage from './pages/SoportePage.jsx'
import ProductosPage from './pages/ProductosPage.jsx'
import DashboardLayout from './pages/dashboard/DashboardLayout.jsx'
import DashboardOverview from './pages/dashboard/DashboardOverview.jsx'
import DashboardSectionGoLive from './pages/dashboard/DashboardSectionGoLive.jsx'
import { TutorialProvider } from './context/TutorialContext.jsx'
export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AuthEnterTransitionProvider>
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
            path="/recuperar-contrasena"
            element={
              <GuestRoute>
                <ForgotPasswordPage />
              </GuestRoute>
            }
          />
          <Route
            path="/registro"
            element={
              <GuestRoute>
                <RegisterWorkspacePage />
              </GuestRoute>
            }
          />
          <Route path="/inicio" element={<ProductHomePage />} />
          <Route path="/privacidad" element={<PrivacyPage />} />
          <Route path="/privacy" element={<PrivacyPage />} />
          <Route path="/privacidad-extension" element={<ExtensionPrivacyPage />} />
          <Route
            path="/"
            element={
              <ProtectedRoute>
                <CompanyProvider>
                  <DashboardAnalyticsProvider>
                    <TutorialProvider>
                      <AppShell />
                    </TutorialProvider>
                  </DashboardAnalyticsProvider>
                </CompanyProvider>
              </ProtectedRoute>
            }
          >
            <Route index element={<Navigate to="/dashboard" replace />} />
            <Route path="dashboard" element={<DashboardLayout />}>
              <Route index element={<DashboardOverview />} />
              <Route path="go-live" element={<DashboardSectionGoLive />} />
              <Route path="sourcing" element={<Navigate to="/dashboard" replace />} />
              <Route path="prospectos" element={<Navigate to="/campanas" replace />} />
              <Route path="responder" element={<Navigate to="/dashboard" replace />} />
              <Route path="outreach" element={<Navigate to="/dashboard" replace />} />
              <Route path="reuniones" element={<Navigate to="/dashboard" replace />} />
            </Route>
            <Route path="campanas" element={<CampanasPage />} />
            <Route path="campanas/:campaignId" element={<CampanaDetallePage />} />
            <Route path="prospectos" element={<Navigate to="/campanas" replace />} />
            <Route path="equipo" element={<EquipoPage />} />
            <Route path="creditos" element={<CajaCreditosPage />} />
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
              <Route path="idioma" element={<Navigate to="/configuracion/integraciones" replace />} />
              <Route path="crm" element={<Navigate to="/configuracion/integraciones" replace />} />
            </Route>
            <Route path="conexiones" element={<Navigate to="/configuracion/integraciones" replace />} />
            <Route path="educacion-ia" element={<Navigate to="/configuracion/integraciones" replace />} />
            <Route path="asistente" element={<Navigate to="/dashboard" replace />} />
            <Route path="operaciones" element={<Navigate to="/dashboard" replace />} />
            <Route path="mi-perfil" element={<MiPerfilPage />} />
            <Route path="soporte" element={<SoportePage />} />
          </Route>
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
        </AuthEnterTransitionProvider>
      </AuthProvider>
    </BrowserRouter>
  )
}
