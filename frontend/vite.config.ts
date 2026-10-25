import path from "path"
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  base: './',
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    // No manual vendor groups on purpose: with Vite 8 / rolldown, forcing recharts or
    // leaflet into named groups hoisted them into the entry's static imports
    // (+50..120 kB gzip at startup). Automatic splitting keeps them in lazy chunks.
  },
  server: {
    host: true,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        // Keep the browser's Host: /api/ws refuses an Origin that does not
        // match Host, and Origin here is the Vite server's (never rewrite it).
        changeOrigin: false,
        ws: true,
      },
      '/docs': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/openapi.json': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
    watch: {
      usePolling: true,
    },
  },
})
