<script setup lang="ts">
/**
 * Onglet "Énergie" : réseau (import/export) vs solaire vs batterie, par
 * zone. Chaque groupe de tuiles jour/semaine/mois/année est calculé ici
 * (relevé de fin - relevé de début sur les séries "total"/"totalNeg", via
 * /api/series/<id>/range) -- la même méthode que /decompte, plus une
 * plage de dates personnalisable ; seuls les graphs journaliers/mensuels
 * (historique passé) utilisent en plus un delta jour-sur-jour. Voir
 * CLAUDE.md, "Refactor extraction/lecture des données dashboard"
 * (2026-09-24) pour le détail (remplace les compteurs vivants Loxone
 * totalDay/Week/Month/Year, qui s'écartaient du relevé réel de 13,5 % sur
 * un mois testé). Port de tabs/energy-tab.js.
 */

import { computed, onMounted, ref, watch } from 'vue'
import { Bar, Line } from 'vue-chartjs'
import { loadAllSeries } from '@shared/api/series'
import type { Series } from '@shared/types/series'
import { baseBarOptions, baseLineOptions } from '@shared/charts'
import type { RangeKey } from '@shared/ranges'
import type { Bounds } from '@shared/periods'
import ZoneSelect from '../components/ZoneSelect.vue'
import RangeButtons from '../components/RangeButtons.vue'
import KpiTile from '../components/KpiTile.vue'
import NoteText from '../components/NoteText.vue'
import DateRangePicker from '../components/DateRangePicker.vue'
import { buildZoneOptionGroups } from '../utils/zoneOptions'
import { seriesFor, type ZoneEnergySeries } from './energy/seriesFor'
import { periodGroupData, rangeTile, type PeriodTile } from './energy/periodGroup'
import { computeAutoconso, type AutoconsoResult } from './energy/autoconso'
import { computeBattery, type BatteryResult } from './energy/battery'
import { buildDailyGridSolarChart, buildMonthlyGridSolarChart, buildPowerChart } from './energy/charts'

const ENERGY_RESOURCE_TYPES = ['energie_reseau', 'energie_solaire', 'energie_batterie', 'energie_flux']

const allSeries = ref<Series[]>([])
const zone = ref('')
const range = ref<RangeKey>('24h')

const zoneOptions = computed(() =>
  buildZoneOptionGroups(allSeries.value, (s) => ENERGY_RESOURCE_TYPES.includes(s.resource_type || '')),
)

const hasAnyForZone = ref(true)
const gridTiles = ref<PeriodTile[]>([])
const gridExportTiles = ref<PeriodTile[]>([])
const solarTiles = ref<PeriodTile[]>([])
const kpiNoteVisible = ref(false)

const autoconso = ref<AutoconsoResult>({ tiles: [], notes: [], visible: false })
const battery = ref<BatteryResult>({ hasSeries: false, hasActivity: false, hint: '', tiles: [], note: '', chart: null })

const customBounds = ref<Bounds | null>(null)
const customTiles = ref<PeriodTile[]>([])

const powerChartData = ref<Record<string, unknown> | null>(null)
const dailyChartData = ref<{ labels: string[]; datasets: Record<string, unknown>[] } | null>(null)
const monthlyChartData = ref<{ labels: string[]; datasets: Record<string, unknown>[] } | null>(null)

const barOptions = baseBarOptions(true)
const lineOptions = baseLineOptions()

async function refresh() {
  if (!zone.value) return
  const sids: ZoneEnergySeries = seriesFor(zone.value)

  hasAnyForZone.value = !!(
    sids.gridActual || sids.gridTotal || sids.solarActual || sids.solarTotal ||
    sids.batteryActual || sids.batteryTotal || sids.efmGpwr || sids.efmPpwr
  )
  if (!hasAnyForZone.value) return

  const grid = await periodGroupData('Réseau (import)', sids.gridTotal)
  const gridExport = await periodGroupData('Réseau (export)', sids.gridNegTotal)
  const solar = await periodGroupData('Solaire', sids.solarTotal)
  gridTiles.value = grid.tiles
  gridExportTiles.value = gridExport.tiles
  solarTiles.value = solar.tiles
  kpiNoteVisible.value = grid.any || gridExport.any || solar.any

  autoconso.value = await computeAutoconso(sids, grid.todayKwh, gridExport.todayKwh, solar.todayKwh)
  battery.value = await computeBattery(sids)
  powerChartData.value = await buildPowerChart(sids, range.value)
  dailyChartData.value = await buildDailyGridSolarChart(sids.gridTotal?.series_id, sids.solarTotal?.series_id)
  monthlyChartData.value = await buildMonthlyGridSolarChart(sids.gridTotal?.series_id, sids.solarTotal?.series_id)
  await refreshCustomRange()
}

async function refreshCustomRange() {
  if (!customBounds.value || !zone.value) { customTiles.value = []; return }
  const sids = seriesFor(zone.value)
  const [gridT, gridExportT, solarT] = await Promise.all([
    rangeTile('Réseau (import)', sids.gridTotal, customBounds.value),
    rangeTile('Réseau (export)', sids.gridNegTotal, customBounds.value),
    rangeTile('Solaire', sids.solarTotal, customBounds.value),
  ])
  customTiles.value = [gridT, gridExportT, solarT].filter((t): t is PeriodTile => t !== null)
}

