import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const proxyTarget = (env.VITE_PROXY_TARGET || env.VITE_API_URL || 'http://127.0.0.1:8002').replace(/\/+$/, '')

  return {
    appType: 'spa',
    plugins: [react(), tailwindcss()],
    server: {
      host: '0.0.0.0',
      port: 5174,
      strictPort: false,
      proxy: {
        '^/(auth|support|health|notifications)(/|$|\\?)': {
          target: proxyTarget,
          changeOrigin: true,
        },
      },
    },
    preview: {
      port: 4174,
      strictPort: false,
    },
  }
})
