<script setup lang="ts">
import { computed } from 'vue'
import { Bar } from 'vue-chartjs'
import type { DecomptePayload, Period } from '../types/decompte'
import { PALETTE_GENERIC, PALETTE_SOLAR, groupedKwhOptions } from '../utils/charts'

const props = defineProps<{ payload: DecomptePayload; periodes: Period[] }>()

const data = computed(() => ({
  labels: props.periodes.map((p) => p.label_court),
  datasets: [
    {
      label: 'Consommation de l\'immeuble',
      data: props.periodes.map((p) => props.payload.batiment.periodes[p.key]?.consommation_totale ?? null),
      backgroundColor: PALETTE_GENERIC,
    },
    {
      label: 'Production solaire',
      data: props.periodes.map((p) => props.payload.batiment.periodes[p.key]?.production.kwh ?? null),
      backgroundColor: PALETTE_SOLAR,
    },
  ],
}))

const options = groupedKwhOptions()
</script>

<template>
  <div class="h-80"><Bar :data="data" :options="options" /></div>
</template>
