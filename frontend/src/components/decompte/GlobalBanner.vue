<script setup lang="ts">
import { computed } from 'vue'
import type { DecomptePayload } from '../../types/decompte'

const props = defineProps<{ payload: DecomptePayload }>()

/** Combien de mois terminés sont facturables. Un mois ne l'est pas quand il
 * manque une donnée (compteur pas encore posé, trou de collecte) -- ce
 * n'est pas un problème d'installation. */
const info = computed(() => {
  const termines = props.payload.periodes.filter((p) => !props.payload.batiment.periodes[p.key].en_cours)
  const incomplets = termines.filter((p) =>
    props.payload.zones.some((z) => z.periodes[p.key] && !z.periodes[p.key].facturable))
  return { termines, incomplets }
})
</script>

<template>
  <div v-if="info.incomplets.length" class="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
    <strong>{{ info.termines.length - info.incomplets.length }} mois facturables sur {{ info.termines.length }} mois terminés.</strong>
    Données insuffisantes sur : {{ info.incomplets.map((p) => p.label).join(', ') }}.
    Le détail par zone est dans la colonne « État » du tableau — il s'agit de mois
    antérieurs à la pose des compteurs, ou de trous de collecte, pas d'une anomalie
    de comptage.
  </div>
</template>
