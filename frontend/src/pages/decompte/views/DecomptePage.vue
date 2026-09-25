<script setup lang="ts">
/**
 * Page /decompte. Un seul appel à /api/decompte ramène tous les mois
 * disponibles : changer de mois dans le calendrier ne re-interroge pas le
 * serveur. Le mois en cours est déjà borné au dernier relevé connu
 * (billing.reading_delta lit le dernier point avant la fin de période).
 */

import { computed, onMounted, ref } from 'vue'
import { fetchDecompte, fetchMiniservers } from '../api/decompte'
import type { DecomptePayload, Period } from '../types/decompte'
import { fmtDay, fmtPeriodBounds } from '../utils/format'
import { useHealthFooter } from '@shared/composables/useHealthFooter'
import { authState } from '@shared/auth'
import AuthStatus from '@shared/components/AuthStatus.vue'

import Card from '../components/Card.vue'
import GlobalBanner from '../components/GlobalBanner.vue'
import PeriodeKpis from '../components/PeriodeKpis.vue'
import ZoneTable from '../components/ZoneTable.vue'
import EvolutionChart from '../components/EvolutionChart.vue'
import SolaireChart from '../components/SolaireChart.vue'

const SITE_STORAGE_KEY = 'decompte-site'

const { text: healthText } = useHealthFooter()

const sites = ref<string[]>([])
const currentSite = ref('')
const payload = ref<DecomptePayload | null>(null)

const periodeKey = ref('')

const loading = ref(true)
const loadingMessage = ref('Chargement du décompte…')
const errored = ref(false)
const empty = ref(false)

const selectedPeriod = computed<Period | null>(() => {
  if (!payload.value) return null
  return payload.value.periodes.find((p) => p.key === periodeKey.value) || null
})

const monthMin = computed(() => payload.value?.periodes[0]?.key ?? '')
const monthMax = computed(() => {
  const list = payload.value?.periodes
  return list && list.length ? list[list.length - 1].key : ''
})

const periodeEnCours = computed(() => {
  if (!payload.value || !periodeKey.value) return false
  return payload.value.batiment.periodes[periodeKey.value]?.en_cours ?? false
})

/** Dernier relevé réellement utilisé sur le mois choisi (zones + production). */
const dernierReleveTs = computed<number | null>(() => {
  if (!payload.value || !periodeKey.value) return null
  const stamps: number[] = []
  for (const z of payload.value.zones) {
    const e = z.periodes[periodeKey.value]
    if (!e) continue
    if (e.reseau.releve_fin_ts) stamps.push(e.reseau.releve_fin_ts)
    if (e.solaire.releve_fin_ts) stamps.push(e.solaire.releve_fin_ts)
  }
  const prod = payload.value.batiment.periodes[periodeKey.value]?.production.releve_fin_ts
  if (prod) stamps.push(prod)
  return stamps.length ? Math.max(...stamps) : null
})

const periodeCouverture = computed(() => {
  if (!selectedPeriod.value) return '—'
  if (periodeEnCours.value && dernierReleveTs.value) {
    return `${fmtDay(selectedPeriod.value.start)} → ${fmtDay(dernierReleveTs.value)} (dernier relevé)`
  }
  return fmtPeriodBounds(selectedPeriod.value)
})

function hasZoneData(p: DecomptePayload, periodKey: string): boolean {
  return p.zones.some((z) => {
    const e = z.periodes[periodKey]
    return e && e.total !== null
  })
}

async function loadSite(site: string) {
  currentSite.value = site
  localStorage.setItem(SITE_STORAGE_KEY, site)

  loading.value = true
  errored.value = false
  empty.value = false
  loadingMessage.value = 'Chargement du décompte…'

  try {
    const data = await fetchDecompte({ miniserver: site })
    payload.value = data

    if (!data.periodes.length || !data.zones.length) {
      loading.value = false
      empty.value = true
      loadingMessage.value =
        'Aucune donnée exploitable pour un décompte sur ce site : il faut au moins ' +
        'une zone avec des compteurs cumulatifs (state « total ») en base.'
      return
    }

    const dernier = (list: Period[]) => list[list.length - 1]
    const avecDonnees = (list: Period[]) => list.filter((p) => hasZoneData(data, p.key))
    const termines = data.periodes.filter((p) => !data.batiment.periodes[p.key].en_cours)
    const defaut = dernier(avecDonnees(termines)) || dernier(avecDonnees(data.periodes)) ||
      dernier(termines) || dernier(data.periodes)
    periodeKey.value = defaut.key

    loading.value = false
  } catch (err) {
    loading.value = false
    errored.value = true
    loadingMessage.value = `Impossible de charger le décompte : ${(err as Error).message}`
  }
}

