import { createRouter, createWebHistory } from 'vue-router'
import DashboardPage from './pages/dashboard/views/DashboardPage.vue'
import AdminPage from './pages/admin/views/AdminPage.vue'
import DecomptePage from './pages/decompte/views/DecomptePage.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: DashboardPage },
    { path: '/admin', component: AdminPage },
    { path: '/decompte', component: DecomptePage },
  ],
})
