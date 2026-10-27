<script setup lang="ts">
/**
 * Page /admin -- port Vue de templates/admin.html + static/js/admin.js.
 * L'appartement/type de ressource devinés automatiquement peuvent être
 * corrigés ici ; une fois enregistrée, une correction n'est plus jamais
 * écrasée par le poller (flags `apartment_manual`/`resource_type_manual`
 * en base -- règle stricte du projet, voir CLAUDE.md).
 */

import { computed, onMounted, ref } from 'vue'
import { fetchResourceTypeLabels, fetchSeries } from '../api/admin'
import type { EditableRow } from '../types/series'
import { compareApartments } from '@shared/format'
import { useSiteActif } from '@shared/composables/useSiteActif'
import ClassificationTable from '../components/ClassificationTable.vue'

const { site, loaded: siteLoaded } = useSiteActif()

const rows = ref<EditableRow[]>([])
const labels = ref<Record<string, string>>({})
const loading = ref(true)
const errored = ref(false)
const errorMessage = ref('')

const shown = computed(() => rows.value.filter((r) => r.miniserver === site.value))

const knownApartments = computed(() => {
  const set = new Set<string>()
  for (const r of shown.value) if (r.apartment) set.add(r.apartment)
  return Array.from(set).sort(compareApartments)
})

onMounted(async () => {
  try {
    const [series, labelMap] = await Promise.all([fetchSeries(), fetchResourceTypeLabels()])
    labels.value = labelMap
    rows.value = series.map((s) => ({
      ...s,
      editApartment: s.apartment || '',
      editResourceType: s.resource_type || '',
      status: '',
    }))
  } catch (err) {
    errored.value = true
    errorMessage.value = (err as Error).message
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="w-full px-4 py-6">
    <main class="min-w-0 w-full">
      <h1 class="mb-4 text-xl font-bold text-neutral-900">Classification des capteurs</h1>
      <p class="mb-6 text-sm text-neutral-500">
        Appartement et type de ressource sont devinés automatiquement (préfixe
        APPxx dans le nom, mots-clés pour le type). Corrige les lignes qui ne
        sont pas bonnes : une fois enregistrée, une correction n'est plus jamais
        écrasée automatiquement (icône <span class="rounded-full bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-800">manuel</span>).
        Le bouton ↺ repasse une ligne en devinette automatique.
      </p>

      <p v-if="loading || !siteLoaded" class="text-sm text-neutral-500">Chargement…</p>
      <p v-else-if="errored" class="text-sm text-red-600">Impossible de charger les capteurs : {{ errorMessage }}</p>
      <p v-else-if="!site" class="text-sm text-neutral-500">Aucun site n'est accessible avec ce compte.</p>
      <ClassificationTable v-else :rows="shown" :labels="labels" :known-apartments="knownApartments" />
    </main>
  </div>
</template>
