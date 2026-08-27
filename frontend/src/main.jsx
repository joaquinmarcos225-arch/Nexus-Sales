import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import { applyDocumentLocale } from './utils/locale.js'
import { registerSalesServiceWorker } from './utils/pwaNotifications.js'

applyDocumentLocale()
void registerSalesServiceWorker()

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
