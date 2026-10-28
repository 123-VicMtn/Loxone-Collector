<script setup lang="ts">
/**
 * Historique mois par mois de l'immeuble affiché : consommation comparée
 * à la production solaire, et devenir de cette production. Mêmes chiffres
 * que le décompte (GET /api/decompte, tous les mois du site) ; ces deux
 * graphiques étaient sur la page /decompte jusqu'au 2026-10-28.
 */

import { ref, watch } from 'vue'
import { useSiteActif } from '@shared/composables/useSiteActif'
import { fetchDecompte } from '../../decompte/api/decompte'
import type { DecomptePayload } from '../../decompte/types/decompte'
import EvolutionChart from './EvolutionChart.vue'
import SolaireChart from './SolaireChart.vue'

const { site } = useSiteActif()
const payload = ref<DecomptePayload | null>(null)
const chargement = ref(false)
const erreur = ref('')
let requete = 0

async function charger(nom: string) {
  const numero = ++requete
  chargement.value = true
  erreur.value = ''
  try {
    const data = await fetchDecompte({ miniserver: nom })
    if (numero !== requete) return
    payload.value = data
  } catch (err) {
    if (numero !== requete) return
    console.error(err)
    payload.value = null
    erreur.value = 'L\'historique mensuel n\'a pas pu être chargé. Réessayez dans quelques minutes.'
  } finally {
    if (numero === requete) chargement.value = false
  }
}

watch(site, (nom) => {
  if (nom) void charger(nom)
}, { immediate: true })
</script>

<template>
  <section class="mt-6 flex flex-col gap-4">
    <h2 class="text-base font-semibold text-neutral-800">Historique mensuel</h2>
    <p v-if="chargement && !payload" class="text-sm text-neutral-500">Chargement de l'historique…</p>
    <p v-else-if="erreur" class="text-sm text-red-700">{{ erreur }}</p>
    <p v-else-if="payload && !payload.periodes.length" class="text-sm text-neutral-500">
      Pas encore de relevés mensuels sur ce site.
    </p>
    <template v-else-if="payload">
      <div class="w-full rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
        <h3 class="mb-1 text-sm font-semibold text-neutral-700">Consommation mensuelle de l'immeuble</h3>
        <p class="mb-2 text-sm text-neutral-500">Comparée à la production solaire.</p>
        <EvolutionChart :payload="payload" :periodes="payload.periodes" />
        <p class="mt-4 text-sm text-neutral-500">
          Chaque mois, la consommation de l'immeuble (somme des zones) est
          mise à côté de la production solaire du bâtiment. Les deux barres
          ne s'additionnent pas.
        </p>
      </div>
      <div class="w-full rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
        <h3 class="mb-1 text-sm font-semibold text-neutral-700">Devenir de la production solaire</h3>
        <p class="mb-2 text-sm text-neutral-500">Consommé sur place ou réinjecté.</p>
        <SolaireChart :payload="payload" :periodes="payload.periodes" />
        <p class="mt-4 text-sm text-neutral-500">
          La hauteur totale de chaque barre est la production photovoltaïque du
          mois. En vert, ce qui a été consommé dans l'immeuble ; en bleu, le
          surplus réinjecté au réseau.
        </p>
      </div>
    </template>
  </section>
</template>
