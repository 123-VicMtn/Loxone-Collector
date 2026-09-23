<script setup lang="ts">
import { computed } from 'vue'
import { Bar } from 'vue-chartjs'
import type { BatimentPeriod, DecomptePayload, Period } from '../../types/decompte'
import { PALETTE_GENERIC, PALETTE_SOLAR, stackedKwhOptions } from '../../utils/charts'

const props = defineProps<{ payload: DecomptePayload; periodes: Period[] }>()

function val(pick: (b: BatimentPeriod) => number | null) {
  return props.periodes.map((p) => pick(props.payload.batiment.periodes[p.key]))
}

const data = computed(() => ({
  labels: props.periodes.map((p) => p.label_court),
  datasets: [
    { label: 'Autoconsommé sur place', data: val((b) => b.autoconsommation), backgroundColor: PALETTE_SOLAR },
    { label: 'Injecté au réseau', data: val((b) => b.injection), backgroundColor: PALETTE_GENERIC },
  ],
}))

const options = stackedKwhOptions()
</script>

<template>
  <div class="h-80"><Bar :data="data" :options="options" /></div>
</template>
