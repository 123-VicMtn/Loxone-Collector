<script setup lang="ts">
/**
 * Relevés : dernier index connu de chaque compteur cumulatif
 * (total / totalNeg). La page filtre et regroupe ces index ;
 * elle ne calcule pas de consommation.
 */

import { computed, onMounted, ref, watch } from 'vue'
import { fetchJSON } from '@shared/api/http'
import { compareApartments, fmtNumber, zoneLabel } from '@shared/format'
import {
  FAMILY_LABELS,
  FAMILY_ORDER,
  compteurCount,
  groupMeters,
  matchesQuery,
  meterIsStale,
  readingClock,
  shownTs,
  type MeterFamily,
  type MeterRow,
  type Releve,
} from './releves/present'

const SITE_KEY = 'releves-site'
/** Au-delà, les zones se replient : Sequoia dépasse les 200 compteurs. */
const ACCORDION_ABOVE = 40

const FAMILY_DOT: Record<MeterFamily, string> = {
  electricite: 'bg-amber-400',
  eau: 'bg-sky-500',
  chaleur: 'bg-orange-500',
  autre: 'bg-neutral-400',
}

const rows = ref<Releve[]>([])
const loading = ref(true)
const error = ref('')
const site = ref('')
const query = ref('')
const family = ref<MeterFamily | 'tous'>('tous')
const onlyStale = ref(false)
const expanded = ref<Record<string, boolean>>({})
const copiedId = ref('')
const nowMs = ref(Date.now())
let copyTimer = 0

const siteNames = computed(() => Array.from(new Set(rows.value.map((r) => r.miniserver))).sort())

const siteRows = computed(() => rows.value.filter((r) => r.miniserver === site.value))
const siteMeters = computed(() => groupMeters(siteRows.value))

const zones = computed(() => {
  const codes = Array.from(new Set(siteMeters.value.map((m) => m.apartment)))
  codes.sort((a, b) => {
    if (!a) return -1
    if (!b) return 1
    return compareApartments(a, b)
  })
  return codes.map((apartment) => ({ apartment, label: zoneLabel(apartment) }))
})

const familyCounts = computed(() => {
  const counts: Record<MeterFamily, number> = { electricite: 0, eau: 0, chaleur: 0, autre: 0 }
  for (const meter of scopedMeters(false)) counts[meter.family] += 1
  return counts
})

const familyChoices = computed(() => FAMILY_ORDER.filter((f) => familyCounts.value[f] > 0))

const staleCount = computed(() => scopedMeters(false).filter((m) => meterIsStale(m, nowMs.value)).length)

const visibleMeters = computed(() => scopedMeters(true))

const showFamilyFilters = computed(() => familyChoices.value.length > 1 || staleCount.value > 0)

interface ZoneGroup {
  apartment: string
  label: string
  meters: MeterRow[]
  latestTs: number | null
  hasStale: boolean
}

const groups = computed<ZoneGroup[]>(() => {
  const byZone = new Map<string, MeterRow[]>()
  for (const meter of visibleMeters.value) {
    const list = byZone.get(meter.apartment) ?? []
    list.push(meter)
    byZone.set(meter.apartment, list)
  }
  return zones.value
    .filter((z) => byZone.has(z.apartment))
    .map((z) => {
      const meters = byZone.get(z.apartment) ?? []
      const times = meters.map(shownTs).filter((t): t is number => t !== null)
      return {
        apartment: z.apartment,
        label: z.label,
        meters,
        latestTs: times.length ? Math.max(...times) : null,
        hasStale: meters.some((m) => meterIsStale(m, nowMs.value)),
      }
    })
})

/** Une recherche ou un filtre ouvre les zones : le résultat doit se lire tout de suite. */
const narrowed = computed(() =>
  query.value.trim() !== '' || family.value !== 'tous' || onlyStale.value,
)

const accordion = computed(() => !narrowed.value && visibleMeters.value.length > ACCORDION_ABOVE)

const summary = computed(() => compteurCount(visibleMeters.value.length))

