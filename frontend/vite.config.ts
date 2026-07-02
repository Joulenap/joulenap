import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Source lives in frontend/; production build lands in frontend/dist, which the
// FastAPI backend serves as static files. In dev, the Vite server proxies the API
// to the running backend so the SPA and API share an origin.
export default defineConfig({
  plugins: [react()],
  build: { outDir: 'dist', emptyOutDir: true },
  server: {
    port: 5173,
    proxy: { '/api': 'http://localhost:8080' },
  },
})
