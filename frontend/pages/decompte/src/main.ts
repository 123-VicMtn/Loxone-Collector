import { createApp } from 'vue'
import { Chart, registerables } from 'chart.js'
import './style.css'
import App from './App.vue'

// Équivalent du bundle UMD vendored (vendor/chart.umd.min.js) utilisé par
// static/js/decompte/charts.js : tout enregistrer une fois, au démarrage.
Chart.register(...registerables)

createApp(App).mount('#app')
