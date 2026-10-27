/**
 * État de la collecte des sites du compte (GET /api/collecte), rafraîchi
 * toutes les 30 s : le bandeau des sites qui ne remontent plus
 * (CollecteBanner) et la page Communication.
 *
 * Remplace useHealthFooter, qui lisait /health : public, il donnait à tout
 * compte les noms et les erreurs de tous les sites, pas seulement les siens.
 */

import { computed, onMounted, onUnmounted, ref } from 'vue'
import { fetchCollecte, type Collecte } from '../api/collecte'

const REFRESH_MS = 30000

export function useCollecte() {
  const collecte = ref<Collecte | null>(null)
  const indisponible = ref(false)
  let timer: ReturnType<typeof setInterval> | undefined

  async function refresh() {
    try {
      collecte.value = await fetchCollecte()
      indisponible.value = false
    } catch {
      indisponible.value = true
    }
  }

  const enRetard = computed(() => collecte.value?.sites.filter((s) => s.en_retard) ?? [])

  onMounted(() => {
    refresh()
    timer = setInterval(refresh, REFRESH_MS)
  })
  onUnmounted(() => {
    if (timer) clearInterval(timer)
  })

  return { collecte, indisponible, enRetard }
}
