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
import ClassificationTable from '../components/ClassificationTable.vue'

const rows = ref<EditableRow[]>([])
const labels = ref<Record<string, string>>({})
const loading = ref(true)
const errored = ref(false)
const errorMessage = ref('')

const knownApartments = computed(() => {
  const set = new Set<string>()
  for (const r of rows.value) if (r.apartment) set.add(r.apartment)
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
  <div class="mx-auto max-w-6xl space-y-6 px-4 py-8">
    <header class="border-b border-neutral-200 pb-6">
      <p class="text-sm"><a href="/" class="text-blue-600 hover:underline">&larr; Retour au dashboard</a></p>
      <h1 class="mt-2 text-2xl font-bold text-neutral-900">Classification des capteurs</h1>
      <p class="mt-1 text-sm text-neutral-500">
        Appartement et type de ressource sont devinés automatiquement (préfixe
        APPxx dans le nom, mots-clés pour le type). Corrige les lignes qui ne
        sont pas bonnes : une fois enregistrée, une correction n'est plus jamais
        écrasée automatiquement (icône <span class="rounded-full bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-800">manuel</span>).
        Le bouton ↺ repasse une ligne en devinette automatique.
      </p>
    </header>

    <p v-if="loading" class="text-sm text-neutral-500">Chargement…</p>
    <p v-else-if="errored" class="text-sm text-red-600">Impossible de charger les capteurs : {{ errorMessage }}</p>
    <ClassificationTable v-else :rows="rows" :labels="labels" :known-apartments="knownApartments" />
  </div>
</template>
