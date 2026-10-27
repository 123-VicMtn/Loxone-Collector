<script setup lang="ts">
import { computed } from 'vue'
import type { DecomptePayload } from '../types/decompte'

const props = defineProps<{ payload: DecomptePayload; periodKey: string }>()

/** Le bandeau ne parle que du mois affiché. Un autre mois incomplet
 * (compteur pas encore posé, trou) ne s'affiche pas tant qu'on ne l'a pas
 * choisi dans le calendrier. */
const moisIncomplet = computed(() => {
  const p = props.payload.periodes.find((x) => x.key === props.periodKey)
  if (!p) return null
  const bat = props.payload.batiment.periodes[p.key]
  if (!bat || bat.en_cours) return null
  const manque = props.payload.zones.some(
    (z) => z.periodes[p.key] && !z.periodes[p.key].facturable,
  )
  return manque ? p : null
})
</script>

<template>
  <div v-if="payload.statistiques_erreur" class="mb-3 rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
    <span class="font-semibold">Relevés pas à jour.</span> {{ payload.statistiques_erreur }}
  </div>
  <div v-if="moisIncomplet" class="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
    Des relevés manquent sur {{ moisIncomplet.label }}.
    Le décompte de ce mois peut quand même être établi : les totaux utilisent les
    relevés disponibles, et le détail des manques s'affiche sous le tableau.
  </div>
</template>
