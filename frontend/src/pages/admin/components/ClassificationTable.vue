<script setup lang="ts">
import { classify } from '../api/admin'
import type { EditableRow } from '../types/series'
import ManualBadge from './ManualBadge.vue'

defineProps<{
  rows: EditableRow[]
  labels: Record<string, string>
  knownApartments: string[]
}>()

const STATUS_MS = { save: 3000, reset: 4000 }

/** Efface le statut après un délai, sauf s'il a déjà changé entre-temps
 * (un nouveau clic pendant le délai ne doit pas se faire effacer par
 * l'ancien timer) -- port de static/js/admin.js::flashStatus. */
function flashStatus(row: EditableRow, text: string, ms: number) {
  row.status = text
  if (!ms) return
  setTimeout(() => {
    if (row.status === text) row.status = ''
  }, ms)
}

async function save(row: EditableRow) {
  flashStatus(row, '…', 0)
  const ok = await classify(row.series_id, {
    apartment: row.editApartment.trim(),
    resource_type: row.editResourceType,
  })
  if (ok) {
    row.apartment = row.editApartment.trim()
    row.resource_type = row.editResourceType || null
    row.apartment_manual = 1
    row.resource_type_manual = 1
  }
  flashStatus(row, ok ? '✓ enregistré' : '✗ erreur', STATUS_MS.save)
}

async function reset(row: EditableRow) {
  flashStatus(row, '…', 0)
  const ok = await classify(row.series_id, { reset: true })
  flashStatus(row, ok ? '✓ réinitialisé (recalculé au prochain poll)' : '✗ erreur', STATUS_MS.reset)
}
</script>

<template>
  <datalist id="apartments-datalist">
    <option v-for="a in knownApartments" :key="a" :value="a" />
  </datalist>

  <div class="overflow-x-auto">
    <table class="w-full border-collapse text-sm">
      <thead>
        <tr class="border-b border-neutral-200 text-left text-neutral-500">
          <th class="py-2 pr-3 font-medium">Capteur</th>
          <th class="py-2 px-3 font-medium">Pièce</th>
          <th class="py-2 px-3 font-medium">Miniserver</th>
          <th class="py-2 px-3 font-medium">Appartement</th>
          <th class="py-2 px-3 font-medium">Type de ressource</th>
          <th class="py-2 pl-3 font-medium">Action</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.series_id" class="border-b border-neutral-100">
          <td class="py-2 pr-3">
            {{ row.label }}
            <span v-if="row.unit" class="text-neutral-400">({{ row.unit }})</span>
          </td>
          <td class="py-2 px-3 text-neutral-500">{{ row.room }}</td>
          <td class="py-2 px-3 text-neutral-500">{{ row.miniserver }}</td>
          <td class="py-2 px-3">
            <input
              v-model="row.editApartment"
              type="text"
              list="apartments-datalist"
              placeholder="ex: APP01"
              class="w-32 rounded border border-neutral-300 px-2 py-1"
            >
            <ManualBadge :manual="row.apartment_manual" />
          </td>
          <td class="py-2 px-3">
            <select v-model="row.editResourceType" class="rounded border border-neutral-300 px-2 py-1">
              <option value="">— aucun —</option>
              <option v-for="(label, key) in labels" :key="key" :value="key">{{ label }}</option>
            </select>
            <ManualBadge :manual="row.resource_type_manual" />
          </td>
          <td class="py-2 pl-3 whitespace-nowrap">
            <button type="button" class="rounded bg-blue-600 px-3 py-1 text-white hover:bg-blue-700" @click="save(row)">
              Enregistrer
            </button>
            <button
              type="button"
              class="ml-1 rounded border border-neutral-300 px-2 py-1 hover:bg-neutral-50"
              title="Revenir à la classification automatique"
              @click="reset(row)"
            >↺</button>
            <span class="ml-2 text-neutral-500">{{ row.status }}</span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
