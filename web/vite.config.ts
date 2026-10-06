import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // The API runs on 8010 locally. Proxying rather than calling it directly
    // keeps the browser on one origin in development, so CORS only has to be
    // correct for the deployed case -- and a proxy misconfiguration fails
    // loudly here instead of becoming a CORS mystery later.
    proxy: {
      '/v1': { target: 'http://localhost:8010', changeOrigin: true },
      '/metrics': { target: 'http://localhost:8010', changeOrigin: true },
      '/readyz': { target: 'http://localhost:8010', changeOrigin: true },
    },
  },
})
