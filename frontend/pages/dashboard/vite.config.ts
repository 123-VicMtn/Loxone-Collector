import path from 'node:path'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

// frontend/ (racine du projet npm : package.json, node_modules, shared/,
// public/, .env.local) -- deux niveaux au-dessus de pages/dashboard/.
const frontendRoot = path.resolve(import.meta.dirname, '../..')
// Racine du dépôt (contient static/, app.py...) -- voir pages/decompte/vite.config.ts
// pour le bug d'un `outDir` relatif en dur que ce calcul explicite évite.
const repoRoot = path.resolve(frontendRoot, '..')

// Cible du proxy dev : voir pages/decompte/vite.config.ts.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, frontendRoot, '')
  const target = env.VITE_API_PROXY_TARGET || 'http://localhost:8082'
  const proxy = { '/api': target, '/health': target }
  return {
    root: import.meta.dirname,
    envDir: frontendRoot,
    publicDir: path.resolve(frontendRoot, 'public'),
    resolve: {
      alias: { '@shared': path.resolve(frontendRoot, 'shared') },
    },
    plugins: [vue(), tailwindcss()],
    base: '/static/dashboard-app/',
    build: {
      outDir: path.resolve(repoRoot, 'static/dashboard-app'),
      emptyOutDir: true,
      rollupOptions: {
        output: {
          entryFileNames: 'app.js',
          chunkFileNames: 'app-[name].js',
          assetFileNames: 'app[extname]',
        },
      },
    },
    server: { proxy },
    preview: { proxy },
  }
})
