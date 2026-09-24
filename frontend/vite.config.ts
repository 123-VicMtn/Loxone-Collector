import path from 'node:path'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

// Backend 100% API (voir CLAUDE.md, "Backend 100% API") : ce frontend
// n'est plus servi par Flask -- il tourne comme une app autonome, sur sa
// propre origine (son propre serveur/hébergement), et parle au backend
// uniquement via /api/* et /health. Le proxy ci-dessous joue en dev le
// rôle que jouera un reverse proxy (Caddy, voir
// docs/plan-installation-auth-frontend-docker.md) en prod -- cible
// surchargeable via VITE_API_PROXY_TARGET dans frontend/.env.local
// (gitignored).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, import.meta.dirname, '')
  const target = env.VITE_API_PROXY_TARGET || 'http://localhost:5050'
  const proxy = { '/api': target, '/health': target }
  return {
    plugins: [vue(), tailwindcss()],
    resolve: {
      alias: { '@shared': path.resolve(import.meta.dirname, 'shared') },
    },
    server: { port: 5173, strictPort: true, proxy },
    preview: { port: 4173, strictPort: true, proxy },
    build: {
      outDir: 'dist',
      emptyOutDir: true,
    },
  }
})
