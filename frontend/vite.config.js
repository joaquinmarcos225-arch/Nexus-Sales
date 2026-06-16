import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

function normalizeProxyTarget(raw) {
  const fallback = 'http://127.0.0.1:8002'
  if (raw == null || String(raw).trim() === '') return fallback
  let s = String(raw).trim().replace(/^\uFEFF/, '')
  while (
    (s.startsWith('"') && s.endsWith('"')) ||
    (s.startsWith("'") && s.endsWith("'"))
  ) {
    s = s.slice(1, -1).trim()
  }
  while (s.endsWith('"') || s.endsWith("'")) {
    s = s.slice(0, -1).trim()
  }
  s = s.replace(/\/+$/, '')
  return s || fallback
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const proxyTarget = normalizeProxyTarget(env.VITE_PROXY_TARGET || env.VITE_API_URL)

  return {
    /** SPA: todas las rutas deben responder con index.html (dev + preview lo manejan con appType). */
    appType: 'spa',
    plugins: [react(), tailwindcss()],
    server: {
      port: 5173,
      strictPort: false,
      open: false,
      proxy: {
        '^/(companies|campaigns|prospects|products|analytics|health|users|gmail|google-calendar|assistant|auth|lead-sourcing)(/|$|\\?)':
          {
            target: proxyTarget,
            changeOrigin: true,
          },
      },
    },
    preview: {
      port: 4173,
      strictPort: false,
    },
  }
})
