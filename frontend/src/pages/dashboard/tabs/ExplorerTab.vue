<script setup lang="ts">
/**
 * Onglet "Explorer" : graph ligne unique sur la plage choisie, pour tous
 * les capteurs cochés dans la sidebar (état possédé par DashboardPage.vue,
 * voir composables/useExplorerSelection -- la sidebar est un frère de cet
 * onglet, pas un enfant). Port de tabs/explorer-tab.js.
 */

import { computed, ref, watch } from 'vue'
import { Line } from 'vue-chartjs'
import { fetchSeriesData } from '@shared/api/series'
import { fmtRangeTs } from '@shared/format'
import { PALETTE_ROTATING, baseLineOptions } from '@shared/charts'
import type { RangeKey } from '@shared/ranges'
import RangeButtons from '../components/RangeButtons.vue'

const props = defineProps<{ selected: Map<string, string> }>()
const emit = defineEmits<{ clear: [] }>()

const range = ref<RangeKey>('24h')
const chartData = ref<{ labels: string[]; datasets: Record<string, unknown>[] }>({ labels: [], datasets: [] })

async function refreshChart() {
  if (props.selected.size === 0) {
    chartData.value = { labels: [], datasets: [] }
    return
  }
  const datasets: Record<string, unknown>[] = []
  let labels: string[] | null = null
  let colorIdx = 0

  for (const [seriesId, label] of props.selected.entries()) {
    let data
    try {
      data = await fetchSeriesData(seriesId, range.value)
    } catch (err) {
      console.error(err)
      continue
    }
    const pointLabels = data.points.map((p) => fmtRangeTs(p.ts, range.value))
    if (!labels) labels = pointLabels

    const color = PALETTE_ROTATING[colorIdx % PALETTE_ROTATING.length]
    colorIdx += 1
    datasets.push({
      label,
      data: data.points.map((p) => p.value),
      borderColor: color,
      backgroundColor: color,
      pointRadius: 0,
      borderWidth: 2,
      tension: 0.15,
      spanGaps: true,
    })
  }

  chartData.value = { labels: labels || [], datasets }
}

// `selected` est un Map réactif (reactive()) mutable en place par
// DashboardPage.vue -- un watch profond est nécessaire pour détecter un
// set()/delete() dessus (une simple dépendance sur la référence ne
// changerait jamais, le Map lui-même n'est jamais réassigné).
watch(() => props.selected, refreshChart, { deep: true })
watch(range, refreshChart)

const chartOptions = baseLineOptions(false)
const hasSelection = computed(() => props.selected.size > 0)
</script>

<template>
  <div>
    <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
      <RangeButtons v-model="range" />
      <button
        type="button"
        class="rounded border border-neutral-300 px-3 py-1 text-sm hover:bg-neutral-50"
        @click="emit('clear')"
      >Tout désélectionner</button>
    </div>

    <div v-if="hasSelection" class="h-96 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
      <Line :data="chartData as never" :options="chartOptions" />
    </div>
    <p v-else class="text-sm text-neutral-500">
      Coche un ou plusieurs capteurs à gauche pour afficher leur historique.
    </p>
  </div>
</template>
