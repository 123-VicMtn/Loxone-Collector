<script setup lang="ts">
/**
 * Sidebar de sélection des capteurs (colonne de gauche, onglet Explorer).
 * Port de static/js/sidebar.js -- ne connaît rien de "quel capteur est
 * actuellement sélectionné" ni du graph associé, c'est la responsabilité
 * de l'appelant (ExplorerTab.vue), passée via `isSelected`/`onToggle`
 * (provide/inject, voir sidebarSelection.ts). Garde la sidebar réutilisable
 * si un jour un autre onglet a besoin d'une sélection multi-capteurs.
 */

import { computed, onMounted, provide, ref } from 'vue'
import type { Series } from '@shared/types/series'
import { loadAllSeries } from '@shared/api/series'
import { loadResourceTypeLabels } from '@shared/config'
import { compareApartments, resourceLabel } from '@shared/format'
import type { ApartmentGroup, RoomGroup } from '../types/sidebar'
import { SIDEBAR_SELECTION_KEY, type SidebarSelection } from '../sidebarSelection'
import SidebarGroupList from './SidebarGroupList.vue'

const props = defineProps<{
  isSelected: (seriesId: string) => boolean
  onToggle: (series: Series, checked: boolean) => void
}>()

provide<SidebarSelection>(SIDEBAR_SELECTION_KEY, {
  isSelected: (id) => props.isSelected(id),
  onToggle: (s, checked) => props.onToggle(s, checked),
})

const allSeries = ref<Series[]>([])
const labels = ref<Record<string, string>>({})
const groupMode = ref<'apartment' | 'room'>('apartment')

onMounted(async () => {
  const [series, labelMap] = await Promise.all([loadAllSeries(), loadResourceTypeLabels()])
  allSeries.value = series
  labels.value = labelMap
})

const bySite = computed(() => {
  const map = new Map<string, Series[]>()
  for (const s of allSeries.value) {
    const site = s.miniserver || ''
    if (!map.has(site)) map.set(site, [])
    map.get(site)!.push(s)
  }
  return map
})

const sites = computed(() => Array.from(bySite.value.keys()).sort((a, b) => a.localeCompare(b)))
const multiSite = computed(() => sites.value.length > 1)

function buildApartmentGroups(seriesArr: Series[]): ApartmentGroup[] {
  const byApartment = new Map<string, Map<string, Series[]>>()
  for (const s of seriesArr) {
    const apt = s.apartment || 'Sans appartement'
    const typeLabel = resourceLabel(s.resource_type, labels.value)
    if (!byApartment.has(apt)) byApartment.set(apt, new Map())
    const byType = byApartment.get(apt)!
    if (!byType.has(typeLabel)) byType.set(typeLabel, [])
    byType.get(typeLabel)!.push(s)
  }
  return Array.from(byApartment.keys())
    .sort(compareApartments)
    .map((apt) => ({
      label: apt,
      types: Array.from(byApartment.get(apt)!.keys())
        .sort((a, b) => a.localeCompare(b))
        .map((typeLabel) => ({ label: typeLabel, series: byApartment.get(apt)!.get(typeLabel)! })),
    }))
}

function buildRoomGroups(seriesArr: Series[]): RoomGroup[] {
  const byRoom = new Map<string, Series[]>()
  for (const s of seriesArr) {
    const room = s.room || 'Sans pièce'
    if (!byRoom.has(room)) byRoom.set(room, [])
    byRoom.get(room)!.push(s)
  }
  return Array.from(byRoom.keys())
    .sort((a, b) => a.localeCompare(b))
    .map((room) => ({ label: room, series: byRoom.get(room)! }))
}

const siteGroups = computed(() => sites.value.map((site) => {
  const seriesForSite = bySite.value.get(site)!
  return {
    site,
    count: seriesForSite.length,
    apartmentGroups: groupMode.value === 'apartment' ? buildApartmentGroups(seriesForSite) : [],
    roomGroups: groupMode.value === 'room' ? buildRoomGroups(seriesForSite) : [],
  }
}))
</script>

<template>
  <div>
    <div class="mb-3 text-sm">
      <button
        type="button"
        :class="['rounded px-2 py-1', groupMode === 'apartment' ? 'bg-blue-100 text-blue-800' : 'text-neutral-500 hover:bg-neutral-100']"
        @click="groupMode = 'apartment'"
      >Par appartement</button>
      <button
        type="button"
        :class="['ml-1 rounded px-2 py-1', groupMode === 'room' ? 'bg-blue-100 text-blue-800' : 'text-neutral-500 hover:bg-neutral-100']"
        @click="groupMode = 'room'"
      >Par pièce</button>
    </div>

    <p v-if="!allSeries.length" class="text-sm text-neutral-500">
      Aucun capteur en base pour l'instant. Le poller n'a peut-être pas
      encore terminé son premier cycle — recharge cette page dans
      quelques instants, ou consulte <a href="/health" class="text-blue-600 hover:underline">/health</a>.
    </p>

    <template v-else>
      <template v-for="sg in siteGroups" :key="sg.site">
        <details v-if="multiSite" open>
          <summary class="cursor-pointer py-1 text-sm font-semibold text-neutral-900">
            {{ sg.site || 'Sans site' }} <span class="text-neutral-400">({{ sg.count }})</span>
          </summary>
          <SidebarGroupList
            :group-mode="groupMode"
            :apartment-groups="sg.apartmentGroups"
            :room-groups="sg.roomGroups"
          />
        </details>
        <SidebarGroupList
          v-else
          :group-mode="groupMode"
          :apartment-groups="sg.apartmentGroups"
          :room-groups="sg.roomGroups"
        />
      </template>
    </template>
  </div>
</template>
