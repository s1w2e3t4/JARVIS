import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const accountProxy = {
  '/api/account': 'http://127.0.0.1:8000',
}

export default defineConfig({
  plugins: [react()],
  server: { proxy: accountProxy },
  preview: { proxy: accountProxy },
})
