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
  return {
    plugins: [vue(), tailwindcss()],
    server: {
      proxy: {
        '/api': target,
        '/health': target,
      },
    },
  }
})
