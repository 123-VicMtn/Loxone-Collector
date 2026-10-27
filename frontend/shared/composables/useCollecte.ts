/**
 * État de la collecte des sites du compte (GET /api/collecte), rafraîchi
 * toutes les 30 s : le pied de page (dernier relevé de chaque site) et le
 * bandeau des sites qui ne remontent plus (CollecteBanner).
 *
 * Remplace useHealthFooter, qui lisait /health : public, il donnait à tout
 * compte les noms et les erreurs de tous les sites, pas seulement les siens.
 */

import { computed, onMounted, onUnmounted, ref } from 'vue'
import { fetchCollecte, type Collecte } from '../api/collecte'
import { fmtDateTimeShort } from '../format'

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

  const text = computed(() => {
    if (indisponible.value) return 'Statut indisponible.'
    if (!collecte.value) return ''
    return collecte.value.sites.map((s) => {
      const when = s.dernier_releve_ts ? fmtDateTimeShort(s.dernier_releve_ts) : 'jamais'
      return `${s.miniserver} : dernier relevé ${when}`
    }).join('\n')
  })

  onMounted(() => {
    refresh()
    timer = setInterval(refresh, REFRESH_MS)
  })
  onUnmounted(() => {
    if (timer) clearInterval(timer)
  })

  return { collecte, indisponible, enRetard, text }
}
