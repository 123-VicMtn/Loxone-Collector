import { createRouter, createWebHistory } from 'vue-router'
import DashboardPage from './pages/dashboard/views/DashboardPage.vue'
import AdminPage from './pages/admin/views/AdminPage.vue'
import DecomptePage from './pages/decompte/views/DecomptePage.vue'
import LoginPage from './pages/auth/LoginPage.vue'
import { authState, checkAuth } from '@shared/auth'
import { setUnauthorizedHandler } from '@shared/api/http'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: DashboardPage },
    { path: '/admin', component: AdminPage },
    { path: '/decompte', component: DecomptePage },
    { path: '/login', component: LoginPage },
  ],
})

/** Garde de route : vérifie la session une seule fois par chargement de
 * page (authState.checked), pas à chaque navigation -- les navigations
 * suivantes ne font que lire l'état déjà connu. Toute route protégée sans
 * session connue redirige vers /login en mémorisant la destination
 * (`redirect`, lu par LoginPage.vue pour y revenir après connexion). */
router.beforeEach(async (to) => {
  if (to.path === '/login') return true
  if (!authState.checked) await checkAuth()
  if (!authState.username) return { path: '/login', query: { redirect: to.fullPath } }
  return true
})

/** Session expirée EN COURS DE NAVIGATION (cookie qui expire pendant que
 * l'utilisateur est déjà sur une page) -- voir shared/api/http.ts pour ce
 * qui déclenche ce handler (un 401 sur un appel API normal, hors /api/me
 * et /api/login qui gèrent leur 401 eux-mêmes). */
setUnauthorizedHandler(() => {
  authState.username = null
  router.push({ path: '/login', query: { redirect: router.currentRoute.value.fullPath } })
})

export default router
