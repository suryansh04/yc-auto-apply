import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    // shadcn/ui generates imports rooted at `@/`, so the alias is part of
    // the component contract, not a convenience.
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  server: {
    proxy: {
      // Proxy target uses 127.0.0.1, not localhost: on Windows "localhost"
      // can resolve to ::1 first and the backend (bound to 127.0.0.1) then
      // refuses the connection.
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.js'],
    globals: false,
  },
})
