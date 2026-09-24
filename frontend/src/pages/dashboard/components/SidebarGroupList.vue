<script setup lang="ts">
import type { ApartmentGroup, RoomGroup } from '../types/sidebar'
import SeriesCheckboxList from './SeriesCheckboxList.vue'

defineProps<{
  groupMode: 'apartment' | 'room'
  apartmentGroups: ApartmentGroup[]
  roomGroups: RoomGroup[]
}>()

function totalOf(apt: ApartmentGroup): number {
  return apt.types.reduce((sum, t) => sum + t.series.length, 0)
}
</script>

<template>
  <template v-if="groupMode === 'apartment'">
    <details v-for="apt in apartmentGroups" :key="apt.label" open class="ml-2">
      <summary class="cursor-pointer py-1 text-sm font-medium text-neutral-800">
        {{ apt.label }} <span class="text-neutral-400">({{ totalOf(apt) }})</span>
      </summary>
      <details v-for="t in apt.types" :key="t.label" open class="ml-3">
        <summary class="cursor-pointer py-1 text-sm text-neutral-600">
          {{ t.label }} <span class="text-neutral-400">({{ t.series.length }})</span>
        </summary>
        <SeriesCheckboxList :series="t.series" />
      </details>
    </details>
  </template>
  <template v-else>
    <details v-for="room in roomGroups" :key="room.label" open class="ml-2">
      <summary class="cursor-pointer py-1 text-sm font-medium text-neutral-800">
        {{ room.label }} <span class="text-neutral-400">({{ room.series.length }})</span>
      </summary>
      <SeriesCheckboxList :series="room.series" />
    </details>
  </template>
</template>
