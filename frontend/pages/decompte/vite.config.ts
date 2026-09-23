import path from 'node:path'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

// frontend/ (racine du projet npm : package.json, node_modules, shared/,
// public/, .env.local) -- deux niveaux au-dessus de pages/decompte/.
const frontendRoot = path.resolve(import.meta.dirname, '../..')
// Racine du dépôt (contient static/, app.py...) -- un niveau au-dessus de
// frontend/. Dérivé explicitement plutôt qu'un `../../../static/...` en
// dur dans `outDir` : un niveau de nesting supplémentaire (ex: encore un
// sous-dossier sous pages/) ne peut plus décaler le décompte silencieusement
// vers le mauvais dossier (bug réel rencontré en ajoutant pages/, corrigé
// avant livraison -- voir CLAUDE.md).
const repoRoot = path.resolve(frontendRoot, '..')

// Cible du proxy dev : le port Flask dépend du config.yaml lancé (voir
// clé `port` dans config.yaml/config.demo.yaml -- 8082 en démo, 5000 en
// prod). Surcharger avec VITE_API_PROXY_TARGET dans frontend/.env.local
// (gitignored) plutôt que de modifier ce fichier.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, frontendRoot, '')
  const target = env.VITE_API_PROXY_TARGET || 'http://localhost:8082'
  const proxy = { '/api': target, '/health': target }
  return {
    // Racine explicite : indépendant du cwd d'où `vite`/`npm run` est
    // invoqué (le root par défaut de Vite est `process.cwd()`, pas le
    // dossier du fichier de config -- important puisque les scripts npm
    // de frontend/package.json invoquent ce fichier depuis frontend/).
    root: import.meta.dirname,
    envDir: frontendRoot,
    publicDir: path.resolve(frontendRoot, 'public'),
    resolve: {
      alias: { '@shared': path.resolve(frontendRoot, 'shared') },
    },
    plugins: [vue(), tailwindcss()],
    // Le build est servi par Flask depuis static/decompte-app/ (voir
    // app.py::decompte() -- a remplacé la page Jinja legacy le
    // 2026-09-23).
    base: '/static/decompte-app/',
    build: {
      outDir: path.resolve(repoRoot, 'static/decompte-app'),
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
