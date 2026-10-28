<script setup lang="ts">
/**
 * Aperçu d'un immeuble depuis minuit, heure de Zurich : tuiles (index
 * cumulatif, même méthode que /decompte) et courbe de puissance.
 * La courbe est lue sur le Miniserver (Statistics), pas dans les mesures
 * du collecteur : elle s'affiche même si le poller est arrêté.
 */

import { computed, onMounted, ref, watch } from 'vue'
import { useSiteActif } from '@shared/composables/useSiteActif'
import { Line } from 'vue-chartjs'
import type { ChartOptions } from 'chart.js'
import { fetchMiniserverPower, fetchRange, loadAllSeries, type MiniserverPower } from '@shared/api/series'
import type { ReadingDelta, Series } from '@shared/types/series'
import { PALETTE_GRID, PALETTE_SOLAR } from '@shared/charts'
import { fmtNumber } from '@shared/format'
import { todayBounds } from '@shared/periods'
import {
  actualsOf,
  balanceFromKwh,
  buildingConsumerSeries,
  kwhCovering,
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
const { site } = useSiteActif()
const balance = ref<DayBalance | null>(null)
const loading = ref(true)
/** Texte montré à l'utilisateur ; le détail technique part dans la console. */
const erreur = ref('')
const powerLoading = ref(false)
const powerError = ref('')
const powerMissing = ref(0)

const power = ref<{ ts: number; solar: number; consumption: number }[]>([])
const axisFrom = ref(0)
const axisTo = ref(0)
let refreshToken = 0

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

async function coveredKwh(delta: Promise<ReadingDelta | null>, from: number, to: number): Promise<number | null> {
  return kwhCovering(await delta, from, to)
}

/** Somme des index, ou null si un seul compteur ne couvre pas la fenêtre.
 * Un relevé manquant ne se remplace pas par zéro : le total serait trop bas. */
async function sumCovered(list: Series[], from: number, to: number): Promise<number | null> {
  if (!list.length) return null
  const values = await Promise.all(list.map((s) => coveredKwh(fetchRange(s.series_id, from, to), from, to)))
  const numbers = values.filter((v): v is number => v !== null)
  if (numbers.length !== values.length) return null
  return numbers.reduce((acc, v) => acc + v, 0)
}

async function refresh() {
  if (!site.value) return
  const token = ++refreshToken
  loading.value = true
  // Un compteur retiré de Loxone n'a plus de relevé aujourd'hui : il
  // rendrait la somme « incomplète » à tort.
  const series = allSeries.value.filter((s) => s.miniserver === site.value && !s.hors_decompte && !s.retiree)
  const [from, to] = todayBounds()
  axisFrom.value = from
  axisTo.value = to
  const productionSeries = pickProduction(series)
  const solarActual = actualsOf(series, productionSeries ? [productionSeries] : [])
  const consumerActual = actualsOf(series, buildingConsumerSeries(series))
  const seriesIds = [...solarActual, ...consumerActual].map((s) => s.series_id)
  powerLoading.value = true
  powerError.value = ''
  powerMissing.value = 0
  power.value = []
  const emptyPower: MiniserverPower = { points: {}, missing: [] }
  const powerPromise: Promise<MiniserverPower> = seriesIds.length
    ? fetchMiniserverPower(site.value, seriesIds, from, to)
    : Promise.resolve(emptyPower)

  const production = productionSeries
    ? await coveredKwh(fetchRange(productionSeries.series_id, from, to), from, to)
    : null

  let solarOnSite: number | null
  let consumption: number | null
  if (usesZoneSplit(series)) {
    solarOnSite = await sumCovered(zoneSolarSeries(series), from, to)
    const grid = await sumCovered(zoneGridSeries(series), from, to)
    consumption = solarOnSite !== null && grid !== null ? solarOnSite + grid : null
  } else {
    const exp = pickExport(series)
    const exported = exp
      ? await coveredKwh(fetchRange(exp.series_id, from, to), from, to)
      : null
    solarOnSite = production !== null && exported !== null ? production - exported : null
    consumption = await sumCovered(lotConsumptionSeries(series), from, to)
  }

  if (token !== refreshToken) return
  balance.value = balanceFromKwh(production, solarOnSite, consumption)
  loading.value = false

  try {
    const live = await powerPromise
    if (token !== refreshToken) return
    powerMissing.value = live.missing.length
    power.value = stackPower(
      solarActual.map((s) => live.points[s.series_id] || []),
      consumerActual.map((s) => live.points[s.series_id] || []),
    )
  } catch (err) {
    console.error(err)
    powerError.value = 'Le Miniserver n’a pas renvoyé la puissance.'
    power.value = []
  } finally {
    if (token === refreshToken) powerLoading.value = false
  }
}

onMounted(async () => {
  try {
    allSeries.value = await loadAllSeries()
  } catch (err) {
    // Sans ce catch, la promesse rejetée n'était attrapée par personne
    // (« Unhandled error during execution of mounted hook ») et l'aperçu
    // restait sur « Calcul depuis minuit… ».
    console.error(err)
    erreur.value = 'Les compteurs n\'ont pas pu être chargés. Réessayez dans quelques minutes.'
    loading.value = false
    return
  }
  if (site.value) void refresh()
  else loading.value = false
})

watch(site, (name) => {
  if (name && allSeries.value.length) void refresh()
})
</script>

<template>
  <div>
    <p v-if="loading" class="text-sm text-neutral-500">Calcul depuis minuit…</p>
    <p v-else-if="erreur" class="text-sm text-red-700">{{ erreur }}</p>

    <div v-else-if="balance" class="flex flex-col gap-4">
      <div class="grid w-full grid-cols-2 content-start gap-3 sm:grid-cols-4">
        <p class="col-span-2 text-sm font-medium text-neutral-700 sm:col-span-4">Depuis minuit</p>
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
        <h2 class="mb-1 text-sm font-semibold text-neutral-700">Puissance depuis minuit</h2>
        <p class="mb-2 text-sm text-neutral-500">Enregistrée par le Miniserver.</p>
        <p v-if="powerLoading" class="text-sm text-neutral-500">Lecture de la puissance sur le Miniserver…</p>
        <p v-else-if="powerError" class="text-sm text-red-700">{{ powerError }}</p>
        <div v-else class="h-[32rem] w-full">
          <Line v-if="chartData && power.length" :data="chartData as never" :options="areaOptions" />
          <p v-else class="text-sm text-neutral-500">Pas de puissance enregistrée sur le Miniserver depuis minuit.</p>
        </div>
        <p v-if="powerMissing" class="mt-2 text-sm text-neutral-500">
          {{ powerMissing }} compteur(s) sans historique Statistics sur le Miniserver.
        </p>
      </div>
    </div>
  </div>
</template>
