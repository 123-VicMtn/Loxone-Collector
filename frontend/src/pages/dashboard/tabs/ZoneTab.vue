<script setup lang="ts">
/**
 * Onglet "Consommations par zone" : vue générique (toute ressource --
 * chauffage, eau chaude, énergie, etc.), une zone + une ressource à la
 * fois, avec les mêmes graphs journalier/mensuel que l'onglet Énergie mais
 * sur une seule série cumulative ("total"). Tuiles jour/semaine/mois/année
 * + plage personnalisée calculées via /api/series/<id>/range (relevé de
 * fin - relevé de début), voir CLAUDE.md, "Refactor extraction/lecture des
 * données dashboard". Port de tabs/zone-tab.js.
 */

import { computed, onMounted, ref, watch } from 'vue'
import { Bar } from 'vue-chartjs'
import { loadAllSeries, findSeries } from '@shared/api/series'
import { loadResourceTypeLabels } from '@shared/config'
import { matchesZone, resourceLabel } from '@shared/format'
import { baseBarOptions } from '@shared/charts'
import type { Series } from '@shared/types/series'
import type { Bounds } from '@shared/periods'
import ZoneSelect from '../components/ZoneSelect.vue'
import KpiTile from '../components/KpiTile.vue'
import DateRangePicker from '../components/DateRangePicker.vue'
import { buildZoneOptionGroups } from '../utils/zoneOptions'
import { periodGroupData, rangeTile, type PeriodTile } from './energy/periodGroup'
import { buildSingleDailyChart, buildSingleMonthlyChart } from './zone/charts'

const allSeries = ref<Series[]>([])
const labels = ref<Record<string, string>>({})
const zone = ref('')
const resourceType = ref('')

const zoneOptions = computed(() => buildZoneOptionGroups(allSeries.value))

const resourceOptions = computed(() => {
  if (!zone.value) return []
  const types = new Set<string>()
  for (const s of allSeries.value) {
    if (matchesZone(s, zone.value) && s.state_name === 'total') types.add(s.resource_type || 'autre')
  }
  return Array.from(types)
    .sort((a, b) => resourceLabel(a, labels.value).localeCompare(resourceLabel(b, labels.value)))
    .map((rtype) => ({ value: rtype, label: resourceLabel(rtype, labels.value) }))
})

const hasData = ref(true)
const kpiTiles = ref<PeriodTile[]>([])
const dailyChartData = ref<{ labels: string[]; datasets: Record<string, unknown>[] } | null>(null)
const monthlyChartData = ref<{ labels: string[]; datasets: Record<string, unknown>[] } | null>(null)
const barOptions = baseBarOptions(false)

const customBounds = ref<Bounds | null>(null)
const customTile = ref<PeriodTile | null>(null)
const currentTotalSeries = ref<Series | null>(null)

async function refresh() {
  if (!zone.value || !resourceType.value) return

  const findState = (state: string) =>
    findSeries((s) => matchesZone(s, zone.value) && s.resource_type === resourceType.value && s.state_name === state)
  const totalSeries = findState('total')
  currentTotalSeries.value = totalSeries

  if (!totalSeries) {
    hasData.value = false
    return
  }
  hasData.value = true

  const unit = totalSeries.unit || ''
  const group = await periodGroupData('', totalSeries)
  kpiTiles.value = group.tiles
  dailyChartData.value = await buildSingleDailyChart(totalSeries.series_id, unit)
  monthlyChartData.value = await buildSingleMonthlyChart(totalSeries.series_id, unit)
  await refreshCustomRange()
}

async function refreshCustomRange() {
  if (!customBounds.value || !currentTotalSeries.value) { customTile.value = null; return }
  customTile.value = await rangeTile('Plage choisie', currentTotalSeries.value, customBounds.value)
}

/** Un changement de zone peut invalider la ressource choisie (pas
 * forcément présente dans la nouvelle zone) -- reprend la première
 * disponible, comme zone-tab.js::initZoneTab -> buildResourceOptions()
 * rappelé à chaque changement de zone. */
watch(zone, () => {
  resourceType.value = resourceOptions.value[0]?.value ?? ''
})
watch([zone, resourceType], refresh)
watch(customBounds, refreshCustomRange)

onMounted(async () => {
  const [series, labelMap] = await Promise.all([loadAllSeries(), loadResourceTypeLabels()])
  allSeries.value = series
  labels.value = labelMap
  const firstZone = zoneOptions.value.groups[0]?.options[0]
  if (firstZone) zone.value = firstZone.value
})
</script>

<template>
  <div>
    <div class="mb-4 flex flex-wrap items-center gap-4">
      <label class="flex flex-col text-sm text-neutral-600">
        Zone
        <ZoneSelect v-model="zone" :groups="zoneOptions.groups" :multi-site="zoneOptions.multiSite" class="mt-1" />
      </label>
      <label class="flex flex-col text-sm text-neutral-600">
        Ressource
        <select v-model="resourceType" class="mt-1 rounded border border-neutral-300 px-2 py-1">
          <option v-for="o in resourceOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
        </select>
      </label>
    </div>

    <p v-if="!hasData" class="text-sm text-neutral-500">
      Aucune série cumulative (« total ») pour cette combinaison zone/ressource.
    </p>

    <div v-else class="space-y-8">
      <div class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        <KpiTile v-for="t in kpiTiles" :key="t.label" :label="t.label" :value="t.value" :unit="t.unit" />
      </div>

      <div class="rounded-lg border border-neutral-200 bg-white p-4">
        <h4 class="mb-2 text-sm font-semibold text-neutral-700">Plage personnalisée</h4>
        <DateRangePicker v-model:bounds="customBounds" />
        <div v-if="customTile" class="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          <KpiTile :label="customTile.label" :value="customTile.value" :unit="customTile.unit" />
        </div>
        <p v-else-if="customBounds" class="mt-2 text-sm text-neutral-500">Aucune donnée sur cette plage.</p>
      </div>

      <section>
        <h3 class="mb-2 text-lg font-semibold text-neutral-900">
          Consommation journalière <span class="text-sm font-normal text-neutral-500">30 derniers jours</span>
        </h3>
        <div v-if="dailyChartData" class="h-80 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
          <Bar :data="dailyChartData as never" :options="barOptions" />
        </div>
      </section>

      <section>
        <h3 class="mb-2 text-lg font-semibold text-neutral-900">
          Consommation mensuelle <span class="text-sm font-normal text-neutral-500">12 derniers mois</span>
        </h3>
        <div v-if="monthlyChartData" class="h-80 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
          <Bar :data="monthlyChartData as never" :options="barOptions" />
        </div>
      </section>
    </div>
  </div>
</template>
