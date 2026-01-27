import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  base: '/',
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
  },
  server: {
    proxy: {
      '/ws': {
        target: 'ws://localhost:8045',
        ws: true,
      },
      '/api': {
        target: 'http://localhost:8045',
      },
    },
  },
})
