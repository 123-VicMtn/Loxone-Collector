<script setup lang="ts">
/**
 * Sélecteur de plage de dates personnalisée pour les tuiles KPI du
 * dashboard, en plus des 4 presets fixes (aujourd'hui/semaine/mois/année)
 * -- répond à l'exigence explicite de pouvoir choisir des dates dans le
 * dashboard (voir CLAUDE.md, "Refactor extraction/lecture des données
 * dashboard"). Deux <input type="date">, émet les bornes calculées
 * (shared/periods.ts::customRangeBounds) dès que la plage est valide.
 */
import { computed, ref, watch } from 'vue'
import { customRangeBounds, type Bounds } from '@shared/periods'

const emit = defineEmits<{ 'update:bounds': [Bounds | null] }>()

const from = ref('')
const to = ref('')

const bounds = computed<Bounds | null>(() => (from.value && to.value ? customRangeBounds(from.value, to.value) : null))
const invalid = computed(() => !!(from.value && to.value) && bounds.value === null)

watch(bounds, (b) => emit('update:bounds', b), { immediate: true })
</script>

<template>
  <div class="flex flex-wrap items-end gap-2">
    <label class="flex flex-col text-sm text-neutral-600">
      Du
      <input v-model="from" type="date" class="mt-1 rounded border border-neutral-300 px-2 py-1" />
    </label>
    <label class="flex flex-col text-sm text-neutral-600">
      Au
      <input v-model="to" type="date" class="mt-1 rounded border border-neutral-300 px-2 py-1" />
    </label>
    <p v-if="invalid" class="text-sm text-red-600">Plage invalide (la date de fin doit être après le début).</p>
  </div>
</template>
