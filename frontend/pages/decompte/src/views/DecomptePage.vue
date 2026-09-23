<script setup lang="ts">
/**
 * Page /decompte -- port Vue de static/js/decompte/main.js. Un seul appel à
 * /api/decompte ramène tous les mois disponibles : changer de mois dans le
 * sélecteur ne re-interroge pas le serveur, la réactivité Vue re-rend
 * simplement les tuiles/tableaux/graphs qui dépendent de `periodeKey`.
 * Les graphs d'évolution/solaire/taux couvrent la plage choisie dans
 * « Historique affiché » (`historiqueKey`) et sont indépendants du mois
 * facturé.
 */

import { computed, onMounted, ref } from 'vue'
import { fetchDecompte, fetchMiniservers, fetchTarifs } from '../api/decompte'
import type { DecomptePayload, Period, Tarif } from '../types/decompte'
import { fmtPeriodBounds } from '../utils/format'
import { useHealthFooter } from '@shared/composables/useHealthFooter'

import Card from '../components/Card.vue'
import GlobalBanner from '../components/GlobalBanner.vue'
import PeriodeKpis from '../components/PeriodeKpis.vue'
import ZoneTable from '../components/ZoneTable.vue'
import BatimentTable from '../components/BatimentTable.vue'
import ControleTable from '../components/ControleTable.vue'
import SourcesTable from '../components/SourcesTable.vue'
import TarifsPanel from '../components/TarifsPanel.vue'
import EvolutionChart from '../components/EvolutionChart.vue'
import ZonesChart from '../components/ZonesChart.vue'
import SolaireChart from '../components/SolaireChart.vue'
import TauxChart from '../components/TauxChart.vue'

const SITE_STORAGE_KEY = 'decompte-site'

const { text: healthText } = useHealthFooter()

const sites = ref<string[]>([])
const currentSite = ref('')
const payload = ref<DecomptePayload | null>(null)
const tarifs = ref<Tarif[]>([])

const periodeKey = ref('')
const historiqueKey = ref('')

const loading = ref(true)
const loadingMessage = ref('Chargement du décompte…')
const errored = ref(false)

const selectedPeriod = computed<Period | null>(() => {
  if (!payload.value) return null
  return payload.value.periodes.find((p) => p.key === periodeKey.value) || null
})

/** Mois affichés dans les graphs et tableaux d'immeuble : à partir de celui
 * choisi dans « Historique affiché ». Indépendant du mois facturé. */
const visiblePeriods = computed<Period[]>(() => {
  if (!payload.value) return []
  const i = payload.value.periodes.findIndex((p) => p.key === historiqueKey.value)
  return i < 0 ? payload.value.periodes : payload.value.periodes.slice(i)
})

const periodeOptions = computed(() => {
  if (!payload.value) return []
  return payload.value.periodes.map((p) => ({
    key: p.key,
    text: p.label + (payload.value!.batiment.periodes[p.key].en_cours ? ' (en cours)' : ''),
  }))
})

const historiqueOptions = computed(() => {
  if (!payload.value) return []
  return payload.value.periodes.map((p) => ({ key: p.key, text: `depuis ${p.label}` }))
})

/** Un mois a des données dès qu'au moins une zone a une consommation
 * calculable. Sert à ne pas ouvrir la page sur une série de barres vides :
 * les compteurs du Moniteur de flux d'énergie n'existent que depuis
 * octobre 2025. */
function hasZoneData(p: DecomptePayload, periodKey: string): boolean {
  return p.zones.some((z) => {
    const e = z.periodes[periodKey]
    return e && e.total !== null
  })
}

async function reloadDecompteAndTarifs() {
  if (!currentSite.value) return
  const [data, t] = await Promise.all([
    fetchDecompte({ miniserver: currentSite.value }),
    fetchTarifs(currentSite.value),
  ])
  payload.value = data
  tarifs.value = t
}

/** Charge et affiche tout le décompte d'UN site -- appelé au chargement de
 * la page et à chaque changement de site dans le sélecteur. Un décompte est
 * toujours celui d'un seul site à la fois : mélanger deux immeubles
 * fausserait les montants facturés (voir CLAUDE.md, "Décompte de charges"
 * et app.py::_resolve_miniserver). */
