<script setup lang="ts">
import { computed } from 'vue'
import { Bar } from 'vue-chartjs'
import type { HistoriqueMois } from '../types/historique'
import { PALETTE_GENERIC, PALETTE_SOLAR, groupedKwhOptions, libelleMois } from '../utils/charts'

const props = defineProps<{ mois: HistoriqueMois[] }>()

const data = computed(() => ({
  labels: props.mois.map(libelleMois),
  datasets: [
    {
      label: 'Consommation de l\'immeuble',
      data: props.mois.map((m) => m.consommation),
      backgroundColor: PALETTE_GENERIC,
    },
    {
      label: 'Production solaire',
      data: props.mois.map((m) => m.production),
      backgroundColor: PALETTE_SOLAR,
    },
  ],
}))

const options = groupedKwhOptions()
</script>

<template>
  <div class="h-80"><Bar :data="data" :options="options" /></div>
</template>
