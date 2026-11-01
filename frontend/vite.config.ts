import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

// Dev-прокси: браузер ходит на свой же порт, а Vite пересылает /api на backend.
// Порты меняются переменными FRONT_PORT / BACKEND_PORT (их выставляет scripts/run.sh),
// полный адрес backend можно задать через VITE_API_TARGET.
declare const process: { env: Record<string, string | undefined> } // Node-переменные без установки @types/node

const apiTarget = process.env.VITE_API_TARGET ?? `http://127.0.0.1:${process.env.BACKEND_PORT ?? 8000}`

export default defineConfig({
  plugins: [svelte()],
  server: {
    port: Number(process.env.FRONT_PORT ?? 5173),
    proxy: { '/api': { target: apiTarget, changeOrigin: true } },
  },
})