async function loadSite(site: string) {
  currentSite.value = site
  localStorage.setItem(SITE_STORAGE_KEY, site)

  loading.value = true
  errored.value = false
  loadingMessage.value = 'Chargement du décompte…'

  try {
    const [data, t] = await Promise.all([
      fetchDecompte({ miniserver: site }),
      fetchTarifs(site),
    ])
    payload.value = data
    tarifs.value = t

    if (!data.periodes.length || !data.zones.length) {
      loadingMessage.value =
        'Aucune donnée exploitable pour un décompte sur ce site : il faut au moins ' +
        'une zone avec des compteurs cumulatifs (state « total ») en base.'
      return
    }

    // Mois proposé par défaut : le dernier mois TERMINÉ qui a des données,
    // c'est-à-dire celui qu'on cherche à facturer -- ni le mois en cours
    // (incomplet), ni un mois antérieur aux compteurs. Les replis successifs
    // couvrent une installation trop récente pour avoir un mois terminé.
    const dernier = (list: Period[]) => list[list.length - 1]
    const avecDonnees = (list: Period[]) => list.filter((p) => hasZoneData(data, p.key))
    const termines = data.periodes.filter((p) => !data.batiment.periodes[p.key].en_cours)
    const defaut = dernier(avecDonnees(termines)) || dernier(avecDonnees(data.periodes)) ||
      dernier(termines) || dernier(data.periodes)
    periodeKey.value = defaut.key

    const premierAvecDonnees = data.periodes.find((p) => hasZoneData(data, p.key))
    historiqueKey.value = (premierAvecDonnees || data.periodes[0]).key

    loading.value = false
  } catch (err) {
    loading.value = false
    errored.value = true
    loadingMessage.value = `Impossible de charger le décompte : ${(err as Error).message}`
  }
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
          autoconsommée, mois par mois — heure locale Europe/Zurich.
        </p>
      </div>
      <nav class="flex gap-4 text-sm text-blue-600">
        <a href="/" class="hover:underline">← Dashboard</a>
        <a href="/admin" class="hover:underline">⚙ Classification</a>
      </nav>
    </header>

    <GlobalBanner v-if="payload && !loading" :payload="payload" />

    <p v-if="loading" class="text-sm text-neutral-500">{{ loadingMessage }}</p>

    <template v-else-if="payload && !errored">
      <Card>
        <div class="flex flex-wrap items-end gap-4">
          <label v-if="sites.length > 1" class="flex flex-col text-sm text-neutral-600">
            Site
            <select
              class="mt-1 rounded border border-neutral-300 px-2 py-1"
              :value="currentSite"
              @change="loadSite(($event.target as HTMLSelectElement).value)"
            >
              <option v-for="s in sites" :key="s" :value="s">{{ s }}</option>
            </select>
          </label>
          <label class="flex flex-col text-sm text-neutral-600">
            Mois à facturer
            <select v-model="periodeKey" class="mt-1 rounded border border-neutral-300 px-2 py-1">
              <option v-for="o in periodeOptions" :key="o.key" :value="o.key">{{ o.text }}</option>
            </select>
          </label>
          <label class="flex flex-col text-sm text-neutral-600">
            Historique affiché (graphs)
            <select v-model="historiqueKey" class="mt-1 rounded border border-neutral-300 px-2 py-1">
              <option v-for="o in historiqueOptions" :key="o.key" :value="o.key">{{ o.text }}</option>
            </select>
          </label>
          <div class="flex flex-col text-sm text-neutral-600">
            Période couverte
            <span class="mt-1 py-1 font-medium text-neutral-900">{{ fmtPeriodBounds(selectedPeriod) }}</span>
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
          <strong>achetés au réseau</strong> (compteur « Grid » du Moniteur de
          flux d'énergie Loxone) et les kWh <strong>solaires autoconsommés</strong>
          (compteur « Solaire », que Loxone expose aussi sous le nom
          <em>selfConsumption</em>). Les deux sont facturés à des prix
          différents, comme dans un regroupement de consommation propre (RCP).
        </p>
      </Card>

      <Card title="Consommation mensuelle de l'immeuble" hint="part réseau et part solaire">
        <EvolutionChart :payload="payload" :periodes="visiblePeriods" />
        <p class="mt-4 text-sm text-neutral-500">
          Chaque barre est un mois. La hauteur totale est l'énergie consommée
          par l'ensemble des zones ; la part verte est celle couverte par le
          solaire de l'immeuble, la part orange celle achetée au réseau.
        </p>
      </Card>

      <Card title="Répartition par zone" :hint="selectedPeriod?.label">
        <ZonesChart :payload="payload" :period-key="periodeKey" />
      </Card>

      <Card title="Devenir de la production solaire" hint="consommé sur place ou réinjecté">
        <SolaireChart :payload="payload" :periodes="visiblePeriods" />
        <p class="mt-4 text-sm text-neutral-500">
          La hauteur totale de chaque barre est la production photovoltaïque du
          mois. En vert, ce qui a été consommé dans l'immeuble et refacturé aux
          zones ; en bleu, le surplus réinjecté au réseau.
        </p>

        <h3 class="mt-8 mb-1 text-lg font-semibold text-neutral-900">
          Les deux taux d'autonomie
          <span class="text-sm font-normal text-neutral-500">ils évoluent en sens inverse, et c'est normal</span>
        </h3>
        <TauxChart :payload="payload" :periodes="visiblePeriods" />
        <p class="mt-4 text-sm text-neutral-500">
          Le <strong>taux d'autoproduction</strong> (vert) est la part de la
          consommation couverte par le solaire : il monte en été, quand le
          soleil produit plus. C'est l'indicateur qui parle à un propriétaire.
          Le <strong>taux d'autoconsommation</strong> (bleu pointillé) est la
          part de la production consommée sur place plutôt que réinjectée : il
          <em>baisse</em> en été, parce qu'on produit alors beaucoup plus que ce
          que l'immeuble peut absorber. Les deux sont corrects — ils ne
          répondent simplement pas à la même question.
        </p>

        <div class="mt-6">
          <BatimentTable :payload="payload" :periodes="visiblePeriods" />
        </div>
      </Card>

      <Card title="Tarifs" hint="appliqués aux mois commençant après leur date de prise d'effet">
        <TarifsPanel :tarifs="tarifs" :miniserver="currentSite" @changed="reloadDecompteAndTarifs" />
      </Card>

      <details class="rounded-lg border border-neutral-200 bg-white p-6 shadow-sm">
        <summary class="cursor-pointer text-lg font-semibold text-neutral-900">
          Compteurs de contrôle et correspondance des séries
        </summary>
        <p class="mt-4 text-sm text-neutral-500">
          Chaque zone possède aussi un compteur plus ancien (« Appartement 1 »,
          « Commerce »…), posé avant les compteurs du Moniteur de flux
          d'énergie en octobre 2025. Il mesure un <strong>périmètre
          différent</strong> — sur App 1 en août 2026 il enregistre 6,3 kWh/jour
          quand le seul compteur réseau en enregistre 7,0 — et n'est donc
          <strong>pas</strong> utilisé pour facturer. Il est affiché ici à titre
          de contrôle.
        </p>
        <div class="mt-4">
          <ControleTable :payload="payload" :periodes="visiblePeriods" />
        </div>

        <h4 class="mt-8 mb-1 font-semibold text-neutral-900">Quelle série alimente quelle colonne</h4>
        <p class="text-sm text-neutral-500">
          Une correspondance fausse se corrige dans
          <a href="/admin" class="text-blue-600 hover:underline">⚙ Classification</a>
          (le type de ressource d'un capteur), pas ici.
        </p>
        <div class="mt-4">
          <SourcesTable :payload="payload" />
        </div>
      </details>
    </template>

    <p v-else-if="errored" class="text-sm text-red-600">{{ loadingMessage }}</p>

    <footer class="whitespace-pre-line border-t border-neutral-200 pt-4 text-xs text-neutral-400">
      {{ healthText }}
    </footer>
  </div>
</template>
