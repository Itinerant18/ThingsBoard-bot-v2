import react from '@vitejs/plugin-react'
import { dirname, resolve } from 'path'
import { fileURLToPath } from 'url'
import { defineConfig } from 'vite'

const __dirname = dirname(fileURLToPath(import.meta.url))

export default defineConfig({
  plugins: [react()],

  // FastAPI serves this build at app.mount("/ui", ...), so asset URLs must be
  // /ui/assets/... . Vite's default of "/" emits /assets/... which 404s under the
  // mount — the page loads blank with no error in the server log.
  base: '/ui/',

  build: {
    // The Java app served the bundle from src/main/resources/static; here the
    // Dockerfile copies dist/ into the image instead.
    outDir: resolve(__dirname, 'dist'),
    emptyOutDir: true,
  },

  server: {
    proxy: {
      // `npm run dev` alone cannot answer /api — proxy to a locally running app
      // (uvicorn defaults to 8083 in this project).
      '/api': {
        target: 'http://localhost:8083',
        changeOrigin: true,
        // Don't buffer/compress SSE responses — forward chunks as they arrive.
        configure: (proxy) => {
          proxy.on('proxyReq', (proxyReq) => {
            proxyReq.setHeader('Accept-Encoding', 'identity')
          })
        },
      },
      '/ask': { target: 'http://localhost:8083', changeOrigin: true },
    },
  },
})
