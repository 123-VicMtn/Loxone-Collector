<script setup lang="ts">
import { computed } from 'vue'
import { Line } from 'vue-chartjs'
import type { BatimentPeriod, DecomptePayload, Period } from '../types/decompte'
import { PALETTE_GENERIC, PALETTE_SOLAR, tauxChartOptions } from '../utils/charts'

const props = defineProps<{ payload: DecomptePayload; periodes: Period[] }>()

function val(pick: (b: BatimentPeriod) => number | null) {
  return props.periodes.map((p) => pick(props.payload.batiment.periodes[p.key]))
}

/** Les deux taux évoluent en sens INVERSE au fil des saisons -- les
 * superposer exprès rend le croisement saisonnier lisible plutôt que de
 * laisser lire l'un pour l'autre (voir CLAUDE.md, "Les deux taux"). */
const data = computed(() => ({
  labels: props.periodes.map((p) => p.label_court),
  datasets: [
    {
      label: 'Autoproduction (solaire ÷ consommation)',
      data: val((b) => b.taux_autoproduction),
      borderColor: PALETTE_SOLAR, backgroundColor: PALETTE_SOLAR,
      tension: 0.25, spanGaps: false,
    },
    {
      label: 'Autoconsommation (solaire ÷ production)',
      data: val((b) => b.taux_autoconsommation),
      borderColor: PALETTE_GENERIC, backgroundColor: PALETTE_GENERIC,
      borderDash: [6, 4], tension: 0.25, spanGaps: false,
    },
  ],
}))

const options = tauxChartOptions()
</script>

<template>
  <div class="h-80"><Line :data="data" :options="options" /></div>
</template>
