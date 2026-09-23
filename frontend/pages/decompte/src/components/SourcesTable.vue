<script setup lang="ts">
import type { DecomptePayload, SourceRef } from '../types/decompte'

const props = defineProps<{ payload: DecomptePayload }>()

function cellText(src: SourceRef | null, ambigus?: string[]): string {
  if (!src) return 'aucune série trouvée'
  const extra = ambigus && ambigus.length ? ` (autres candidats : ${ambigus.join(', ')})` : ''
  return src.label + extra
}

const batiment = props.payload.batiment.sources
</script>

<template>
  <div class="overflow-x-auto">
    <table class="w-full border-collapse text-sm">
      <thead>
        <tr class="border-b border-neutral-200 text-left text-neutral-500">
          <th class="py-2 pr-3 font-medium">Zone</th>
          <th class="py-2 px-3 font-medium">Réseau — facturé</th>
          <th class="py-2 px-3 font-medium">Solaire — facturé</th>
          <th class="py-2 pl-3 font-medium">Contrôle — non facturé</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="z in payload.zones" :key="z.zone" class="border-b border-neutral-100">
          <td class="py-2 pr-3">{{ z.label }}</td>
          <td
            class="py-2 px-3"
            :class="{ 'text-red-600': !z.sources.reseau }"
            :title="z.sources.reseau?.series_id"
          >{{ cellText(z.sources.reseau, z.ambigus.reseau) }}</td>
          <td
            class="py-2 px-3"
            :class="{ 'text-red-600': !z.sources.solaire }"
            :title="z.sources.solaire?.series_id"
          >{{ cellText(z.sources.solaire, z.ambigus.solaire) }}</td>
          <td
            class="py-2 pl-3"
            :class="{ 'text-red-600': !z.sources.controle }"
            :title="z.sources.controle?.series_id"
          >{{ cellText(z.sources.controle, z.ambigus.controle) }}</td>
        </tr>
        <tr class="text-neutral-500">
          <td class="py-2 pr-3">Immeuble</td>
          <td class="py-2 px-3" :title="batiment.production?.series_id">production PV : {{ cellText(batiment.production) }}</td>
          <td class="py-2 px-3" :title="batiment.reseau_import?.series_id">réseau (autre périmètre) : {{ cellText(batiment.reseau_import) }}</td>
          <td class="py-2 pl-3" :title="batiment.reseau_export?.series_id">réseau (autre périmètre) : {{ cellText(batiment.reseau_export) }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