onMounted(async () => {
  allSeries.value = await loadAllSeries()
  const groups = zoneOptions.value.groups
  const first = groups[0]?.options[0]
  if (first) zone.value = first.value
  await refresh()
})

watch([zone, range], refresh)
watch(customBounds, refreshCustomRange)
</script>

<template>
  <div>
    <div class="mb-4 flex flex-wrap items-center gap-4">
      <label class="flex flex-col text-sm text-neutral-600">
        Zone
        <ZoneSelect v-model="zone" :groups="zoneOptions.groups" :multi-site="zoneOptions.multiSite" class="mt-1" />
      </label>
      <RangeButtons v-model="range" />
    </div>

    <p v-if="!hasAnyForZone" class="text-sm text-neutral-500">
      Aucune donnée Réseau/Solaire/Batterie pour cette zone.
    </p>

    <div v-else class="space-y-8">
      <section>
        <h3 class="mb-2 text-lg font-semibold text-neutral-900">
          Réseau &amp; solaire
          <span class="text-sm font-normal text-neutral-500">import/export, jour/semaine/mois/année</span>
        </h3>
        <div class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          <KpiTile v-for="t in gridTiles" :key="t.label" :label="t.label" :value="t.value" :unit="t.unit" accent="grid" />
          <KpiTile v-for="t in gridExportTiles" :key="t.label" :label="t.label" :value="t.value" :unit="t.unit" accent="grid" />
          <KpiTile v-for="t in solarTiles" :key="t.label" :label="t.label" :value="t.value" :unit="t.unit" accent="solar" />
        </div>
        <NoteText v-if="kpiNoteVisible">
          Calculé ici (relevé de fin - relevé de début sur le compteur cumulatif "total"/"totalNeg"), comme un
          décompte de charges -- pas une lecture des compteurs vivants du Miniserver.
        </NoteText>

        <div class="mt-4 rounded-lg border border-neutral-200 bg-white p-4">
          <h4 class="mb-2 text-sm font-semibold text-neutral-700">Plage personnalisée</h4>
          <DateRangePicker v-model:bounds="customBounds" />
          <div v-if="customTiles.length" class="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            <KpiTile v-for="t in customTiles" :key="t.label" :label="t.label" :value="t.value" :unit="t.unit" accent="grid" />
          </div>
          <p v-else-if="customBounds" class="mt-2 text-sm text-neutral-500">Aucune donnée sur cette plage.</p>
        </div>
      </section>

      <section v-if="autoconso.visible">
        <h3 class="mb-2 text-lg font-semibold text-neutral-900">Autoconsommation</h3>
        <div class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          <KpiTile v-for="t in autoconso.tiles" :key="t.label" :label="t.label" :value="t.value" :unit="t.unit" accent="auto" />
        </div>
        <NoteText v-for="(n, i) in autoconso.notes" :key="i">{{ n }}</NoteText>
      </section>

      <section v-if="battery.hasSeries">
        <h3 class="mb-2 text-lg font-semibold text-neutral-900">Batterie</h3>
        <p v-if="!battery.hasActivity" class="text-sm text-neutral-500">{{ battery.hint }}</p>
        <template v-else>
          <div class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            <KpiTile v-for="t in battery.tiles" :key="t.label" :label="t.label" :value="t.value" :unit="t.unit" accent="battery" />
          </div>
          <NoteText>{{ battery.note }}</NoteText>
          <div v-if="battery.chart" class="mt-4 h-80 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
            <Bar :data="battery.chart as never" :options="barOptions" />
          </div>
        </template>
      </section>

      <section>
        <h3 class="mb-2 text-lg font-semibold text-neutral-900">
          Puissance instantanée <span class="text-sm font-normal text-neutral-500">réseau vs solaire</span>
        </h3>
        <div v-if="powerChartData" class="h-80 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
          <Line :data="powerChartData as never" :options="lineOptions" />
        </div>
      </section>

      <section>
        <h3 class="mb-2 text-lg font-semibold text-neutral-900">
          Consommation / production journalière <span class="text-sm font-normal text-neutral-500">30 derniers jours</span>
        </h3>
        <div v-if="dailyChartData" class="h-80 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
          <Bar :data="dailyChartData as never" :options="barOptions" />
        </div>
        <NoteText>
          Calculé ici à partir de deux relevés du compteur cumulatif ("total"), comme un décompte de charges -- le
          Miniserver ne fournit nativement que le cumul du jour en cours, pas d'historique journalier au-delà.
        </NoteText>
      </section>

      <section>
        <h3 class="mb-2 text-lg font-semibold text-neutral-900">
          Consommation / production mensuelle <span class="text-sm font-normal text-neutral-500">12 derniers mois</span>
        </h3>
        <div v-if="monthlyChartData" class="h-80 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
          <Bar :data="monthlyChartData as never" :options="barOptions" />
        </div>
      </section>
    </div>
  </div>
</template>
