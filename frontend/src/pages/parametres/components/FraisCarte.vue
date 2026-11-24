<script setup lang="ts">
/**
 * Frais accessoires : montants saisis à la main pour une zone et une
 * période, sans calcul ni prorata. Ils figurent sur le décompte dont la
 * période les contient entièrement.
 */

import { computed, ref } from 'vue'
import type { Frais, ZoneSite } from '../types/parametres'
import { ajouterFrais, messageErreur, supprimerFrais } from '../api/parametres'
import { fmtFrancs, fmtJour } from '../utils/format'

const props = defineProps<{
  miniserver: string
  frais: Frais[]
  zones: ZoneSite[]
  peutModifier: boolean
}>()
const emit = defineEmits<{ maj: [frais: Frais[]] }>()

const libelles = computed(() => Object.fromEntries(props.zones.map((z) => [z.zone, z.label])))

const ouvert = ref(false)
const zone = ref('')
const du = ref('')
const au = ref('')
const libelle = ref('')
const montant = ref('')
const tva = ref(true)
const note = ref('')
const erreur = ref('')
const envoi = ref(false)
const aConfirmer = ref<number | null>(null)

function ouvrir() {
  zone.value = props.zones[0]?.zone ?? ''
  du.value = ''
  au.value = ''
  libelle.value = ''
  montant.value = ''
  tva.value = true
  note.value = ''
  erreur.value = ''
  ouvert.value = true
}

async function enregistrer() {
  erreur.value = ''
  envoi.value = true
  try {
    emit('maj', await ajouterFrais({
      miniserver: props.miniserver, du: du.value, au: au.value, zone: zone.value,
      libelle: libelle.value, montant_ht: montant.value, tva: tva.value, note: note.value,
    }))
    ouvert.value = false
  } catch (err) {
    console.error(err)
    erreur.value = messageErreur(err)
  } finally {
    envoi.value = false
  }
}

async function supprimer(f: Frais) {
  erreur.value = ''
  if (aConfirmer.value !== f.id) {
    aConfirmer.value = f.id
    return
  }
  aConfirmer.value = null
  try {
    emit('maj', await supprimerFrais(f.id, props.miniserver))
  } catch (err) {
    console.error(err)
    erreur.value = messageErreur(err)
  }
}
</script>

<template>
  <section class="rounded-lg border border-neutral-200 bg-white p-4 sm:p-5">
    <h2 class="text-base font-semibold text-neutral-900">Frais accessoires</h2>
    <p class="mt-1 text-sm text-neutral-500">
      Montants ajoutés à la main (entretien, frais exceptionnels…), sans calcul. Un frais figure
      sur le décompte dont la période le contient entièrement.
    </p>

    <p v-if="!frais.length" class="mt-4 text-sm text-neutral-500">Aucun frais saisi.</p>
    <div v-else class="mt-4 overflow-x-auto">
      <table class="w-full border-collapse text-sm">
        <thead>
          <tr class="border-b border-neutral-200 text-left text-neutral-500">
            <th class="py-2 pr-3 font-medium">Période</th>
            <th class="py-2 px-3 font-medium">Zone</th>
            <th class="py-2 px-3 font-medium">Libellé</th>
            <th class="py-2 px-3 text-right font-medium">Montant HT (CHF)</th>
            <th class="py-2 px-3 font-medium">TVA</th>
            <th class="py-2 pl-3" />
          </tr>
        </thead>
        <tbody>
          <tr v-for="f in frais" :key="f.id" class="border-b border-neutral-100">
            <td class="py-2 pr-3 whitespace-nowrap">{{ fmtJour(f.du) }} → {{ fmtJour(f.au) }}</td>
            <td class="py-2 px-3">{{ libelles[f.zone] ?? f.zone }}</td>
            <td class="py-2 px-3">
              {{ f.libelle }}
              <span v-if="f.note" class="block text-xs text-neutral-500">{{ f.note }}</span>
            </td>
            <td class="py-2 px-3 text-right tabular-nums">{{ fmtFrancs(f.montant_ht) }}</td>
            <td class="py-2 px-3">{{ f.tva ? 'oui' : 'non' }}</td>
            <td class="py-2 pl-3 text-right">
              <button
                v-if="peutModifier"
                type="button"
                class="text-xs text-red-700 hover:underline"
                @click="supprimer(f)"
              >{{ aConfirmer === f.id ? 'Confirmer la suppression' : 'Supprimer' }}</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <button
      v-if="peutModifier && !ouvert"
      type="button"
      class="mt-4 text-sm font-medium text-blue-700 hover:underline"
      @click="ouvrir"
    >Ajouter un frais</button>

    <form
      v-if="ouvert"
      class="mt-4 grid gap-3 rounded-md border border-neutral-200 bg-neutral-50 p-3 sm:grid-cols-2"
      @submit.prevent="enregistrer"
    >
      <label class="flex flex-col gap-1 text-sm text-neutral-700">
        Zone
        <select v-model="zone" class="rounded border border-neutral-300 bg-white px-2 py-1.5 text-neutral-900">
          <option v-for="z in zones" :key="z.zone" :value="z.zone">{{ z.label }}</option>
        </select>
      </label>
      <label class="flex flex-col gap-1 text-sm text-neutral-700">
        Libellé
        <input v-model="libelle" type="text" class="rounded border border-neutral-300 bg-white px-2 py-1.5 text-neutral-900">
      </label>
      <label class="flex flex-col gap-1 text-sm text-neutral-700">
        Du
        <input v-model="du" type="date" class="rounded border border-neutral-300 bg-white px-2 py-1.5 text-neutral-900">
      </label>
      <label class="flex flex-col gap-1 text-sm text-neutral-700">
        Au (inclus)
        <input v-model="au" type="date" class="rounded border border-neutral-300 bg-white px-2 py-1.5 text-neutral-900">
      </label>
      <label class="flex flex-col gap-1 text-sm text-neutral-700">
        Montant HT (CHF)
        <input v-model="montant" type="text" inputmode="decimal" class="rounded border border-neutral-300 bg-white px-2 py-1.5 text-neutral-900">
      </label>
      <label class="flex items-center gap-2 self-end pb-2 text-sm text-neutral-800">
        <input v-model="tva" type="checkbox" class="h-4 w-4">
        TVA en sus
      </label>
      <label class="flex flex-col gap-1 text-sm text-neutral-700 sm:col-span-2">
        Remarque (facultatif)
        <input v-model="note" type="text" class="rounded border border-neutral-300 bg-white px-2 py-1.5 text-neutral-900">
      </label>
      <p v-if="erreur" class="rounded bg-red-50 px-3 py-2 text-sm text-red-700 sm:col-span-2" role="alert">{{ erreur }}</p>
      <div class="flex gap-2 sm:col-span-2">
        <button
          type="submit"
          :disabled="envoi"
          class="rounded bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >Enregistrer</button>
        <button type="button" class="rounded px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-100" @click="ouvert = false">
          Annuler
        </button>
      </div>
    </form>
    <p v-else-if="erreur" class="mt-2 text-sm text-red-700" role="alert">{{ erreur }}</p>
  </section>
</template>
