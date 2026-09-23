<script setup lang="ts">
/**
 * Onglet "Énergie" : réseau (import/export) vs solaire vs batterie, par
 * zone. Chaque groupe de tuiles jour/semaine/mois/année lit directement
 * les states totalX/totalNegX du Miniserver (déjà recalculés côté Loxone,
 * jamais redérivés côté dashboard) ; seuls les graphs journaliers/mensuels
 * (historique passé, non disponible nativement au-delà d'aujourd'hui)
 * utilisent un delta jour-sur-jour du compteur cumulatif ("total"/
 * "totalNeg"). Voir CLAUDE.md, section "Dashboard énergie", pour le détail
 * du modèle (mesuré / recalculé par le Miniserver / calculé par nous).
 * Port de tabs/energy-tab.js.
 */

import { computed, onMounted, ref, watch } from 'vue'
import { Bar, Line } from 'vue-chartjs'
import { loadAllSeries } from '@shared/api/series'
import type { Series } from '@shared/types/series'
import { baseBarOptions, baseLineOptions } from '@shared/charts'
import type { RangeKey } from '@shared/ranges'
import ZoneSelect from '../components/ZoneSelect.vue'
import RangeButtons from '../components/RangeButtons.vue'
import KpiTile from '../components/KpiTile.vue'
import NoteText from '../components/NoteText.vue'
import { buildZoneOptionGroups } from '../utils/zoneOptions'
import { seriesFor, type ZoneEnergySeries } from './energy/seriesFor'
import { periodGroupData, type PeriodTile } from './energy/periodGroup'
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

  const grid = await periodGroupData('Réseau (import)', {
    day: sids.gridDay, week: sids.gridWeek, month: sids.gridMonth, year: sids.gridYear, total: sids.gridTotal,
  })
  const gridExport = await periodGroupData('Réseau (export)', {
    day: sids.gridNegDay, week: sids.gridNegWeek, month: sids.gridNegMonth, year: sids.gridNegYear, total: sids.gridNegTotal,
  })
  const solar = await periodGroupData('Solaire', {
    day: sids.solarDay, week: sids.solarWeek, month: sids.solarMonth, year: sids.solarYear, total: sids.solarTotal,
  })
  gridTiles.value = grid.tiles
  gridExportTiles.value = gridExport.tiles
  solarTiles.value = solar.tiles
  kpiNoteVisible.value = grid.any || gridExport.any || solar.any

  autoconso.value = await computeAutoconso(sids, grid.dayV, gridExport.dayV, solar.dayV)
  battery.value = await computeBattery(sids)
  powerChartData.value = await buildPowerChart(sids, range.value)
  dailyChartData.value = await buildDailyGridSolarChart(sids.gridTotal?.series_id, sids.solarTotal?.series_id)
  monthlyChartData.value = await buildMonthlyGridSolarChart(sids.gridTotal?.series_id, sids.solarTotal?.series_id)
}

onMounted(async () => {
  allSeries.value = await loadAllSeries()
  const groups = zoneOptions.value.groups
  const first = groups[0]?.options[0]
  if (first) zone.value = first.value
  await refresh()
})

watch([zone, range], refresh)
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
          <span class="text-sm font-normal text-neutral-500">import/export, jour/semaine/mois/année déjà calculés par le Miniserver</span>
        </h3>
        <div class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          <KpiTile v-for="t in gridTiles" :key="t.label" :label="t.label" :value="t.value" :unit="t.unit" accent="grid" />
          <KpiTile v-for="t in gridExportTiles" :key="t.label" :label="t.label" :value="t.value" :unit="t.unit" accent="grid" />
          <KpiTile v-for="t in solarTiles" :key="t.label" :label="t.label" :value="t.value" :unit="t.unit" accent="solar" />
        </div>
        <NoteText v-if="kpiNoteVisible">
          Valeurs recalculées et remises à zéro par le Miniserver Loxone lui-même (states totalDay/Week/Month/Year
          et totalNegDay/Week/Month/Year) -- pas un delta calculé côté dashboard.
        </NoteText>
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
