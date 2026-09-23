<script setup lang="ts">
import { computed } from 'vue'
import type { DecomptePayload } from '../types/decompte'
import { fmtKwh, fmtCHF, fmtPct } from '../utils/format'
import StatusBadge from './StatusBadge.vue'

const props = defineProps<{ payload: DecomptePayload; periodKey: string }>()

const rows = computed(() =>
  props.payload.zones
    .map((z) => ({ zone: z, entry: z.periodes[props.periodKey] }))
    .filter((r) => r.entry),
)

type TotKey = 'reseau' | 'solaire' | 'total' | 'ht' | 'tva' | 'ttc'

const totals = computed(() => {
  const totals: Record<TotKey, number> = { reseau: 0, solaire: 0, total: 0, ht: 0, tva: 0, ttc: 0 }
  // Fiabilité suivie COLONNE PAR COLONNE : sans tarif enregistré, les
  // montants sont indisponibles alors que les kWh, eux, sont parfaitement
  // calculables -- un seul drapeau global effacerait aussi les totaux kWh.
  const ok: Record<TotKey, boolean> = { reseau: true, solaire: true, total: true, ht: true, tva: true, ttc: true }

  for (const { entry: e } of rows.value) {
    const m = e.montants
    const values: [TotKey, number | null][] = [
      ['reseau', e.reseau.kwh], ['solaire', e.solaire.kwh], ['total', e.total],
      ['ht', m.ht], ['tva', m.tva], ['ttc', m.ttc],
    ]
    for (const [k, v] of values) {
      if (v === null) ok[k] = false
      else totals[k] += v
    }
  }

  const autoprod = ok.total && totals.total ? (totals.solaire / totals.total) * 100 : null
  return { totals, ok, autoprod }
})

function t(k: TotKey, f: (v: number) => string): string {
  return totals.value.ok[k] ? f(totals.value.totals[k]) : '—'
}
const kwh1 = (v: number) => fmtKwh(v, 1)
</script>

<template>
  <div class="overflow-x-auto">
    <table class="w-full border-collapse text-sm">
      <thead>
        <tr class="border-b border-neutral-200 text-left text-neutral-500">
          <th class="py-2 pr-3 font-medium">Zone</th>
          <th class="py-2 px-3 text-right font-medium">Réseau (kWh)</th>
          <th class="py-2 px-3 text-right font-medium">Solaire (kWh)</th>
          <th class="py-2 px-3 text-right font-medium">Consommation (kWh)</th>
          <th class="py-2 px-3 text-right font-medium">Autoproduction</th>
          <th class="py-2 px-3 text-right font-medium">HT</th>
          <th class="py-2 px-3 text-right font-medium">TVA</th>
          <th class="py-2 px-3 text-right font-medium">TTC</th>
          <th class="py-2 pl-3 font-medium">État</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="{ zone, entry } in rows"
          :key="zone.zone"
          :class="['border-b border-neutral-100', entry.facturable ? '' : 'bg-amber-50/50']"
        >
          <td class="py-2 pr-3">{{ zone.label }}</td>
          <td class="py-2 px-3 text-right">{{ fmtKwh(entry.reseau.kwh, 1) }}</td>
          <td class="py-2 px-3 text-right">{{ fmtKwh(entry.solaire.kwh, 1) }}</td>
          <td class="py-2 px-3 text-right font-semibold">{{ fmtKwh(entry.total, 1) }}</td>
          <td class="py-2 px-3 text-right">{{ fmtPct(entry.taux_autoproduction, 0, false) }}</td>
          <td class="py-2 px-3 text-right">{{ fmtCHF(entry.montants.ht) }}</td>
          <td class="py-2 px-3 text-right">{{ fmtCHF(entry.montants.tva) }}</td>
          <td class="py-2 px-3 text-right font-semibold">{{ fmtCHF(entry.montants.ttc) }}</td>
          <td class="py-2 pl-3"><StatusBadge :entry="entry" /></td>
        </tr>
      </tbody>
      <tfoot>
        <tr class="border-t-2 border-neutral-300 text-left font-semibold">
          <td class="py-2 pr-3">Total immeuble</td>
          <td class="py-2 px-3 text-right">{{ t('reseau', kwh1) }}</td>
          <td class="py-2 px-3 text-right">{{ t('solaire', kwh1) }}</td>
          <td class="py-2 px-3 text-right">{{ t('total', kwh1) }}</td>
          <td class="py-2 px-3 text-right">{{ fmtPct(totals.autoprod, 0, false) }}</td>
          <td class="py-2 px-3 text-right">{{ t('ht', fmtCHF) }}</td>
          <td class="py-2 px-3 text-right">{{ t('tva', fmtCHF) }}</td>
          <td class="py-2 px-3 text-right">{{ t('ttc', fmtCHF) }}</td>
          <td class="py-2 pl-3"></td>
        </tr>
      </tfoot>
    </table>
  </div>
</template>
