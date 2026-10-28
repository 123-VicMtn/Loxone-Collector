<script setup lang="ts">
/**
 * Page /decompte. Un seul calendrier (PeriodPicker) choisit la période :
 * un mois entier ou des dates libres. Le bandeau KPI, le tableau, les
 * messages et les téléchargements suivent cette période. Les graphiques
 * mensuels de l'immeuble sont sur la page Graphiques (HistoriqueMensuel).
 *
 * La liste « Zone » restreint toute la page à un appartement (départ d'un
 * locataire) : KPI, tableau, messages et téléchargements ne portent que
 * sur lui.
 *
 * Un appel à /api/decompte ramène tous les mois disponibles : choisir un
 * mois entier ne re-interroge pas le serveur. Des dates libres demandent
 * leur décompte au serveur (`du` / `au`, même calcul que l'export). Le mois
 * en cours est borné au dernier relevé connu.
 *
 * Le serveur ne lit que sa base (synchro des Statistics chaque heure) ;
 * « Actualiser » la fait relire tout de suite pour ce site.
 */

import { computed, onMounted, ref, watch } from 'vue'
import {
  actualiserDecompte, downloadDecompte, downloadDecomptePeriode, fetchDecompte, fetchDecomptePlage,
} from '../api/decompte'
import type { DecomptePayload, Period, RepartitionInfo } from '../types/decompte'
import { fmtDay, fmtPeriodBounds } from '../utils/format'
import { fmtDateTimeShort } from '@shared/format'
import { useSiteActif } from '@shared/composables/useSiteActif'

import Card from '../components/Card.vue'
import GlobalBanner from '../components/GlobalBanner.vue'
import PeriodeKpis from '../components/PeriodeKpis.vue'
import PeriodPicker, { type Plage } from '../components/PeriodPicker.vue'
import ZoneTable from '../components/ZoneTable.vue'

const SITE_STORAGE_KEY = 'decompte-site'

const { site: siteActif, loadSites } = useSiteActif()

const currentSite = ref('')
/** Tous les mois du site : la période, quand c'est un mois entier. */
const payload = ref<DecomptePayload | null>(null)
/** Mois par défaut à l'ouverture du site. */
const periodeKey = ref('')

/** Période choisie dans le calendrier (YYYY-MM-DD, `au` inclus). */
const selection = ref<Plage>({ du: '', au: '' })
/** Décompte de dates libres, demandé au serveur. */
const payloadPlage = ref<DecomptePayload | null>(null)
const chargementPlage = ref(false)
const erreurPlage = ref('')
let requetePlage = 0

const downloading = ref(false)
const downloadError = ref('')
const confirmFormat = ref<'xlsx' | 'csv' | null>(null)
/** Code de la zone affichée seule (départ d'un locataire), '' = tout l'immeuble. */
const zoneChoisie = ref('')

const actualisation = ref(false)
const actualisationErreur = ref('')

const aJourAu = computed(() => {
  const ts = payload.value?.statistiques_jusqu_au
  return ts ? fmtDateTimeShort(ts) : '—'
})

interface ReleveManquant {
  zone: string
  cote: string
  detail: string
}

const loading = ref(true)
/** Texte montré à l'utilisateur ; le détail technique part dans la console. */
const MESSAGE_INDISPONIBLE =
  'Le décompte n\'a pas pu être chargé. Réessayez dans quelques minutes ; ' +
  'si le problème persiste, contactez l\'administrateur.'
const loadingMessage = ref('Chargement du décompte…')
const errored = ref(false)
const empty = ref(false)

