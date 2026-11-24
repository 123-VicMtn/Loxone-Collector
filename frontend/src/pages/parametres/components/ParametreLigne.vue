<script setup lang="ts">
/**
 * Un paramètre : valeur en vigueur, formulaire « Nouvelle valeur à partir
 * du … » et historique des versions. Pas de modification en place : on
 * ajoute une version (la même date corrige la version existante).
 */

import { computed, ref } from 'vue'
import type { EtatParametres, ParametreCatalogue, VersionParametre } from '../types/parametres'
import { ajouterVersion, messageErreur, supprimerVersion } from '../api/parametres'
import { fmtJour, fmtValeur } from '../utils/format'

const props = defineProps<{
  param: ParametreCatalogue
  etat: EtatParametres
  /** Ligne de l'onglet « Valeurs globales » (site ''). */
  global: boolean
  /** Phrase sous le libellé (exemple, rappel du prix réseau…). */
  aide?: string
}>()
const emit = defineEmits<{ maj: [etat: EtatParametres] }>()

const peutModifier = computed(() => (props.global ? props.etat.peut_modifier_global : props.etat.peut_modifier))

const enVigueur = computed(() => {
  if (props.global) {
    const v = versionsAuJour(props.etat.globales)
    return v ? { valeur: v.valeur, depuis: v.valid_from, origine: 'global' as const } : null
  }
  return props.etat.en_vigueur[props.param.cle] ?? null
})

const historique = computed(() =>
  (props.global ? props.etat.globales : props.etat.versions)
    .filter((v) => v.cle === props.param.cle)
    .slice()
    .sort((a, b) => b.valid_from.localeCompare(a.valid_from)),
)

function versionsAuJour(versions: VersionParametre[]): VersionParametre | null {
  const applicables = versions
    .filter((v) => v.cle === props.param.cle && v.valid_from <= props.etat.aujourdhui)
    .sort((a, b) => b.valid_from.localeCompare(a.valid_from))
  return applicables[0] ?? null
}

const origine = computed(() => {
  const v = enVigueur.value
  if (!v || v.origine === 'defaut') {
    return v && v.valeur !== null ? 'valeur par défaut' : 'non renseigné'
  }
  const depuis = `depuis le ${fmtJour(v.depuis)}`
  return v.origine === 'global' && !props.global ? `${depuis}, valeur commune à tous les sites` : depuis
})

const valeurAffichee = computed(() => {
  const v = enVigueur.value
  if (!v || (v.valeur === null && v.origine === 'defaut')) return '—'
  return fmtValeur(props.param, v.valeur)
})

// --- Formulaire ------------------------------------------------------------

const ouvert = ref(false)
const afficherHistorique = ref(false)
const saisie = ref('')
const ouiNon = ref(false)
const aucune = ref(false)
const depuis = ref('')
const note = ref('')
const erreur = ref('')
const envoi = ref(false)
/** Version dont la suppression attend une confirmation (second clic). */
const aConfirmer = ref<number | null>(null)

function ouvrir() {
  const v = enVigueur.value?.valeur
  saisie.value = typeof v === 'number' ? String(v).replace('.', ',') : ''
  ouiNon.value = v === true
  aucune.value = false
  depuis.value = props.etat.aujourdhui
  note.value = ''
  erreur.value = ''
  ouvert.value = true
}

const retroactif = computed(() => !!depuis.value && depuis.value < props.etat.aujourdhui)

/** Clé PAC : un seul champ, l'eau chaude en complément. */
const complementPac = computed(() => {
  if (props.param.cle !== 'pac_part_chauffage') return null
  const n = Number(saisie.value.replace(',', '.'))
  return Number.isFinite(n) && n >= 0 && n <= 100 ? 100 - n : null
})

async function enregistrer() {
  erreur.value = ''
  if (!depuis.value) {
    erreur.value = "Indiquez la date de prise d'effet."
    return
  }
  let valeur: string | boolean | null = saisie.value
  if (props.param.type === 'oui_non') valeur = ouiNon.value
  else if (props.param.facultatif && aucune.value) valeur = null
  envoi.value = true
  try {
    const etat = await ajouterVersion({
      miniserver: props.etat.miniserver,
      cle: props.param.cle,
      valid_from: depuis.value,
      valeur,
      note: note.value,
      global: props.global,
    })
    ouvert.value = false
    emit('maj', etat)
  } catch (err) {
    console.error(err)
    erreur.value = messageErreur(err)
  } finally {
    envoi.value = false
  }
}