/** Compteurs du site, après zone et recherche. `withChips` applique aussi famille et anciens. */
function scopedMeters(withChips: boolean): MeterRow[] {
  const q = query.value.trim().toLowerCase()
  return siteMeters.value.filter((meter) => {
    if (q) {
      const hay = `${zoneLabel(meter.apartment)} ${meter.apartment} ${meter.name} ${meter.unit}`
      if (!matchesQuery(hay, q)) return false
    }
    if (!withChips) return true
    if (family.value !== 'tous' && meter.family !== family.value) return false
    if (onlyStale.value && !meterIsStale(meter, nowMs.value)) return false
    return true
  })
}

function pickSite(names: string[]): string {
  if (site.value && names.includes(site.value)) return site.value
  let saved = ''
  try {
    saved = localStorage.getItem(SITE_KEY) || localStorage.getItem('decompte-site') || ''
  } catch {
    saved = ''
  }
  if (saved && names.includes(saved)) return saved
  return names[0] || ''
}

function isOpen(apartment: string): boolean {
  if (!accordion.value) return true
  return expanded.value[apartment] === true
}

/** Une zone ouverte prend toute la largeur pour lire ses index.
 * Repliée, elle reste une tuile dans la grille. */
function wideCard(group: ZoneGroup): boolean {
  if (!isOpen(group.apartment)) return false
  if (accordion.value) return true
  return group.meters.length > 8
}

function toggleZone(apartment: string) {
  if (!accordion.value) return
  expanded.value = { ...expanded.value, [apartment]: !expanded.value[apartment] }
}

function selectFamily(next: MeterFamily | 'tous') {
  family.value = next
  if (next !== 'tous') onlyStale.value = false
}

function showAll() {
  family.value = 'tous'
  onlyStale.value = false
}

function toggleStale() {
  onlyStale.value = !onlyStale.value
  if (onlyStale.value) family.value = 'tous'
}

function clock(ts: number | null) {
  return readingClock(ts, nowMs.value)
}

async function copyValue(id: string, value: number | null) {
  if (value === null) return
  try {
    await navigator.clipboard.writeText(fmtNumber(value, 3))
  } catch {
    return
  }
  copiedId.value = id
  window.clearTimeout(copyTimer)
  copyTimer = window.setTimeout(() => {
    if (copiedId.value === id) copiedId.value = ''
  }, 1500)
}

watch([site, family, onlyStale, query, accordion], () => {
  if (!accordion.value) {
    expanded.value = {}
    return
  }
  const hasBuilding = groups.value.some((g) => g.apartment === '')
  expanded.value = hasBuilding ? { '': true } : {}
})

watch(familyChoices, (choices) => {
  if (family.value !== 'tous' && !choices.includes(family.value)) family.value = 'tous'
})

watch(site, (name) => {
  if (!name) return
  try {
    localStorage.setItem(SITE_KEY, name)
  } catch {
    /* navigation privée : le choix reste en mémoire le temps de la page */
  }
})

