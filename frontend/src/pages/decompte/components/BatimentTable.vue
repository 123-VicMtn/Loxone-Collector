<script setup lang="ts">
import type { DecomptePayload, Period } from '../types/decompte'
import { fmtKwh, fmtPct } from '../utils/format'

defineProps<{ payload: DecomptePayload; periodes: Period[] }>()
</script>

<template>
  <div class="overflow-x-auto">
    <table class="w-full border-collapse text-sm">
      <thead>
        <tr class="border-b border-neutral-200 text-left text-neutral-500">
          <th class="py-2 pr-3 font-medium">Mois</th>
          <th class="py-2 px-3 text-right font-medium">Production PV (kWh)</th>
          <th class="py-2 px-3 text-right font-medium">Autoconsommé (kWh)</th>
          <th class="py-2 px-3 text-right font-medium">Injecté au réseau (kWh)</th>
          <th class="py-2 px-3 text-right font-medium">Acheté au réseau (kWh)</th>
          <th class="py-2 px-3 text-right font-medium">Consommation totale (kWh)</th>
          <th class="py-2 px-3 text-right font-medium">Autoproduction</th>
          <th class="py-2 pl-3 text-right font-medium">Autoconsommation</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="p in periodes"
          :key="p.key"
          :class="['border-b border-neutral-100', payload.batiment.periodes[p.key].en_cours ? 'bg-amber-50/50' : '']"
        >
          <td class="py-2 pr-3">{{ p.label }}{{ payload.batiment.periodes[p.key].en_cours ? ' (en cours)' : '' }}</td>
          <td class="py-2 px-3 text-right">{{ fmtKwh(payload.batiment.periodes[p.key].production.kwh) }}</td>
          <td class="py-2 px-3 text-right">{{ fmtKwh(payload.batiment.periodes[p.key].autoconsommation) }}</td>
          <td class="py-2 px-3 text-right">{{ fmtKwh(payload.batiment.periodes[p.key].injection) }}</td>
          <td class="py-2 px-3 text-right">{{ fmtKwh(payload.batiment.periodes[p.key].achat_reseau) }}</td>
          <td class="py-2 px-3 text-right font-semibold">{{ fmtKwh(payload.batiment.periodes[p.key].consommation_totale) }}</td>
          <td class="py-2 px-3 text-right font-semibold">{{ fmtPct(payload.batiment.periodes[p.key].taux_autoproduction, 0, false) }}</td>
          <td class="py-2 pl-3 text-right text-neutral-500">{{ fmtPct(payload.batiment.periodes[p.key].taux_autoconsommation, 0, false) }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
