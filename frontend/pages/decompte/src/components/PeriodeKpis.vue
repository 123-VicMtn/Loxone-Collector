<script setup lang="ts">
import { computed } from 'vue'
import type { DecomptePayload } from '../types/decompte'
import { fmtKwh, fmtCHF, fmtPct } from '../utils/format'
import KpiTile from './KpiTile.vue'

const props = defineProps<{ payload: DecomptePayload; periodKey: string }>()

/** Somme d'une valeur sur toutes les zones. Retourne null dès qu'une zone
 * manque : un total partiel affiché comme un total complet ferait
 * sous-estimer la facture de l'immeuble. */
function sumZones(pick: (e: DecomptePayload['zones'][number]['periodes'][string]) => number | null): number | null {
  let sum = 0
  for (const z of props.payload.zones) {
    const e = z.periodes[props.periodKey]
    const v = e ? pick(e) : null
    if (v === null || v === undefined) return null
    sum += v
  }
  return sum
}

const reseau = computed(() => sumZones((e) => e.reseau.kwh))
const solaire = computed(() => sumZones((e) => e.solaire.kwh))
const ttc = computed(() => sumZones((e) => e.montants.ttc))
const total = computed(() => (reseau.value === null || solaire.value === null ? null : reseau.value + solaire.value))
const autoprod = computed(() => (total.value ? (solaire.value! / total.value) * 100 : null))

const problemes = computed(() => {
  const out: string[] = []
  for (const z of props.payload.zones) {
    const e = z.periodes[props.periodKey]
    if (e && !e.facturable) out.push(`${z.label} : ${e.alertes[0] || 'données incomplètes'}`)
  }
  return out
})
</script>

<template>
  <div class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
    <KpiTile label="Consommation totale" :value="fmtKwh(total)" unit="kWh" />
    <KpiTile label="Acheté au réseau" :value="fmtKwh(reseau)" unit="kWh" accent="grid" />
    <KpiTile label="Solaire autoconsommé" :value="fmtKwh(solaire)" unit="kWh" accent="solar" />
    <KpiTile label="Taux d'autoproduction" :value="autoprod === null ? '—' : fmtPct(autoprod, 0, false)" accent="auto" />
    <KpiTile label="Montant TTC" :value="ttc === null ? '—' : fmtCHF(ttc)" />
  </div>

  <div v-if="problemes.length" class="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm">
    <p class="font-semibold text-amber-900">{{ problemes.length }} zone(s) non facturable(s) sur ce mois :</p>
    <ul class="mt-1 list-disc pl-5 text-amber-800">
      <li v-for="p in problemes" :key="p">{{ p }}</li>
    </ul>
  </div>
  <p v-else class="mt-4 text-sm text-neutral-500">
    Toutes les zones sont facturables sur ce mois. Le taux d'autoproduction est la part
    de la consommation couverte par le solaire de l'immeuble.
  </p>
</template>
