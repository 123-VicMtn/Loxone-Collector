<script setup lang="ts">
/**
 * Aperçu du jour (minuit Europe/Zurich → maintenant) pour un immeuble :
 * production, consommation, autoconsommation, autonomie.
 */

import { computed, onMounted, ref, watch } from 'vue'
import { Line } from 'vue-chartjs'
import type { ChartOptions } from 'chart.js'
import { fetchRange, fetchSeriesWindow, loadAllSeries } from '@shared/api/series'
import type { Series } from '@shared/types/series'
import { PALETTE_GRID, PALETTE_SOLAR } from '@shared/charts'
import { fmtNumber } from '@shared/format'
import { todayBounds } from '@shared/periods'
import {
  actualsOf,
  balanceFromKwh,
  buildingConsumerSeries,
  lotConsumptionSeries,
  pickExport,
  pickProduction,
  stackPower,
  usesZoneSplit,
  zoneGridSeries,
  zoneSolarSeries,
  type DayBalance,
} from './apercu/dayBalance'

const allSeries = ref<Series[]>([])
const site = ref('')
const balance = ref<DayBalance | null>(null)
const loading = ref(true)

const sites = computed(() => Array.from(new Set(allSeries.value.map((s) => s.miniserver))).sort())
const power = ref<{ ts: number; solar: number; consumption: number }[]>([])
const axisFrom = ref(0)
const axisTo = ref(0)

function hourLabel(epoch: number): string {
  return new Date(epoch * 1000).toLocaleTimeString('fr-CH', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'Europe/Zurich',
  })
}

/** Graduations depuis minuit, puis toutes les heures (ou 2 h / 3 h si la
 * journée est déjà longue), pour que le premier libellé soit toujours 00:00. */
function axisTicks(from: number, to: number): number[] {
  const span = Math.max(to - from, 1)
  const step = span > 12 * 3600 ? 3 * 3600 : span > 6 * 3600 ? 2 * 3600 : 3600
  const ticks = []
  for (let t = from; t < to; t += step) ticks.push(t)
  ticks.push(to)
  return ticks
}

const areaOptions = computed<ChartOptions<'line'>>(() => ({
  responsive: true,
  maintainAspectRatio: false,
  animation: false,
  interaction: { mode: 'index', intersect: false },
  scales: {
    x: {
      type: 'linear',
      min: axisFrom.value,
      max: axisTo.value,
      afterBuildTicks(axis) {
        axis.ticks = axisTicks(axisFrom.value, axisTo.value).map((value) => ({ value }))
      },
      ticks: {
        callback: (value) => hourLabel(Number(value)),
      },
    },
    y: { beginAtZero: true, title: { display: true, text: 'kW' } },
  },
  plugins: {
    legend: { position: 'bottom' },
    tooltip: {
      callbacks: {
        title: (items) => hourLabel(Number(items[0]?.parsed.x ?? 0)),
      },
    },
  },
}))

const chartData = computed(() => {
  if (!axisTo.value) return null
  return {
    datasets: [
      {
        label: 'Consommation totale',
        data: power.value.map((p) => ({ x: p.ts, y: p.consumption })),
        borderColor: PALETTE_GRID,
        backgroundColor: 'rgba(217, 119, 6, 0.35)',
        fill: 'origin',
        pointRadius: 0,
        tension: 0.2,
      },
      {
        label: 'Solaire',
        data: power.value.map((p) => ({ x: p.ts, y: p.solar })),
        borderColor: PALETTE_SOLAR,
        backgroundColor: 'rgba(22, 163, 74, 0.45)',
        fill: 'origin',
        pointRadius: 0,
        tension: 0.2,
      },
    ],
  }
})

async function sumDeltas(list: Series[], from: number, to: number): Promise<number | null> {
  if (!list.length) return null
  const deltas = await Promise.all(list.map((s) => fetchRange(s.series_id, from, to)))
  if (deltas.some((d) => !d || d.kwh === null)) return null
  return deltas.reduce((acc, d) => acc + (d?.kwh ?? 0), 0)
}

