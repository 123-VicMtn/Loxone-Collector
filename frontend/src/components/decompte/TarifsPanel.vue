<script setup lang="ts">
import { reactive, ref } from 'vue'
import type { Tarif } from '../../types/decompte'
import { deleteTarif, saveTarif } from '../../api/decompte'
import { fmtCHF, fmtDay } from '../../utils/format'

const props = defineProps<{ tarifs: Tarif[]; miniserver: string }>()
const emit = defineEmits<{ changed: [] }>()

const message = ref('')

const form = reactive({
  valid_from: '',
  prix_reseau: '0.000',
  prix_solaire: '0.000',
  taux_tva: '8.1',
  note: '',
})

async function handleSubmit() {
  message.value = ''
  try {
    await saveTarif({
      miniserver: props.miniserver,
      valid_from: form.valid_from,
      prix_reseau: Number(form.prix_reseau),
      prix_solaire: Number(form.prix_solaire),
      taux_tva: Number(form.taux_tva),
      note: form.note,
    })
    message.value = 'Tarif enregistré, décompte recalculé.'
    form.valid_from = ''
    form.note = ''
    emit('changed')
  } catch (err) {
    message.value = `Échec de l'enregistrement : ${(err as Error).message}`
  }
}

async function handleDelete(t: Tarif) {
  if (!window.confirm(
    `Supprimer le tarif valable dès le ${t.valid_from} ?\n` +
    'Les mois qui s\'appuyaient dessus seront recalculés avec le tarif précédent.',
  )) return
  await deleteTarif(t.id, props.miniserver)
  message.value = 'Tarif supprimé, décompte recalculé.'
  emit('changed')
}
</script>

<template>
  <div class="overflow-x-auto">
    <table class="w-full border-collapse text-sm">
      <template v-if="tarifs.length">
        <thead>
          <tr class="border-b border-neutral-200 text-left text-neutral-500">
            <th class="py-2 pr-3 font-medium">Valable dès le</th>
            <th class="py-2 px-3 text-right font-medium">Réseau</th>
            <th class="py-2 px-3 text-right font-medium">Solaire</th>
            <th class="py-2 px-3 text-right font-medium">TVA</th>
            <th class="py-2 px-3 font-medium">Note</th>
            <th class="py-2 pl-3"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in tarifs" :key="t.id" class="border-b border-neutral-100">
            <td class="py-2 pr-3">{{ fmtDay(Date.parse(t.valid_from) / 1000) }}</td>
            <td class="py-2 px-3 text-right">{{ fmtCHF(t.prix_reseau) }} / kWh</td>
            <td class="py-2 px-3 text-right">{{ fmtCHF(t.prix_solaire) }} / kWh</td>
            <td class="py-2 px-3 text-right">{{ t.taux_tva }} %</td>
            <td class="py-2 px-3">{{ t.note || '—' }}</td>
            <td class="py-2 pl-3">
              <button type="button" class="text-blue-600 hover:underline" @click="handleDelete(t)">Supprimer</button>
            </td>
          </tr>
        </tbody>
      </template>
      <tbody v-else>
        <tr>
          <td colspan="6" class="py-3 text-neutral-500">
            Aucun tarif enregistré : les colonnes HT / TVA / TTC du décompte restent vides
            tant qu'aucun prix n'est saisi ci-dessous.
          </td>
        </tr>
      </tbody>
    </table>
  </div>

  <form class="mt-4 flex flex-wrap items-end gap-4" @submit.prevent="handleSubmit">
    <label class="flex flex-col text-sm text-neutral-600">
      Valable dès le
      <input v-model="form.valid_from" type="date" required class="mt-1 rounded border border-neutral-300 px-2 py-1">
    </label>
    <label class="flex flex-col text-sm text-neutral-600">
      Prix réseau (CHF/kWh)
      <input v-model="form.prix_reseau" type="number" step="0.001" min="0" required class="mt-1 w-32 rounded border border-neutral-300 px-2 py-1">
    </label>
    <label class="flex flex-col text-sm text-neutral-600">
      Prix solaire (CHF/kWh)
      <input v-model="form.prix_solaire" type="number" step="0.001" min="0" required class="mt-1 w-32 rounded border border-neutral-300 px-2 py-1">
    </label>
    <label class="flex flex-col text-sm text-neutral-600">
      TVA (%)
      <input v-model="form.taux_tva" type="number" step="0.1" min="0" required class="mt-1 w-24 rounded border border-neutral-300 px-2 py-1">
    </label>
    <label class="flex flex-1 min-w-[12rem] flex-col text-sm text-neutral-600">
      Note
      <input v-model="form.note" type="text" placeholder="ex: tarif 2026, contrat Romande Énergie" class="mt-1 rounded border border-neutral-300 px-2 py-1">
    </label>
    <button type="submit" class="rounded bg-blue-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-blue-700">
      Enregistrer
    </button>
  </form>
  <p v-if="message" class="mt-2 text-sm text-neutral-600">{{ message }}</p>
  <p class="mt-2 text-sm text-neutral-500">
    Les tarifs sont enregistrés en base, avec leur date de prise d'effet :
    un mois déjà facturé garde ses prix d'origine même si les prix
    changent ensuite. Le taux de TVA est pré-rempli à 8,1 % (taux normal
    suisse) — à ajuster si ce n'est pas celui qui s'applique.
  </p>
</template>
