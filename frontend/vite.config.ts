import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

// Cible du proxy dev : le port Flask dépend du config.yaml lancé (voir
// clé `port` dans config.yaml/config.demo.yaml -- 8082 en démo, 5000 en
// prod). Surcharger avec VITE_API_PROXY_TARGET dans frontend/.env.local
// (gitignored) plutôt que de modifier ce fichier.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const target = env.VITE_API_PROXY_TARGET || 'http://localhost:8082'
  const proxy = { '/api': target, '/health': target }
  return {
    plugins: [vue(), tailwindcss()],
    // Le build est servi par Flask depuis static/decompte-app/ (voir
    // app.py::decompte_vue -- route de prévisualisation le temps de la
    // migration, remplacera /decompte une fois validé visuellement).
    base: '/static/decompte-app/',
    build: {
      outDir: '../static/decompte-app',
      emptyOutDir: true,
      rollupOptions: {
        // Noms de fichiers fixes plutôt qu'un manifest de hashs de cache-
        // busting : plus simple à servir depuis Flask pour un projet solo,
        // voir CLAUDE.md "Choix de stack figé". À revoir si le cache
        // navigateur pose un jour un problème réel après déploiement.
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
