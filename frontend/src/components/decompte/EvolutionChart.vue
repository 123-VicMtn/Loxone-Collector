<script setup lang="ts">
import { computed } from 'vue'
import { Bar } from 'vue-chartjs'
import type { DecomptePayload, Period, ZonePeriod } from '../../types/decompte'
import { PALETTE_GRID, PALETTE_SOLAR, stackedKwhOptions } from '../../utils/charts'

const props = defineProps<{ payload: DecomptePayload; periodes: Period[] }>()

function sumZones(periodKey: string, pick: (e: ZonePeriod) => number | null): number | null {
  let sum = 0
  for (const z of props.payload.zones) {
    const e = z.periodes[periodKey]
    const v = e ? pick(e) : null
    if (v === null || v === undefined) return null
    sum += v
  }
  return sum
}

const data = computed(() => ({
  labels: props.periodes.map((p) => p.label_court),
  datasets: [
    { label: 'Réseau', data: props.periodes.map((p) => sumZones(p.key, (e) => e.reseau.kwh)), backgroundColor: PALETTE_GRID },
    { label: 'Solaire autoconsommé', data: props.periodes.map((p) => sumZones(p.key, (e) => e.solaire.kwh)), backgroundColor: PALETTE_SOLAR },
  ],
}))

const options = stackedKwhOptions()
</script>

<template>
  <div class="h-80"><Bar :data="data" :options="options" /></div>
</template>
