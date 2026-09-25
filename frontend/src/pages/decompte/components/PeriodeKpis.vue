<script setup lang="ts">
import { computed } from 'vue'
import type { DecomptePayload } from '../types/decompte'
import { fmtKwh, fmtPct } from '../utils/format'
import KpiTile from './KpiTile.vue'

const props = defineProps<{ payload: DecomptePayload; periodKey: string }>()

/** Somme des valeurs connues. Une zone sans relevé est omise, pas mise à
 * zéro : le message de la page liste ces relevés, et le total reste
 * calculable. Null seulement si aucune zone n'a de valeur. */
function sumZones(pick: (e: DecomptePayload['zones'][number]['periodes'][string]) => number | null): number | null {
  let sum = 0
  let seen = false
  for (const z of props.payload.zones) {
    const e = z.periodes[props.periodKey]
    const v = e ? pick(e) : null
    if (v === null || v === undefined) continue
    sum += v
    seen = true
  }
  return seen ? sum : null
}

const reseau = computed(() => sumZones((e) => e.reseau.kwh))
const solaire = computed(() => sumZones((e) => e.solaire.kwh))
const total = computed(() => sumZones((e) => e.total))
const autoprod = computed(() => (total.value ? (solaire.value! / total.value) * 100 : null))

const complet = computed(() => {
  return props.payload.zones.every((z) => {
    const e = z.periodes[props.periodKey]
    return !e || (e.reseau.kwh !== null && e.solaire.kwh !== null)
  })
})
</script>

<template>
  <div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
    <KpiTile label="Consommation totale" :value="fmtKwh(total)" unit="kWh" />
    <KpiTile label="Acheté au réseau" :value="fmtKwh(reseau)" unit="kWh" accent="grid" />
    <KpiTile label="Solaire autoconsommé" :value="fmtKwh(solaire)" unit="kWh" accent="solar" />
    <KpiTile label="Taux d'autoproduction" :value="autoprod === null ? '—' : fmtPct(autoprod, 0, false)" accent="auto" />
  </div>

  <p v-if="complet" class="mt-4 text-sm text-neutral-500">
    Le taux d'autoproduction est la part de la consommation couverte par le solaire de l'immeuble.
  </p>
  <p v-else class="mt-4 text-sm text-neutral-500">
    Totaux calculés sur les relevés disponibles. Les relevés manquants sont listés sous le tableau.
  </p>
</template>
