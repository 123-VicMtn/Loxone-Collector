/**
 * Bandeau de statut en bas de page (dernier poll de chaque miniserver).
 * Port de static/js/core/health.js : se rafraîchit tout seul toutes les 30s.
 */

import { onMounted, onUnmounted, ref } from 'vue'
import { fetchHealth } from '../api/health'

const REFRESH_MS = 30000

export function useHealthFooter() {
  const text = ref('')
  let timer: ReturnType<typeof setInterval> | undefined

  async function refresh() {
    try {
      const h = await fetchHealth()
      const lines = Object.keys(h.last_poll_ts || {}).map((name) => {
        const ts = h.last_poll_ts[name]
        const ok = h.last_poll_ok[name]
        const err = h.last_error[name]
        const when = ts ? new Date(ts * 1000).toLocaleString('fr-CH') : 'jamais'
        const count = h.series_count[name] || 0
        return `${name}: ${ok ? 'OK' : 'ERREUR'} — dernier poll ${when} — ${count} capteurs${err ? ' — ' + err : ''}`
      })
      text.value = lines.join('\n') || 'En attente du premier cycle de poll…'
    } catch {
      text.value = 'Statut indisponible.'
    }
  }

  onMounted(() => {
    refresh()
    timer = setInterval(refresh, REFRESH_MS)
  })
  onUnmounted(() => {
    if (timer) clearInterval(timer)
  })

  return { text }
}
