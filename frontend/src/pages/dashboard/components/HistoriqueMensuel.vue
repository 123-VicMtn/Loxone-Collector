<script setup lang="ts">
/**
 * Historique mois par mois de l'immeuble affiché : consommation comparée
 * à la production solaire, et devenir de cette production. Lit
 * GET /api/historique : mêmes chiffres que le décompte pour un mois
 * complet, et les mois incomplets (début des relevés, mois en cours,
 * relevés arrêtés) plutôt qu'un mois vide, signalés d'une étoile.
 */

import { computed, ref, watch } from 'vue'
import { useSiteActif } from '@shared/composables/useSiteActif'
import { fmtDateShort } from '@shared/format'
import { fetchHistorique } from '../api/historique'
import type { HistoriqueMois } from '../types/historique'
import EvolutionChart from './EvolutionChart.vue'
import SolaireChart from './SolaireChart.vue'

const { site } = useSiteActif()
const mois = ref<HistoriqueMois[] | null>(null)
const chargement = ref(false)
const erreur = ref('')
let requete = 0

async function charger(nom: string) {
  const numero = ++requete
  chargement.value = true
  erreur.value = ''
  try {
    const data = await fetchHistorique(nom)
    if (numero !== requete) return
    mois.value = data
  } catch (err) {
    if (numero !== requete) return
    console.error(err)
    mois.value = null
    erreur.value = 'L\'historique mensuel n\'a pas pu être chargé. Réessayez dans quelques minutes.'
  } finally {
    if (numero === requete) chargement.value = false
  }
}

watch(site, (nom) => {
  if (nom) void charger(nom)
}, { immediate: true })

/** « Octobre 2026 : du 16.10 au 28.10 (en cours) » pour chaque mois étoilé. */
const incomplets = computed(() => (mois.value || [])
  .filter((m) => m.partiel || m.en_cours)
  .map((m) => {
    const fin = fmtDateShort(m.fin_ts - (m.en_cours ? 0 : 1))
    const etat = m.releves_arretes ? ' (plus de relevé ensuite)' : m.en_cours ? ' (en cours)' : ''
    return `${m.label} : du ${fmtDateShort(m.debut_ts)} au ${fin}${etat}`
  }))

/** Horizon : ses compteurs de bâtiment ne permettent pas de séparer
 * l'autoconsommé de l'injecté. */
const devenirCalculable = computed(() => (mois.value || []).some((m) => m.autoconsommation !== null))
</script>

<template>
  <section class="mt-6 flex flex-col gap-4">
    <h2 class="text-base font-semibold text-neutral-800">Historique mensuel</h2>
    <p v-if="chargement && !mois" class="text-sm text-neutral-500">Chargement de l'historique…</p>
    <p v-else-if="erreur" class="text-sm text-red-700">{{ erreur }}</p>
    <p v-else-if="mois && !mois.length" class="text-sm text-neutral-500">
      Pas encore de relevés mensuels sur ce site.
    </p>
    <template v-else-if="mois">
      <div class="w-full rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
        <h3 class="mb-1 text-sm font-semibold text-neutral-700">Consommation mensuelle de l'immeuble</h3>
        <p class="mb-2 text-sm text-neutral-500">Comparée à la production solaire.</p>
        <EvolutionChart :mois="mois" />
        <p class="mt-4 text-sm text-neutral-500">
          Chaque mois, la consommation de l'immeuble (somme des zones) est
          mise à côté de la production solaire du bâtiment. Les deux barres
          ne s'additionnent pas.
        </p>
      </div>
      <div class="w-full rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
        <h3 class="mb-1 text-sm font-semibold text-neutral-700">Devenir de la production solaire</h3>
        <p class="mb-2 text-sm text-neutral-500">Consommé sur place ou réinjecté.</p>
        <template v-if="devenirCalculable">
          <SolaireChart :mois="mois" />
          <p class="mt-4 text-sm text-neutral-500">
            La hauteur totale de chaque barre est la production photovoltaïque du
            mois. En vert, ce qui a été consommé dans l'immeuble ; en bleu, le
            surplus réinjecté au réseau.
          </p>
        </template>
        <p v-else class="text-sm text-neutral-500">
          Pas calculable sur ce site : ses compteurs ne permettent pas de séparer
          le solaire consommé sur place de celui réinjecté au réseau.
        </p>
      </div>
      <div v-if="incomplets.length" class="text-sm text-neutral-500">
        <p>* Mois incomplets :</p>
        <ul class="ml-4 list-disc">
          <li v-for="ligne in incomplets" :key="ligne">{{ ligne }}</li>
        </ul>
      </div>
    </template>
  </section>
</template>