function onMonthInput(value: string) {
  if (!payload.value) return
  if (payload.value.periodes.some((p) => p.key === value)) periodeKey.value = value
}

onMounted(async () => {
  try {
    const list = await fetchMiniservers()
    sites.value = list
    if (!list.length) {
      loading.value = false
      errored.value = true
      loadingMessage.value = 'Aucun site (miniserver) configuré.'
      return
    }
    const saved = localStorage.getItem(SITE_STORAGE_KEY)
    const initial = saved && list.includes(saved) ? saved : list[0]
    await loadSite(initial)
  } catch (err) {
    loading.value = false
    errored.value = true
    loadingMessage.value = `Impossible de charger la liste des sites : ${(err as Error).message}`
  }
})
</script>

<template>
  <div class="mx-auto max-w-6xl space-y-6 px-4 py-8">
    <header class="flex flex-wrap items-start justify-between gap-4 border-b border-neutral-200 pb-6">
      <div>
        <h1 class="text-2xl font-bold text-neutral-900">Décompte de charges</h1>
        <p class="mt-1 text-sm text-neutral-500">
          Consommation de chaque zone répartie en part réseau et part solaire
          autoconsommée — heure locale Europe/Zurich.
        </p>
      </div>
      <div class="flex flex-col items-end gap-2">
        <AuthStatus />
        <nav class="flex gap-4 text-sm text-blue-600">
          <router-link to="/" class="hover:underline">← Dashboard</router-link>
          <router-link v-if="authState.role === 'admin'" to="/admin" class="hover:underline">Classification</router-link>
        </nav>
      </div>
    </header>

    <label v-if="sites.length > 1" class="flex w-fit flex-col text-sm text-neutral-600">
      Site
      <select
        class="mt-1 rounded border border-neutral-300 px-2 py-1"
        :value="currentSite"
        @change="loadSite(($event.target as HTMLSelectElement).value)"
      >
        <option v-for="s in sites" :key="s" :value="s">{{ s }}</option>
      </select>
    </label>

    <GlobalBanner v-if="payload && !loading && !empty" :payload="payload" />

    <p v-if="loading || empty" class="text-sm text-neutral-500">{{ loadingMessage }}</p>

    <template v-else-if="payload && !errored">
      <Card>
        <div class="flex flex-wrap items-end gap-4">
          <label class="flex flex-col text-sm text-neutral-600">
            Mois
            <input
              type="month"
              class="mt-1 rounded border border-neutral-300 px-2 py-1"
              :min="monthMin"
              :max="monthMax"
              :value="periodeKey"
              @input="onMonthInput(($event.target as HTMLInputElement).value)"
            >
          </label>
          <div class="flex flex-col text-sm text-neutral-600">
            Période couverte
            <span class="mt-1 py-1 font-medium text-neutral-900">{{ periodeCouverture }}</span>
          </div>
        </div>

        <div class="mt-6">
          <PeriodeKpis :payload="payload" :period-key="periodeKey" />
        </div>
      </Card>

      <Card title="Décompte par zone" :hint="selectedPeriod?.label">
        <ZoneTable :payload="payload" :period-key="periodeKey" />
        <p class="mt-4 text-sm text-neutral-500">
          La consommation de chaque zone est scindée en deux : les kWh
          <strong>achetés au réseau</strong> et les kWh
          <strong>solaires autoconsommés</strong>. L'autoproduction est la part
          de cette consommation couverte par le solaire.
        </p>
      </Card>

      <Card title="Consommation mensuelle de l'immeuble" hint="comparée à la production solaire">
        <EvolutionChart :payload="payload" :periodes="payload.periodes" />
        <p class="mt-4 text-sm text-neutral-500">
          Chaque mois, la consommation de l'immeuble (somme des zones) est
          mise à côté de la production solaire du bâtiment. Les deux barres
          ne s'additionnent pas.
        </p>
      </Card>

      <Card title="Devenir de la production solaire" hint="consommé sur place ou réinjecté">
        <SolaireChart :payload="payload" :periodes="payload.periodes" />
        <p class="mt-4 text-sm text-neutral-500">
          La hauteur totale de chaque barre est la production photovoltaïque du
          mois. En vert, ce qui a été consommé dans l'immeuble ; en bleu, le
          surplus réinjecté au réseau.
        </p>
      </Card>
    </template>

    <p v-else-if="errored" class="text-sm text-red-600">{{ loadingMessage }}</p>

    <footer class="whitespace-pre-line border-t border-neutral-200 pt-4 text-xs text-neutral-400">
      {{ healthText }}
    </footer>
  </div>
</template>
