<script setup lang="ts">
import type { DecomptePayload, Period } from '../../types/decompte'
import { fmtKwh } from '../../utils/format'

defineProps<{ payload: DecomptePayload; periodes: Period[] }>()
</script>

<template>
  <div class="overflow-x-auto">
    <table class="w-full border-collapse text-sm">
      <thead>
        <tr class="border-b border-neutral-200 text-left text-neutral-500">
          <th class="py-2 pr-3 font-medium">Mois</th>
          <th v-for="z in payload.zones" :key="z.zone" class="py-2 px-3 text-right font-medium">{{ z.label }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="p in periodes" :key="p.key" class="border-b border-neutral-100">
          <td class="py-2 pr-3">{{ p.label }}</td>
          <td v-for="z in payload.zones" :key="z.zone" class="py-2 px-3 text-right">
            {{ fmtKwh(z.periodes[p.key]?.controle?.kwh ?? null, 1) }}
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