function iso(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function bornesDuMois(key: string): Plage {
  const [y, m] = key.split('-').map(Number)
  return { du: `${key}-01`, au: iso(new Date(y, m, 0)) }
}

/** Clé du mois si la sélection est un mois entier connu du payload. */
const moisChoisi = computed<string | null>(() => {
  const { du, au } = selection.value
  if (!payload.value || !du) return null
  const key = du.slice(0, 7)
  const bornes = bornesDuMois(key)
  if (du !== bornes.du || au !== bornes.au) return null
  return payload.value.periodes.some((p) => p.key === key) ? key : null
})

/** Ce que montrent le bandeau KPI, le tableau et les messages. */
const vuePayload = computed<DecomptePayload | null>(() => (
  moisChoisi.value ? payload.value : payloadPlage.value
))
const vueKey = computed(() => moisChoisi.value ?? payloadPlage.value?.periode_cle ?? '')

/** Zones de la période, dans l'ordre du tableau. */
const zonesDisponibles = computed(() => (vuePayload.value?.zones ?? []).map((z) => ({ zone: z.zone, label: z.label })))
const zoneLabel = computed(() => zonesDisponibles.value.find((z) => z.zone === zoneChoisie.value)?.label ?? '')

// Pendant le calcul d'une nouvelle période, la liste est vide un instant :
// la zone choisie n'est oubliée que si la période chargée ne l'a pas.
watch(zonesDisponibles, (zones) => {
  if (zones.length && zoneChoisie.value && !zones.some((z) => z.zone === zoneChoisie.value)) {
    zoneChoisie.value = ''
  }
})

/** `vuePayload` restreint à la zone choisie. Le calcul reste celui de
 * l'immeuble (le serveur fait de même pour l'export) : seules les autres
 * zones sont retirées. */
const affiche = computed<DecomptePayload | null>(() => {
  const p = vuePayload.value
  if (!p || !zoneChoisie.value) return p
  const zones = p.reserves_zones ?? []
  return {
    ...p,
    zones: p.zones.filter((z) => z.zone === zoneChoisie.value),
    reserves: p.reserves.filter((_, i) => zones[i] === zoneChoisie.value),
    reserves_zones: zones.filter((z) => z === zoneChoisie.value),
  }
})

const dateMin = computed(() => {
  const premier = payload.value?.periodes[0]?.key
  return premier ? `${premier}-01` : ''
})
const dateMax = iso(new Date())

const selectedPeriod = computed<Period | null>(() => {
  if (!vuePayload.value) return null
  return vuePayload.value.periodes.find((p) => p.key === vueKey.value) || null
})

/** Verdict de la période choisie : les messages ne parlent que d'elle. */
const repartitionMois = computed<RepartitionInfo>(() => {
  const rep = vueKey.value ? vuePayload.value?.repartition[vueKey.value] : undefined
  return rep || { source: 'loxone', confiance_verifiee: false, raisons: [] }
})

const periodeEnCours = computed(() => {
  if (!vuePayload.value || !vueKey.value) return false
  return vuePayload.value.batiment.periodes[vueKey.value]?.en_cours ?? false
})

/** Dernier relevé réellement utilisé sur la période (zones + production). */
const dernierReleveTs = computed<number | null>(() => {
  if (!affiche.value || !vueKey.value) return null
  const stamps: number[] = []
  for (const z of affiche.value.zones) {
    const e = z.periodes[vueKey.value]
    if (!e) continue
    if (e.controle.releve_fin_ts) stamps.push(e.controle.releve_fin_ts)
    if (e.reseau.releve_fin_ts) stamps.push(e.reseau.releve_fin_ts)
    if (e.solaire.releve_fin_ts) stamps.push(e.solaire.releve_fin_ts)
  }
  const prod = affiche.value.batiment.periodes[vueKey.value]?.production.releve_fin_ts
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

const relevesManquants = computed<ReleveManquant[]>(() => {
  if (!affiche.value || !vueKey.value) return []
  const out: ReleveManquant[] = []
  for (const z of affiche.value.zones) {
    const e = z.periodes[vueKey.value]
    if (!e) continue
    for (const [cote, block] of [['Réseau', e.reseau], ['Solaire', e.solaire]] as const) {
      for (const detail of block.alertes) out.push({ zone: z.label, cote, detail })
    }
  }
  return out
})

function hasZoneData(p: DecomptePayload, periodKey: string): boolean {
  return p.zones.some((z) => {
    const e = z.periodes[periodKey]
    return e && e.total !== null
  })
}

/** Décompte des dates choisies, quand ce n'est pas un mois entier. Une
 * réponse arrivée après un autre choix est ignorée. */
async function chargerPlage() {
  const site = currentSite.value
  const { du, au } = selection.value
  const numero = ++requetePlage
  erreurPlage.value = ''
  if (!site || !du || moisChoisi.value) {
    payloadPlage.value = null
    chargementPlage.value = false
    return
  }
  chargementPlage.value = true
  try {
    const data = await fetchDecomptePlage(site, du, au)
    if (numero !== requetePlage) return
    payloadPlage.value = data
  } catch (err) {
    if (numero !== requetePlage) return
    console.error(err)
    payloadPlage.value = null
    // Le calendrier n'envoie que des dates valides : un refus du serveur
    // veut dire, en pratique, qu'il n'a aucun relevé sur la période.
    erreurPlage.value = (err as { status?: number }).status === 400
      ? 'Pas de relevés sur cette période : choisissez d\'autres dates.'
      : MESSAGE_INDISPONIBLE
  } finally {
    if (numero === requetePlage) chargementPlage.value = false
  }
}

watch(selection, () => {
  downloadError.value = ''
  void chargerPlage()
})

async function loadSite(site: string) {
  currentSite.value = site
  zoneChoisie.value = ''  // deux sites peuvent avoir chacun un « App 1 »
  localStorage.setItem(SITE_STORAGE_KEY, site)

  loading.value = true
  errored.value = false
  empty.value = false
  loadingMessage.value = 'Chargement du décompte…'
  payloadPlage.value = null

  try {
    const data = await fetchDecompte({ miniserver: site })
    payload.value = data

    if (!data.periodes.length || !data.zones.length) {
      loading.value = false
      empty.value = true
      loadingMessage.value = 'Pas encore de relevés pour établir un décompte sur ce site.'
      return
    }

    const dernier = (list: Period[]) => list[list.length - 1]
    const avecDonnees = (list: Period[]) => list.filter((p) => hasZoneData(data, p.key))
    const termines = data.periodes.filter((p) => !data.batiment.periodes[p.key].en_cours)
    const defaut = dernier(avecDonnees(termines)) || dernier(avecDonnees(data.periodes)) ||
      dernier(termines) || dernier(data.periodes)
    periodeKey.value = defaut.key
    selection.value = bornesDuMois(defaut.key)

    loading.value = false
  } catch (err) {
    loading.value = false
    errored.value = true
    console.error(err)
    loadingMessage.value = MESSAGE_INDISPONIBLE
  }
}

/** Relit les Statistics du site puis recharge le décompte, sans repasser
 * par l'écran de chargement ni changer la période choisie. Si le
 * Miniserver ne répond pas, le bandeau « Relevés pas à jour » le dit
 * (statistiques_erreur). */
async function actualiser() {
  const site = currentSite.value
  if (!site || actualisation.value) return
  actualisation.value = true
  actualisationErreur.value = ''
  try {
    await actualiserDecompte(site)
    const data = await fetchDecompte({ miniserver: site })
    if (site !== currentSite.value) return
    payload.value = data
    await chargerPlage()
  } catch (err) {
    console.error(err)
    actualisationErreur.value = 'L\'actualisation n\'a pas abouti. Réessayez dans quelques minutes.'
  } finally {
    actualisation.value = false
  }
}

async function download(format: 'xlsx' | 'csv') {
  if (!currentSite.value || !vueKey.value) return
  if (relevesManquants.value.length) {
    confirmFormat.value = format
    return
  }
  await runDownload(format)
}

async function confirmDownload() {
  const format = confirmFormat.value
  confirmFormat.value = null
  if (format) await runDownload(format)
}

async function runDownload(format: 'xlsx' | 'csv') {
  if (!currentSite.value || !vueKey.value) return
  downloading.value = true
  downloadError.value = ''
  try {
    if (moisChoisi.value) {
      await downloadDecompte(format, currentSite.value, moisChoisi.value, zoneChoisie.value || undefined)
    } else {
      await downloadDecomptePeriode(
        format, currentSite.value, selection.value.du, selection.value.au, zoneChoisie.value || undefined,
      )
    }
  } catch (err) {
    console.error(err)
    downloadError.value = 'Le fichier n\'a pas pu être préparé. Réessayez dans quelques minutes.'
  } finally {
    downloading.value = false
  }
}

let demarre = false

onMounted(async () => {
  try {
    await loadSites()
    demarre = true
    if (!siteActif.value) {
      loading.value = false
      errored.value = true
      loadingMessage.value = 'Aucun site n\'est accessible avec ce compte.'
      return
    }
    await loadSite(siteActif.value)
  } catch (err) {
    loading.value = false
    errored.value = true
    console.error(err)
    loadingMessage.value = MESSAGE_INDISPONIBLE
  }
})

watch(siteActif, (name) => {
  if (!demarre || !name || name === currentSite.value) return
  void loadSite(name)
})
</script>

<template>
  <div class="w-full px-4 py-6">
    <main class="min-w-0 w-full">
    <h1 class="mb-4 text-xl font-bold text-neutral-900">Décompte de charges</h1>
    <p class="mb-6 text-sm text-neutral-500">
      Consommation de chaque zone répartie en part réseau et part solaire
      autoconsommée — heure locale Europe/Zurich.
    </p>

    <div class="space-y-6">

    <GlobalBanner
      v-if="payload && !loading && !empty"
      :payload="affiche ?? payload"
      :period-key="affiche ? vueKey : periodeKey"
    />

    <p v-if="loading || empty" class="text-sm text-neutral-500">{{ loadingMessage }}</p>

    <template v-else-if="payload && !errored">
      <Card>
        <div class="flex flex-wrap items-end gap-4">
          <div class="flex flex-col text-sm text-neutral-600">
            Période
            <PeriodPicker v-model="selection" :min="dateMin" :max="dateMax" />
          </div>
          <div v-if="zonesDisponibles.length > 1" class="flex flex-col text-sm text-neutral-600">
            <label for="zone-choisie">Zone</label>
            <select
              id="zone-choisie"
              v-model="zoneChoisie"
              class="mt-1 rounded border border-neutral-300 bg-white py-1.5 pr-8 pl-3 text-sm font-medium text-neutral-900"
            >
              <option value="">Tout l'immeuble</option>
              <option v-for="z in zonesDisponibles" :key="z.zone" :value="z.zone">{{ z.label }}</option>
            </select>
          </div>
          <!-- Des dates libres sont déjà dans le calendrier : la ligne ne
               sert que pour un mois, ou une période arrêtée au dernier relevé. -->
          <div v-if="moisChoisi || periodeEnCours" class="flex flex-col text-sm text-neutral-600">
            Période couverte
            <span class="mt-1 py-1 font-medium text-neutral-900">{{ periodeCouverture }}</span>
          </div>
          <div class="flex flex-col text-sm text-neutral-600 sm:ml-auto">
            Relevés à jour au
            <span class="mt-1 flex items-center gap-2 py-1">
              <span class="font-medium text-neutral-900">{{ aJourAu }}</span>
              <button
                type="button"
                class="rounded border border-neutral-300 px-2 py-0.5 text-sm text-neutral-800 hover:bg-neutral-50 disabled:opacity-50"
                :disabled="actualisation"
                @click="actualiser"
              >{{ actualisation ? 'Actualisation…' : 'Actualiser' }}</button>
            </span>
          </div>
        </div>
        <p v-if="actualisationErreur" class="mt-2 text-sm text-red-600">{{ actualisationErreur }}</p>

        <div class="mt-6">
          <p v-if="chargementPlage" class="text-sm text-neutral-500">Calcul du décompte sur ces dates…</p>
          <p v-else-if="erreurPlage" class="text-sm text-red-600" role="alert">{{ erreurPlage }}</p>
          <PeriodeKpis v-else-if="affiche" :payload="affiche" :period-key="vueKey" />
        </div>
      </Card>

      <Card
        v-if="affiche && !chargementPlage && !erreurPlage"
        :title="zoneChoisie ? `Décompte — ${zoneLabel}` : 'Décompte par zone'"
        :hint="selectedPeriod?.label"
      >
        <div class="mb-4 flex flex-wrap items-center gap-2">
          <button
            type="button"
            class="rounded border border-neutral-300 px-3 py-1 text-sm text-neutral-800 hover:bg-neutral-50 disabled:opacity-50"
            :disabled="downloading"
            @click="download('xlsx')"
          >Télécharger Excel</button>
          <button
            type="button"
            class="rounded border border-neutral-300 px-3 py-1 text-sm text-neutral-800 hover:bg-neutral-50 disabled:opacity-50"
            :disabled="downloading"
            @click="download('csv')"
          >Télécharger CSV</button>
          <span v-if="downloadError" class="text-sm text-red-600">{{ downloadError }}</span>
        </div>
        <ZoneTable
          :payload="affiche"
          :period-key="vueKey"
          :total-label="zoneChoisie ? 'Total' : 'Total immeuble'"
        />
        <div
          v-if="relevesManquants.length"
          class="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900"
        >
          <p class="font-semibold">Relevés incomplets sur cette période</p>
          <ul class="mt-2 list-disc space-y-1 pl-5">
            <li v-for="(r, i) in relevesManquants" :key="i">
              {{ r.zone }} — {{ r.cote }} : {{ r.detail }}
            </li>
          </ul>
          <p class="mt-2">
            Le décompte est calculé avec les relevés disponibles. Le téléchargement
            demande de confirmer qu'il est établi en l'état.
          </p>
        </div>
        <p class="mt-4 text-sm text-neutral-500">
          La consommation de chaque zone est scindée en deux : les kWh
          <strong>achetés au réseau</strong> et les kWh
          <strong>solaires autoconsommés</strong>.
        </p>
        <p v-if="repartitionMois.source === 'calculee'" class="mt-2 text-sm text-neutral-600">
          Ces parts sont calculées. La consommation vient du compteur du lot,
          répartie selon le mix du bâtiment (production moins réinjection).
          Les séries Grid et Solaire du Miniserver ne servent pas à ce décompte.
        </p>
        <p v-else-if="repartitionMois.source === 'mixte'" class="mt-2 text-sm text-neutral-600">
          Selon le mois, ces parts sont calculées (compteur du lot réparti
          selon le mix du bâtiment) ou mesurées par le Miniserver. Le fichier
          Excel donne le détail mois par mois.
        </p>
        <p
          v-else-if="!repartitionMois.confiance_verifiee"
          class="mt-2 text-sm text-neutral-600"
        >
          Ces parts sont les mesures du Miniserver. Le bilan du bâtiment ne
          permet pas de les revérifier.
        </p>
        <ul
          v-if="repartitionMois.source !== 'loxone' && repartitionMois.raisons.length"
          class="mt-2 list-disc space-y-1 pl-5 text-sm text-neutral-500"
        >
          <li v-for="(raison, i) in repartitionMois.raisons" :key="i">{{ raison }}</li>
        </ul>
        <div
          v-if="affiche.reserves?.length"
          class="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900"
        >
          <p class="font-semibold">Sous réserve</p>
          <p v-for="(reserve, i) in affiche.reserves" :key="i" class="mt-1">{{ reserve }}</p>
        </div>
      </Card>

    </template>

    <p v-else-if="errored" class="text-sm text-red-600">{{ loadingMessage }}</p>
    </div>

    <div
      v-if="confirmFormat"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="decompte-confirm-title"
    >
      <div class="w-full max-w-lg rounded-lg bg-white p-6 shadow-lg">
        <h2 id="decompte-confirm-title" class="text-lg font-semibold text-neutral-900">
          Établir le décompte en l'état
        </h2>
        <p class="mt-2 text-sm text-neutral-600">
          Des relevés manquent pour {{ selectedPeriod?.label }}. Le fichier sera
          produit avec les données disponibles, sans ces relevés.
        </p>
        <ul class="mt-3 max-h-48 list-disc space-y-1 overflow-y-auto pl-5 text-sm text-amber-900">
          <li v-for="(r, i) in relevesManquants" :key="i">
            {{ r.zone }} — {{ r.cote }} : {{ r.detail }}
          </li>
        </ul>
        <div class="mt-6 flex justify-end gap-2">
          <button
            type="button"
            class="rounded border border-neutral-300 px-3 py-1.5 text-sm"
            @click="confirmFormat = null"
          >Annuler</button>
          <button
            type="button"
            class="rounded bg-neutral-900 px-3 py-1.5 text-sm text-white"
            @click="confirmDownload"
          >Établir le décompte</button>
        </div>
      </div>
    </div>
    </main>
  </div>
</template>
