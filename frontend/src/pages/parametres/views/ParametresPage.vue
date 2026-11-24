<script setup lang="ts">
/**
 * Paramètres du décompte du site affiché : prix, TVA, clé de la PAC,
 * location de compteurs, buanderie, frais accessoires. Le responsable du
 * site (compte `user`) les modifie ; l'onglet « Valeurs globales » (TVA
 * commune à tous les sites) n'est proposé qu'à l'admin. Les montants sont
 * calculés côté serveur, à l'export du décompte.
 */

import { computed, ref, watch } from 'vue'
import { authState } from '@shared/auth'
import { useSiteActif } from '@shared/composables/useSiteActif'
import type { EtatParametres, Frais, ParametreCatalogue } from '../types/parametres'
import { fetchCatalogue, fetchFrais, fetchParametres } from '../api/parametres'
import ParametreLigne from '../components/ParametreLigne.vue'
import FraisCarte from '../components/FraisCarte.vue'
import { fmtNombre } from '../utils/format'

const { site } = useSiteActif()

const catalogue = ref<ParametreCatalogue[]>([])
const etat = ref<EtatParametres | null>(null)
const frais = ref<Frais[]>([])
const chargement = ref(false)
const erreur = ref('')
const onglet = ref<'site' | 'global'>('site')

const estAdmin = computed(() => authState.role === 'admin')

const THEMES: { cle: ParametreCatalogue['theme']; titre: string; texte: string }[] = [
  {
    cle: 'electricite',
    titre: 'Électricité et TVA',
    texte: "Prix saisis HT. La TVA s'ajoute au réseau, à la location et à l'énergie de la "
      + 'buanderie ; au solaire seulement si la case est cochée.',
  },
  {
    cle: 'pac',
    titre: 'Pompe à chaleur',
    texte: "Le coût de la chaleur est l'électricité du compteur de la PAC. Cette part va au "
      + "chauffage, au prorata des kWh th de chaque lot ; le reste va à l'eau chaude, au prorata des m³.",
  },
  {
    cle: 'location',
    titre: 'Location de compteurs',
    texte: 'Forfait annuel par lot, quel que soit le nombre de compteurs, au prorata de la '
      + 'période. Ni les Communs ni la PAC ne paient de location. « Aucune » : pas de location.',
  },
  {
    cle: 'buanderie',
    titre: 'Buanderie',
    texte: "Chaque cycle est facturé à ce prix, plus l'énergie consommée au prix du kWh réseau.",
  },
]

const sansPac = computed(() => !!etat.value && !etat.value.zones.some((z) => z.zone === 'PAC'))

function parametresDuTheme(theme: ParametreCatalogue['theme']) {
  return catalogue.value.filter((p) => p.theme === theme)
}

function aide(p: ParametreCatalogue): string | undefined {
  if (p.cle === 'buanderie_prix_cycle') {
    const prix = etat.value?.en_vigueur.prix_reseau?.valeur
    return 'Montant final, sans TVA. Énergie au prix du kWh réseau'
      + (typeof prix === 'number' ? ` (${fmtNombre(prix)} CHF HT).` : '.')
  }
  if (p.cle === 'taux_tva' && onglet.value === 'site') {
    return 'Une valeur saisie ici remplace, pour ce site, la valeur commune à tous les sites.'
  }
  return undefined
}

const globaux = computed(() => catalogue.value.filter((p) => p.globale))

async function charger(nom: string) {
  if (!nom) return
  chargement.value = true
  erreur.value = ''
  try {
    const [cat, e, f] = await Promise.all([
      catalogue.value.length ? Promise.resolve(catalogue.value) : fetchCatalogue(),
      fetchParametres(nom),
      fetchFrais(nom),
    ])
    if (nom !== site.value) return  // site changé pendant le chargement
    catalogue.value = cat
    etat.value = e
    frais.value = f
  } catch (err) {
    console.error(err)
    erreur.value = 'Impossible de charger les paramètres de ce site.'
  } finally {
    chargement.value = false
  }
}

watch(site, (nom) => {
  etat.value = null
  frais.value = []
  void charger(nom)
}, { immediate: true })
</script>

<template>
  <div class="w-full px-4 py-6">
    <main class="mx-auto min-w-0 w-full max-w-4xl">
      <h1 class="mb-1 text-xl font-bold text-neutral-900">Paramètres</h1>
      <p class="mb-6 text-sm text-neutral-500">
        Prix et consignes du décompte<span v-if="site"> de {{ site }}</span>. Chaque valeur
        s'applique à partir de sa date, jusqu'à la suivante. Le décompte d'une période prend
        les valeurs en vigueur à son premier jour.
      </p>

      <p v-if="erreur" class="mb-4 rounded bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">{{ erreur }}</p>
      <p v-else-if="chargement && !etat" class="text-sm text-neutral-500">Chargement…</p>

      <template v-if="etat">
        <div v-if="estAdmin" class="mb-4 flex gap-1 border-b border-neutral-200" role="tablist">
          <button
            v-for="o in [{ cle: 'site', label: site }, { cle: 'global', label: 'Valeurs globales' }] as const"
            :key="o.cle"
            type="button"
            role="tab"
            :aria-selected="onglet === o.cle"
            :class="['-mb-px border-b-2 px-3 py-2 text-sm font-medium',
                     onglet === o.cle ? 'border-blue-600 text-blue-700' : 'border-transparent text-neutral-600 hover:text-neutral-900']"
            @click="onglet = o.cle"
          >{{ o.label }}</button>
        </div>

        <div v-if="onglet === 'site'" class="flex flex-col gap-4">
          <p v-if="!etat.peut_modifier" class="rounded bg-neutral-100 px-3 py-2 text-sm text-neutral-700">
            Lecture seule : vous ne pouvez pas modifier les paramètres de ce site.
          </p>
          <section
            v-for="t in THEMES"
            :key="t.cle"
            class="rounded-lg border border-neutral-200 bg-white p-4 sm:p-5"
          >
            <h2 class="text-base font-semibold text-neutral-900">{{ t.titre }}</h2>
            <p class="mt-1 text-sm text-neutral-500">{{ t.texte }}</p>
            <p v-if="t.cle === 'pac' && sansPac" class="mt-2 text-sm text-amber-700">
              Aucun compteur de PAC sur ce site : le chauffage et l'eau chaude n'ont pas de montant.
            </p>
            <ParametreLigne
              v-for="p in parametresDuTheme(t.cle)"
              :key="p.cle"
              :param="p"
              :etat="etat"
              :global="false"
              :aide="aide(p)"
              @maj="etat = $event"
            />
          </section>
          <FraisCarte
            :miniserver="etat.miniserver"
            :frais="frais"
            :zones="etat.zones"
            :peut-modifier="etat.peut_modifier"
            @maj="frais = $event"
          />
        </div>

        <section v-else class="rounded-lg border border-neutral-200 bg-white p-4 sm:p-5">
          <h2 class="text-base font-semibold text-neutral-900">Valeurs communes à tous les sites</h2>
          <p class="mt-1 text-sm text-neutral-500">
            Utilisées par tout site qui n'a pas sa propre valeur. Réservées à l'administrateur.
          </p>
          <ParametreLigne
            v-for="p in globaux"
            :key="p.cle"
            :param="p"
            :etat="etat"
            :global="true"
            @maj="etat = $event"
          />
        </section>
      </template>
    </main>
  </div>
</template>