async function refresh() {
  if (!site.value) return
  loading.value = true
  const series = allSeries.value.filter((s) => s.miniserver === site.value)
  const [from, to] = todayBounds()
  axisFrom.value = from
  axisTo.value = to
  const productionSeries = pickProduction(series)
  const production = productionSeries ? (await fetchRange(productionSeries.series_id, from, to))?.kwh ?? null : null

  let solarOnSite: number | null
  let consumption: number | null
  if (usesZoneSplit(series)) {
    solarOnSite = await sumDeltas(zoneSolarSeries(series), from, to)
    const grid = await sumDeltas(zoneGridSeries(series), from, to)
    consumption = solarOnSite !== null && grid !== null ? solarOnSite + grid : null
  } else {
    const exp = pickExport(series)
    const exported = exp ? (await fetchRange(exp.series_id, from, to))?.kwh ?? null : null
    solarOnSite = production !== null && exported !== null ? production - exported : null
    consumption = await sumDeltas(lotConsumptionSeries(series), from, to)
  }

  balance.value = balanceFromKwh(production, solarOnSite, consumption)

  const solarTotals = productionSeries ? [productionSeries] : []
  const consumers = buildingConsumerSeries(series)
  const [solarPoints, consumptionPoints] = await Promise.all([
    Promise.all(actualsOf(series, solarTotals).map((s) => fetchSeriesWindow(s.series_id, from, to))),
    Promise.all(actualsOf(series, consumers).map((s) => fetchSeriesWindow(s.series_id, from, to))),
  ])
  power.value = stackPower(solarPoints, consumptionPoints)
  loading.value = false
}

onMounted(async () => {
  allSeries.value = await loadAllSeries()
  site.value = sites.value[0] || ''
})

watch(site, () => { void refresh() })
</script>

<template>
  <div>
    <label v-if="sites.length > 1" class="mb-4 flex w-fit flex-col text-sm text-neutral-600">
      Site
      <select v-model="site" class="mt-1 rounded border border-neutral-300 px-2 py-1">
        <option v-for="s in sites" :key="s" :value="s">{{ s }}</option>
      </select>
    </label>

    <p v-if="loading" class="text-sm text-neutral-500">Calcul depuis minuit…</p>

    <div v-else-if="balance" class="flex flex-col gap-4">
      <div class="grid w-full grid-cols-2 content-start gap-3 sm:grid-cols-4">
        <div class="rounded-lg border border-green-200 bg-green-50 p-4">
          <div class="text-sm text-neutral-500">Production</div>
          <div class="mt-1 text-xl font-semibold">{{ fmtNumber(balance.production, 1) }} <span class="text-sm font-normal">kWh</span></div>
        </div>
        <div class="rounded-lg border border-amber-200 bg-amber-50 p-4">
          <div class="text-sm text-neutral-500">Consommation</div>
          <div class="mt-1 text-xl font-semibold">{{ fmtNumber(balance.consumption, 1) }} <span class="text-sm font-normal">kWh</span></div>
        </div>
        <div class="rounded-lg border border-blue-200 bg-blue-50 p-4">
          <div class="text-sm text-neutral-500">Autoconsommation</div>
          <div class="mt-1 text-xl font-semibold">{{ fmtNumber(balance.autoconsommation, 0) }} <span class="text-sm font-normal">%</span></div>
        </div>
        <div class="rounded-lg border border-neutral-200 bg-white p-4">
          <div class="text-sm text-neutral-500">Autonomie</div>
          <div class="mt-1 text-xl font-semibold">{{ fmtNumber(balance.autonomie, 0) }} <span class="text-sm font-normal">%</span></div>
        </div>
        <p v-if="balance.note" class="col-span-2 text-sm text-neutral-500 sm:col-span-4">{{ balance.note }}</p>
        <p v-else class="text-sm text-neutral-500 sm:col-span-4">
          Autoconsommation = solaire sur place / production.
          Autonomie = solaire sur place / consommation.
        </p>
      </div>

      <div class="w-full rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
        <h2 class="mb-2 text-sm font-semibold text-neutral-700">Puissance depuis minuit</h2>
        <div class="h-[32rem] w-full">
          <Line v-if="chartData" :data="chartData as never" :options="areaOptions" />
          <p v-else class="text-sm text-neutral-500">Pas de puissance relevée aujourd’hui.</p>
        </div>
      </div>
    </div>
  </div>
</template>