async function supprimer(v: VersionParametre) {
  erreur.value = ''
  if (aConfirmer.value !== v.id) {
    aConfirmer.value = v.id
    return
  }
  aConfirmer.value = null
  try {
    emit('maj', await supprimerVersion(v.id, props.etat.miniserver))
  } catch (err) {
    console.error(err)
    erreur.value = messageErreur(err)
  }
}
</script>

<template>
  <div class="border-b border-neutral-100 py-4 last:border-b-0">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div class="min-w-0">
        <p class="text-sm font-medium text-neutral-900">{{ param.libelle }}</p>
        <p v-if="aide" class="mt-0.5 text-xs text-neutral-500">{{ aide }}</p>
      </div>
      <div class="ml-auto text-right">
        <p class="text-sm font-semibold text-neutral-900">{{ valeurAffichee }}</p>
        <p class="text-xs text-neutral-500">{{ origine }}</p>
      </div>
    </div>

    <div class="mt-2 flex flex-wrap gap-3 text-sm">
      <button
        v-if="peutModifier && !ouvert"
        type="button"
        class="font-medium text-blue-700 hover:underline"
        @click="ouvrir"
      >Nouvelle valeur</button>
      <button
        v-if="historique.length"
        type="button"
        class="text-neutral-600 hover:underline"
        :aria-expanded="afficherHistorique"
        @click="afficherHistorique = !afficherHistorique"
      >Historique ({{ historique.length }})</button>
    </div>

    <form
      v-if="ouvert"
      class="mt-3 grid gap-3 rounded-md border border-neutral-200 bg-neutral-50 p-3 sm:grid-cols-2"
      @submit.prevent="enregistrer"
    >
      <label v-if="param.type === 'oui_non'" class="flex items-center gap-2 text-sm text-neutral-800 sm:col-span-2">
        <input v-model="ouiNon" type="checkbox" class="h-4 w-4">
        {{ param.libelle }}
      </label>
      <template v-else>
        <label class="flex flex-col gap-1 text-sm text-neutral-700">
          Valeur ({{ param.unite }})
          <input
            v-model="saisie"
            type="text"
            inputmode="decimal"
            :disabled="aucune"
            class="rounded border border-neutral-300 bg-white px-2 py-1.5 text-neutral-900 disabled:bg-neutral-100"
          >
          <span v-if="complementPac !== null" class="text-xs text-neutral-500">
            Eau chaude : {{ complementPac.toLocaleString('fr-CH') }} %
          </span>
        </label>
        <label v-if="param.facultatif" class="flex items-center gap-2 self-end pb-2 text-sm text-neutral-800">
          <input v-model="aucune" type="checkbox" class="h-4 w-4">
          Aucune
        </label>
      </template>
      <label class="flex flex-col gap-1 text-sm text-neutral-700">
        À partir du
        <input v-model="depuis" type="date" class="rounded border border-neutral-300 bg-white px-2 py-1.5 text-neutral-900">
      </label>
      <label class="flex flex-col gap-1 text-sm text-neutral-700 sm:col-span-2">
        Remarque (facultatif)
        <input v-model="note" type="text" class="rounded border border-neutral-300 bg-white px-2 py-1.5 text-neutral-900">
      </label>
      <p v-if="retroactif" class="rounded bg-amber-50 px-3 py-2 text-sm text-amber-800 sm:col-span-2">
        Cette valeur modifie les décomptes déjà produits depuis le {{ fmtJour(depuis) }}.
      </p>
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

    <ul v-if="afficherHistorique" class="mt-3 divide-y divide-neutral-100 rounded-md border border-neutral-200 text-sm">
      <li v-for="v in historique" :key="v.id" class="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
        <span class="text-neutral-800">
          {{ fmtJour(v.valid_from) }} : <strong>{{ fmtValeur(param, v.valeur) }}</strong>
          <span v-if="v.note" class="text-neutral-500"> — {{ v.note }}</span>
        </span>
        <span class="flex items-center gap-3 text-xs text-neutral-500">
          <span v-if="v.updated_by">par {{ v.updated_by }}</span>
          <button
            v-if="peutModifier"
            type="button"
            class="text-red-700 hover:underline"
            @click="supprimer(v)"
          >{{ aConfirmer === v.id ? 'Confirmer la suppression' : 'Supprimer' }}</button>
        </span>
      </li>
    </ul>
  </div>
</template>
