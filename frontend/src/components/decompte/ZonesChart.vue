<script setup lang="ts">
import { computed } from 'vue'
import { Bar } from 'vue-chartjs'
import type { DecomptePayload } from '../../types/decompte'
import { PALETTE_GRID, PALETTE_SOLAR, stackedKwhOptions } from '../../utils/charts'

const props = defineProps<{ payload: DecomptePayload; periodKey: string }>()

const data = computed(() => ({
  labels: props.payload.zones.map((z) => z.label),
  datasets: [
    { label: 'Réseau', data: props.payload.zones.map((z) => z.periodes[props.periodKey]?.reseau?.kwh ?? null), backgroundColor: PALETTE_GRID },
    { label: 'Solaire autoconsommé', data: props.payload.zones.map((z) => z.periodes[props.periodKey]?.solaire?.kwh ?? null), backgroundColor: PALETTE_SOLAR },
  ],
}))

const options = stackedKwhOptions()
</script>

<template>
  <div class="h-80"><Bar :data="data" :options="options" /></div>
</template>
