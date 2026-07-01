import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/airports': 'http://localhost:8000',
      '/routes': 'http://localhost:8000',
      '/tails': 'http://localhost:8000',
      '/tail': 'http://localhost:8000',
      '/state': 'http://localhost:8000',
      '/disrupt': 'http://localhost:8000',
    }
  }
})