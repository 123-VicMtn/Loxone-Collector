<script setup lang="ts">
/**
 * Relevés : dernier index connu de chaque compteur cumulatif
 * (total / totalNeg). Pas de calcul de consommation.
 */

import { computed, onMounted, ref } from 'vue'
import { fetchJSON } from '@shared/api/http'
import { compareApartments, fmtNumber, zoneLabel } from '@shared/format'

interface Releve {
  series_id: string
  miniserver: string
  label: string
  apartment: string | null
  unit: string
  state_name: string
  ts: number | null
  value: number | null
}

const rows = ref<Releve[]>([])
const loading = ref(true)
const error = ref('')

const sites = computed(() => {
  const names = Array.from(new Set(rows.value.map((r) => r.miniserver))).sort()
  return names.map((site) => {
    const ofSite = rows.value.filter((r) => r.miniserver === site)
    const apartments = Array.from(new Set(ofSite.map((r) => r.apartment || ''))).sort(compareApartments)
    return {
      site,
      groups: apartments.map((apartment) => ({
        apartment,
        label: zoneLabel(apartment),
        rows: ofSite
          .filter((r) => (r.apartment || '') === apartment)
          .sort((a, b) => a.label.localeCompare(b.label, 'fr')),
      })),
    }
  })
})

function when(ts: number | null): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString('fr-CH', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'Europe/Zurich',
  })
}

onMounted(async () => {
  try {
    rows.value = await fetchJSON<Releve[]>('/api/releves')
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Impossible de charger les relevés.'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <p v-if="loading" class="text-sm text-neutral-500">Chargement des relevés…</p>
  <p v-else-if="error" class="text-sm text-red-700">{{ error }}</p>
  <p v-else-if="!rows.length" class="text-sm text-neutral-500">Aucun compteur cumulatif.</p>

  <div v-else class="flex flex-col gap-8">
    <section v-for="site in sites" :key="site.site">
      <h2 v-if="sites.length > 1" class="mb-3 text-base font-semibold text-neutral-900">{{ site.site }}</h2>
      <div v-for="group in site.groups" :key="group.apartment" class="mb-6">
        <h3 class="mb-2 text-sm font-semibold text-neutral-700">{{ group.label }}</h3>
        <div class="overflow-x-auto rounded-lg border border-neutral-200 bg-white">
          <table class="w-full text-sm">
            <thead class="bg-neutral-50 text-left text-neutral-500">
              <tr>
                <th class="px-3 py-2 font-medium">Compteur</th>
                <th class="px-3 py-2 text-right font-medium">Relevé</th>
                <th class="px-3 py-2 font-medium">Unité</th>
                <th class="px-3 py-2 font-medium">Date</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in group.rows" :key="row.series_id" class="border-t border-neutral-100">
                <td class="px-3 py-2">{{ row.label }}</td>
                <td class="px-3 py-2 text-right tabular-nums">{{ fmtNumber(row.value, 3) }}</td>
                <td class="px-3 py-2 text-neutral-500">{{ row.unit || '—' }}</td>
                <td class="px-3 py-2 text-neutral-500">{{ when(row.ts) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>
  </div>
</template>
