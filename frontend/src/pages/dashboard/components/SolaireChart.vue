<script setup lang="ts">
import { computed } from 'vue'
import { Bar } from 'vue-chartjs'
import type { HistoriqueMois } from '../types/historique'
import { PALETTE_GENERIC, PALETTE_SOLAR, libelleMois, stackedKwhOptions } from '../utils/charts'

const props = defineProps<{ mois: HistoriqueMois[] }>()

const data = computed(() => ({
  labels: props.mois.map(libelleMois),
  datasets: [
    { label: 'Autoconsommé sur place', data: props.mois.map((m) => m.autoconsommation), backgroundColor: PALETTE_SOLAR },
    { label: 'Injecté au réseau', data: props.mois.map((m) => m.injection), backgroundColor: PALETTE_GENERIC },
  ],
}))

const options = stackedKwhOptions()
</script>

<template>
  <div class="h-80"><Bar :data="data" :options="options" /></div>
</template>
