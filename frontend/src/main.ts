import { createApp } from 'vue'
import { Chart, registerables } from 'chart.js'
import './style.css'
import App from './App.vue'
import router from './router'

// Un seul enregistrement pour toute l'app (decompte + dashboard utilisent
// Chart.js, admin non -- l'enregistrer partout ne coûte rien et évite un
// oubli si un futur composant en a besoin).
Chart.register(...registerables)

createApp(App).use(router).mount('#app')