onMounted(async () => {
  try {
    rows.value = await fetchJSON<Releve[]>('/api/releves')
    site.value = pickSite(siteNames.value)
    nowMs.value = Date.now()
  } catch (err) {
    console.error(err)
    error.value = 'Les relevés n\'ont pas pu être chargés. Réessayez dans quelques minutes.'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <p v-if="loading" class="text-sm text-neutral-500">Chargement des relevés…</p>
  <p v-else-if="error" class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{{ error }}</p>
  <p v-else-if="!rows.length" class="rounded-lg border border-dashed border-neutral-300 px-4 py-8 text-center text-sm text-neutral-500">Aucun relevé pour le moment.</p>

  <div v-else class="flex flex-col gap-4">
    <div class="z-20 rounded-lg border border-neutral-200 bg-white p-3 shadow-sm md:sticky md:top-14 md:bg-white/95 md:backdrop-blur lg:top-0">
      <div class="flex flex-col gap-3 sm:flex-row sm:items-end">
        <label v-if="siteNames.length > 1" class="flex w-full flex-col gap-1 text-xs font-medium text-neutral-500 sm:w-52">
          Immeuble
          <select v-model="site" class="h-9 rounded-md border border-neutral-300 bg-white px-2 text-sm font-normal text-neutral-900">
            <option v-for="name in siteNames" :key="name" :value="name">{{ name }}</option>
          </select>
        </label>

        <label class="relative min-w-0 flex-1">
          <span class="sr-only">Recherche</span>
          <svg class="pointer-events-none absolute top-1/2 left-2.5 h-4 w-4 -translate-y-1/2 text-neutral-400" viewBox="0 0 20 20" fill="none" aria-hidden="true">
            <circle cx="8.5" cy="8.5" r="5.25" stroke="currentColor" stroke-width="1.5" />
            <path d="M12.5 12.5L16.5 16.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
          </svg>
          <input
            v-model="query"
            type="search"
            placeholder="Appartement ou compteur"
            aria-label="Recherche"
            class="h-9 w-full rounded-md border border-neutral-300 bg-white pr-3 pl-8 text-sm text-neutral-900 placeholder:text-neutral-400"
            @keydown.escape="query = ''"
          >
        </label>
      </div>

      <div v-if="showFamilyFilters || summary" class="mt-3 flex flex-wrap items-center gap-2">
        <div v-if="showFamilyFilters" class="flex flex-wrap gap-1.5" role="group" aria-label="Type de compteur">
          <button
            v-if="familyChoices.length > 1"
            type="button"
            class="inline-flex h-8 items-center rounded-full border px-3 text-sm"
            :class="family === 'tous' && !onlyStale ? 'border-blue-600 bg-blue-600 text-white' : 'border-neutral-200 bg-white text-neutral-700 hover:border-neutral-300'"
            :aria-pressed="family === 'tous' && !onlyStale"
            @click="showAll"
          >Tous</button>
          <button
            v-for="choice in familyChoices"
            :key="choice"
            type="button"
            class="inline-flex h-8 items-center gap-1.5 rounded-full border px-2.5 text-sm"
            :class="family === choice ? 'border-blue-600 bg-blue-600 text-white' : 'border-neutral-200 bg-white text-neutral-700 hover:border-neutral-300'"
            :aria-pressed="family === choice"
            @click="selectFamily(choice)"
          >
            <span class="h-1.5 w-1.5 rounded-full" :class="family === choice ? 'bg-white' : FAMILY_DOT[choice]" />
            {{ FAMILY_LABELS[choice] }}
          </button>
          <button
            v-if="staleCount"
            type="button"
            class="inline-flex h-8 items-center rounded-full border px-3 text-sm"
            :class="onlyStale ? 'border-amber-700 bg-amber-700 text-white' : 'border-amber-200 bg-amber-50 text-amber-900 hover:border-amber-300'"
            :aria-pressed="onlyStale"
            @click="toggleStale"
          >Pas à jour</button>
        </div>
        <p class="text-sm text-neutral-500 sm:ml-auto">{{ summary }}</p>
      </div>
    </div>

    <p v-if="!groups.length" class="rounded-lg border border-dashed border-neutral-300 px-4 py-10 text-center text-sm text-neutral-500">
      Aucun compteur ne correspond.
    </p>

    <div v-else class="grid grid-cols-1 gap-3 md:grid-cols-2">
      <article
        v-for="group in groups"
        :key="group.apartment || 'batiment'"
        class="overflow-hidden rounded-lg border bg-white shadow-sm"
        :class="[
          wideCard(group) ? 'md:col-span-2' : '',
          accordion && isOpen(group.apartment) ? 'border-blue-200' : 'border-neutral-200',
        ]"
      >
        <button
          v-if="accordion"
          type="button"
          class="flex w-full items-center gap-2 px-4 py-3 text-left hover:bg-neutral-50"
          :aria-expanded="isOpen(group.apartment)"
          @click="toggleZone(group.apartment)"
        >
          <svg
            class="h-4 w-4 shrink-0 text-neutral-400 transition-transform"
            :class="isOpen(group.apartment) ? 'rotate-90' : ''"
            viewBox="0 0 20 20"
            fill="currentColor"
            aria-hidden="true"
          >
            <path d="M7.2 4.5a1 1 0 0 1 1.4 0l5 5.2a1 1 0 0 1 0 1.4l-5 5.2a1 1 0 1 1-1.4-1.4L11.4 10 7.2 5.9a1 1 0 0 1 0-1.4Z" />
          </svg>
          <span class="text-sm font-semibold text-neutral-900">{{ group.label }}</span>
          <span class="text-xs text-neutral-500">{{ compteurCount(group.meters.length) }}</span>
          <span
            v-if="group.hasStale"
            class="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-900"
          >pas à jour</span>
        </button>
        <div v-else class="flex items-center gap-2 px-4 py-3">
          <h2 class="text-sm font-semibold text-neutral-900">{{ group.label }}</h2>
          <span class="text-xs text-neutral-500">{{ compteurCount(group.meters.length) }}</span>
          <span
            v-if="group.hasStale"
            class="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-900"
          >pas à jour</span>
        </div>

        <ul
          v-if="isOpen(group.apartment)"
          class="border-t border-neutral-100 p-1.5"
          :class="wideCard(group) ? 'md:grid md:grid-cols-2 md:gap-x-2' : ''"
        >
          <li
            v-for="meter in group.meters"
            :key="meter.key"
            class="flex items-start justify-between gap-3 rounded-md px-2.5 py-2"
            :class="meterIsStale(meter, nowMs) ? 'bg-amber-50' : 'hover:bg-neutral-50'"
          >
            <div class="min-w-0 flex-1 pt-0.5">
              <p class="text-sm leading-5 text-neutral-800">{{ meter.name }}</p>
            </div>
            <div class="shrink-0 text-right whitespace-nowrap">
              <button
                v-if="meter.index && meter.index.value !== null"
                type="button"
                class="rounded px-1 text-[15px] font-medium text-neutral-900 tabular-nums hover:bg-blue-50 hover:text-blue-800"
                :class="meter.index && copiedId === meter.index.seriesId ? 'text-green-700' : ''"
                :title="`Copier le relevé ${fmtNumber(meter.index.value, 3)}`"
                @click="copyValue(meter.index.seriesId, meter.index.value)"
              >
                {{ meter.index && copiedId === meter.index.seriesId ? 'copié' : fmtNumber(meter.index.value, 3) }}
                <span v-if="!(meter.index && copiedId === meter.index.seriesId) && meter.unit" class="ml-1 text-xs font-normal text-neutral-400">{{ meter.unit }}</span>
              </button>
              <span v-else class="text-neutral-400">—</span>
              <div v-if="meter.inverse && meter.inverse.value !== null">
                <button
                  type="button"
                  class="rounded px-1 text-xs text-neutral-500 tabular-nums hover:bg-blue-50 hover:text-blue-800"
                  :class="copiedId === meter.inverse.seriesId ? 'text-green-700' : ''"
                  :title="`Relevé dans l'autre sens. Copier ${fmtNumber(meter.inverse.value, 3)}`"
                  @click="copyValue(meter.inverse.seriesId, meter.inverse.value)"
                >
                  <template v-if="copiedId === meter.inverse.seriesId">copié</template>
                  <template v-else>
                    autre sens {{ fmtNumber(meter.inverse.value, 3) }}
                    <span v-if="meter.unit">{{ meter.unit }}</span>
                  </template>
                </button>
              </div>
              <p
                class="mt-0.5 text-xs tabular-nums"
                :class="clock(shownTs(meter)).stale ? 'text-amber-800' : 'text-neutral-400'"
                :title="clock(shownTs(meter)).title"
              >
                {{ clock(shownTs(meter)).text }}
                <span v-if="clock(shownTs(meter)).stale" class="ml-1 font-medium text-amber-800">pas à jour</span>
              </p>
            </div>
          </li>
        </ul>
      </article>
    </div>
  </div>
</template>
