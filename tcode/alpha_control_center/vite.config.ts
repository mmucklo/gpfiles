import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { existsSync } from 'fs'
import { fileURLToPath } from 'url'
import { dirname, join } from 'path'

const __dirname = dirname(fileURLToPath(import.meta.url));
const rechartsInstalled = existsSync(join(__dirname, 'node_modules', 'recharts'));

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3001,
    host: true
  },
  build: {
    rollupOptions: {
      // Externalize recharts if not yet installed (fresh clone before npm install).
      // Once `npm install` runs, recharts will be bundled normally.
      external: rechartsInstalled ? [] : ['recharts'],
    },
  },
})
